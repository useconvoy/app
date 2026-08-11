/**
 * Temporary action aliases for old route/event callers. Product actions now
 * target the Agent directly; this file can disappear when the frozen wire
 * vocabulary is renamed.
 */
"use server";

import { revalidatePath } from "next/cache";

import { runAgentNow } from "@/lib/agents/run-actions";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import {
  getRoutine,
  setRoutineSchedule,
  type RoutineRecord,
} from "./queries";

/** Session + active membership, for every routine action. */
async function requireActor() {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (!membership || membership.status !== "active") {
    throw new Error("You are not an active member of this organization");
  }
  return { session, membership };
}

async function requireRoutine(orgId: string, routineId: string): Promise<RoutineRecord> {
  const routine = await getRoutine(orgId, routineId);
  if (!routine) throw new Error("That Agent does not exist");
  return routine;
}

function detailPath(routineId: string): string {
  return `/app/agents/${routineId}`;
}

/** Assign a person or team as an approver on an Agent. */
export async function assignApprover(
  routineId: string,
  assigneeType: string,
  assigneeId: string,
): Promise<void> {
  const { session, membership } = await requireActor();
  if (!can("edit_routines_rehearsal", membership.role, membership.capabilities)) {
    throw new Error("You cannot change approvers");
  }
  await requireRoutine(session.orgId, routineId);
  if (assigneeType !== "user" && assigneeType !== "team") {
    throw new Error("Unknown assignee kind");
  }
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    // The assignee must exist inside this org; RLS already bounds the
    // lookup, the WHERE makes a bad id a no-op instead of a stray row.
    const result = await client.query(
      `INSERT INTO agent_assignments (org_id, agent_id, assignee_type, assignee_id, relationship)
       SELECT $1, $2, $3, $4, 'approver'
        WHERE ($3 = 'user' AND EXISTS (
                SELECT 1 FROM memberships m
                 WHERE m.org_id = $1 AND m.user_id = $4 AND m.status = 'active'))
           OR ($3 = 'team' AND EXISTS (
                SELECT 1 FROM teams t WHERE t.org_id = $1 AND t.id = $4))
       ON CONFLICT DO NOTHING`,
      [session.orgId, routineId, assigneeType, assigneeId],
    );
    if (result.rowCount === 0) {
      throw new Error("That person or team cannot be assigned");
    }
  });
  revalidatePath(detailPath(routineId));
}

/** Remove an approver assignment from an Agent. */
export async function removeApprover(routineId: string, assignmentId: string): Promise<void> {
  const { session, membership } = await requireActor();
  if (!can("edit_routines_rehearsal", membership.role, membership.capabilities)) {
    throw new Error("You cannot change approvers");
  }
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    const result = await client.query(
      `DELETE FROM agent_assignments
        WHERE id = $1 AND org_id = $2 AND agent_id = $3 AND relationship = 'approver'`,
      [assignmentId, session.orgId, routineId],
    );
    if (result.rowCount === 0) throw new Error("That assignment no longer exists");
  });
  revalidatePath(detailPath(routineId));
}

/**
 * Edit an Agent's schedule text. Editing triggers of live Agents is
 * promoter territory: Operators hold it, Admins and senior
 * Members via the promoter capability.
 */
export async function updateTriggerSchedule(routineId: string, description: string): Promise<void> {
  const { session, membership } = await requireActor();
  if (!can("promote", membership.role, membership.capabilities)) {
    throw new Error("You cannot edit triggers of a live agent");
  }
  await requireRoutine(session.orgId, routineId);
  const trimmed = description.trim();
  if (trimmed.length > 200) throw new Error("Keep the schedule under 200 characters");
  await setRoutineSchedule(session.orgId, routineId, trimmed);
  revalidatePath(detailPath(routineId));
}

export interface RunNowResult {
  runId: string;
  target: "rehearsal" | "production";
}

export type RunTarget = RunNowResult["target"];

/**
 * The on-demand run. Anyone who may trigger runs gets the rehearsal
 * copy by default; the Agent's live binding is used only when the caller holds
 * the promote capability, and assigned Members stay within the routine's
 * budget cap either way (the cap rides the create call).
 */
export async function runRoutineNow(
  routineId: string,
  target: RunTarget = "rehearsal",
): Promise<RunNowResult> {
  return runAgentNow(routineId, target);
}
