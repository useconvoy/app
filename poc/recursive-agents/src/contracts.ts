export type ComputeMode = "auto" | "fast" | "economy" | "dedicated";

export interface MissionBudget {
  maxCostCents: number;
  estimatedCostPerAgentCents: number;
}

export interface MissionSpec {
  missionId: string;
  objective: string;
  deadline: string;
  maxParallelAgents: number;
  maxTotalAgents: number;
  maxDepth: number;
  checkpointIntervalSeconds: number;
  computeMode: ComputeMode;
  budget: MissionBudget;
  deploymentId: string;
  environmentId: string;
  policyRef: string;
  toolRefs: string[];
  inputRefs: string[];
}

export interface SpawnCandidate {
  objective: string;
}

export type NextIntent =
  | { type: "use_tool"; toolRef: string; input: unknown }
  | { type: "spawn_agents"; candidates: SpawnCandidate[] }
  | { type: "wait_for_children" }
  | { type: "request_approval"; reason: string }
  | { type: "wait_for_event"; eventType: string }
  | { type: "replan"; reason: string }
  | { type: "complete"; summary: string };

export interface AgentInput {
  spec: MissionSpec;
  agentId: string;
  depth: number;
  objective: string;
  episodeIndex: number;
}

export interface EpisodeResult {
  missionId: string;
  agentId: string;
  depth: number;
  episodeIndex: number;
  nextIntent: NextIntent;
  artifactKey: string;
  completedAt: string;
}

export interface AgentResult extends EpisodeResult {
  descendants: AgentResult[];
  totalAgents: number;
}

export interface MissionResult {
  missionId: string;
  root: AgentResult;
  totalAgents: number;
  completedAt: string;
}

export interface LaunchEpisodeInput extends AgentInput {
  workflowId: string;
}

export interface AdmitSpawnInput {
  spec: MissionSpec;
  parentAgentId: string;
  parentDepth: number;
  candidates: SpawnCandidate[];
}

export interface AdmitSpawnResult {
  admitted: SpawnCandidate[];
  rejectedCount: number;
  reason?: string;
}
