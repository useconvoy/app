"use server";

import { revalidatePath } from "next/cache";

import { environmentsClient } from "@/lib/api/environments";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

export interface CreateAgentPayload {
  workspaceId: string;
  name: string;
  purpose: string;
  goal: string;
  planSteps: string[];
  scheduleDescription: string;
  budgetCapUsd: number;
  sandboxTemplate: string;
  browserEnabled: boolean;
  allowedDomains: string[];
  persistBrowserProfile: boolean;
  makeDefault: boolean;
}

export async function createAgent(payload: CreateAgentPayload): Promise<{ id: string }> {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    throw new Error("You cannot manage agents");
  }

  const name = payload.name.trim();
  const purpose = payload.purpose.trim();
  const goal = payload.goal.trim();
  const planSteps = payload.planSteps.map((step) => step.trim()).filter(Boolean);
  const scheduleDescription = payload.scheduleDescription.trim();
  const sandboxTemplate = payload.sandboxTemplate.trim();
  if (name.length < 2 || name.length > 120) {
    throw new Error("Agent name must be between 2 and 120 characters");
  }
  if (purpose.length < 2 || purpose.length > 200) {
    throw new Error("Describe what this agent does");
  }
  if (goal.length < 2 || goal.length > 2_000) {
    throw new Error("Describe the outcome this agent owns");
  }
  if (planSteps.length > 50 || planSteps.some((step) => step.length > 500)) {
    throw new Error("Keep the instructions to 50 steps and 500 characters per step");
  }
  if (scheduleDescription.length > 200) {
    throw new Error("Keep the schedule under 200 characters");
  }
  if (!Number.isFinite(payload.budgetCapUsd) || payload.budgetCapUsd < 0 || payload.budgetCapUsd > 1_000_000) {
    throw new Error("Choose a valid per-run budget");
  }
  if (sandboxTemplate.length < 2 || sandboxTemplate.length > 128) {
    throw new Error("Choose a valid compute template");
  }
  const workspace = await environmentsClient().getWorkspace(session.orgId, payload.workspaceId);
  if (!workspace) throw new Error("That workspace does not exist");

  const allowedDomains = [
    ...new Set(payload.allowedDomains.map((domain) => domain.trim().toLowerCase())),
  ].filter(Boolean);
  if (allowedDomains.some((domain) => !/^(\*\.)?[a-z0-9][a-z0-9.-]*[a-z0-9]$/i.test(domain))) {
    throw new Error("Browser domains must be hostnames such as app.example.com");
  }

  const agent = await environmentsClient().createAgent(session.orgId, {
    workspaceId: workspace.id,
    name,
    purpose,
    goal,
    planSteps,
    scheduleDescription: scheduleDescription || null,
    budgetCapUsd: payload.budgetCapUsd,
    sandboxTemplate,
    browserPolicy: payload.browserEnabled
      ? { allowedDomains, persistProfile: payload.persistBrowserProfile }
      : null,
    makeDefault: payload.makeDefault,
  });

  await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
    client
      .query("INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)", [
        session.orgId,
        session.userId,
        "agent.created",
        agent.id,
      ])
      .then(() => undefined),
  );
  revalidatePath("/app/agents");
  revalidatePath(`/app/workspaces/${workspace.id}`);
  return { id: agent.id };
}
