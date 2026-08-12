"use server";

import { revalidatePath } from "next/cache";

import { environmentsClient } from "@/lib/api/environments";
import { createRun } from "@/lib/api/runs";
import { requireOrgSession } from "@/lib/auth/session";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { missingSystems, systemDisplayNames } from "@/lib/workspaces/fit";
import { toolIdsForRoutine } from "@/lib/workspaces/system-catalog";
import { agentRuntimeToolIds } from "./capabilities";
import { isAssignedToAgent, orgTenantId } from "./queries";

export type RunTarget = "rehearsal" | "production";

export interface RunAgentResult {
  runId: string;
  target: RunTarget;
}
/** Start the selected Agent with its own goal, limits, and runtime setup. */
export async function runAgentNow(
  agentId: string,
  target: RunTarget = "rehearsal",
): Promise<RunAgentResult> {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("trigger_production_run", membership.role, membership.capabilities)
  ) {
    throw new Error("You cannot start runs");
  }
  if (target !== "rehearsal" && target !== "production") {
    throw new Error("Unknown run target");
  }

  const agent = await environmentsClient().getAgent(session.orgId, agentId);
  if (!agent) throw new Error("That agent does not exist");
  if (!agent.automationConfigured || !agent.goal.trim()) {
    throw new Error("Give this agent a goal before starting it");
  }
  const workspace = await environmentsClient().getWorkspace(session.orgId, agent.workspaceId);
  if (!workspace) throw new Error("This agent's workspace no longer exists");
  const missing = missingSystems(agent.systems, workspace);
  if (missing.length > 0) {
    const names = systemDisplayNames([workspace]);
    throw new Error(
      `This workspace does not connect ${missing.map((id) => names[id] ?? id).join(", ")}`,
    );
  }

  const production = target === "production";
  if (production && !can("promote", membership.role, membership.capabilities)) {
    throw new Error("You cannot start production runs");
  }
  if (production && membership.role === "member") {
    const assigned = await isAssignedToAgent(session.orgId, session.userId, agent.id);
    if (!assigned) throw new Error("You are not assigned to this agent");
  }

  const tools = [
    ...new Set([
      ...toolIdsForRoutine(agent.systems, workspace.systems),
      ...agentRuntimeToolIds(agent),
    ]),
  ].sort();
  const tenantId = await orgTenantId(session.orgId);
  const { runId } = await createRun(
    { actorId: session.userId, tenantId },
    {
      goal: agent.goal,
      environmentId: production ? agent.productionBindingId : agent.rehearsalBindingId,
      budgetUsd: agent.budgetCapUsd,
      tools,
      agentId: agent.id,
      startedById: session.userId,
      startedVia: "manual",
    },
  );
  revalidatePath(`/app/agents/${agent.id}`);
  revalidatePath("/app/agents");
  revalidatePath("/app/runs");
  return { runId, target };
}
