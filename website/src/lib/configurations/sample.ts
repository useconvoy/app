/**
 * The generic sample workspace: shown when an account has no workspace document,
 * and used by local development and tests. Three configurations of one tabletop
 * arm, each with one test robot:
 *
 * - Edge planner: a planner on the Jetson; its robot is bound to the workspace's
 *   configured device, so its telemetry and traces are measured live;
 * - Cloud planner: a hosted planner, the Jetson as a thin edge; a simulated robot;
 * - Edge VLA: a small VLA policy on the Jetson; a simulated robot with suite evals.
 *
 * Stored values are tagged "sample" (illustrative). The sample's rollouts name no
 * control-plane episode, so none of them offers a replay. The sample is never
 * saved: an account's first change starts its own document.
 *
 * `createSampleWorkspace(now)` is deterministic for a given `now`.
 */
import { EDGE_MODELS, HARDWARE, JETSON_FLAG_RULES, routeMode, routingFor } from "./create";
import { wilson } from "./format";
import { CONFIGURED_DEVICE, WORKSPACE_SCHEMA_VERSION } from "./types";
import type { CloudModel, ConfigRevision, Configuration, ConvoyWorkspace, EdgeModel, EvalRun, EvalSuite, Robot, RobotSpec, Rollout, RolloutOutcome, SliceResult, StoredProvenance } from "./types";

export const SAMPLE_WORKSPACE_ID = "sample";

const MINUTE = 60_000, HOUR = 60 * MINUTE, DAY = 24 * HOUR;
const SAMPLE: StoredProvenance = { kind: "sample" };

/** Park–Miller minimal standard generator: the same seed always yields the same sequence in (0, 1). */
export function seededRandom(seed: number): () => number {
  let state = Math.abs(Math.trunc(seed)) % 2147483647 || 1;
  return () => (state = (state * 16807) % 2147483647) / 2147483647;
}

const ARM: RobotSpec = {
  name: "Tabletop arm", summary: "6-DOF arm · parallel gripper", cameras: ["Wrist RGB", "Overhead RGB"], controlRateHz: 20,
  actionSpace: "End-effector delta and gripper", simTwin: { name: "Tabletop twin", engine: "MuJoCo" }, provenance: SAMPLE,
};
const [PLANNER, POLICY] = EDGE_MODELS;
const CLOUD_PLANNER: CloudModel = { id: "cloud-planner", role: "planner", name: "Hosted LLM planner", shortName: "Hosted LLM", serving: "Managed endpoint", state: "active" };

function revision(createdAt: string, edge: EdgeModel[], cloud: CloudModel[]): ConfigRevision {
  return {
    rev: "r1", createdAt, robot: ARM, edgeHardware: HARDWARE[0].hardware, edgeModels: edge, cloudModels: cloud,
    routing: routingFor(routeMode(edge.length > 0, cloud.length > 0)), safety: { name: "Default", definitions: [] }, flagRules: JETSON_FLAG_RULES, compatibility: [],
  };
}

const SUITE: EvalSuite = {
  id: "pick-place-v1", name: "Pick and place", version: "v1", description: "Three tabletop tasks under six scenario slices.",
  simEngine: "MuJoCo", timing: "Offline lockstep simulation", episodesPerRun: 60, seedsPerCell: 10,
  tasks: [{ id: "pick-place", name: "Pick and place" }, { id: "open-drawer", name: "Open drawer" }, { id: "push", name: "Push to target" }],
  sliceFamilies: [
    { id: "lighting", name: "Lighting", slices: [{ id: "light-normal", name: "Normal light" }, { id: "light-low", name: "Low light" }] },
    { id: "clutter", name: "Clutter", slices: [{ id: "clutter-clear", name: "Clear table" }, { id: "clutter-dense", name: "Cluttered table" }] },
    { id: "objects", name: "Objects", slices: [{ id: "objects-seen", name: "Seen objects" }, { id: "objects-unseen", name: "Unseen objects" }] },
  ],
  safety: [],
  gate: [{ id: "overall", label: "Overall success", target: "≥ 70 %" }],
};
const SLICE_IDS = SUITE.sliceFamilies.flatMap(family => family.slices.map(slice => slice.id));
const TASK_IDS = SUITE.tasks.map(task => task.id);

/** Successes per slice (30 episodes each; each family's two slices cover all 60 episodes). */
function slices(successes: number[], episodes = 30): SliceResult[] {
  return SLICE_IDS.map((sliceId, i) => ({ sliceId, episodes, successes: successes[i], ci95: wilson(successes[i], episodes), safetyPer100: null, medianS: null }));
}

/** Builds the sample workspace with timestamps relative to `now` (ms or Date). */
export function createSampleWorkspace(now: number | Date = Date.now()): ConvoyWorkspace {
  const t = Math.floor((typeof now === "number" ? now : now.getTime()) / MINUTE) * MINUTE;
  const ago = (ms: number) => new Date(t - ms).toISOString();

  const configurations: Configuration[] = [
    { id: "edge-planner", name: "Edge planner", purpose: "", status: "testing", recommended: false, productionRev: null, candidateRev: "r1",
      revisions: [revision(ago(12 * DAY), [PLANNER], [])], suiteId: SUITE.id, createdAt: ago(12 * DAY), updatedAt: ago(2 * HOUR) },
    { id: "cloud-planner", name: "Cloud planner", purpose: "", status: "testing", recommended: false, productionRev: null, candidateRev: "r1",
      revisions: [revision(ago(10 * DAY), [], [CLOUD_PLANNER])], suiteId: SUITE.id, createdAt: ago(10 * DAY), updatedAt: ago(25 * MINUTE) },
    { id: "edge-vla", name: "Edge VLA", purpose: "", status: "testing", recommended: false, productionRev: null, candidateRev: "r1",
      revisions: [revision(ago(8 * DAY), [POLICY], [])], suiteId: SUITE.id, createdAt: ago(8 * DAY), updatedAt: ago(4 * HOUR) },
  ];

  const robot = (id: string, name: string, configId: string, extra: Partial<Robot>): Robot => ({
    id, name, configId, role: "test", site: "Lab", rev: "r1", kind: "simulator", health: "not-reported", healthReason: null, flags: [], registeredAt: ago(7 * DAY), ...extra,
  });
  const robots: Robot[] = [
    robot("bench-01", "Bench 01", "edge-planner", { kind: "bench", deviceId: CONFIGURED_DEVICE, registeredAt: ago(11 * DAY) }),
    robot("sim-02", "Sim 02", "cloud-planner", { registeredAt: ago(9 * DAY) }),
    robot("sim-01", "Sim 01", "edge-vla", { registeredAt: ago(8 * DAY) }),
  ];

  const suiteRun = (o: { id: string; number: number; configId: string; robotId: string; startedAgo: number; minutes: number; successes: number; slices: number[]; medianS: number }): EvalRun => {
    const passed = o.successes / 60 >= 0.7;
    return {
      id: o.id, number: o.number, kind: "suite", title: `${SUITE.name} ${SUITE.version}`, suiteId: SUITE.id, configId: o.configId, rev: "r1", variant: "r1", robotId: o.robotId,
      status: passed ? "passed-gate" : "below-gate", progress: null, startedAt: ago(o.startedAgo), finishedAt: ago(o.startedAgo - o.minutes * MINUTE), simEngine: SUITE.simEngine,
      counts: { episodes: 60, successes: o.successes }, successCi95: wilson(o.successes, 60), safety: null,
      episodeTime: { medianS: o.medianS, p90S: Math.round(o.medianS * 16) / 10, clock: "simulated" },
      perTask: [], slices: slices(o.slices), failureModes: [],
      gate: { passed, results: [{ criterionId: "overall", passed, actual: `${(o.successes / 60 * 100).toFixed(1)} %` }] }, provenance: SAMPLE,
    };
  };
  const runs: EvalRun[] = [
    suiteRun({ id: "run-1", number: 1, configId: "edge-vla", robotId: "sim-01", startedAgo: 3 * DAY, minutes: 52, successes: 38, slices: [22, 16, 23, 15, 24, 14], medianS: 10.4 }),
    suiteRun({ id: "run-2", number: 2, configId: "edge-vla", robotId: "sim-01", startedAgo: DAY + 3 * HOUR, minutes: 49, successes: 44, slices: [25, 19, 26, 18, 27, 17], medianS: 9.9 }),
    suiteRun({ id: "run-3", number: 3, configId: "edge-vla", robotId: "sim-01", startedAgo: 5 * HOUR, minutes: 47, successes: 47, slices: [26, 21, 27, 20, 28, 19], medianS: 9.6 }),
    suiteRun({ id: "run-4", number: 4, configId: "cloud-planner", robotId: "sim-02", startedAgo: 20 * HOUR, minutes: 58, successes: 51, slices: [27, 24, 28, 23, 28, 23], medianS: 11.2 }),
    {
      ...suiteRun({ id: "run-5", number: 5, configId: "cloud-planner", robotId: "sim-02", startedAgo: 25 * MINUTE, minutes: 0, successes: 21, slices: [11, 10, 11, 10, 12, 9], medianS: 11.0 }),
      status: "running", progress: { done: 24, total: 60 }, finishedAt: null, counts: { episodes: 24, successes: 21 }, successCi95: wilson(21, 24), gate: null,
      slices: slices([11, 10, 11, 10, 12, 9], 12),
    },
  ];

  /** Eight stored rollouts per run, outcomes drawn at the run's success share. No control-plane episode: no replay. */
  const rollouts: Rollout[] = runs.flatMap(run => {
    const random = seededRandom(run.number * 97);
    const share = (run.counts.successes ?? 0) / Math.max(1, run.counts.episodes);
    const count = run.status === "running" ? 4 : 8;
    return Array.from({ length: count }, (_, i): Rollout => {
      const roll = random();
      const outcome: RolloutOutcome = roll < share ? "succeeded" : roll < share + (1 - share) * 0.7 ? "failed" : "timeout";
      const durationS = outcome === "timeout" ? 20 : Math.round((6 + random() * 8) * 10) / 10;
      return {
        id: `${run.id}-ep-${String(i + 1).padStart(2, "0")}`, runId: run.id, taskId: TASK_IDS[i % TASK_IDS.length], sliceIds: [SLICE_IDS[(i * 3) % SLICE_IDS.length]],
        seed: (run.number * 7 + i * 3) % 10, outcome, failureMode: outcome === "succeeded" ? null : outcome === "timeout" ? "Timed out" : "Grasp lost",
        violations: [], durationS, steps: Math.round(durationS * 20), rateHz: 20, reward: outcome === "succeeded" ? 1 : 0, events: [], provenance: SAMPLE,
      };
    });
  });

  // A JSON round trip: exactly what a stored document looks like, with no objects shared between parts.
  return JSON.parse(JSON.stringify({
    schemaVersion: WORKSPACE_SCHEMA_VERSION,
    meta: { id: SAMPLE_WORKSPACE_ID, name: "Sample workspace", label: "Sample", description: "Three sample configurations of one tabletop arm.", updatedAt: ago(25 * MINUTE), sample: true },
    configurations, robots, suites: [SUITE], runs, rollouts, traces: [], logs: [], activity: [],
  } satisfies ConvoyWorkspace)) as ConvoyWorkspace;
}
