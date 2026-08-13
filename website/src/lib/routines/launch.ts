/**
 * Launch resolution for a routine: the Agent supplies the goal,
 * instructions, budget, and runtime profile; the routine's Workspace
 * supplies the environment bindings and connector grants. Manual runs,
 * schedules, and event rules all freeze the same template, so every
 * trigger class starts identical runs.
 */
import "server-only";

import { environmentsClient, type Agent, type Workspace } from "@/lib/api/environments";
import { agentRuntimeToolIds } from "@/lib/agents/capabilities";
import { missingSystems, systemDisplayNames } from "@/lib/workspaces/fit";
import { toolIdsForRoutine } from "@/lib/workspaces/system-catalog";
import { getRoutineBinding, type RoutineBinding } from "./records";

export type RunTargetKind = "rehearsal" | "production";

export interface RoutineLaunchContext {
  routine: RoutineBinding;
  agent: Agent;
  workspace: Workspace;
}

/**
 * Resolve a routine to its runnable parts, failing with plain sentences
 * when the agent is unconfigured or the workspace no longer covers the
 * systems the agent uses.
 */
export async function resolveRoutineLaunch(
  orgId: string,
  routineId: string,
): Promise<RoutineLaunchContext> {
  const routine = await getRoutineBinding(orgId, routineId);
  if (!routine) throw new Error("That routine does not exist");
  const client = environmentsClient();
  const [agent, workspace] = await Promise.all([
    client.getAgent(orgId, routine.agentId),
    client.getWorkspace(orgId, routine.workspaceId),
  ]);
  if (!agent) throw new Error("This routine's agent no longer exists");
  if (!agent.automationConfigured || !agent.goal.trim()) {
    throw new Error("Give this routine's agent a goal before starting it");
  }
  if (!workspace) throw new Error("This routine's workspace no longer exists");
  const missing = missingSystems(agent.systems, workspace);
  if (missing.length > 0) {
    const names = systemDisplayNames([workspace]);
    throw new Error(
      `This routine's workspace does not connect ${missing
        .map((id) => names[id] ?? id)
        .join(", ")}`,
    );
  }
  return { routine, agent, workspace };
}

export interface FrozenRunTemplate {
  goal: string;
  environment_id: string;
  budget_usd: string;
  tools: string[];
  instructions: string[];
  started_by: string;
}

/** The frozen facts a run of this routine starts from. */
export function routineRunTemplate(
  context: RoutineLaunchContext,
  target: RunTargetKind,
  startedBy: string,
): FrozenRunTemplate {
  const { agent, workspace } = context;
  const tools = [
    ...new Set([
      ...toolIdsForRoutine(agent.systems, workspace.systems),
      ...agentRuntimeToolIds(agent),
    ]),
  ].sort();
  return {
    goal: agent.goal,
    environment_id:
      target === "production" ? workspace.environmentId : workspace.rehearsalEnvironmentId,
    budget_usd: agent.budgetCapUsd,
    tools,
    instructions: agent.planSteps,
    started_by: startedBy,
  };
}
