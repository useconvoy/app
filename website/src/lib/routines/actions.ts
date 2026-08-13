/**
 * Server actions on routines: create and remove the binding, start a run,
 * and manage approver assignments. Schedule and event-trigger edits live
 * in trigger-actions.ts. Every action re-checks the session, membership,
 * and capability server-side.
 */
"use server";

import { revalidatePath } from "next/cache";

import { environmentsClient, listEventRules, deleteEventRule } from "@/lib/api/environments";
import { createRun } from "@/lib/api/runs";
import { controlPlane } from "@/lib/api/client";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { missingSystems } from "@/lib/workspaces/fit";
import { orgTenantId } from "@/lib/agents/queries";
import { resolveRoutineLaunch, routineRunTemplate } from "./launch";
import {
  agentIdForRoutine,
  deleteRoutineBinding,
  getRoutineBinding,
  insertRoutineBinding,
} from "./records";
import { isAssignedToRoutine } from "./queries";

/** Session + active membership, for every routine action. */
async function requireActor() {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (!membership || membership.status !== "active") {
    throw new Error("You are not an active member of this organization");
  }
  return { session, membership };
}

function detailPath(routineId: string): string {
  return `/app/routines/${routineId}`;
}

export interface CreateRoutinePayload {
  agentId: string;
  workspaceId: string;
  name: string;
}

/** Bind an Agent to a Workspace as a new routine. */
export async function createRoutine(payload: CreateRoutinePayload): Promise<{ id: string }> {
  const { session, membership } = await requireActor();
  if (!can("manage_workspaces", membership.role, membership.capabilities)) {
    throw new Error("You cannot manage routines");
  }
  const name = payload.name.trim();
  if (name.length < 2 || name.length > 120) {
    throw new Error("Routine name must be between 2 and 120 characters");
  }
  const registry = environmentsClient();
  const [agent, workspace] = await Promise.all([
    registry.getAgent(session.orgId, payload.agentId),
    registry.getWorkspace(session.orgId, payload.workspaceId),
  ]);
  if (!agent) throw new Error("That agent does not exist");
  if (!agent.automationConfigured || !agent.goal.trim()) {
    throw new Error("Give the agent a goal before creating routines from it");
  }
  if (!workspace) throw new Error("That workspace does not exist");
  if (missingSystems(agent.systems, workspace).length > 0) {
    throw new Error("That workspace does not connect every system this agent uses");
  }

  const { id } = await insertRoutineBinding(session.orgId, {
    agentId: agent.id,
    workspaceId: workspace.id,
    name,
    createdBy: session.userId,
  });
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
    client
      .query("INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)", [
        session.orgId,
        session.userId,
        "routine.created",
        id,
      ])
      .then(() => undefined),
  );
  revalidatePath("/app/routines");
  revalidatePath(`/app/agents/${agent.id}`);
  return { id };
}

/**
 * Remove a routine. Its runtime schedule and registry event rules are
 * removed first so nothing keeps starting runs for a binding that no
 * longer exists; if the registry is unreachable the removal aborts rather
 * than leaving live triggers behind.
 */
export async function removeRoutine(routineId: string): Promise<void> {
  const { session, membership } = await requireActor();
  if (!can("manage_workspaces", membership.role, membership.capabilities)) {
    throw new Error("You cannot manage routines");
  }
  const routine = await getRoutineBinding(session.orgId, routineId);
  if (!routine) throw new Error("That routine does not exist");

  const tenantId = await orgTenantId(session.orgId);
  const { response, error } = await controlPlane({
    actorId: session.userId,
    tenantId,
  }).DELETE("/agents/{agent_id}/schedule", {
    params: { path: { agent_id: routineId } },
  });
  // A 404 means the runtime never had a schedule for it.
  if (error && response.status !== 404) {
    throw new Error("The routine's schedule could not be removed; nothing was deleted");
  }
  const rules = await listEventRules(session.orgId, routineId);
  for (const rule of rules ?? []) {
    await deleteEventRule(session.orgId, rule.ruleId);
  }

  await deleteRoutineBinding(session.orgId, routineId);
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
    client
      .query("INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)", [
        session.orgId,
        session.userId,
        "routine.removed",
        routineId,
      ])
      .then(() => undefined),
  );
  revalidatePath("/app/routines");
  revalidatePath(`/app/agents/${routine.agentId}`);
}

export interface RunNowResult {
  runId: string;
  target: "rehearsal" | "production";
}

export type RunTarget = RunNowResult["target"];

/**
 * The on-demand run of a routine. Anyone who may trigger runs gets the
 * workspace's rehearsal copy; the live binding is used only when the
 * caller holds the promote capability, and assigned Members stay within
 * the agent's budget cap either way (the cap rides the create call).
 */
export async function runRoutineNow(
  routineId: string,
  target: RunTarget = "rehearsal",
): Promise<RunNowResult> {
  const { session, membership } = await requireActor();
  if (!can("trigger_production_run", membership.role, membership.capabilities)) {
    throw new Error("You cannot start runs");
  }
  if (target !== "rehearsal" && target !== "production") {
    throw new Error("Unknown run target");
  }
  const production = target === "production";
  if (production && !can("promote", membership.role, membership.capabilities)) {
    throw new Error("You cannot start production runs");
  }
  if (production && membership.role === "member") {
    const assigned = await isAssignedToRoutine(session.orgId, session.userId, routineId);
    if (!assigned) throw new Error("You are not assigned to this routine");
  }

  const context = await resolveRoutineLaunch(session.orgId, routineId);
  const template = routineRunTemplate(context, target, session.userId);
  const tenantId = await orgTenantId(session.orgId);
  const { runId } = await createRun(
    { actorId: session.userId, tenantId },
    {
      goal: template.goal,
      environmentId: template.environment_id,
      budgetUsd: template.budget_usd,
      tools: template.tools,
      instructions: template.instructions,
      agentId: routineId,
      startedById: session.userId,
      startedVia: "manual",
    },
  );
  revalidatePath(detailPath(routineId));
  revalidatePath("/app/routines");
  revalidatePath("/app/runs");
  return { runId, target };
}

/** Assign a person or team as an approver on a routine's Agent. */
export async function assignApprover(
  routineId: string,
  assigneeType: string,
  assigneeId: string,
): Promise<void> {
  const { session, membership } = await requireActor();
  if (!can("edit_routines_rehearsal", membership.role, membership.capabilities)) {
    throw new Error("You cannot change approvers");
  }
  const agentId = await agentIdForRoutine(session.orgId, routineId);
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
      [session.orgId, agentId, assigneeType, assigneeId],
    );
    if (result.rowCount === 0) {
      throw new Error("That person or team cannot be assigned");
    }
  });
  revalidatePath(detailPath(routineId));
}

/** Remove an approver assignment from a routine's Agent. */
export async function removeApprover(routineId: string, assignmentId: string): Promise<void> {
  const { session, membership } = await requireActor();
  if (!can("edit_routines_rehearsal", membership.role, membership.capabilities)) {
    throw new Error("You cannot change approvers");
  }
  const agentId = await agentIdForRoutine(session.orgId, routineId);
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    const result = await client.query(
      `DELETE FROM agent_assignments
        WHERE id = $1 AND org_id = $2 AND agent_id = $3 AND relationship = 'approver'`,
      [assignmentId, session.orgId, agentId],
    );
    if (result.rowCount === 0) throw new Error("That assignment no longer exists");
  });
  revalidatePath(detailPath(routineId));
}
