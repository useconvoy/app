/**
 * The routines table: one row binds an Agent to a Workspace plus how runs
 * start. This is the storage layer only; permission checks live in the
 * server actions and page composition (agent and workspace display data)
 * lives in the pages. Migrated rows share their agent's id (see migration
 * 0015), so an attribution id from an older run resolves here first and
 * falls back to the agents table when no row matches.
 */
import "server-only";

import { withOrgContext } from "@/lib/db";

export interface RoutineBinding {
  id: string;
  agentId: string;
  workspaceId: string;
  name: string;
  /** Raw stored schedule; parse with parseStoredSchedule before use. */
  schedule: unknown | null;
  createdAt: Date;
  updatedAt: Date;
}

const BINDING_COLUMNS = `id, agent_id AS "agentId", workspace_id AS "workspaceId",
       name, schedule, created_at AS "createdAt", updated_at AS "updatedAt"`;

/** Route params are arbitrary text; only a uuid can be a row id. */
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function listRoutineBindings(orgId: string): Promise<RoutineBinding[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<RoutineBinding>(
      `SELECT ${BINDING_COLUMNS} FROM routines WHERE org_id = $1 ORDER BY created_at, id`,
      [orgId],
    );
    return rows;
  });
}

export async function getRoutineBinding(
  orgId: string,
  routineId: string,
): Promise<RoutineBinding | null> {
  if (!UUID_PATTERN.test(routineId)) return null;
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<RoutineBinding>(
      `SELECT ${BINDING_COLUMNS} FROM routines WHERE org_id = $1 AND id = $2`,
      [orgId, routineId],
    );
    return rows[0] ?? null;
  });
}

export async function listRoutineBindingsForAgent(
  orgId: string,
  agentId: string,
): Promise<RoutineBinding[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<RoutineBinding>(
      `SELECT ${BINDING_COLUMNS} FROM routines
        WHERE org_id = $1 AND agent_id = $2 ORDER BY created_at, id`,
      [orgId, agentId],
    );
    return rows;
  });
}

export interface CreateRoutineBindingInput {
  agentId: string;
  workspaceId: string;
  name: string;
  createdBy: string;
}

export async function insertRoutineBinding(
  orgId: string,
  input: CreateRoutineBindingInput,
): Promise<{ id: string }> {
  return withOrgContext({ orgId, userId: input.createdBy }, async (client) => {
    const { rows } = await client.query<{ id: string }>(
      `INSERT INTO routines (org_id, agent_id, workspace_id, name, created_by)
       VALUES ($1, $2, $3, $4, $5) RETURNING id`,
      [orgId, input.agentId, input.workspaceId, input.name, input.createdBy],
    );
    return { id: rows[0]!.id };
  });
}

export async function deleteRoutineBinding(orgId: string, routineId: string): Promise<void> {
  await withOrgContext({ orgId }, (client) =>
    client.query(`DELETE FROM routines WHERE org_id = $1 AND id = $2`, [orgId, routineId]),
  );
}

/** Persist the structured schedule (or null to return to on demand). */
export async function storeRoutineSchedule(
  orgId: string,
  routineId: string,
  schedule: object | null,
): Promise<void> {
  await withOrgContext({ orgId }, (client) =>
    client.query(
      `UPDATE routines SET schedule = $3::jsonb, updated_at = now()
        WHERE org_id = $1 AND id = $2`,
      [orgId, routineId, schedule === null ? null : JSON.stringify(schedule)],
    ),
  );
}

/**
 * The Agent behind an attribution id. Routine ids resolve through the
 * routines table; an id with no routine row is an agent id itself (a run,
 * rule, or bookmark from before routines returned as their own object).
 */
export async function agentIdForRoutine(orgId: string, id: string): Promise<string> {
  if (!UUID_PATTERN.test(id)) return id;
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<{ agentId: string }>(
      `SELECT agent_id AS "agentId" FROM routines WHERE org_id = $1 AND id = $2`,
      [orgId, id],
    );
    return rows[0]?.agentId ?? id;
  });
}
