/**
 * Compatibility aliases for code paths whose runtime/event vocabulary has
 * not yet moved from `routine` to `agent`. Product state lives exclusively
 * on `agents`; there is no routines table after migration 0012.
 */
import "server-only";

import type { PoolClient } from "pg";

import { withOrgContext, type DbContext } from "@/lib/db";
import {
  isAssignedToAgent,
  listAgentApprovers,
  orgTenantId as agentOrgTenantId,
} from "@/lib/agents/queries";
import { agentIdForRoutine } from "./records";

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
  /** Compatibility self-reference: the job and runtime are one Agent. */
  agentId: string | null;
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
  agentId: string | null;
  scheduleDescription: string | null;
  sourceEntryId: string | null;
  sourceVersion: number | null;
  createdAt: Date;
}

const ROUTINE_COLUMNS = `id, name, goal AS descriptor, systems,
       budget_cap_usd AS "budgetCapUsd",
       plan_steps AS "planSteps",
       workspace_id AS "workspaceId",
       id AS "agentId",
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

/** Configured Agents, oldest first. Kept under the old name for wire callers. */
export async function listRoutines(orgId: string): Promise<RoutineRecord[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<RoutineRow>(
      `SELECT ${ROUTINE_COLUMNS} FROM agents
        WHERE org_id = $1 AND automation_configured
        ORDER BY created_at, id`,
      [orgId],
    );
    return rows.map(toRecord);
  });
}

/**
 * The configured Agent behind an attribution id, or null when it does not
 * exist in this org. The id may be a routine id (resolved through the
 * routines table) or a bare agent id from before routines returned.
 */
export async function getRoutine(orgId: string, routineId: string): Promise<RoutineRecord | null> {
  if (!UUID_PATTERN.test(routineId)) return null;
  const agentId = await agentIdForRoutine(orgId, routineId);
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<RoutineRow>(
      `SELECT ${ROUTINE_COLUMNS} FROM agents
        WHERE org_id = $1 AND id = $2 AND automation_configured`,
      [orgId, agentId],
    );
    return rows[0] ? toRecord(rows[0]) : null;
  });
}

export interface InstallRoutineInput {
  name: string;
  descriptor: string;
  /** The working goal the runtime plans from; defaults to the descriptor. */
  goal?: string;
  systems: string[];
  budgetCapUsd: number;
  workspaceId: string;
  agentId: string;
  sourceEntryId: string;
  sourceVersion: number;
}

/**
 * Enrich an Agent from a catalog template. Reinstalling the same entry
 * re-pins the Agent that already carries it; a first install enriches the
 * Agent selected by the user. Runs on the caller's transaction.
 */
export async function upsertInstalledRoutine(
  client: PoolClient,
  ctx: Required<DbContext>,
  input: InstallRoutineInput,
): Promise<{ id: string }> {
  const existing = await client.query<{ id: string }>(
    `SELECT id FROM agents WHERE org_id = $1 AND source_entry_id = $2`,
    [ctx.orgId, input.sourceEntryId],
  );
  const targetId = existing.rows[0]?.id ?? input.agentId;
  const { rows } = await client.query<{ id: string }>(
    `UPDATE agents
        SET name = $2,
            purpose = $3,
            goal = $4,
            systems = $5,
            budget_cap_usd = $6,
            plan_steps = '[]',
            workspace_id = $7,
            source_entry_id = $8,
            source_version = $9,
            automation_configured = true,
            updated_at = now()
      WHERE org_id = $1 AND id = $10
      RETURNING id`,
    [
      ctx.orgId,
      input.name,
      input.descriptor,
      input.goal?.trim() || input.descriptor,
      input.systems,
      input.budgetCapUsd,
      input.workspaceId,
      input.sourceEntryId,
      input.sourceVersion,
      targetId,
    ],
  );
  if (!rows[0]) throw new Error("That agent no longer exists");
  // An installed Agent must be runnable: make sure at least one routine
  // pairs it with the chosen workspace. Reinstalls keep existing routines.
  await client.query(
    `INSERT INTO routines (org_id, agent_id, workspace_id, name, created_by)
     SELECT $1, $2, $3, $4, $5
      WHERE NOT EXISTS (SELECT 1 FROM routines WHERE agent_id = $2)`,
    [ctx.orgId, rows[0]!.id, input.workspaceId, input.name, ctx.userId],
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
      `UPDATE agents SET schedule_description = $3, updated_at = now()
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
  return listAgentApprovers(orgId, await agentIdForRoutine(orgId, routineId));
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
  return isAssignedToAgent(orgId, userId, await agentIdForRoutine(orgId, routineId));
}

/**
 * The runtime tenant id for an org, for the actor context on run calls.
 * Registry-linked organizations use the registry's organization id because
 * environment bindings are tenant-checked there. Unlinked deployments keep
 * the original website tenant id and the table-backed stub behavior.
 */
export async function orgTenantId(orgId: string): Promise<string> {
  return agentOrgTenantId(orgId);
}
