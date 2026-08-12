/**
 * The frozen run template: what the console resolves from an Agent at
 * launch/save time. Manual runs, schedules, and event rules all freeze the
 * same facts, so every trigger class starts identical runs.
 */
import "server-only";

import { environmentsClient, type Agent } from "@/lib/api/environments";
import { toolIdsForRoutine } from "@/lib/workspaces/system-catalog";
import { agentRuntimeToolIds } from "./capabilities";

export type RunTargetKind = "rehearsal" | "production";

export interface FrozenRunTemplate {
  goal: string;
  environment_id: string;
  budget_usd: string;
  tools: string[];
  instructions: string[];
  started_by: string;
}

export async function buildRunTemplate(
  orgId: string,
  agent: Agent,
  target: RunTargetKind,
  startedBy: string,
): Promise<FrozenRunTemplate> {
  if (!agent.automationConfigured || !agent.goal.trim()) {
    throw new Error("Give this agent a goal before wiring triggers");
  }
  const workspace = await environmentsClient().getWorkspace(orgId, agent.workspaceId);
  if (!workspace) throw new Error("This agent's workspace no longer exists");
  const tools = [
    ...new Set([
      ...toolIdsForRoutine(agent.systems, workspace.systems),
      ...agentRuntimeToolIds(agent),
    ]),
  ].sort();
  return {
    goal: agent.goal,
    environment_id: target === "production" ? agent.productionBindingId : agent.rehearsalBindingId,
    budget_usd: agent.budgetCapUsd,
    tools,
    instructions: agent.planSteps,
    started_by: startedBy,
  };
}
