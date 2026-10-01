/**
 * The Configurations workspace document (`ConvoyWorkspace`, schema version 1).
 *
 * One owner-scoped JSON document named "configurations" holds the declared
 * configurations, robots, evaluation suites and runs, rollouts, action traces
 * and logs. Every metric carries a provenance. A stored provenance is never
 * "measured": measured values come only from live device bindings at runtime
 * (see live.ts), so the stored types use `StoredProvenance`.
 *
 * Conventions: ids match `ID_PATTERN` (they appear in routes); times are ISO
 * 8601 strings; a missing value is `null` (rendered "Not reported", never 0);
 * percentages named `…Pct` are 0–100 and shares named `…Share`/`ci95` are 0–1.
 */

export const WORKSPACE_SCHEMA_VERSION = 1 as const;
/** Name of the workspace document in the control plane (`/api/platform/workspace-documents/{name}`). */
export const WORKSPACE_DOCUMENT_NAME = "configurations";
/** `Robot.deviceId` value that binds a robot to the workspace's configured device (the one `/api/portal/snapshot` reports). */
export const CONFIGURED_DEVICE = "configured-device";
/** Ids are route segments: letters, digits, `_` and `-`, 1–64 characters. */
export const ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

/* ---------- Evidence ---------- */

/** measured: read from a connected device now (live bindings only) · recorded: stored evidence with a date · sample: illustrative · not-reported: missing. */
export type ProvenanceKind = "measured" | "recorded" | "sample" | "not-reported";
export interface Provenance {
  kind: ProvenanceKind;
  /** When the evidence was recorded (recorded) or sampled (measured). */
  at?: string;
  /** End of a recorded range, for labels such as "Recorded · Sep 13–14". */
  until?: string;
  /** Sample size behind a statistic. */
  n?: number;
  /** Where the evidence lives, e.g. a document section, run or device. */
  source?: string;
}
/** Provenance that may be stored in the document: everything except measured. */
export type StoredProvenance = Provenance & { kind: Exclude<ProvenanceKind, "measured"> };

/** Which clock a time was read from. Different clocks are never mixed in one figure. */
export type ClockKind = "device" | "browser" | "wall" | "simulated" | "server";
/** Display zone for a device clock, e.g. { zone: "UTC", utcOffsetMinutes: 0 }. */
export interface ClockLabel { zone: string; utcOffsetMinutes: number }

/** Evenly spaced values, oldest first. `null` leaves a gap; it never means 0. */
export interface UniformSeries { stepS: number; values: Array<number | null> }
/** A uniform series anchored to a wall-clock start. */
export interface SampledSeries extends UniformSeries { start: string }
/** One point of an expanded or measured series. */
export interface SeriesPoint { at: string; value: number | null }
export interface Percentiles { p50: number | null; p95: number | null; n?: number }

/* ---------- Workspace ---------- */

export interface WorkspaceMeta {
  id: string;
  /** Full name, e.g. "Sample workspace". */
  name: string;
  /** Short label, e.g. "Sample". */
  label: string;
  description: string;
  updatedAt: string;
  /** True only for the built-in generic sample. */
  sample?: boolean;
}

export interface ConvoyWorkspace {
  schemaVersion: typeof WORKSPACE_SCHEMA_VERSION;
  meta: WorkspaceMeta;
  configurations: Configuration[];
  robots: Robot[];
  suites: EvalSuite[];
  runs: EvalRun[];
  rollouts: Rollout[];
  traces: ActionTrace[];
  logs: LogLine[];
  activity: ActivityEvent[];
}

/* ---------- Configurations ---------- */

export type ConfigStatus = "draft" | "testing" | "production";

export interface Configuration {
  id: string;
  name: string;
  purpose: string;
  status: ConfigStatus;
  recommended: boolean;
  /** Revision production robots run, e.g. "r3"; null until a revision is promoted. */
  productionRev: string | null;
  /** Revision under test, e.g. "r4"; null when nothing is being tested. */
  candidateRev: string | null;
  /** Oldest first. Every rev referenced elsewhere must be listed here. */
  revisions: ConfigRevision[];
  /** Suite whose gate decides promotion. */
  suiteId: string | null;
  createdAt: string;
  updatedAt: string;
  /** One-line verdict for the index card, e.g. lead "Recommended." text "Passes the suite gate.". */
  highlight?: ConfigHighlight;
  /** Aggregate view of the production robots (sample or recorded only). */
  production?: ProductionSummary;
}

export interface ConfigHighlight { tone: "good" | "warning" | "blocked"; lead: string; text: string; provenance?: StoredProvenance }

/** One immutable revision: robot + edge hardware + edge models + cloud models + routing + safety. */
export interface ConfigRevision {
  rev: string;
  createdAt: string;
  /** What changed, e.g. "Cloud policy v3.1". */
  note?: string;
  robot: RobotSpec;
  edgeHardware: EdgeHardware;
  edgeModels: EdgeModel[];
  cloudModels: CloudModel[];
  routing: RoutingPolicy;
  safety: SafetyEnvelope;
  flagRules: FlagRules;
  compatibility: CompatibilityCheck[];
}

export interface RobotSpec {
  name: string;
  /** e.g. "2 × 7-DOF arms · omnidirectional base". */
  summary: string;
  details?: string;
  cameras: string[];
  controlRateHz: number | null;
  actionSpace: string;
  safetyController?: string;
  simTwin: { name: string; engine: string; note?: string } | null;
  /** Declared values are usually assumptions for the pilot: sample. */
  provenance: StoredProvenance;
}

export interface PowerMode { id: string; label: string; /** null = uncapped (e.g. MAXN SUPER). */ capW: number | null; note?: string; recommended?: boolean }
/** Thermal limits of the edge device's SoC sensor. */
export interface ThermalLimits { sensor: string; swThrottleC: number | null; hwThrottleC: number | null; shutdownC: number | null }

export interface EdgeHardware {
  name: string;
  /** Usable memory, shared by CPU and GPU on a Jetson. */
  memoryGiB: number;
  memoryNote?: string;
  compute?: string;
  software?: string;
  powerModes: PowerMode[];
  /** Selected mode; must be one of `powerModes`. */
  powerModeId: string;
  thermal: ThermalLimits;
  /** Memory budget for all edge models resident. */
  edgeBudgetGiB?: number | null;
}

export type ModelRole = "planner" | "policy" | "fallback-policy" | "skill-pack" | "verifier";
export type ModelState = "active" | "fallback-only" | "proposed" | "blocked" | "not-deployed";

export interface EdgeModel {
  id: string;
  role: ModelRole;
  name: string;
  /** Compact label for cards and stepper values. */
  shortName: string;
  runtime: string;
  detail?: string;
  contextTokens?: number | null;
  /** Resident memory estimate on the edge device. */
  residentGiB: number | null;
  state: ModelState;
  /** Stored evidence for this model on this hardware. */
  evidence?: StoredProvenance;
}

export interface CloudModel {
  id: string;
  role: ModelRole;
  name: string;
  shortName: string;
  serving: string | null;
  detail?: string;
  /** Action chunk contract for motor policies. */
  chunk?: { actions: number; rateHz: number; validityMs: number } | null;
  state: ModelState;
  evidence?: StoredProvenance;
}

export type RouteMode = "hybrid" | "cloud-only" | "edge-only";
export interface RoutingPolicy {
  mode: RouteMode;
  /** e.g. "Edge plans, cloud acts". */
  summary: string;
  defaultRoute: string;
  fallbackTrigger: { cloudRttP95Ms: number | null; windowS: number | null; packetLossPct: number | null; missedDeadlines: number | null };
  fallbackAction: { validityMs: number | null; blendInto: string | null; speedFactor: number | null; otherwise: "safe-hold" | "pause-and-escalate" };
  escalation: { stallS: number | null; failedGrasps: number | null; onVerifierAnomaly: "escalate" | "log"; channel: string };
  thermalGuard: { socTempC: number | null; overCurrentEventsPer10Min: number | null; action: string };
}

export type Severity = "minor" | "major" | "critical";
/** A safety check counted in evaluations and enforced on the robot, e.g. id "S1". */
export interface SafetyDefinition { id: string; name: string; rule: string; severity: Severity; criticalWhen?: string }
export interface SafetyEnvelope { name: string; definitions: SafetyDefinition[]; note?: string }

/** Display rules Convoy evaluates on reported telemetry (null disables a rule). */
export interface FlagRules {
  socTempWarnC: number | null;
  socTempAttentionC: number | null;
  /** Board input at or above this share of the power-mode cap, percent. */
  boardPowerWarnPctOfCap: number | null;
  /** Memory available below this share of total, percent. */
  memoryAvailableWarnPct: number | null;
  fallbackAttentionPct: number | null;
  edgeP95BudgetMs: number | null;
  cloudTimeoutBudgetPct: number | null;
  /** No report for this long needs attention. */
  reportAttentionS: number | null;
}

export type CheckVerdict = "pass" | "warn" | "block" | "pending";
export interface CompatibilityCheck {
  id: string;
  verdict: CheckVerdict;
  title: string;
  detail: string;
  evidence: StoredProvenance;
  action?: { label: string; href: string | null };
}

/** Latency percentiles per step (hourly in the sample), oldest first; arrays have equal length. */
export interface LatencySeries {
  start: string;
  stepS: number;
  p50: Array<number | null>;
  p95: Array<number | null>;
  /** Share of cloud chunks that fell back to the edge per step, percent. */
  fallbackPct?: Array<number | null>;
  clock: ClockKind;
}

export interface ProductionSummary {
  provenance: StoredProvenance;
  window: { from: string; to: string; label: string };
  /** Production robots aggregated. */
  robots: number;
  /** Production robots that reported within the report window. */
  reporting: number;
  edgePlannerMs: Percentiles;
  cloudChunkMs: Percentiles | null;
  fallbackPct: number | null;
  interventionsPerRobotHour: { mean: number | null; min: number | null; max: number | null } | null;
  safetyEvents: { critical: number; major: number; minor: number; note?: string } | null;
  latency: { edge: LatencySeries | null; cloud: LatencySeries | null };
}

/* ---------- Robots ---------- */

export type RobotRole = "test" | "production";
/** robot: a physical robot · bench: edge hardware on a hardware-in-the-loop bench · simulator: a simulated robot without device telemetry. */
export type RobotKind = "robot" | "bench" | "simulator";
export type RobotHealth = "healthy" | "degraded" | "attention" | "offline" | "not-reported";
export type TelemetryMetric = "cpuPct" | "gpuPct" | "memAvailableMiB" | "socTempC" | "boardPowerW";

export interface TelemetryReading {
  at: string | null;
  cpuPct: number | null;
  gpuPct: number | null;
  memAvailableMiB: number | null;
  memTotalMiB: number | null;
  /** Jetson SoC temperature, max zone. */
  socTempC: number | null;
  /** Board input power (VDD_IN). */
  boardPowerW: number | null;
  /** Highest board input in the reported window. */
  boardPowerPeakW?: number | null;
  batteryPct?: number | null;
  runtimeState?: string | null;
}

export interface RobotTelemetry {
  provenance: StoredProvenance;
  latest: TelemetryReading;
  /** Recent samples about one per heartbeat (robot page sparklines). */
  recent?: Partial<Record<TelemetryMetric, SampledSeries>>;
  /** Last 24 h, hourly: socTempC = hourly max, boardPowerW = hourly peak, others hourly mean. */
  day?: Partial<Record<TelemetryMetric, SampledSeries>>;
  clock: ClockKind;
}

export interface RobotLatency {
  provenance: StoredProvenance;
  /** e.g. "24 h" or "newest 20 requests". */
  window: string;
  edgePlannerMs: Percentiles;
  ttftMs?: Percentiles | null;
  /** Observation to chunk received, end to end. */
  cloudChunkMs: Percentiles | null;
  /** Share of cloud chunks on fallback, percent. */
  fallbackPct: number | null;
  cloudTimeoutPct?: number | null;
}

export type FlagSeverity = "warning" | "attention";
export type FlagRule = "soc-temp" | "board-power" | "memory" | "edge-p95" | "cloud-timeouts" | "fallback" | "no-report" | "safety-stop" | "manual";
export interface Flag {
  id: string;
  rule: FlagRule;
  severity: FlagSeverity;
  /** Short reason, e.g. "Near thermal throttle". */
  label: string;
  /** Evidence sentence, e.g. "Jetson SoC 97.6 °C, at or above the 97 °C attention rule". */
  detail: string;
  at: string;
  /** Manual flags carry the operator's note. */
  note?: string;
  by?: string;
  provenance: StoredProvenance;
}

export interface Robot {
  id: string;
  name: string;
  /** null = registered but not attached to a configuration. */
  configId: string | null;
  role: RobotRole;
  site: string;
  /** Configuration revision it runs; null when unattached. */
  rev: string | null;
  /** Live binding: a control-plane device id, or `CONFIGURED_DEVICE`. Bound robots store no telemetry. */
  deviceId?: string;
  /**
   * Control-plane project (`prj_…`) whose evaluations, and missions run outside an
   * evaluation, are this robot's evals: metrics, rollouts and replays come from the
   * platform, not the document.
   */
  projectId?: string | null;
  /** Control-plane robot (`rob_…`) in `projectId`; when set, only its evaluations and missions count. */
  platformRobotId?: string | null;
  kind: RobotKind;
  description?: string;
  /** Declared or sample health; a bound robot's health is derived from live data. */
  health: RobotHealth;
  healthReason?: string | null;
  flags: Flag[];
  telemetry?: RobotTelemetry;
  latency?: RobotLatency;
  interventions?: { perHour: number | null; robotHours?: number | null; provenance: StoredProvenance };
  clock?: ClockLabel;
  lastSeenAt?: string | null;
  registeredAt: string;
  agentVersion?: string | null;
}

/* ---------- Evaluations ---------- */

export interface EvalTask { id: string; name: string; referenceMedianS?: number | null }
export interface SliceDefinition { id: string; name: string; description?: string }
export interface SliceFamily { id: string; name: string; slices: SliceDefinition[] }
export interface GateCriterion { id: string; label: string; target: string }

export interface EvalSuite {
  id: string;
  name: string;
  version: string;
  description: string;
  simEngine: string;
  /** Which clock and harness the suite uses, e.g. offline lockstep simulation. */
  timing: string;
  episodesPerRun: number;
  seedsPerCell: number;
  tasks: EvalTask[];
  sliceFamilies: SliceFamily[];
  safety: SafetyDefinition[];
  gate: GateCriterion[];
}

export type RunStatus = "queued" | "running" | "passed-gate" | "below-gate" | "completed" | "did-not-qualify" | "cancelled" | "failed";
/** suite: an evaluation-suite run · hil-latency: a latency soak on bench hardware · timing: a wall-clock timing experiment · episode: a single recorded episode. */
export type RunKind = "suite" | "hil-latency" | "timing" | "episode";

export interface RunSafety {
  /** Episodes with a violation per 100 episodes. */
  per100: number | null;
  episodesWithViolations: number;
  critical: number;
  major: number;
  minor: number;
  byCheck: Array<{ checkId: string; count: number }>;
}
export interface TaskResult { taskId: string; episodes: number; successes: number; medianS: number | null; safetyPer100: number | null }
export interface SliceResult { sliceId: string; episodes: number; successes: number; ci95?: [number, number] | null; safetyPer100: number | null; medianS: number | null }
export interface GateResult { criterionId: string; passed: boolean; actual: string }

export interface EvalRun {
  id: string;
  /** Display number: "Run 23" (never "#23"). Unique per workspace. */
  number: number;
  kind: RunKind;
  /** Suite or experiment name shown with the run. */
  title: string;
  suiteId: string | null;
  configId: string;
  rev: string;
  /** Policy variant under test, e.g. "Hybrid". */
  variant: string;
  robotId: string;
  /** e.g. "Candidate · seeds 1–10". */
  purpose?: string;
  status: RunStatus;
  /** Present while queued or running. */
  progress?: { done: number; total: number } | null;
  startedAt: string | null;
  finishedAt: string | null;
  simEngine?: string;
  /** Episodes so far (running) or in total; successes null when not scored. */
  counts: { episodes: number; successes: number | null };
  /** 95 % confidence interval of the success share, 0–1. */
  successCi95?: [number, number] | null;
  /** null: safety is not measured in this run (show "Not reported"). */
  safety: RunSafety | null;
  episodeTime: { medianS: number | null; p90S: number | null; clock: ClockKind } | null;
  baselineRunId?: string | null;
  perTask: TaskResult[];
  slices: SliceResult[];
  failureModes: Array<{ label: string; count: number }>;
  gate: { passed: boolean; results: GateResult[] } | null;
  /** Edge latency measured during the run on bench hardware (recorded). */
  hilLatency?: { plannerMs: Percentiles; ttftMs?: Percentiles | null; provenance: StoredProvenance } | null;
  provenance: StoredProvenance;
  /** Control-plane evaluation id (`eva_…`) when this run is a real recorded evaluation. */
  recordedEvaluationId?: string | null;
  /** Control-plane episode id (`epi_…`) with a published recording for replay. */
  recordedEpisodeId?: string | null;
  note?: string;
}

export type RolloutOutcome = "succeeded" | "failed" | "timeout" | "safety-stop";
export interface RolloutEvent { atS: number; label: string; tone: "info" | "warning" | "good"; detail?: string }
export interface RolloutStep { step: number; atS: number; planner: string | null; action: string; latencyMs: number | null; path?: PathKind | null; note?: string }
export interface Rollout {
  id: string;
  runId: string;
  taskId: string;
  sliceIds: string[];
  seed: number;
  outcome: RolloutOutcome;
  failureMode?: string | null;
  violations: Array<{ checkId: string; severity: Severity; atS: number }>;
  /** Simulated seconds. */
  durationS: number;
  steps: number;
  rateHz: number | null;
  reward?: number | null;
  /** Wall-clock seconds the episode took to compute (lockstep), when recorded. */
  wallS?: number | null;
  events: RolloutEvent[];
  stepDetail?: RolloutStep[];
  /** Synced replay plots on the simulated clock. */
  signals?: { gripperAperture?: UniformSeries; eeSpeed?: UniformSeries; eeSpeedLimit?: number | null };
  provenance: StoredProvenance;
  /** Control-plane episode id with a published recording, for the real replay. */
  episodeId?: string | null;
}

/* ---------- Actions, traces and logs ---------- */

export type PathKind = "edge" | "cloud" | "fallback";
export type SpanPath = PathKind | "operator" | "none";
export type TraceOutcome = "succeeded" | "failed" | "escalated" | "clarified";
export interface TraceSpan {
  id: string;
  name: string;
  path: SpanPath;
  startMs: number;
  /** 0 = an instant event. */
  durationMs: number;
  /** Child spans indent under their parent. */
  parentId?: string | null;
  /** Spans can be drawn in several waterfalls (see `ActionTrace.spanGroups`). */
  group?: string;
}
export interface FactGroup { title: string; facts: Array<{ label: string; value: string; detail?: string }> }

export interface ActionTrace {
  /** Trace id, e.g. "trc_3f9a0c41d7e2". */
  id: string;
  robotId: string;
  at: string;
  clock: ClockKind;
  instruction: string;
  reference?: string | null;
  decision: { kind: "skill" | "declined"; skill: string | null; params?: string | null; reason?: string | null };
  path: PathKind;
  /** How the action was served, e.g. "Cloud policy + verifier". */
  route: string;
  plannerMs: number | null;
  /** null = no policy call. */
  policyMs: Percentiles | null;
  policyNote?: string | null;
  outcome: TraceOutcome;
  outcomeNote?: string | null;
  durationS: number | null;
  escalated: boolean;
  spans: TraceSpan[];
  /** Waterfalls to draw; spans without a group belong to the first. */
  spanGroups?: Array<{ id: string; title: string; unit: "ms" | "s" }>;
  facts: FactGroup[];
  provenance: StoredProvenance;
  configRev?: string | null;
}

export type LogLevel = "info" | "warn" | "error";
export interface LogLine {
  id: string;
  at: string;
  clock: ClockKind;
  level: LogLevel;
  /** Component, e.g. "router", "planner", "agent". */
  source: string;
  robotId: string | null;
  configId: string | null;
  message: string;
  provenance: StoredProvenance;
}

export type ActivityKind = "flagged" | "flag-cleared" | "run-queued" | "run-started" | "passed-gate" | "below-gate" | "promoted" | "configuration-created" | "robot-added" | "role-changed" | "reconnected";
export interface ActivityEvent {
  id: string;
  at: string;
  kind: ActivityKind;
  subject: { type: "robot" | "run" | "configuration"; id: string };
  configId: string | null;
  message: string;
  provenance: StoredProvenance;
}
