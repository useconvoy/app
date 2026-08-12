"use server";

/**
 * Agent schedule management. The console stores the editable definition on
 * `agents.schedule` and materializes it as the runtime's per-agent Temporal
 * Schedule (PUT/DELETE /agents/{id}/schedule), sending the same frozen run
 * template a manual launch would compute — binding target, tools,
 * instructions, budget. Saving re-freezes the template, so editing the
 * agent and re-saving the schedule is how schedule runs pick up changes.
 */
import { revalidatePath } from "next/cache";

import { environmentsClient } from "@/lib/api/environments";
import { controlPlane } from "@/lib/api/client";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { orgTenantId } from "./queries";
import { buildRunTemplate } from "./template";
import {
  resolveCron,
  scheduleDisplay,
  type AgentSchedule,
  type ScheduleFormInput,
} from "./schedule";

async function guardScheduleEdit(agentId: string) {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    throw new Error("You cannot manage schedules");
  }
  const agent = await environmentsClient().getAgent(session.orgId, agentId);
  if (!agent) throw new Error("That agent does not exist");
  return { session, membership, agent };
}

export async function setAgentSchedule(
  agentId: string,
  input: ScheduleFormInput,
): Promise<AgentSchedule> {
  const { session, membership, agent } = await guardScheduleEdit(agentId);
  if (
    input.target === "production" &&
    !can("promote", membership.role, membership.capabilities)
  ) {
    throw new Error("Scheduling live runs needs the promote permission");
  }
  const schedule: AgentSchedule = {
    cron: resolveCron(input),
    timezone: input.timezone.trim() || "UTC",
    target: input.target,
    enabled: input.enabled,
  };

  const tenantId = await orgTenantId(session.orgId);
  const template = await buildRunTemplate(session.orgId, agent, schedule.target, session.userId);
  const { error } = await controlPlane({ actorId: session.userId, tenantId }).PUT(
    "/agents/{agent_id}/schedule",
    {
      params: { path: { agent_id: agentId } },
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

  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    await client.query(
      `UPDATE agents SET schedule = $2::jsonb, schedule_description = $3,
              updated_at = now() WHERE id = $1`,
      [agentId, JSON.stringify(schedule), scheduleDisplay(schedule)],
    );
    await client.query(
      `INSERT INTO admin_audit (org_id, actor_id, action, subject)
       VALUES ($1, $2, 'agent.schedule_set', $3)`,
      [session.orgId, session.userId, `${agentId} ${schedule.cron} ${schedule.target}`],
    );
  });
  revalidatePath(`/app/agents/${agentId}`);
  revalidatePath("/app/agents");
  return schedule;
}

export async function clearAgentSchedule(agentId: string): Promise<void> {
  const { session } = await guardScheduleEdit(agentId);
  const tenantId = await orgTenantId(session.orgId);
  const { response, error } = await controlPlane({ actorId: session.userId, tenantId }).DELETE(
    "/agents/{agent_id}/schedule",
    { params: { path: { agent_id: agentId } } },
  );
  // A 404 means the runtime never had it — clearing the row is still right.
  if (error && response.status !== 404) {
    throw new Error("The schedule could not be removed");
  }
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    await client.query(
      `UPDATE agents SET schedule = NULL, schedule_description = NULL,
              updated_at = now() WHERE id = $1`,
      [agentId],
    );
    await client.query(
      `INSERT INTO admin_audit (org_id, actor_id, action, subject)
       VALUES ($1, $2, 'agent.schedule_cleared', $3)`,
      [session.orgId, session.userId, agentId],
    );
  });
  revalidatePath(`/app/agents/${agentId}`);
  revalidatePath("/app/agents");
}
