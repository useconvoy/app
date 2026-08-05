/**
 * Server actions for routine surfaces: approver assignment, trigger edits,
 * and the on-demand run. Every action derives org and actor from the
 * verified session and re-checks the permissions matrix server-side; the
 * UI lens hiding a control is convenience only. Routine assignment is
 * routing, not org administration, so it carries no admin_audit row; run
 * triggering is attributed in the runtime's own audit trail via the actor
 * header.
 */
"use server";

import { revalidatePath } from "next/cache";

import { environmentsClient, workspaceForRoutine } from "@/lib/api/environments";
import { createRun } from "@/lib/api/runs";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { routines } from "@/lib/fixtures/world";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { recordRunTarget } from "./data";
import { isAssignedToRoutine, orgTenantId } from "./queries";
import { setTriggerSchedule as writeTriggerSchedule } from "./triggers";

/** Session + active membership, for every routine action. */
async function requireActor() {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (!membership || membership.status !== "active") {
    throw new Error("You are not an active member of this organization");
  }
  return { session, membership };
}

function requireRoutine(routineId: string) {
  const routine = routines.find((candidate) => candidate.id === routineId);
  if (!routine) throw new Error("That routine does not exist");
  return routine;
}

function detailPath(routineId: string): string {
  return `/app/routines/${routineId}`;
}

/** Assign a person or team as an approver on a routine. */
export async function assignApprover(
  routineId: string,
  assigneeType: string,
  assigneeId: string,
): Promise<void> {
  const { session, membership } = await requireActor();
  if (!can("edit_routines_rehearsal", membership.role, membership.capabilities)) {
    throw new Error("You cannot change approvers");
  }
  requireRoutine(routineId);
  if (assigneeType !== "user" && assigneeType !== "team") {
    throw new Error("Unknown assignee kind");
  }
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    // The assignee must exist inside this org; RLS already bounds the
    // lookup, the WHERE makes a bad id a no-op instead of a stray row.
    const result = await client.query(
      `INSERT INTO routine_assignments (org_id, routine_id, assignee_type, assignee_id, relationship)
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

/** Remove an approver assignment from a routine. */
export async function removeApprover(routineId: string, assignmentId: string): Promise<void> {
  const { session, membership } = await requireActor();
  if (!can("edit_routines_rehearsal", membership.role, membership.capabilities)) {
    throw new Error("You cannot change approvers");
  }
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    const result = await client.query(
      `DELETE FROM routine_assignments
        WHERE id = $1 AND org_id = $2 AND routine_id = $3 AND relationship = 'approver'`,
      [assignmentId, session.orgId, routineId],
    );
    if (result.rowCount === 0) throw new Error("That assignment no longer exists");
  });
  revalidatePath(detailPath(routineId));
}

/**
 * Edit a routine's schedule text. Editing triggers of live routines is
 * promoter territory: Operators hold it, Admins and senior
 * Members via the promoter capability.
 */
export async function updateTriggerSchedule(routineId: string, description: string): Promise<void> {
  const { session, membership } = await requireActor();
  if (!can("promote", membership.role, membership.capabilities)) {
    throw new Error("You cannot edit triggers of a live routine");
  }
  requireRoutine(routineId);
  const trimmed = description.trim();
  if (trimmed.length > 200) throw new Error("Keep the schedule under 200 characters");
  writeTriggerSchedule(session.orgId, routineId, trimmed);
  revalidatePath(detailPath(routineId));
}

export interface RunNowResult {
  runId: string;
  target: "rehearsal" | "production";
}

/**
 * The on-demand run. Anyone who may trigger runs gets the rehearsal
 * copy by default; the live workspace is used only when the caller holds
 * the promote capability, and assigned Members stay within the routine's
 * budget cap either way (the cap rides the create call).
 */
export async function runRoutineNow(routineId: string): Promise<RunNowResult> {
  const { session, membership } = await requireActor();
  if (!can("trigger_production_run", membership.role, membership.capabilities)) {
    throw new Error("You cannot start runs");
  }
  const routine = requireRoutine(routineId);
  const workspaces = await environmentsClient().listWorkspaces(session.orgId);
  const workspace = workspaceForRoutine(routine, workspaces);
  if (!workspace) {
    throw new Error("No workspace connects the systems this routine needs");
  }
  const production = can("promote", membership.role, membership.capabilities);
  if (production && membership.role === "member") {
    // Members trigger live runs only on routines assigned to them.
    const assigned = await isAssignedToRoutine(session.orgId, session.userId, routineId);
    if (!assigned) throw new Error("You are not assigned to this routine");
  }
  const tenantId = await orgTenantId(session.orgId);
  const { runId } = await createRun(
    { actorId: session.userId, tenantId },
    {
      goal: `${routine.name}: ${routine.descriptor}`,
      environmentId: production ? workspace.environmentId : workspace.rehearsalEnvironmentId,
      budgetUsd: String(routine.budgetCapUsd),
      routineId: routine.id,
    },
  );
  const target = production ? "production" : "rehearsal";
  recordRunTarget(runId, target);
  revalidatePath(detailPath(routineId));
  return { runId, target };
}
