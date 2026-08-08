/**
 * Server-only data access for routines (website-side routing facts: what a
 * routine is called, which systems it touches, where it runs) and their
 * approver assignments. Every query runs through withOrgContext so RLS
 * bounds it; org ids arrive from the verified session, never from client
 * input.
 */
import "server-only";

import type { PoolClient } from "pg";

import { withOrgContext, type DbContext } from "@/lib/db";

export interface RoutineRecord {
  id: string;
  name: string;
  /** Plain descriptor shown in lists: no jargon, no ids. */
  descriptor: string;
  systems: string[];
  /** Numeric string as Postgres returns it, e.g. "75.00". */
  budgetCapUsd: string;
  planSteps: string[];
  workspaceId: string | null;
  /** Plain schedule text; null means on demand only. */
  scheduleDescription: string | null;
  /** Catalog provenance: the entry and pinned version of the install. */
  sourceEntryId: string | null;
  sourceVersion: number | null;
  createdAt: Date;
}

interface RoutineRow {
  id: string;
  name: string;
  descriptor: string;
  systems: string[];
  budgetCapUsd: string;
  planSteps: unknown;
  workspaceId: string | null;
  scheduleDescription: string | null;
  sourceEntryId: string | null;
  sourceVersion: number | null;
  createdAt: Date;
}

const ROUTINE_COLUMNS = `id, name, descriptor, systems,
       budget_cap_usd AS "budgetCapUsd",
       plan_steps AS "planSteps",
       workspace_id AS "workspaceId",
       schedule_description AS "scheduleDescription",
       source_entry_id AS "sourceEntryId",
       source_version AS "sourceVersion",
       created_at AS "createdAt"`;

/** Hydrate a stored row into the typed shape, tolerating older rows. */
function toRecord(row: RoutineRow): RoutineRecord {
  return {
    ...row,
    planSteps: Array.isArray(row.planSteps)
      ? (row.planSteps as unknown[]).filter((step): step is string => typeof step === "string")
      : [],
  };
}

/** Route params are arbitrary text; only a uuid can be a row id. */
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The org's routines, oldest first so lists keep a stable order. */
export async function listRoutines(orgId: string): Promise<RoutineRecord[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<RoutineRow>(
      `SELECT ${ROUTINE_COLUMNS} FROM routines WHERE org_id = $1 ORDER BY created_at, id`,
      [orgId],
    );
    return rows.map(toRecord);
  });
}

/** One routine, or null when it does not exist in this org. */
export async function getRoutine(orgId: string, routineId: string): Promise<RoutineRecord | null> {
  if (!UUID_PATTERN.test(routineId)) return null;
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<RoutineRow>(
      `SELECT ${ROUTINE_COLUMNS} FROM routines WHERE org_id = $1 AND id = $2`,
      [orgId, routineId],
    );
    return rows[0] ? toRecord(rows[0]) : null;
  });
}

export interface InstallRoutineInput {
  name: string;
  descriptor: string;
  systems: string[];
  budgetCapUsd: number;
  workspaceId: string;
  sourceEntryId: string;
  sourceVersion: number;
}

/**
 * Record a catalog install as a real routine of the org. Installing the
 * same entry again re-pins the existing routine (fresh snapshot fields,
 * possibly a new workspace) instead of stacking duplicates; the partial
 * unique index in migration 0006 is what makes the upsert well-defined.
 * Runs on the caller's client so the install and its audit row share one
 * transaction.
 */
export async function upsertInstalledRoutine(
  client: PoolClient,
  ctx: Required<DbContext>,
  input: InstallRoutineInput,
): Promise<{ id: string }> {
  const { rows } = await client.query<{ id: string }>(
    `INSERT INTO routines
       (org_id, name, descriptor, systems, budget_cap_usd, plan_steps,
        workspace_id, source_entry_id, source_version, created_by)
     VALUES ($1, $2, $3, $4, $5, '[]', $6, $7, $8, $9)
     ON CONFLICT (org_id, source_entry_id) WHERE source_entry_id IS NOT NULL
     DO UPDATE SET
       name = EXCLUDED.name,
       descriptor = EXCLUDED.descriptor,
       systems = EXCLUDED.systems,
       workspace_id = EXCLUDED.workspace_id,
       source_version = EXCLUDED.source_version,
       updated_at = now()
     RETURNING id`,
    [
      ctx.orgId,
      input.name,
      input.descriptor,
      input.systems,
      input.budgetCapUsd,
      input.workspaceId,
      input.sourceEntryId,
      input.sourceVersion,
      ctx.userId,
    ],
  );
  return { id: rows[0]!.id };
}

/** Overwrite the schedule text; edits are promoter-gated by the caller. */
export async function setRoutineSchedule(
  orgId: string,
  routineId: string,
  description: string,
): Promise<void> {
  await withOrgContext({ orgId }, (client) =>
    client.query(
      `UPDATE routines SET schedule_description = $3, updated_at = now()
        WHERE org_id = $1 AND id = $2`,
      [orgId, routineId, description || null],
    ),
  );
}

export interface ApproverAssignment {
  id: string;
  assigneeType: "user" | "team";
  assigneeId: string;
  /** Display name of the person or team, for assignment chips. */
  name: string;
}

/** Approvers assigned to a routine, people and teams together. */
export async function listApprovers(orgId: string, routineId: string): Promise<ApproverAssignment[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<ApproverAssignment>(
      `SELECT ra.id, ra.assignee_type AS "assigneeType", ra.assignee_id AS "assigneeId",
              COALESCE(u.name, t.name, 'Removed') AS name
         FROM routine_assignments ra
         LEFT JOIN users u ON ra.assignee_type = 'user' AND u.id = ra.assignee_id
         LEFT JOIN teams t ON ra.assignee_type = 'team' AND t.id = ra.assignee_id
        WHERE ra.org_id = $1 AND ra.routine_id = $2 AND ra.relationship = 'approver'
        ORDER BY name`,
      [orgId, routineId],
    );
    return rows;
  });
}

/**
 * Whether a user is assigned to a routine, directly or through a team, in
 * any relationship. The gate it serves: assigned Members may trigger on-demand
 * production runs within caps.
 */
export async function isAssignedToRoutine(
  orgId: string,
  userId: string,
  routineId: string,
): Promise<boolean> {
  return withOrgContext({ orgId, userId }, async (client) => {
    const { rows } = await client.query<{ assigned: boolean }>(
      `SELECT EXISTS (
         SELECT 1 FROM routine_assignments ra
          WHERE ra.org_id = $1 AND ra.routine_id = $2
            AND (
              (ra.assignee_type = 'user' AND ra.assignee_id = $3)
              OR (ra.assignee_type = 'team' AND ra.assignee_id IN (
                    SELECT tm.team_id FROM team_memberships tm WHERE tm.user_id = $3))
            )
       ) AS assigned`,
      [orgId, routineId, userId],
    );
    return rows[0]?.assigned ?? false;
  });
}

/** The runtime tenant id for an org, for the actor context on run calls. */
export async function orgTenantId(orgId: string): Promise<string> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<{ tenantId: string }>(
      `SELECT tenant_id AS "tenantId" FROM organizations WHERE id = $1`,
      [orgId],
    );
    const tenantId = rows[0]?.tenantId;
    if (!tenantId) throw new Error("organization not found");
    return tenantId;
  });
}
