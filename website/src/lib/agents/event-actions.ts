"use server";

/**
 * Event-trigger management: rules live in the environments registry (it
 * owns the webhook doors); the console freezes the same run template a
 * manual launch or schedule would, so an event-fired run is identical to a
 * clicked one apart from started_via="event".
 */
import { revalidatePath } from "next/cache";

import {
  createEventRule,
  deleteEventRule,
  environmentsClient,
  setConnectionWebhookSecret,
} from "@/lib/api/environments";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { buildRunTemplate, type RunTargetKind } from "./template";

async function guardTriggerEdit(agentId: string) {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    throw new Error("You cannot manage triggers");
  }
  const agent = await environmentsClient().getAgent(session.orgId, agentId);
  if (!agent) throw new Error("That agent does not exist");
  return { session, membership, agent };
}

export interface EventRuleFormInput {
  connectionId: string;
  eventType: string;
  target: RunTargetKind;
  /** Optional: store/replace the provider's webhook signing secret. */
  webhookSecret?: string;
}

export async function addAgentEventRule(
  agentId: string,
  input: EventRuleFormInput,
): Promise<void> {
  const { session, membership, agent } = await guardTriggerEdit(agentId);
  if (input.target === "production" && !can("promote", membership.role, membership.capabilities)) {
    throw new Error("Event triggers for live runs need the promote permission");
  }
  const eventType = input.eventType.trim();
  if (!eventType) throw new Error("Pick the event that should start this agent");

  if (input.webhookSecret?.trim()) {
    await setConnectionWebhookSecret(session.orgId, input.connectionId, input.webhookSecret.trim());
  }
  const template = await buildRunTemplate(session.orgId, agent, input.target, session.userId);
  const rule = await createEventRule(session.orgId, {
    connectionId: input.connectionId,
    eventType,
    agentId: agent.id,
    goal: template.goal,
    environmentId: template.environment_id,
    budgetUsd: template.budget_usd,
    tools: template.tools,
    instructions: template.instructions,
  });
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    await client.query(
      `INSERT INTO admin_audit (org_id, actor_id, action, subject)
       VALUES ($1, $2, 'agent.event_rule_added', $3)`,
      [session.orgId, session.userId, `${agentId} ${eventType} -> ${rule.ruleId}`],
    );
  });
  revalidatePath(`/app/agents/${agentId}`);
}

export async function removeAgentEventRule(agentId: string, ruleId: string): Promise<void> {
  const { session } = await guardTriggerEdit(agentId);
  await deleteEventRule(session.orgId, ruleId);
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    await client.query(
      `INSERT INTO admin_audit (org_id, actor_id, action, subject)
       VALUES ($1, $2, 'agent.event_rule_removed', $3)`,
      [session.orgId, session.userId, `${agentId} ${ruleId}`],
    );
  });
  revalidatePath(`/app/agents/${agentId}`);
}
