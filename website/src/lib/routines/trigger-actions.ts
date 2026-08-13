/**
 * How a routine starts, beyond by hand: the structured schedule the
 * runtime materializes as a Temporal Schedule (PUT/DELETE
 * /agents/{id}/schedule, keyed by the routine id), and event rules in the
 * environments registry (it owns the webhook doors). Both freeze the same
 * run template a manual launch computes, so a scheduled or event-fired
 * run is identical to a clicked one apart from started_via.
 */
"use server";

import { revalidatePath } from "next/cache";

import {
  createEventRule,
  deleteEventRule,
  setConnectionWebhookSecret,
} from "@/lib/api/environments";
import { controlPlane } from "@/lib/api/client";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { orgTenantId } from "@/lib/agents/queries";
import {
  resolveCron,
  scheduleDisplay,
  type AgentSchedule,
  type ScheduleFormInput,
} from "@/lib/agents/schedule";
import { resolveRoutineLaunch, routineRunTemplate, type RunTargetKind } from "./launch";
import { getRoutineBinding, storeRoutineSchedule } from "./records";

/**
 * The shared edit gate. Only edits that freeze a new template need the
 * routine to be launchable; clearing a schedule or removing a rule must
 * work on a routine whose workspace drifted, so existence is enough here.
 */
async function guardTriggerEdit(routineId: string) {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    throw new Error("You cannot manage how routines start");
  }
  const routine = await getRoutineBinding(session.orgId, routineId);
  if (!routine) throw new Error("That routine does not exist");
  return { session, membership };
}

export async function setRoutineSchedule(
  routineId: string,
  input: ScheduleFormInput,
): Promise<AgentSchedule> {
  const { session, membership } = await guardTriggerEdit(routineId);
  if (
    input.target === "production" &&
    !can("promote", membership.role, membership.capabilities)
  ) {
    throw new Error("Scheduling live runs needs the promote permission");
  }
  const context = await resolveRoutineLaunch(session.orgId, routineId);
  const schedule: AgentSchedule = {
    cron: resolveCron(input),
    timezone: input.timezone.trim() || "UTC",
    target: input.target,
    enabled: input.enabled,
  };

  const tenantId = await orgTenantId(session.orgId);
  const template = routineRunTemplate(context, schedule.target, session.userId);
  const { error } = await controlPlane({ actorId: session.userId, tenantId }).PUT(
    "/agents/{agent_id}/schedule",
    {
      params: { path: { agent_id: routineId } },
      body: {
        schedule: {
          cron: schedule.cron,
          timezone: schedule.timezone,
          enabled: schedule.enabled,
        },
        template,
      },
    },
  );
  if (error) {
    const detail = (error as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : "The schedule was not accepted");
  }

  await storeRoutineSchedule(session.orgId, routineId, schedule);
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
    client
      .query(
        `INSERT INTO admin_audit (org_id, actor_id, action, subject)
         VALUES ($1, $2, 'routine.schedule_set', $3)`,
        [session.orgId, session.userId, `${routineId} ${scheduleDisplay(schedule)}`],
      )
      .then(() => undefined),
  );
  revalidatePath(`/app/routines/${routineId}`);
  revalidatePath("/app/routines");
  return schedule;
}

export async function clearRoutineSchedule(routineId: string): Promise<void> {
  const { session } = await guardTriggerEdit(routineId);
  const tenantId = await orgTenantId(session.orgId);
  const { response, error } = await controlPlane({ actorId: session.userId, tenantId }).DELETE(
    "/agents/{agent_id}/schedule",
    { params: { path: { agent_id: routineId } } },
  );
  // A 404 means the runtime never had it; clearing the row is still right.
  if (error && response.status !== 404) {
    throw new Error("The schedule could not be removed");
  }
  await storeRoutineSchedule(session.orgId, routineId, null);
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
    client
      .query(
        `INSERT INTO admin_audit (org_id, actor_id, action, subject)
         VALUES ($1, $2, 'routine.schedule_cleared', $3)`,
        [session.orgId, session.userId, routineId],
      )
      .then(() => undefined),
  );
  revalidatePath(`/app/routines/${routineId}`);
  revalidatePath("/app/routines");
}

export interface EventRuleFormInput {
  connectionId: string;
  eventType: string;
  target: RunTargetKind;
  /** Optional: store/replace the provider's webhook signing secret. */
  webhookSecret?: string;
}

export async function addRoutineEventRule(
  routineId: string,
  input: EventRuleFormInput,
): Promise<void> {
  const { session, membership } = await guardTriggerEdit(routineId);
  if (input.target === "production" && !can("promote", membership.role, membership.capabilities)) {
    throw new Error("Event triggers for live runs need the promote permission");
  }
  const eventType = input.eventType.trim();
  if (!eventType) throw new Error("Pick the event that should start this routine");
  const context = await resolveRoutineLaunch(session.orgId, routineId);

  if (input.webhookSecret?.trim()) {
    await setConnectionWebhookSecret(session.orgId, input.connectionId, input.webhookSecret.trim());
  }
  const template = routineRunTemplate(context, input.target, session.userId);
  const rule = await createEventRule(session.orgId, {
    connectionId: input.connectionId,
    eventType,
    agentId: routineId,
    goal: template.goal,
    environmentId: template.environment_id,
    budgetUsd: template.budget_usd,
    tools: template.tools,
    instructions: template.instructions,
  });
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
    client
      .query(
        `INSERT INTO admin_audit (org_id, actor_id, action, subject)
         VALUES ($1, $2, 'routine.event_rule_added', $3)`,
        [session.orgId, session.userId, `${routineId} ${eventType} -> ${rule.ruleId}`],
      )
      .then(() => undefined),
  );
  revalidatePath(`/app/routines/${routineId}`);
  revalidatePath("/app/routines");
}

export async function removeRoutineEventRule(routineId: string, ruleId: string): Promise<void> {
  const { session } = await guardTriggerEdit(routineId);
  await deleteEventRule(session.orgId, ruleId);
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
    client
      .query(
        `INSERT INTO admin_audit (org_id, actor_id, action, subject)
         VALUES ($1, $2, 'routine.event_rule_removed', $3)`,
        [session.orgId, session.userId, `${routineId} ${ruleId}`],
      )
      .then(() => undefined),
  );
  revalidatePath(`/app/routines/${routineId}`);
  revalidatePath("/app/routines");
}
