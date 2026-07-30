import {
  condition,
  defineSignal,
  executeChild,
  proxyActivities,
  setHandler,
  workflowInfo,
} from "@temporalio/workflow";
import type * as activities from "./activities";
import type {
  AgentInput,
  AgentResult,
  AdmitSpawnResult,
  EpisodeResult,
  MissionSpec,
  MissionResult,
} from "./contracts";

const { initializeMission, admitAgentSpawns, recordMissionSummary } =
  proxyActivities<typeof activities>({
    startToCloseTimeout: "30 seconds",
    retry: {
      initialInterval: "2 seconds",
      maximumAttempts: 5,
    },
  });

const { launchAgentEpisode } = proxyActivities<typeof activities>({
  startToCloseTimeout: "2 minutes",
  retry: {
    initialInterval: "2 seconds",
    maximumAttempts: 3,
  },
});

const { acquireAgentSlot, releaseAgentSlot } =
  proxyActivities<typeof activities>({
    startToCloseTimeout: "30 seconds",
    scheduleToCloseTimeout: "15 minutes",
    retry: {
      initialInterval: "1 second",
      maximumInterval: "10 seconds",
    },
  });

export const episodeCompleted =
  defineSignal<[EpisodeResult]>("episodeCompleted");

export async function agentWorkflow(input: AgentInput): Promise<AgentResult> {
  let episode: EpisodeResult | undefined;

  setHandler(episodeCompleted, (result) => {
    episode = result;
  });

  await acquireAgentSlot({
    missionId: input.spec.missionId,
    agentId: input.agentId,
    maxParallelAgents: input.spec.maxParallelAgents,
  });
  try {
    await launchAgentEpisode({
      ...input,
      workflowId: workflowInfo().workflowId,
    });
    await condition(() => episode !== undefined);
  } finally {
    await releaseAgentSlot({
      missionId: input.spec.missionId,
      agentId: input.agentId,
    });
  }

  let admission: AdmitSpawnResult = {
    admitted: [],
    rejectedCount: 0,
  };
  if (episode!.nextIntent.type === "spawn_agents") {
    admission = await admitAgentSpawns({
      spec: input.spec,
      parentAgentId: input.agentId,
      parentDepth: input.depth,
      candidates: episode!.nextIntent.candidates,
    });
  }

  const descendants = await Promise.all(
    admission.admitted.map((candidate, index) => {
      const childAgentId = `${input.agentId}.${index + 1}`;
      return executeChild(agentWorkflow, {
        workflowId: `${input.spec.missionId}/agent/${childAgentId}`,
        args: [
          {
            ...input,
            agentId: childAgentId,
            depth: input.depth + 1,
            objective: candidate.objective,
            episodeIndex: 0,
          },
        ],
      });
    }),
  );

  return {
    ...episode!,
    descendants,
    totalAgents:
      1 + descendants.reduce((total, child) => total + child.totalAgents, 0),
  };
}

export async function missionWorkflow(
  input: MissionSpec,
): Promise<MissionResult> {
  await initializeMission(input);

  const root = await executeChild(agentWorkflow, {
    workflowId: `${input.missionId}/agent/root`,
    args: [
      {
        spec: input,
        agentId: "root",
        depth: 0,
        objective: input.objective,
        episodeIndex: 0,
      },
    ],
  });

  const result: MissionResult = {
    missionId: input.missionId,
    root,
    totalAgents: root.totalAgents,
    completedAt: new Date().toISOString(),
  };

  await recordMissionSummary(result);
  return result;
}
