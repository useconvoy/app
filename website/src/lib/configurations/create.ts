/**
 * The New configuration form (`/app/configurations/new`): a name, the robot, the
 * edge hardware and up to one edge and one cloud model. The route follows from the
 * models (edge only, cloud only, or edge plans while the cloud acts), so there is
 * nothing else to choose. `buildRevision` turns the form into revision r1; the
 * sample workspace builds its revisions the same way.
 */
import { NOT_SPECIFIED } from "./mutations";
import type { CloudModel, ConfigRevision, EdgeHardware, EdgeModel, FlagRules, ModelRole, RouteMode, RoutingPolicy, StoredProvenance } from "./types";

const NOT_REPORTED: StoredProvenance = { kind: "not-reported" };

/** Display rules for a Jetson's telemetry (null disables a rule). */
export const JETSON_FLAG_RULES: FlagRules = {
  socTempWarnC: 90, socTempAttentionC: 97, boardPowerWarnPctOfCap: 95, memoryAvailableWarnPct: 10,
  fallbackAttentionPct: 15, edgeP95BudgetMs: null, cloudTimeoutBudgetPct: null, reportAttentionS: 90,
};

/** Edge hardware the form offers: nominal memory and power modes; thermal limits only where Convoy measured them. */
export const HARDWARE: ReadonlyArray<{ id: string; hardware: EdgeHardware }> = [
  {
    id: "orin-nano-super-8gb",
    hardware: {
      name: "Jetson Orin Nano Super 8 GB", memoryGiB: 7.4, compute: "1,024 CUDA cores · 6-core Arm Cortex-A78AE", software: "L4T 36.4.7 · CUDA 12.6",
      powerModes: [
        { id: "15w", label: "15 W", capW: 15 },
        { id: "25w", label: "25 W", capW: 25, recommended: true },
        { id: "maxn-super", label: "MAXN SUPER", capW: null },
      ],
      powerModeId: "25w",
      thermal: { sensor: "Jetson SoC, hottest zone", swThrottleC: 99, hwThrottleC: 103, shutdownC: 104.5 },
      edgeBudgetGiB: null,
    },
  },
  {
    id: "orin-nx-16gb",
    hardware: {
      name: "Jetson Orin NX 16 GB", memoryGiB: 14.9, compute: "1,024 CUDA cores · 8-core Arm Cortex-A78AE",
      powerModes: [{ id: "10w", label: "10 W", capW: 10 }, { id: "15w", label: "15 W", capW: 15 }, { id: "25w", label: "25 W", capW: 25, recommended: true }],
      powerModeId: "25w",
      thermal: { sensor: "Jetson SoC, hottest zone", swThrottleC: null, hwThrottleC: null, shutdownC: null },
      edgeBudgetGiB: null,
    },
  },
  {
    id: "agx-orin-64gb",
    hardware: {
      name: "Jetson AGX Orin 64 GB", memoryGiB: 59.6, compute: "2,048 CUDA cores · 12-core Arm Cortex-A78AE",
      powerModes: [{ id: "15w", label: "15 W", capW: 15 }, { id: "30w", label: "30 W", capW: 30 }, { id: "50w", label: "50 W", capW: 50, recommended: true }, { id: "maxn", label: "MAXN", capW: null }],
      powerModeId: "50w",
      thermal: { sensor: "Jetson SoC, hottest zone", swThrottleC: null, hwThrottleC: null, shutdownC: null },
      edgeBudgetGiB: null,
    },
  },
];

/** Known edge models: picking one by name fills in its runtime and footprint. */
export const EDGE_MODELS: readonly EdgeModel[] = [
  { id: "edge-planner", role: "planner", name: "Qwen2.5-1.5B-Instruct Q4_K_M", shortName: "Qwen2.5-1.5B", runtime: "llama.cpp CUDA", contextTokens: 2048, residentGiB: 1.2, state: "active" },
  { id: "edge-policy", role: "policy", name: "SmolVLA (450M)", shortName: "SmolVLA-450M", runtime: "PyTorch CUDA", residentGiB: 1.8, state: "active" },
  { id: "edge-planner-4b", role: "planner", name: "Qwen3-4B Q4_K_M", shortName: "Qwen3-4B", runtime: "llama.cpp CUDA", contextTokens: 4096, residentGiB: 2.6, state: "active" },
];

export const ROLE_LABEL: Partial<Record<ModelRole, string>> = { planner: "Planner", policy: "Policy" };
export const ROUTE_LABEL: Record<RouteMode, string> = { "edge-only": "Edge only", "cloud-only": "Cloud only", hybrid: "Edge + cloud" };

/** Edge only, cloud only, or both (the edge plans and keeps control; the cloud acts). */
export function routeMode(edge: boolean, cloud: boolean): RouteMode {
  return edge && cloud ? "hybrid" : cloud ? "cloud-only" : "edge-only";
}

/** A route policy with no thresholds declared: nothing is assumed about link budgets. */
export function routingFor(mode: RouteMode): RoutingPolicy {
  const summary = ROUTE_LABEL[mode];
  return {
    mode, summary,
    defaultRoute: mode === "edge-only" ? "Every model runs on the edge device." : mode === "cloud-only" ? "The cloud model decides; the edge device executes." : "The edge plans; the cloud model acts.",
    fallbackTrigger: { cloudRttP95Ms: null, windowS: null, packetLossPct: null, missedDeadlines: null },
    fallbackAction: { validityMs: null, blendInto: null, speedFactor: null, otherwise: "safe-hold" },
    escalation: { stallS: null, failedGrasps: null, onVerifierAnomaly: "log", channel: "Operator console" },
    thermalGuard: { socTempC: null, overCurrentEventsPer10Min: null, action: "Alert the operator" },
  };
}

export interface NewConfigurationInput {
  name: string;
  robot: string;
  hardwareId: string;
  edgeModel: string;
  edgeRole: "planner" | "policy";
  cloudModel: string;
  cloudRole: "planner" | "policy";
}
export const EMPTY_INPUT: NewConfigurationInput = { name: "", robot: "", hardwareId: HARDWARE[0].id, edgeModel: "", edgeRole: "planner", cloudModel: "", cloudRole: "planner" };

export type InputErrors = Partial<Record<"name" | "robot" | "models", string>>;
/** Field errors, empty when the form can be saved. Names are unique (case-insensitive). */
export function inputErrors(input: NewConfigurationInput, takenNames: readonly string[]): InputErrors {
  const errors: InputErrors = {};
  const name = input.name.trim();
  if (!name) errors.name = "Enter a name.";
  else if (name.length > 80) errors.name = "Use at most 80 characters.";
  else if (takenNames.some(item => item.trim().toLowerCase() === name.toLowerCase())) errors.name = "A configuration has this name.";
  if (!input.robot.trim()) errors.robot = "Enter the robot.";
  if (!input.edgeModel.trim() && !input.cloudModel.trim()) errors.models = "Add an edge or a cloud model.";
  return errors;
}

function edgeModel(name: string, role: "planner" | "policy"): EdgeModel {
  const known = EDGE_MODELS.find(model => model.name.toLowerCase() === name.toLowerCase());
  return known ? { ...known, role } : { id: "edge-model", role, name, shortName: name, runtime: NOT_SPECIFIED, residentGiB: null, state: "active" };
}
function cloudModel(name: string, role: "planner" | "policy"): CloudModel {
  return { id: "cloud-model", role, name, shortName: name, serving: null, state: "active" };
}

/** Revision r1 from the form (createdAt `now`). */
export function buildRevision(input: NewConfigurationInput, now: number | Date): ConfigRevision {
  const preset = HARDWARE.find(item => item.id === input.hardwareId) ?? HARDWARE[0];
  const edge = input.edgeModel.trim(), cloud = input.cloudModel.trim();
  const mode = routeMode(!!edge, !!cloud);
  return {
    rev: "r1", createdAt: new Date(now).toISOString(),
    robot: { name: input.robot.trim(), summary: NOT_SPECIFIED, cameras: [], controlRateHz: null, actionSpace: NOT_SPECIFIED, simTwin: null, provenance: NOT_REPORTED },
    edgeHardware: preset.hardware,
    edgeModels: edge ? [edgeModel(edge, input.edgeRole)] : [],
    cloudModels: cloud ? [cloudModel(cloud, input.cloudRole)] : [],
    routing: routingFor(mode),
    safety: { name: "Default", definitions: [] },
    flagRules: JETSON_FLAG_RULES,
    compatibility: [],
  };
}
