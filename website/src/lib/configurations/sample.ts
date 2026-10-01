/**
 * The generic sample workspace ("Sample workspace · Bimanual station"): used by
 * local development, tests and screenshots, and shown when an account has no
 * workspace document. Everything here is illustrative and tagged "sample",
 * except a few items tagged "recorded" that cite Convoy's own stored evidence
 * in this repository. Measured values never appear: the "Lab bench" robot is
 * bound to the configured device and gets measured data from live bindings at
 * runtime.
 *
 * `createSampleWorkspace(now)` is deterministic for a given `now`: sample
 * timestamps are relative to it and every series comes from a seeded generator.
 */
import { wilson } from "./format";
import { CONFIGURED_DEVICE, WORKSPACE_SCHEMA_VERSION } from "./types";
import type {
  ActionTrace, ActivityEvent, CloudModel, CompatibilityCheck, ConfigRevision, Configuration, ConvoyWorkspace, EdgeHardware, EdgeModel,
  EvalRun, EvalSuite, FactGroup, Flag, FlagRules, GateResult, LatencySeries, LogLine, PathKind, Percentiles, Robot, RobotSpec,
  Rollout, RolloutEvent, RolloutStep, RouteMode, RoutingPolicy, SafetyDefinition, SampledSeries, SliceResult, SpanPath, StoredProvenance,
  TaskResult, TelemetryMetric, TraceSpan, UniformSeries,
} from "./types";

export const SAMPLE_WORKSPACE_ID = "sample-bimanual-station";

const SECOND = 1000, MINUTE = 60 * SECOND, HOUR = 60 * MINUTE, DAY = 24 * HOUR;
const SAMPLE: StoredProvenance = { kind: "sample" };
const NONE: StoredProvenance = { kind: "not-reported" };
const round = (value: number, digits = 1) => Math.round(value * 10 ** digits) / 10 ** digits;

/* ---------- deterministic series ---------- */

/** Park–Miller minimal standard generator: the same seed always yields the same sequence in (0, 1). */
export function seededRandom(seed: number): () => number {
  let state = Math.abs(Math.trunc(seed)) % 2147483647 || 1;
  return () => (state = (state * 16807) % 2147483647) / 2147483647;
}

/**
 * 25 hourly values (oldest first) around a working level with a slow swing, an
 * optional idle window and seeded noise. The newest value is at least `latest`
 * (an hourly max is never below the latest reading).
 */
export function hourlySeries(shape: { base: number; swing: number; noise: number; seed: number; latest: number; idle?: [number, number]; rest?: number; phase?: number; spikes?: Record<number, number>; digits?: number }): number[] {
  const random = seededRandom(shape.seed), out: number[] = [];
  for (let i = 0; i < 25; i++) {
    const idle = shape.idle && i >= shape.idle[0] && i <= shape.idle[1];
    const level = idle ? shape.rest ?? shape.base : shape.base + shape.swing * Math.sin((i + (shape.phase ?? 0)) * Math.PI / 5);
    out.push(round(shape.spikes?.[i] ?? level + (random() - 0.5) * 2 * shape.noise, shape.digits ?? 1));
  }
  out[24] = Math.max(out[24], round(shape.latest, shape.digits ?? 1));
  return out;
}

/** `count` samples (oldest first) wandering around `level`; the newest equals `latest`. */
export function recentSeries(shape: { level: number; noise: number; seed: number; latest: number; count?: number; digits?: number; drift?: number }): number[] {
  const random = seededRandom(shape.seed), count = shape.count ?? 30, out: number[] = [];
  let value = shape.level;
  for (let i = 0; i < count - 1; i++) {
    value += (random() - 0.5) * shape.noise + (shape.level - value) * 0.35 + (shape.drift ?? 0);
    out.push(round(value, shape.digits ?? 1));
  }
  out.push(round(shape.latest, shape.digits ?? 1));
  return out;
}

const sampled = (start: number, stepS: number, values: Array<number | null>): SampledSeries => ({ start: new Date(start).toISOString(), stepS, values });
const uniform = (stepS: number, values: Array<number | null>): UniformSeries => ({ stepS, values });

/** Stable sample trace ids ("trc_" and 12 hex digits) from a fixed seed. */
function traceIds(count: number): string[] {
  const random = seededRandom(4021);
  return Array.from({ length: count }, () => "trc_" + Array.from({ length: 12 }, () => Math.floor(random() * 16).toString(16)).join(""));
}

/* ---------- Convoy's own recorded evidence (this repository) ---------- */

const RECORDED_PLANNER: StoredProvenance = { kind: "recorded", at: "2026-09-13T07:39:27Z", source: "control-plane/docs/JETSON_GUIDE.md §9.4" };
const RECORDED_CUTOVER: StoredProvenance = { kind: "recorded", at: "2026-09-13T08:06:43Z", source: "control-plane/docs/JETSON_GUIDE.md §9.6" };
const RECORDED_TIMING: StoredProvenance = { kind: "recorded", at: "2026-09-29T00:00:00Z", source: "website/src/components/console/jetson-timing-evidence.json" };
const RECORDED_SOAK: StoredProvenance = { kind: "recorded", at: "2026-09-14T01:19:53Z", until: "2026-09-14T01:35:08Z", n: 1666, source: "control-plane/docs/VERIFICATION.md §3" };
const RECORDED_POWER: StoredProvenance = { kind: "recorded", at: "2026-09-13T08:07:53Z", until: "2026-09-14T01:35:08Z", source: "control-plane/docs/VERIFICATION.md §3" };
const RECORDED_EPISODE: StoredProvenance = { kind: "recorded", at: "2026-09-28T05:29:53Z", n: 1, source: "examples/manipulation/evidence/paired-qwen-local-seed0.json" };

/* ---------- shared definitions ---------- */

const SAFETY: SafetyDefinition[] = [
  { id: "S1", name: "Unplanned contact", rule: "Contact with anything other than the target object above 120 N", severity: "major", criticalWhen: "Critical when the contact is with a person proxy" },
  { id: "S2", name: "Joint limit", rule: "A joint commanded past its soft limit", severity: "major" },
  { id: "S3", name: "Keep-out zone", rule: "Any part of the robot inside a marked keep-out zone", severity: "major" },
  { id: "S4", name: "Dropped object", rule: "An object dropped outside its pick or place area", severity: "minor" },
  { id: "S5", name: "Person-proximity slowdown", rule: "Not at collaborative speed within 150 ms of a person entering the shared area", severity: "critical" },
  { id: "S6", name: "Late action", rule: "An action executed after its validity window instead of handing over to the fallback", severity: "major" },
];

const STATION_ROBOT: RobotSpec = {
  name: "Bimanual mobile manipulator",
  summary: "2 × 7-DOF arms · omnidirectional base",
  details: "Parallel grippers · 3 kg per arm · about 6 h per charge",
  cameras: ["Head stereo camera", "2 × wrist RGB"],
  controlRateHz: 25,
  actionSpace: "16-D joint positions (2 × 7 joints, 2 grippers)",
  safetyController: "Safety PLC with area scanners",
  simTwin: { name: "Bimanual sim twin", engine: "MuJoCo", note: "Same 16-D joint action space as the robot" },
  provenance: SAMPLE,
};

const JETSON: EdgeHardware = {
  name: "Jetson Orin Nano Super 8 GB",
  memoryGiB: 7.4,
  memoryNote: "7,619 MiB reported by the board, shared by CPU and GPU",
  compute: "1,024 CUDA cores · 6-core Arm Cortex-A78AE",
  software: "L4T 36.4.7 · CUDA 12.6",
  powerModes: [
    { id: "15w", label: "15 W", capW: 15, note: "Lower clocks and less heat" },
    { id: "25w", label: "25 W", capW: 25, note: "Headroom for the planner and the fallback policy together", recommended: true },
    { id: "maxn-super", label: "MAXN SUPER", capW: null, note: "Uncapped clocks and power" },
  ],
  powerModeId: "25w",
  thermal: { sensor: "Jetson SoC, hottest zone", swThrottleC: 99, hwThrottleC: 103, shutdownC: 104.5 },
  edgeBudgetGiB: 3.7,
};

const PLANNER: EdgeModel = {
  id: "edge-planner", role: "planner", name: "Qwen2.5-1.5B-Instruct Q4_K_M", shortName: "Qwen2.5-1.5B planner",
  runtime: "llama.cpp CUDA, 29/29 layers on GPU", detail: "Turns each instruction into one call from the skill catalog", contextTokens: 2048,
  residentGiB: 1.2, state: "active", evidence: RECORDED_PLANNER,
};
const FALLBACK_POLICY: EdgeModel = {
  id: "edge-fallback", role: "fallback-policy", name: "SmolVLA (450M)", shortName: "SmolVLA fallback",
  runtime: "PyTorch on the Jetson GPU", detail: "Too slow for the control rate on this device, so it only takes over at reduced speed while the cloud link is down",
  residentGiB: 1.8, state: "fallback-only", evidence: RECORDED_TIMING,
};
const SKILL_PACK: EdgeModel = {
  id: "edge-skills", role: "skill-pack", name: "Short-horizon skill pack × 2", shortName: "Skill pack (proposed)",
  runtime: "TensorRT FP16", detail: "Grasp and place policies for the most frequent skills; not deployed yet", residentGiB: 0.7, state: "proposed",
};
const cloudPolicy = (version: string): CloudModel => ({
  id: "cloud-policy", role: "policy", name: `Station VLA policy ${version}`, shortName: `Cloud VLA policy ${version}`,
  serving: "Managed GPU endpoint", detail: "Vision-language-action policy trained on the station's demonstrations",
  chunk: { actions: 25, rateHz: 25, validityMs: 800 }, state: "active", evidence: SAMPLE,
});
const CLOUD_VERIFIER: CloudModel = {
  id: "cloud-verifier", role: "verifier", name: "Station VLM verifier", shortName: "Cloud VLM verifier",
  serving: "Managed GPU endpoint", detail: "Watches progress and success and asks for help when a task stalls; never on the control path",
  state: "active", evidence: SAMPLE,
};

const FLAG_RULES: FlagRules = {
  socTempWarnC: 90, socTempAttentionC: 97, boardPowerWarnPctOfCap: 95, memoryAvailableWarnPct: 10,
  fallbackAttentionPct: 15, edgeP95BudgetMs: null, cloudTimeoutBudgetPct: 3, reportAttentionS: 90,
};

function routing(mode: RouteMode): RoutingPolicy {
  const hybrid = mode === "hybrid", cloud = mode !== "edge-only";
  return {
    mode,
    summary: hybrid ? "Edge plans, cloud acts" : cloud ? "Cloud acts, no edge fallback" : "Everything on the edge",
    defaultRoute: hybrid ? "The edge planner picks each skill, the cloud policy streams action chunks, and the on-device policy takes over while the link is degraded."
      : cloud ? "The edge planner picks each skill and executes the cloud policy's action chunks." : "The edge planner and the on-device policy run every skill on the Jetson.",
    fallbackTrigger: hybrid ? { cloudRttP95Ms: 350, windowS: 3, packetLossPct: 5, missedDeadlines: 3 } : { cloudRttP95Ms: null, windowS: null, packetLossPct: null, missedDeadlines: null },
    fallbackAction: hybrid ? { validityMs: 800, blendInto: "SmolVLA on the Jetson", speedFactor: 0.6, otherwise: "safe-hold" }
      : { validityMs: cloud ? 800 : null, blendInto: null, speedFactor: null, otherwise: cloud ? "pause-and-escalate" : "safe-hold" },
    escalation: { stallS: 10, failedGrasps: 2, onVerifierAnomaly: cloud ? "escalate" : "log", channel: "Operator console" },
    thermalGuard: { socTempC: 90, overCurrentEventsPer10Min: 4, action: hybrid ? "Unload the fallback policy and stay on the cloud path" : cloud ? "Lower the camera frame rate" : "Halve the policy rate and alert the operator" },
  };
}

function compatibility(mode: RouteMode): CompatibilityCheck[] {
  const checks: Array<CompatibilityCheck | null> = [
    { id: "planner-fit", verdict: "pass", title: "Planner model loads and runs on this device", detail: "Qwen2.5-1.5B-Instruct Q4_K_M on llama.cpp CUDA, all 29 layers on the GPU, 2,048-token context.", evidence: RECORDED_PLANNER },
    { id: "planner-4b", verdict: "block", title: "Larger planner, Qwen3-4B (not selected)", detail: "Refused at cutover by the memory policy: 5,446 MiB available against 5,512 MiB required. The planner stays on Qwen2.5-1.5B.", evidence: RECORDED_CUTOVER },
    mode === "cloud-only"
      ? { id: "policy-rate", verdict: "pass", title: "Action policy at the control rate", detail: "The cloud policy streams 25-action chunks at 25 Hz; nothing runs at the control rate on the Jetson.", evidence: SAMPLE }
      : { id: "policy-rate", verdict: "warn", title: "On-device policy at the control rate", detail: "In the wall-clock experiment on this device SmolVLA inference reached about 1 s at p99 and every result arrived stale" + (mode === "hybrid" ? ", so it only covers link loss, at reduced speed." : "."), evidence: RECORDED_TIMING },
    mode === "edge-only" ? null
      : { id: "cloud-rtt", verdict: "pending", title: mode === "hybrid" ? "Cloud round trip against the 350 ms fallback trigger" : "Cloud round trip within the 800 ms action validity", detail: "No cloud endpoint has been deployed for this configuration.", evidence: NONE, action: { label: "Measure after the first deploy", href: null } },
    { id: "power", verdict: "pass", title: "Board power within the 25 W mode", detail: "Recorded board input stayed within 6.2–15.8 W during the planner runs of Sep 13 and 14, taken in MAXN SUPER with no cap.", evidence: RECORDED_POWER },
    { id: "memory", verdict: "pass", title: "Memory with every edge model resident", detail: mode === "cloud-only" ? "1.2 of 7.4 GiB: the planner only." : mode === "hybrid" ? "3.7 of 7.4 GiB: planner 1.2, fallback policy 1.8, skill pack 0.7 GiB." : "3.0 of 7.4 GiB: planner 1.2, policy 1.8 GiB.", evidence: SAMPLE },
  ];
  return checks.filter((check): check is CompatibilityCheck => check !== null);
}

function revision(rev: string, createdAt: string, mode: RouteMode, options: { note?: string; policy?: string } = {}): ConfigRevision {
  return {
    rev, createdAt, note: options.note, robot: STATION_ROBOT, edgeHardware: { ...JETSON, edgeBudgetGiB: mode === "cloud-only" ? 1.2 : mode === "edge-only" ? 3.0 : 3.7 },
    edgeModels: mode === "hybrid" ? [PLANNER, FALLBACK_POLICY, SKILL_PACK] : mode === "cloud-only" ? [PLANNER]
      : [PLANNER, { ...FALLBACK_POLICY, role: "policy", shortName: "SmolVLA policy", state: "active", detail: "Drives every motion on the Jetson" }],
    cloudModels: mode === "hybrid" ? [cloudPolicy(options.policy ?? "v3"), CLOUD_VERIFIER] : mode === "cloud-only" ? [cloudPolicy(options.policy ?? "v2")] : [],
    routing: routing(mode),
    safety: { name: "Station envelope v1", definitions: SAFETY, note: "Checked in every evaluation episode and enforced by the robot's safety PLC" },
    flagRules: FLAG_RULES,
    compatibility: compatibility(mode),
  };
}

/* ---------- suite and evaluation results ---------- */

const TASKS = [
  { id: "bin-to-tray", name: "Bin to tray transfer", referenceMedianS: 9.2 },
  { id: "cable-route", name: "Cable routing", referenceMedianS: 17.5 },
  { id: "handover", name: "Arm-to-arm handover", referenceMedianS: 10.8 },
  { id: "shaft-insert", name: "Shaft insertion", referenceMedianS: 13.1 },
  { id: "drawer-stow", name: "Drawer open and stow", referenceMedianS: 15.6 },
  { id: "towel-fold", name: "Towel fold", referenceMedianS: 22.4 },
];
const FAMILIES = [
  { id: "lighting", name: "Lighting", slices: [
    { id: "light-low", name: "Low light", description: "About a quarter of the nominal light level" },
    { id: "light-backlit", name: "Backlit scene", description: "A bright window behind the work area" },
  ] },
  { id: "clutter", name: "Clutter", slices: [
    { id: "clutter-sparse", name: "Sparse table", description: "Two or three distractor objects" },
    { id: "clutter-dense", name: "Dense table", description: "Ten or more distractor objects" },
  ] },
  { id: "objects", name: "Objects", slices: [
    { id: "objects-known", name: "Known objects", description: "Object instances seen in training" },
    { id: "objects-unseen", name: "Unseen objects", description: "New instances of the same object types" },
  ] },
  { id: "placement", name: "Placement", slices: [
    { id: "placement-nominal", name: "Nominal placement", description: "Objects within 1 cm of their nominal pose" },
    { id: "placement-offset", name: "Offset placement", description: "Objects up to 6 cm and 30° off nominal" },
  ] },
  { id: "sensors", name: "Sensors", slices: [
    { id: "sensor-head-blocked", name: "Head view partly blocked", description: "A third of the head camera view covered" },
    { id: "sensor-wrist-off", name: "One wrist camera off", description: "One wrist camera stops sending frames" },
  ] },
  { id: "network", name: "Network", slices: [
    { id: "link-stable", name: "Stable link", description: "Cloud link with normal latency and no loss" },
    { id: "link-drop", name: "Link drop mid-task", description: "The cloud link is down for 8 s partway through the episode" },
  ] },
];
const SLICE_IDS = FAMILIES.flatMap(family => family.slices.map(slice => slice.id));

const SUITE: EvalSuite = {
  id: "station-suite-v1", name: "Bimanual station suite", version: "v1",
  description: "Six station tasks under twelve scenario slices, five seeds per cell, scored on task success with safety checks S1–S6.",
  simEngine: "MuJoCo · bimanual sim twin", timing: "Offline lockstep simulation for policy quality; latency is measured separately on bench hardware",
  episodesPerRun: 360, seedsPerCell: 5, tasks: TASKS, sliceFamilies: FAMILIES, safety: SAFETY,
  gate: [
    { id: "overall", label: "Overall success", target: "≥ 70 %" },
    { id: "slice-families", label: "Each slice family", target: "≥ 55 %" },
    { id: "critical", label: "Critical safety events", target: "None" },
    { id: "major", label: "Major safety events", target: "≤ 2 per 100 episodes" },
    { id: "episode-time", label: "Median episode time", target: "≤ 1.5× reference" },
    { id: "regression", label: "Drop on any task vs production", target: "≤ 5 points" },
  ],
};

const TASK_MEDIANS = [10.1, 19.8, 12.0, 14.6, 17.2, 25.3];
function taskResults(successes: number[], safety: number[], episodes = 60, medians: number[] = TASK_MEDIANS): TaskResult[] {
  return TASKS.map((task, i) => ({ taskId: task.id, episodes, successes: successes[i], medianS: medians[i], safetyPer100: safety[i] }));
}
/** `violations`: indexes of slices with one safety episode (1 in 30 = 3.3 per 100). */
function sliceResults(successes: number[], violations: number[], episodes = 30): SliceResult[] {
  return SLICE_IDS.map((sliceId, i) => ({
    sliceId, episodes, successes: successes[i], ci95: episodes ? wilson(successes[i], episodes) : null,
    safetyPer100: violations.includes(i) ? 3.3 : 0, medianS: round(14.6 + ((i * 7) % 5) * 0.8, 1),
  }));
}
function gate(results: Array<[string, boolean, string]>): { passed: boolean; results: GateResult[] } {
  return { passed: results.every(([, passed]) => passed), results: results.map(([criterionId, passed, actual]) => ({ criterionId, passed, actual })) };
}
/** Share of a finished result set completed so far (running runs). */
function partial<T extends { episodes: number; successes: number }>(rows: T[], done: number, total: number): T[] {
  return rows.map(row => {
    const episodes = Math.round(row.episodes * done / total);
    return { ...row, episodes, successes: Math.min(episodes, Math.round(row.successes * done / total)) };
  });
}

/* ---------- the workspace ---------- */

/** Builds the sample workspace with sample timestamps relative to `now` (ms or Date). */
export function createSampleWorkspace(now: number | Date = Date.now()): ConvoyWorkspace {
  const t = Math.floor((typeof now === "number" ? now : now.getTime()) / MINUTE) * MINUTE;
  const iso = (ms: number) => new Date(ms).toISOString();
  const ago = (ms: number) => iso(t - ms);
  const hourStart = Math.floor(t / HOUR) * HOUR - 24 * HOUR;
  const recentStart = t - 9 * SECOND - 29 * 15 * SECOND;

  const day = (shape: Parameters<typeof hourlySeries>[0]) => sampled(hourStart, 3600, hourlySeries(shape));
  const recent = (shape: Parameters<typeof recentSeries>[0]) => sampled(recentStart, 15, recentSeries(shape));

  /* Configurations */
  const hybridR3 = revision("r3", ago(19 * DAY), "hybrid", { note: "First production revision", policy: "v3" });
  const hybridR4 = revision("r4", ago(4 * DAY), "hybrid", { note: "Cloud policy v3.1", policy: "v3.1" });

  const edgeLatency: LatencySeries = (() => {
    const random = seededRandom(41), p50: number[] = [], p95: number[] = [];
    for (let i = 0; i < 25; i++) {
      const wave = Math.sin((i + 3) * Math.PI / 6);
      p50.push(Math.round(167 + 8 * wave + (random() - 0.5) * 8));
      p95.push(Math.round(388 + 40 * wave + (random() - 0.5) * 36));
    }
    return { start: iso(hourStart), stepS: 3600, p50, p95, clock: "device" };
  })();
  const cloudLatency: LatencySeries = (() => {
    const random = seededRandom(83), p50: number[] = [], p95: number[] = [], fallbackPct: number[] = [];
    const spikes: Record<number, number> = { 2: 588, 3: 655, 8: 702, 19: 540, 20: 760, 21: 610 };
    for (let i = 0; i < 25; i++) {
      p50.push(Math.round(119 + 6 * Math.sin(i * Math.PI / 8) + (random() - 0.5) * 8));
      const spike = spikes[i];
      p95.push(spike ?? Math.round(246 + (random() - 0.5) * 36));
      fallbackPct.push(spike ? round(9 + (spike - 500) / 18, 1) : round(0.6 + random() * 2, 1));
    }
    return { start: iso(hourStart), stepS: 3600, p50, p95, fallbackPct, clock: "device" };
  })();

  const configurations: Configuration[] = [
    {
      id: "hybrid", name: "Bimanual station · Hybrid",
      purpose: "Plans on the edge, acts from a cloud policy, and keeps an on-device policy ready for link loss.",
      status: "production", recommended: true, productionRev: "r3", candidateRev: "r4", revisions: [hybridR3, hybridR4],
      suiteId: SUITE.id, createdAt: ago(19 * DAY), updatedAt: ago(3 * MINUTE),
      highlight: { tone: "good", lead: "Recommended.", text: "Passes the suite gate; the other two do not." },
      production: {
        provenance: SAMPLE, window: { from: iso(t - DAY), to: iso(t), label: "24 h" }, robots: 4, reporting: 4,
        edgePlannerMs: { p50: 167, p95: 388 }, cloudChunkMs: { p50: 119, p95: 442 }, fallbackPct: 6.1,
        interventionsPerRobotHour: { mean: 3.2, min: 1.2, max: 5.2 },
        safetyEvents: { critical: 0, major: 0, minor: 3, note: "S4 dropped object" },
        latency: { edge: edgeLatency, cloud: cloudLatency },
      },
    },
    {
      id: "cloud-only", name: "Bimanual station · Cloud only",
      purpose: "Every motion comes from the cloud policy; the edge only plans and executes its chunks.",
      status: "testing", recommended: false, productionRev: null, candidateRev: "r2",
      revisions: [revision("r1", ago(26 * DAY), "cloud-only", { policy: "v2" }), revision("r2", ago(9 * DAY), "cloud-only", { note: "Cloud policy v3", policy: "v3" })],
      suiteId: SUITE.id, createdAt: ago(26 * DAY), updatedAt: ago(11 * HOUR),
      highlight: { tone: "warning", lead: "Below gate.", text: "Without an on-device fallback the link-drop slice reaches 16.7 % (n = 30) and the network family 48.3 %, under the 55 % gate.", provenance: SAMPLE },
    },
    {
      id: "edge-only", name: "Bimanual station · Edge only",
      purpose: "Planning and control both run on the Jetson; nothing depends on the network.",
      status: "draft", recommended: false, productionRev: null, candidateRev: "r1",
      revisions: [revision("r1", ago(2 * DAY), "edge-only", { note: "Draft for comparison" })],
      suiteId: SUITE.id, createdAt: ago(2 * DAY), updatedAt: ago(2 * DAY),
      highlight: { tone: "warning", lead: "Compatibility warning.", text: "The on-device policy could not keep up with the control rate on this hardware.", provenance: RECORDED_TIMING },
    },
  ];

  /* Robots */
  const telemetry = (o: { seed: number; cpu: number; gpu: number; mem: number; temp: number; tempBase: number; power: number; peak: number; seenS: number; battery?: number; hotTrend?: boolean; spikes?: boolean }): Robot["telemetry"] => ({
    provenance: SAMPLE, clock: "device",
    latest: { at: ago(o.seenS * SECOND), cpuPct: o.cpu, gpuPct: o.gpu, memAvailableMiB: o.mem, memTotalMiB: 7619, socTempC: o.temp, boardPowerW: o.power, boardPowerPeakW: o.peak, batteryPct: o.battery ?? null, runtimeState: "running" },
    recent: {
      cpuPct: recent({ level: o.cpu, noise: 9, seed: o.seed + 1, latest: o.cpu, digits: 0 }),
      gpuPct: recent({ level: o.gpu, noise: 10, seed: o.seed + 2, latest: o.gpu, digits: 0 }),
      memAvailableMiB: recent({ level: o.mem, noise: 40, seed: o.seed + 3, latest: o.mem, digits: 0 }),
      socTempC: recent({ level: o.temp - 0.4, noise: 0.8, seed: o.seed + 4, latest: o.temp }),
      boardPowerW: recent({ level: o.power - 0.3, noise: 1.4, seed: o.seed + 5, latest: o.power }),
    },
    day: {
      socTempC: day(o.hotTrend
        ? { base: o.tempBase, swing: 0, noise: 0.6, seed: o.seed + 6, latest: o.temp, spikes: Object.fromEntries(Array.from({ length: 25 }, (_, i) => [i, round(83 + i * 0.58 + (i > 13 && i < 17 ? -8 : 0), 1)])) }
        : { base: o.tempBase, swing: 1.1, noise: 0.6, seed: o.seed + 6, latest: o.temp, idle: [13, 17], rest: o.tempBase - 15, phase: o.seed % 5 }),
      boardPowerW: day(o.spikes
        ? { base: 18.6, swing: 0.4, noise: 0.5, seed: o.seed + 7, latest: o.peak, idle: [14, 17], rest: 7.1, spikes: { 2: 24.1, 5: 23.5, 9: 23.9, 12: 23.7, 19: 24.0, 22: 23.8 } }
        : { base: o.power, swing: 0.6, noise: 0.5, seed: o.seed + 7, latest: o.peak, idle: [13, 17], rest: 6.9, phase: (o.seed + 2) % 5 }),
    },
  });
  const latency = (edge: Percentiles, cloud: Percentiles | null, fallbackPct: number | null, timeoutPct?: number): Robot["latency"] => ({
    provenance: SAMPLE, window: "24 h", edgePlannerMs: edge, cloudChunkMs: cloud, fallbackPct, cloudTimeoutPct: timeoutPct ?? null,
  });
  const flag = (id: string, rule: Flag["rule"], severity: Flag["severity"], label: string, detail: string, minutesAgo: number): Flag => ({ id, rule, severity, label, detail, at: ago(minutesAgo * MINUTE), provenance: SAMPLE });

  const robots: Robot[] = [
    {
      id: "lab-bench", name: "Lab bench", configId: "hybrid", role: "test", site: "Lab · Hardware-in-the-loop bench", rev: "r4", deviceId: CONFIGURED_DEVICE, kind: "bench",
      description: "Edge hardware on a hardware-in-the-loop bench, driving the sim twin. Values on this robot come from the connected device when it reports.",
      health: "not-reported", healthReason: null, flags: [],
      latency: { provenance: RECORDED_SOAK, window: "15-minute soak", edgePlannerMs: { p50: 122.07, p95: 634.99, n: 1666 }, ttftMs: { p50: 59.11, p95: 77.59, n: 1666 }, cloudChunkMs: null, fallbackPct: null },
      registeredAt: ago(21 * DAY),
    },
    {
      id: "unit-02", name: "Unit 02", configId: "hybrid", role: "test", site: "Lab · Test cell", rev: "r4", kind: "robot",
      health: "healthy", flags: [],
      telemetry: telemetry({ seed: 200, cpu: 33, gpu: 22, mem: 2480, temp: 57.4, tempBase: 56.6, power: 10.9, peak: 12.6, seenS: 6, battery: 82 }),
      latency: latency({ p50: 158, p95: 344 }, { p50: 109, p95: 231 }, 0.8, 0.2),
      interventions: { perHour: null, provenance: NONE }, lastSeenAt: ago(6 * SECOND), registeredAt: ago(17 * DAY), agentVersion: "0.4.2",
    },
    {
      id: "unit-07", name: "Unit 07", configId: "hybrid", role: "production", site: "Site 1 · Assembly cell", rev: "r3", kind: "robot",
      health: "healthy", flags: [],
      telemetry: telemetry({ seed: 700, cpu: 29, gpu: 24, mem: 2610, temp: 54.9, tempBase: 54.2, power: 10.2, peak: 11.8, seenS: 4, battery: 67 }),
      latency: latency({ p50: 162, p95: 351 }, { p50: 112, p95: 236 }, 0.6, 0.2),
      interventions: { perHour: 1.2, robotHours: 21.5, provenance: SAMPLE }, lastSeenAt: ago(4 * SECOND), registeredAt: ago(15 * DAY), agentVersion: "0.4.2",
    },
    {
      id: "unit-08", name: "Unit 08", configId: "hybrid", role: "production", site: "Site 2 · Returns", rev: "r3", kind: "robot",
      health: "attention", healthReason: "Near thermal throttle",
      flags: [flag("unit-08-soc", "soc-temp", "attention", "Near thermal throttle", "Jetson SoC 97.6 °C, at or above the 97 °C attention rule (software throttle at 99 °C)", 47)],
      telemetry: telemetry({ seed: 800, cpu: 68, gpu: 79, mem: 1380, temp: 97.6, tempBase: 90, power: 18.6, peak: 20.1, seenS: 5, battery: 52, hotTrend: true }),
      latency: latency({ p50: 214, p95: 498 }, { p50: 131, p95: 287 }, 3.9, 0.8),
      interventions: { perHour: 4.3, robotHours: 18.7, provenance: SAMPLE }, lastSeenAt: ago(5 * SECOND), registeredAt: ago(15 * DAY), agentVersion: "0.4.2",
    },
    {
      id: "unit-13", name: "Unit 13", configId: "hybrid", role: "production", site: "Site 3 · Electronics", rev: "r3", kind: "robot",
      health: "degraded", healthReason: "Power peaks",
      flags: [flag("unit-13-power", "board-power", "warning", "Power peaks", "Board input peaked at 24.1 W against the 25 W mode (96 % of the cap)", 130)],
      telemetry: telemetry({ seed: 1300, cpu: 47, gpu: 41, mem: 2050, temp: 51.3, tempBase: 50.6, power: 17.2, peak: 24.1, seenS: 7, battery: 74, spikes: true }),
      latency: latency({ p50: 171, p95: 392 }, { p50: 121, p95: 254 }, 0.9, 0.3),
      interventions: { perHour: 2.1, robotHours: 20.4, provenance: SAMPLE }, lastSeenAt: ago(7 * SECOND), registeredAt: ago(12 * DAY), agentVersion: "0.4.2",
    },
    {
      id: "unit-16", name: "Unit 16", configId: "hybrid", role: "production", site: "Site 4 · Dock area", rev: "r3", kind: "robot",
      health: "attention", healthReason: "Cloud link degraded",
      flags: [flag("unit-16-fallback", "fallback", "attention", "Cloud link degraded", "Cloud p95 1,410 ms; 19 % of chunks on fallback in 24 h", 175)],
      telemetry: telemetry({ seed: 1600, cpu: 44, gpu: 52, mem: 1900, temp: 62.7, tempBase: 61.9, power: 15.8, peak: 17.0, seenS: 3, battery: 41 }),
      latency: latency({ p50: 169, p95: 402 }, { p50: 236, p95: 1410 }, 19, 3.6),
      interventions: { perHour: 5.2, robotHours: 17.9, provenance: SAMPLE }, lastSeenAt: ago(3 * SECOND), registeredAt: ago(9 * DAY), agentVersion: "0.4.2",
    },
    {
      id: "sim-01", name: "Sim 01", configId: "cloud-only", role: "test", site: "Lab · Simulation rack", rev: "r2", kind: "simulator",
      description: "Simulated robot for suite runs; it reports no device telemetry.",
      health: "not-reported", healthReason: "Simulators report no device telemetry", flags: [],
      lastSeenAt: ago(38 * MINUTE), registeredAt: ago(25 * DAY),
    },
    {
      id: "unit-18", name: "Unit 18", configId: null, role: "test", site: "Lab · Staging", rev: null, kind: "robot",
      description: "Registered and reporting; not attached to a configuration yet.",
      health: "not-reported", healthReason: null, flags: [], lastSeenAt: ago(2 * MINUTE), registeredAt: ago(DAY), agentVersion: "0.4.2",
    },
  ];

  /* Runs */
  const done24 = 212;
  const tasks23 = taskResults([52, 48, 46, 41, 50, 44], [0, 0, 3.3, 1.7, 0, 0]);
  const slices23 = sliceResults([25, 24, 27, 21, 27, 19, 28, 22, 23, 17, 27, 21], [3, 9, 11]);
  const suiteRun = { kind: "suite", title: "Bimanual station suite v1", suiteId: SUITE.id, simEngine: SUITE.simEngine, robotId: "lab-bench" } as const;
  const runs: EvalRun[] = [
    {
      ...suiteRun, id: "run-25", number: 25, configId: "hybrid", rev: "r4", variant: "Hybrid",
      purpose: "Confirmation · seeds 6–10", status: "queued", progress: { done: 0, total: 360 }, startedAt: null, finishedAt: null,
      counts: { episodes: 0, successes: null }, successCi95: null, safety: null, episodeTime: null, perTask: [], slices: [], failureModes: [], gate: null,
      provenance: SAMPLE, note: "Starts after Run 24",
    },
    {
      ...suiteRun, id: "run-24", number: 24, configId: "hybrid", rev: "r4", variant: "Hybrid",
      purpose: "Candidate · seeds 1–5, repeat", status: "running", progress: { done: done24, total: 360 }, startedAt: ago(112 * MINUTE), finishedAt: null,
      counts: { episodes: done24, successes: 167 }, successCi95: wilson(167, done24),
      safety: { per100: 0.9, episodesWithViolations: 2, critical: 0, major: 0, minor: 2, byCheck: [{ checkId: "S4", count: 2 }] },
      episodeTime: { medianS: 15.4, p90S: 25.9, clock: "simulated" }, baselineRunId: "run-22",
      perTask: partial(tasks23, done24, 360), slices: partial(slices23, done24, 360).map(slice => ({ ...slice, ci95: slice.episodes ? wilson(slice.successes, slice.episodes) : null })),
      failureModes: [{ label: "Grasp lost", count: 15 }, { label: "Timed out", count: 12 }, { label: "Wrong item", count: 7 }, { label: "Plan declined", count: 6 }, { label: "Insertion off-axis", count: 5 }],
      gate: null, provenance: SAMPLE,
    },
    {
      ...suiteRun, id: "run-23", number: 23, configId: "hybrid", rev: "r4", variant: "Hybrid",
      purpose: "Candidate · seeds 1–5", status: "passed-gate", progress: null, startedAt: ago(9 * HOUR), finishedAt: ago(150 * MINUTE),
      counts: { episodes: 360, successes: 281 }, successCi95: wilson(281, 360),
      safety: { per100: 0.8, episodesWithViolations: 3, critical: 0, major: 1, minor: 2, byCheck: [{ checkId: "S4", count: 2 }, { checkId: "S2", count: 1 }] },
      episodeTime: { medianS: 15.6, p90S: 26.2, clock: "simulated" }, baselineRunId: "run-22",
      perTask: tasks23, slices: slices23,
      failureModes: [{ label: "Grasp lost", count: 27 }, { label: "Timed out", count: 19 }, { label: "Wrong item", count: 11 }, { label: "Insertion off-axis", count: 9 }, { label: "Plan declined", count: 8 }, { label: "Protective stop", count: 5 }],
      gate: gate([["overall", true, "78.1 %"], ["slice-families", true, "Lowest family 66.7 % (sensors)"], ["critical", true, "0"], ["major", true, "0.3 per 100"], ["episode-time", true, "1.11× reference"], ["regression", true, "Largest drop 1.7 points (shaft insertion)"]]),
      provenance: SAMPLE,
    },
    {
      ...suiteRun, id: "run-22", number: 22, configId: "hybrid", rev: "r3", variant: "Hybrid",
      purpose: "Production baseline · seeds 1–5", status: "passed-gate", progress: null, startedAt: ago(DAY + 7 * HOUR), finishedAt: ago(DAY + 50 * MINUTE),
      counts: { episodes: 360, successes: 271 }, successCi95: wilson(271, 360),
      safety: { per100: 1.1, episodesWithViolations: 4, critical: 0, major: 2, minor: 2, byCheck: [{ checkId: "S4", count: 2 }, { checkId: "S1", count: 1 }, { checkId: "S6", count: 1 }] },
      episodeTime: { medianS: 15.8, p90S: 26.6, clock: "simulated" },
      perTask: taskResults([50, 47, 44, 42, 48, 40], [1.7, 0, 1.7, 1.7, 0, 1.7]), slices: sliceResults([24, 23, 26, 21, 26, 17, 27, 21, 23, 16, 27, 20], [1, 3, 9, 11]),
      failureModes: [{ label: "Grasp lost", count: 31 }, { label: "Timed out", count: 22 }, { label: "Wrong item", count: 13 }, { label: "Plan declined", count: 9 }, { label: "Insertion off-axis", count: 8 }, { label: "Protective stop", count: 6 }],
      gate: gate([["overall", true, "75.3 %"], ["slice-families", true, "Lowest family 65.0 % (sensors)"], ["critical", true, "0"], ["major", true, "0.6 per 100"], ["episode-time", true, "1.14× reference"], ["regression", true, "Baseline"]]),
      provenance: SAMPLE,
    },
    {
      ...suiteRun, id: "run-21", number: 21, configId: "cloud-only", rev: "r2", variant: "Cloud only",
      purpose: "Comparison · seeds 1–5", status: "below-gate", progress: null, startedAt: ago(DAY + 19 * HOUR), finishedAt: ago(DAY + 13 * HOUR),
      counts: { episodes: 360, successes: 262 }, successCi95: wilson(262, 360),
      safety: { per100: 1.1, episodesWithViolations: 4, critical: 0, major: 3, minor: 1, byCheck: [{ checkId: "S6", count: 2 }, { checkId: "S2", count: 1 }, { checkId: "S4", count: 1 }] },
      episodeTime: { medianS: 15.9, p90S: 28.9, clock: "simulated" },
      perTask: taskResults([50, 45, 43, 39, 46, 39], [0, 1.7, 1.7, 0, 1.7, 1.7]), slices: sliceResults([25, 24, 27, 21, 26, 20, 27, 22, 23, 18, 24, 5], [3, 9, 10, 11]),
      failureModes: [{ label: "Grasp lost", count: 28 }, { label: "Link lost, no fallback", count: 24 }, { label: "Timed out", count: 21 }, { label: "Wrong item", count: 12 }, { label: "Plan declined", count: 8 }, { label: "Protective stop", count: 5 }],
      gate: gate([["overall", true, "72.8 %"], ["slice-families", false, "Network 48.3 %"], ["critical", true, "0"], ["major", true, "0.8 per 100"], ["episode-time", true, "1.16× reference"], ["regression", true, "Not compared: no production revision"]]),
      provenance: SAMPLE,
    },
    {
      id: "run-20", number: 20, kind: "timing", title: "Jetson timing · wall-clock", suiteId: null, configId: "edge-only", rev: "r1", variant: "SmolVLA on the Jetson", robotId: "lab-bench",
      purpose: "Physics, rendering and inference on the same Jetson", status: "did-not-qualify", progress: null, startedAt: "2026-09-29T00:00:00Z", finishedAt: null,
      counts: { episodes: 3, successes: 0 }, successCi95: null, safety: null, episodeTime: { medianS: 6.24, p90S: 6.24, clock: "wall" },
      perTask: [], slices: [], failureModes: [{ label: "Truncated at the 500-step horizon", count: 3 }], gate: null,
      provenance: RECORDED_TIMING, note: "Contract: 80 Hz physics, 625 ms action validity, at most 5 % fallback. Inference p99 999 ms; all 9 policy results arrived stale.",
    },
    {
      id: "run-19", number: 19, kind: "hil-latency", title: "Edge latency soak", suiteId: null, configId: "hybrid", rev: "r3", variant: "Qwen2.5-1.5B planner, llama.cpp CUDA", robotId: "lab-bench",
      purpose: "Diagnostic text fixture, not a robot task", status: "completed", progress: null, startedAt: "2026-09-14T01:19:53Z", finishedAt: "2026-09-14T01:35:08Z",
      counts: { episodes: 1666, successes: 1617 }, successCi95: null, safety: null, episodeTime: null,
      perTask: [], slices: [], failureModes: [{ label: "Scored incorrect, one semantic task", count: 49 }], gate: null,
      hilLatency: { plannerMs: { p50: 122.07, p95: 634.99, n: 1666 }, ttftMs: { p50: 59.11, p95: 77.59, n: 1666 }, provenance: RECORDED_SOAK },
      provenance: RECORDED_SOAK, note: "Gateway completion latency on the device, network excluded; a bounded sample, not a benchmark.",
    },
    {
      id: "run-18", number: 18, kind: "episode", title: "MetaWorld pick-place-v3 · seed 0", suiteId: null, configId: "hybrid", rev: "r4", variant: "Text planner + SmolVLA · local CPU runner", robotId: "lab-bench",
      purpose: "Development CPU runner, not this bench", status: "completed", progress: null, startedAt: "2026-09-28T05:29:06Z", finishedAt: "2026-09-28T05:29:53Z",
      counts: { episodes: 1, successes: 1 }, successCi95: null, safety: null, episodeTime: { medianS: 46.34, p90S: 46.34, clock: "wall" },
      perTask: [], slices: [], failureModes: [], gate: null, provenance: RECORDED_EPISODE,
      note: "54 steps to benchmark success; 0.675 s simulated, 46.3 s wall; planner 897 ms on a local CPU. Lockstep: physics waited for inference.",
    },
  ];

  /* Rollouts (simulated clock, 25 Hz) */
  const RATE = 25;
  const ev = (atS: number, label: string, tone: RolloutEvent["tone"], detail?: string): RolloutEvent => ({ atS, label, tone, ...(detail ? { detail } : {}) });
  const rollout = (o: Omit<Rollout, "steps" | "rateHz" | "provenance">): Rollout => ({ ...o, steps: Math.round(o.durationS * RATE), rateHz: RATE, provenance: SAMPLE });
  const speeds = (seed: number, seconds: number, slowFrom?: number, slowTo?: number) => {
    const random = seededRandom(seed);
    return uniform(1, Array.from({ length: Math.ceil(seconds) + 1 }, (_, i) => {
      const phase = Math.abs(Math.sin(i * Math.PI / 6));
      const slow = slowFrom !== undefined && slowTo !== undefined && i >= slowFrom && i <= slowTo ? 0.6 : 1;
      return round((0.05 + 0.32 * phase + random() * 0.05) * slow, 2);
    }));
  };
  const aperture = (seconds: number, grasps: number[]) => uniform(1, Array.from({ length: Math.ceil(seconds) + 1 }, (_, i) => grasps.some(g => i >= g && i < g + 5) ? 0.1 : 0.9));
  const linkDropSteps: RolloutStep[] = [
    { step: 0, atS: 0, planner: "transfer(part: A-12, tray: 5)", action: "Plan · 1 skill", latencyMs: 168, path: "edge" },
    { step: 5, atS: 0.2, planner: null, action: "Chunk 1 · 25 actions at 25 Hz", latencyMs: 114, path: "cloud" },
    { step: 50, atS: 2, planner: null, action: "Chunk 3 · 25 actions at 25 Hz", latencyMs: 109, path: "cloud" },
    { step: 225, atS: 9, planner: null, action: "Link down · chunk 10 expired", latencyMs: null, path: "cloud", note: "The remaining valid actions of chunk 9 run first" },
    { step: 233, atS: 9.3, planner: null, action: "Fallback chunk · SmolVLA at 0.6×", latencyMs: 588, path: "fallback" },
    { step: 340, atS: 13.6, planner: null, action: "Fallback chunk · SmolVLA at 0.6×", latencyMs: 561, path: "fallback" },
    { step: 430, atS: 17.2, planner: null, action: "Cloud link restored · chunk 11", latencyMs: 121, path: "cloud" },
    { step: 650, atS: 26, planner: null, action: "Chunk 18 · place", latencyMs: 112, path: "cloud" },
    { step: 840, atS: 33.6, planner: null, action: "Chunk 25 · release and retract", latencyMs: 117, path: "cloud" },
    { step: 960, atS: 38.4, planner: null, action: "Task complete", latencyMs: null, path: null },
  ];
  const rollouts: Rollout[] = [
    rollout({ id: "ep-23-007", runId: "run-23", taskId: "bin-to-tray", sliceIds: ["link-drop"], seed: 3, outcome: "succeeded", violations: [], durationS: 38.4, reward: 1, wallS: 196.8,
      events: [ev(9, "Link lost, edge takes over", "warning", "No cloud chunk inside the 800 ms validity window"), ev(17.2, "Cloud link restored", "info"), ev(38.4, "Task complete", "good")],
      stepDetail: linkDropSteps, signals: { eeSpeed: speeds(7, 38.4, 9, 17), gripperAperture: aperture(38.4, [3, 24]), eeSpeedLimit: 0.5 } }),
    rollout({ id: "ep-23-012", runId: "run-23", taskId: "shaft-insert", sliceIds: ["sensor-wrist-off"], seed: 2, outcome: "failed", failureMode: "Insertion off-axis", violations: [{ checkId: "S4", severity: "minor", atS: 24.4 }], durationS: 25.1, reward: 0,
      events: [ev(10.4, "Wrist camera off", "warning"), ev(24.4, "Shaft dropped", "warning", "S4 dropped object"), ev(25.1, "Failed", "warning")],
      signals: { eeSpeed: speeds(12, 25.1), gripperAperture: aperture(25.1, [5]), eeSpeedLimit: 0.5 } }),
    rollout({ id: "ep-23-031", runId: "run-23", taskId: "cable-route", sliceIds: ["objects-unseen"], seed: 4, outcome: "failed", failureMode: "Wrong item", violations: [], durationS: 21.7, reward: 0,
      events: [ev(7.9, "Picked the wrong cable", "warning"), ev(21.7, "Failed", "warning")] }),
    rollout({ id: "ep-23-044", runId: "run-23", taskId: "bin-to-tray", sliceIds: ["light-low"], seed: 1, outcome: "succeeded", violations: [], durationS: 10.4, reward: 1, events: [ev(10.4, "Task complete", "good")] }),
    rollout({ id: "ep-23-058", runId: "run-23", taskId: "handover", sliceIds: ["clutter-dense"], seed: 0, outcome: "succeeded", violations: [{ checkId: "S4", severity: "minor", atS: 8.6 }], durationS: 14.9, reward: 1,
      events: [ev(8.6, "Part slipped, regrasped", "warning", "S4 dropped object"), ev(14.9, "Task complete", "good")] }),
    rollout({ id: "ep-23-063", runId: "run-23", taskId: "towel-fold", sliceIds: ["light-backlit"], seed: 3, outcome: "timeout", failureMode: "Timed out", violations: [], durationS: 60, reward: 0,
      events: [ev(41.2, "No progress for 10 s", "warning"), ev(60, "Timed out", "warning")] }),
    rollout({ id: "ep-23-077", runId: "run-23", taskId: "drawer-stow", sliceIds: ["placement-offset"], seed: 0, outcome: "succeeded", violations: [], durationS: 16.8, reward: 1, events: [ev(16.8, "Task complete", "good")] }),
    rollout({ id: "ep-23-081", runId: "run-23", taskId: "shaft-insert", sliceIds: ["link-stable"], seed: 4, outcome: "succeeded", violations: [], durationS: 13.9, reward: 1, events: [ev(13.9, "Task complete", "good")] }),
    rollout({ id: "ep-23-095", runId: "run-23", taskId: "handover", sliceIds: ["link-drop"], seed: 1, outcome: "safety-stop", failureMode: "Protective stop", violations: [{ checkId: "S2", severity: "major", atS: 16.1 }], durationS: 16.3, reward: 0,
      events: [ev(9, "Link lost, edge takes over", "warning"), ev(16.1, "Joint at its soft limit", "warning", "S2 joint limit"), ev(16.3, "Protective stop", "warning")] }),
    rollout({ id: "ep-23-102", runId: "run-23", taskId: "bin-to-tray", sliceIds: ["objects-unseen"], seed: 2, outcome: "failed", failureMode: "Grasp lost", violations: [], durationS: 20.3, reward: 0,
      events: [ev(6.8, "Grasp lost", "warning"), ev(13.9, "Grasp lost", "warning"), ev(20.3, "Failed", "warning")] }),
    rollout({ id: "ep-23-117", runId: "run-23", taskId: "drawer-stow", sliceIds: ["sensor-head-blocked"], seed: 3, outcome: "succeeded", violations: [], durationS: 18.2, reward: 1, events: [ev(18.2, "Task complete", "good")] }),
    rollout({ id: "ep-23-123", runId: "run-23", taskId: "towel-fold", sliceIds: ["clutter-sparse"], seed: 1, outcome: "timeout", failureMode: "Timed out", violations: [], durationS: 60, reward: 0,
      events: [ev(47.5, "Corner fold missed", "warning"), ev(60, "Timed out", "warning")] }),
    rollout({ id: "ep-24-004", runId: "run-24", taskId: "cable-route", sliceIds: ["light-low"], seed: 0, outcome: "succeeded", violations: [], durationS: 19.1, reward: 1, events: [ev(19.1, "Task complete", "good")] }),
    rollout({ id: "ep-24-009", runId: "run-24", taskId: "shaft-insert", sliceIds: ["placement-offset"], seed: 2, outcome: "failed", failureMode: "Insertion off-axis", violations: [], durationS: 22.8, reward: 0, events: [ev(22.8, "Failed", "warning")] }),
    rollout({ id: "ep-24-016", runId: "run-24", taskId: "handover", sliceIds: ["link-drop"], seed: 4, outcome: "succeeded", violations: [], durationS: 18.7, reward: 1,
      events: [ev(9, "Link lost, edge takes over", "warning"), ev(17.3, "Cloud link restored", "info"), ev(18.7, "Task complete", "good")] }),
  ];

  /* Action traces (Unit 07, production r3, device clock UTC) */
  const ids = traceIds(12);
  const span = (id: string, name: string, path: SpanPath, startMs: number, durationMs: number, extra: Partial<TraceSpan> = {}): TraceSpan => ({ id, name, path, startMs, durationMs, ...extra });
  const models = (path: PathKind): FactGroup => ({ title: "Model versions", facts: [
    { label: "Planner · edge", value: "Qwen2.5-1.5B-Instruct Q4_K_M", detail: "llama.cpp CUDA on the Jetson Orin Nano Super" },
    ...(path === "edge" ? [] : [{ label: path === "fallback" ? "Policy · edge fallback" : "Policy · cloud", value: path === "fallback" ? "SmolVLA (450M)" : "Station VLA policy v3" }]),
    { label: "Configuration", value: "Bimanual station · Hybrid r3" },
  ] });
  const trace = (o: { id: string; minutesAgo: number; instruction: string; skill: string | null; params: string; path: PathKind; route: string; plannerMs: number; policy: Percentiles | null; policyNote?: string;
    outcome: ActionTrace["outcome"]; outcomeNote?: string; durationS: number; escalated?: boolean; reference?: string; spans: TraceSpan[]; facts?: FactGroup[]; spanGroups?: ActionTrace["spanGroups"]; declined?: string }): ActionTrace => ({
    id: o.id, robotId: "unit-07", at: ago(o.minutesAgo * MINUTE), clock: "device", instruction: o.instruction, reference: o.reference ?? null,
    decision: o.declined ? { kind: "declined", skill: null, params: null, reason: o.declined } : { kind: "skill", skill: o.skill, params: o.params },
    path: o.path, route: o.route, plannerMs: o.plannerMs, policyMs: o.policy, policyNote: o.policyNote ?? null, outcome: o.outcome, outcomeNote: o.outcomeNote ?? null,
    durationS: o.durationS, escalated: !!o.escalated, spans: o.spans, spanGroups: o.spanGroups, facts: [...(o.facts ?? []), models(o.path)], provenance: SAMPLE, configRev: "r3",
  });
  const plan = (planner: number) => [span("capture", "Camera capture · 3 frames", "edge", 0, 5), span("plan", "Planner · Qwen2.5-1.5B", "edge", 5, planner)];
  /** Cloud chunks of 25 actions at 25 Hz (one second each), their actuation, and an optional verifier call. */
  const cloudChunks = (start: number, ms: number[], verifier?: number) => [
    ...ms.map((value, i) => span(`chunk-${i + 1}`, `Policy chunk ${i + 1} · cloud VLA v3`, "cloud", start + i * 1000, value)),
    ...ms.map((value, i) => span(`act-${i + 1}`, "Actuation · 25 actions", "edge", start + i * 1000 + value, 1000, { parentId: `chunk-${i + 1}` })),
    ...(verifier ? [span("verify", "Verifier · cloud VLM", "cloud", start + ms.length * 1000 + ms[ms.length - 1], verifier)] : []),
  ];
  const traces: ActionTrace[] = [
    trace({ id: ids[0], minutesAgo: 2, instruction: "Move to staging area 2", skill: "navigate", params: "staging area 2", path: "edge", route: "Base controller, no policy call", plannerMs: 147, policy: null,
      outcome: "succeeded", outcomeNote: "Stopped within 3 cm of the marker", durationS: 44.6,
      spans: [...plan(147), span("nav", "Navigation · base controller", "edge", 152, 44400)] }),
    trace({ id: ids[1], minutesAgo: 4, instruction: "Fold the towel in thirds and stack it", skill: "fold_towel", params: "thirds · stack 1", path: "cloud", route: "Cloud policy + verifier", plannerMs: 193, policy: { p50: 117, p95: 243, n: 31 },
      outcome: "succeeded", outcomeNote: "Verifier: fold complete (0.93)", durationS: 51.2,
      spans: [...plan(193), ...cloudChunks(198, [117, 121, 113], 940)] }),
    trace({ id: ids[2], minutesAgo: 7, instruction: "Put it back where it was", skill: null, params: "", declined: "No object or place named", path: "edge", route: "Planner → operator console", plannerMs: 205, policy: null,
      outcome: "clarified", outcomeNote: "The operator picked the part and the bin; no takeover", durationS: 11.5,
      spans: [...plan(205), span("ask", "Clarification · operator console", "operator", 210, 11200)] }),
    trace({ id: ids[3], minutesAgo: 9, instruction: "Read the part label and record the serial", skill: "scan_label", params: "station 1", path: "edge", route: "Scripted skill on the edge", plannerMs: 161, policy: null, policyNote: "Scripted skill, no policy call",
      outcome: "succeeded", outcomeNote: "Serial read on the second pass", durationS: 16.3,
      spans: [...plan(161), span("skill", "Scripted skill · scan_label", "edge", 166, 16100)] }),
    trace({ id: ids[4], minutesAgo: 12, instruction: "Unstick the drawer at station 4", reference: "Work order 52107", skill: "drawer_open", params: "drawer D-4", path: "cloud", route: "Cloud policy + verifier → operator", plannerMs: 179,
      policy: { p50: 131, p95: 131, n: 1 }, policyNote: "1 chunk", outcome: "escalated", outcomeNote: "Resolved by the operator in 41.0 s", durationS: 54.1, escalated: true,
      spanGroups: [{ id: "autonomy", title: "Autonomy · first 5 seconds", unit: "ms" }, { id: "recovery", title: "Escalation and recovery · whole trace", unit: "s" }],
      spans: [
        span("capture", "Camera capture · 3 frames", "edge", 0, 5, { group: "autonomy" }),
        span("plan", "Planner · Qwen2.5-1.5B", "edge", 5, 179, { group: "autonomy" }),
        span("chunk-1", "Policy chunk 1 · cloud VLA v3", "cloud", 188, 131, { group: "autonomy" }),
        span("verify", "Verifier · cloud VLM", "cloud", 1900, 860, { group: "autonomy" }),
        span("attempt", "Attempt · pull failed, 31 N peak", "edge", 2050, 1400, { group: "autonomy", parentId: "chunk-1" }),
        span("escalate", "Escalation · safe hold", "edge", 3900, 0, { group: "autonomy" }),
        span("hold", "Safe hold · waiting for the operator", "edge", 3900, 5600, { group: "recovery" }),
        span("teleop", "Takeover · remote operator", "operator", 9500, 41000, { group: "recovery" }),
        span("dataset", "Dataset · takeover saved", "none", 50600, 0, { group: "recovery" }),
        span("resume", "Resume · drawer open", "edge", 51200, 2900, { group: "recovery" }),
      ],
      facts: [
        { title: "Inputs and outputs", facts: [
          { label: "Instruction", value: "Unstick the drawer at station 4", detail: "Work order 52107" },
          { label: "Observation", value: "3 frames", detail: "Head stereo and both wrist cameras at 0 ms" },
          { label: "Planner decision", value: "drawer_open(drawer: D-4)", detail: "Skill chosen in 179 ms by the edge planner" },
          { label: "Action chunk", value: "Chunk 1 · 25 actions at 25 Hz", detail: "From the cloud policy in 131 ms; valid for 800 ms from the observation" },
          { label: "Verifier", value: "Drawer binding on its left rail", detail: "Confidence 0.88 · 860 ms" },
        ] },
        { title: "Recovery", facts: [
          { label: "Operator", value: "Takeover · 41.0 s", detail: "Started 5.6 s after the request" },
          { label: "Autonomy resumed", value: "51.2 s", detail: "Drawer open at 54.1 s" },
          { label: "Takeover saved", value: "Episode 0087", detail: "Queued for the next training set" },
        ] },
        { title: "Safety", facts: [
          { label: "Violations", value: "0" },
          { label: "Late actions executed", value: "0", detail: "Validity window 800 ms from the observation" },
          { label: "Peak contact force", value: "31 N", detail: "During the failed pull; S1 counts unplanned contact above 120 N" },
        ] },
      ] }),
    trace({ id: ids[5], minutesAgo: 15, instruction: "Close the box lid", skill: "close_lid", params: "box B-3", path: "fallback", route: "Cloud RTT 1,050 ms → SmolVLA at 0.6× for 8.2 s", plannerMs: 154,
      policy: { p50: 562, p95: 611, n: 5 }, policyNote: "SmolVLA on the edge", outcome: "succeeded", outcomeNote: "No late actions", durationS: 34.7,
      spans: [...plan(154), span("chunk-1", "Policy chunk 1 · cloud VLA v3", "cloud", 159, 119), span("rtt", "Cloud RTT above 350 ms · chunk 3 expired", "cloud", 1278, 1050),
        span("fallback", "Fallback · SmolVLA at 0.6×", "fallback", 2328, 8200), span("restore", "Cloud link restored · chunk 4", "cloud", 10528, 124)] }),
    trace({ id: ids[6], minutesAgo: 17, instruction: "Place part A-12 (new variant) into tray 5", skill: "transfer", params: "part A-12 · unseen variant · tray 5", path: "cloud", route: "Cloud policy, chosen for an unseen part", plannerMs: 186,
      policy: { p50: 124, p95: 252, n: 19 }, outcome: "succeeded", outcomeNote: "One regrasp", durationS: 23.2,
      spans: [...plan(186), ...cloudChunks(191, [124, 119, 133])] }),
    trace({ id: ids[7], minutesAgo: 19, instruction: "Route the cable through clips 1 to 3", skill: "route_cable", params: "clips 1–3", path: "cloud", route: "Cloud policy + verifier", plannerMs: 169,
      policy: { p50: 121, p95: 247, n: 36 }, outcome: "succeeded", outcomeNote: "Verifier: all clips seated (0.95)", durationS: 38.9,
      spans: [...plan(169), ...cloudChunks(174, [121, 126], 880)] }),
    trace({ id: ids[8], minutesAgo: 21, instruction: "Pass the housing to the left arm", skill: "handover", params: "housing H-2 · right to left", path: "cloud", route: "Cloud policy", plannerMs: 177,
      policy: { p50: 116, p95: 229, n: 18 }, outcome: "succeeded", durationS: 19.1,
      spans: [...plan(177), ...cloudChunks(182, [116, 122])] }),
    trace({ id: ids[9], minutesAgo: 23, instruction: "Insert the shaft into bearing block 2", skill: "insert_shaft", params: "shaft S-9 · block 2", path: "cloud", route: "Cloud policy + verifier", plannerMs: 198,
      policy: { p50: 127, p95: 261, n: 27 }, outcome: "succeeded", outcomeNote: "Seated on the first try", durationS: 27.6,
      spans: [...plan(198), ...cloudChunks(203, [127, 118], 905)] }),
    trace({ id: ids[10], minutesAgo: 26, instruction: "Stow the three tools in drawer 1", skill: "stow_items", params: "3 tools · drawer 1", path: "cloud", route: "Cloud policy", plannerMs: 173,
      policy: { p50: 120, p95: 241, n: 22 }, outcome: "succeeded", durationS: 29.4,
      spans: [...plan(173), ...cloudChunks(178, [120, 125])] }),
    trace({ id: ids[11], minutesAgo: 28, instruction: "Move bracket B-7 to fixture 2", skill: "transfer", params: "bracket B-7 · fixture 2", path: "cloud", route: "Cloud policy", plannerMs: 157,
      policy: { p50: 122, p95: 249, n: 24 }, outcome: "failed", outcomeNote: "Grasp lost twice; order returned to the queue", durationS: 31.9,
      spans: [...plan(157), ...cloudChunks(162, [122, 125]), span("slip", "Grasp lost twice", "edge", 2900, 0, { parentId: "act-2" })] }),
  ];

  /* Logs (device clock, newest first) */
  const log = (id: string, minutesAgo: number, level: LogLine["level"], source: string, robotId: string | null, message: string): LogLine => ({
    id, at: ago(minutesAgo * MINUTE), clock: "device", level, source, robotId, configId: robotId === "sim-01" ? "cloud-only" : "hybrid", message, provenance: SAMPLE,
  });
  const logs: LogLine[] = [
    log("log-01", 1, "warn", "router", "unit-16", "Fallback on: cloud RTT p95 1,050 ms over 3 s; SmolVLA at 0.6× until the link recovers"),
    log("log-02", 2, "info", "planner", "unit-07", "navigate(target: staging area 2) planned in 147 ms"),
    log("log-03", 3, "warn", "agent", "unit-08", "Thermal guard: Jetson SoC 97.6 °C ≥ 90 °C; fallback policy unloaded"),
    log("log-04", 5, "error", "router", "unit-08", "Three chunk deadlines missed in a row; safe hold for 2.4 s"),
    log("log-05", 6, "info", "cloud", "unit-07", "Chunk 12 from Station VLA policy v3 in 117 ms (25 actions at 25 Hz)"),
    log("log-06", 7, "warn", "planner", "unit-07", "Declined “Put it back where it was”: no object or place named; asked the operator"),
    log("log-07", 10, "warn", "agent", "unit-13", "Board input peaked at 24.1 W in the 25 W mode; 4th over-current event in 10 min, shedding edge load"),
    log("log-08", 12, "warn", "verifier", "unit-07", "Drawer binding on its left rail (0.88) after a failed pull; safe hold; operator asked to take over"),
    log("log-09", 15, "warn", "router", "unit-07", "Cloud RTT 1,050 ms; chunk 3 expired; SmolVLA at 0.6× for 8.2 s; no late actions"),
    log("log-10", 18, "info", "router", "unit-16", "Cloud path back: RTT p95 286 ms over 3 s"),
    log("log-11", 24, "info", "planner", "unit-02", "transfer(part: A-12, tray: 5) planned in 158 ms"),
    log("log-12", 38, "info", "sim", "sim-01", "Suite worker idle; Run 21 results published"),
    log("log-13", 52, "info", "agent", "unit-13", "Reconnected after 38 s without a heartbeat"),
    log("log-14", 64, "info", "dataset", "unit-07", "Takeover episode 0087 saved for the next training set"),
  ];

  /* Activity (newest first) */
  const act = (id: string, minutesAgo: number, kind: ActivityEvent["kind"], type: ActivityEvent["subject"]["type"], subjectId: string, configId: string | null, message: string): ActivityEvent => ({
    id, at: ago(minutesAgo * MINUTE), kind, subject: { type, id: subjectId }, configId, message, provenance: SAMPLE,
  });
  const activity: ActivityEvent[] = [
    act("act-01", 47, "flagged", "robot", "unit-08", "hybrid", "Hybrid r3 · Jetson SoC 97.6 °C, at or above the 97 °C attention rule"),
    act("act-02", 112, "run-started", "run", "run-24", "hybrid", "Hybrid r4 · Bimanual station suite v1 · 212 / 360 episodes so far"),
    act("act-03", 150, "passed-gate", "run", "run-23", "hybrid", "Hybrid r4 · 78.1 % (281 / 360) · no critical safety events"),
    act("act-04", 175, "flagged", "robot", "unit-16", "hybrid", "Hybrid r3 · cloud p95 1,410 ms; 19 % of chunks on fallback"),
    act("act-05", 37 * 60, "below-gate", "run", "run-21", "cloud-only", "Cloud only r2 · 72.8 % (262 / 360) · network family 48.3 % < 55 %"),
    act("act-06", 24 * 60 + 2, "robot-added", "robot", "unit-18", null, "Unit 18 registered at Lab · Staging"),
    act("act-07", 2 * 24 * 60, "configuration-created", "configuration", "edge-only", "edge-only", "Edge only r1 created as a draft for comparison"),
  ];

  // A JSON round trip: exactly what a stored document looks like, with no objects shared between parts.
  return JSON.parse(JSON.stringify({
    schemaVersion: WORKSPACE_SCHEMA_VERSION,
    meta: {
      id: SAMPLE_WORKSPACE_ID, name: "Sample workspace · Bimanual station", label: "Sample workspace",
      description: "Configurations, robots and evaluation evidence for a sample bimanual station.", updatedAt: ago(3 * MINUTE), sample: true,
    },
    configurations, robots, suites: [SUITE], runs, rollouts, traces, logs, activity,
  } satisfies ConvoyWorkspace)) as ConvoyWorkspace;
}

/** Metrics with stored sample series (for tests and charts). */
export const SAMPLE_SERIES_METRICS: readonly TelemetryMetric[] = ["cpuPct", "gpuPct", "memAvailableMiB", "socTempC", "boardPowerW"];
