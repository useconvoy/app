/**
 * Evals and rollouts for the pages, from two sources:
 *
 * - the workspace document: stored runs and rollouts (sample or recorded);
 * - the control plane, read through the existing proxy (`/api/platform/…`):
 *   a robot with `projectId` lists that project's evaluations, and the missions it
 *   ran outside an evaluation, as its evals (only `platformRobotId`'s when set);
 *   a stored run may name `recordedEvaluationId` (its metrics and rollouts come
 *   from that evaluation) or `recordedEpisodeId`; a stored rollout may name
 *   `episodeId`.
 *
 * Only a control-plane episode is replayable: a rollout without one has no replay.
 * Pure functions; the React hooks that fetch live in components/configurations/platform.tsx.
 */
import { median } from "./format";
import type { Episode, EvaluationRun, Mission } from "../platform/client";
import type { ConvoyWorkspace, EvalRun, EvalSuite, Provenance, Robot, Rollout, RolloutOutcome, SliceResult } from "./types";

/* ---------- platform records (the API's own field names) ---------- */

export interface PlatformEvaluation extends EvaluationRun { created_at?: string | null }
export interface PlatformMission extends Mission { project_id?: string; created_at?: string | null }
/** One project's records as the robot pages need them. `episodes` holds the episodes of missions run outside an evaluation. */
export interface PlatformProject {
  evaluations: readonly PlatformEvaluation[];
  missions: readonly PlatformMission[];
  episodes: Readonly<Record<string, Episode | undefined>>;
}

/** Proxy paths (relative to `/api/platform/`). Ids come from the document, so they are encoded. */
export const platformPaths = {
  evaluations: (projectId: string) => `evaluations?project_id=${encodeURIComponent(projectId)}`,
  missions: (projectId: string) => `missions?project_id=${encodeURIComponent(projectId)}`,
  evaluation: (id: string) => `evaluations/${encodeURIComponent(id)}`,
  mission: (id: string) => `missions/${encodeURIComponent(id)}`,
  episode: (id: string) => `episodes/${encodeURIComponent(id)}`,
  replay: (id: string) => `episodes/${encodeURIComponent(id)}/replay`,
  frame: (id: string, index: number) => `episodes/${encodeURIComponent(id)}/replay/frames/${index}`,
  projects: () => "projects",
  devices: () => "devices",
  robots: (projectId: string) => `robots?project_id=${encodeURIComponent(projectId)}`,
} as const;

/* ---------- results ---------- */

export type RunResult = "passed" | "failed" | "below-gate" | "running" | "queued" | "completed" | "cancelled" | "unknown";
export const RESULT_LABEL: Record<RunResult, string> = {
  passed: "Passed", failed: "Failed", "below-gate": "Below gate", running: "Running", queued: "Queued", completed: "Completed", cancelled: "Cancelled", unknown: "Unknown",
};
export type RolloutResult = "passed" | "failed" | "timeout" | "safety-stop" | "invalid" | "running" | "cancelled" | "unknown";
export const ROLLOUT_LABEL: Record<RolloutResult, string> = {
  passed: "Passed", failed: "Failed", timeout: "Timeout", "safety-stop": "Safety stop", invalid: "Invalid", running: "Running", cancelled: "Cancelled", unknown: "Unknown",
};

const STORED_RESULT: Record<EvalRun["status"], RunResult> = {
  queued: "queued", running: "running", "passed-gate": "passed", "below-gate": "below-gate", completed: "completed", "did-not-qualify": "failed", cancelled: "cancelled", failed: "failed",
};
const ACTIVE = new Set(["queued", "starting", "requested", "claimed", "running", "cancel_requested"]);

/** An evaluation's result: its report decides; otherwise its state. */
export function evaluationResult(evaluation: Pick<EvaluationRun, "state" | "report">): RunResult {
  if (evaluation.report) return evaluation.report.passed ? "passed" : "failed";
  if (evaluation.state === "queued" || evaluation.state === "starting") return "queued";
  if (ACTIVE.has(evaluation.state)) return "running";
  if (evaluation.state === "completed") return "completed";
  if (evaluation.state === "cancelled") return "cancelled";
  if (evaluation.state === "failed") return "failed";
  return "unknown";
}

/** A mission's result: a completed mission passed when its episode reports final success. */
export function missionResult(mission: Pick<Mission, "state">, episode: Pick<Episode, "summary"> | null | undefined): RunResult {
  if (mission.state === "completed") {
    const success = episode?.summary?.final_success;
    return success === true ? "passed" : success === false ? "failed" : "completed";
  }
  if (mission.state === "requested" || mission.state === "queued") return "queued";
  if (ACTIVE.has(mission.state)) return "running";
  if (mission.state === "cancelled") return "cancelled";
  if (mission.state === "failed") return "failed";
  return "unknown";
}

/* ---------- evals ---------- */

export type RunSource = "document" | "evaluation" | "mission";
export interface RunView {
  /** Route id: a stored run id, an evaluation id (`eva_…`) or a mission id (`mis_…`). */
  id: string;
  source: RunSource;
  number: number;
  /** "Eval 3". */
  label: string;
  /** Started (stored) or created (platform). */
  at: string | null;
  /** Episodes (scored so far for a running run); null when unknown. */
  episodes: number | null;
  successes: number | null;
  /** Median episode time in seconds (wall clock for platform runs). */
  medianS: number | null;
  result: RunResult;
  progress: { done: number; total: number } | null;
  provenance: Provenance;
  run: EvalRun | null;
  evaluation: PlatformEvaluation | null;
  mission: PlatformMission | null;
}

export const evalLabel = (number: number) => `Eval ${number}`;
const time = (at: string | null | undefined) => (at ? Date.parse(at) : Number.NaN) || 0;
const recordedAt = (at: string | null | undefined, source: string): Provenance => at ? { kind: "recorded", at, source } : { kind: "recorded", source };

export function storedRunView(run: EvalRun): RunView {
  return {
    id: run.id, source: "document", number: run.number, label: evalLabel(run.number), at: run.startedAt ?? run.finishedAt,
    episodes: run.status === "queued" ? null : run.counts.episodes, successes: run.counts.successes, medianS: run.episodeTime?.medianS ?? null,
    result: STORED_RESULT[run.status], progress: run.status === "running" || run.status === "queued" ? run.progress ?? null : null,
    provenance: run.provenance, run, evaluation: null, mission: null,
  };
}

export function evaluationView(evaluation: PlatformEvaluation, number: number): RunView {
  const report = evaluation.report;
  const at = evaluation.created_at ?? evaluation.updated_at ?? null;
  return {
    id: evaluation.id, source: "evaluation", number, label: evalLabel(number), at,
    episodes: report ? report.case_count : evaluation.cases.length || null,
    successes: report ? report.successes : null,
    medianS: report?.median_wall_s ?? null, result: evaluationResult(evaluation), progress: null,
    provenance: recordedAt(evaluation.updated_at ?? at, `Evaluation ${evaluation.id}`), run: null, evaluation, mission: null,
  };
}

export function missionView(mission: PlatformMission, episode: Episode | null | undefined, number: number): RunView {
  const result = missionResult(mission, episode);
  return {
    id: mission.id, source: "mission", number, label: evalLabel(number), at: mission.created_at ?? mission.updated_at ?? null,
    episodes: mission.episode_id ? 1 : null, successes: result === "passed" ? 1 : result === "failed" ? 0 : null, medianS: null,
    result, progress: null, provenance: recordedAt(mission.updated_at, `Mission ${mission.id}`), run: null, evaluation: null, mission,
  };
}

/** Whether a platform record belongs to the robot: same project, and the robot's platform robot when it names one. */
export function belongsTo(robot: Pick<Robot, "projectId" | "platformRobotId">, record: { project_id?: string; robot_id: string }): boolean {
  if (!robot.projectId) return false;
  if (record.project_id !== undefined && record.project_id !== robot.projectId) return false;
  return !robot.platformRobotId || record.robot_id === robot.platformRobotId;
}

/** Missions of the project that ran outside every evaluation of the project. */
export function standaloneMissions(project: PlatformProject): PlatformMission[] {
  const inEvaluation = new Set(project.evaluations.flatMap(evaluation => evaluation.cases.flatMap(item => [item.mission_id, item.episode_id])).filter(Boolean));
  return project.missions.filter(mission => !inEvaluation.has(mission.id) && !(mission.episode_id && inEvaluation.has(mission.episode_id)));
}

/**
 * Every eval on a robot, newest first: its stored runs and, with `projectId`, the
 * project's evaluations and standalone missions. Platform evals are numbered in the
 * order they were created, after the robot's highest stored run number.
 */
export function robotRuns(ws: ConvoyWorkspace, robot: Robot, project: PlatformProject | null | undefined): RunView[] {
  const stored = ws.runs.filter(run => run.robotId === robot.id).map(storedRunView);
  const offset = stored.reduce((max, view) => Math.max(max, view.number), 0);
  const platform: Array<{ at: number; make: (number: number) => RunView }> = [];
  if (project && robot.projectId) {
    for (const evaluation of project.evaluations) {
      if (belongsTo(robot, evaluation)) platform.push({ at: time(evaluation.created_at ?? evaluation.updated_at), make: number => evaluationView(evaluation, number) });
    }
    for (const mission of standaloneMissions(project)) {
      if (belongsTo(robot, mission)) platform.push({ at: time(mission.created_at ?? mission.updated_at), make: number => missionView(mission, mission.episode_id ? project.episodes[mission.episode_id] : null, number) });
    }
  }
  const numbered = platform.toSorted((a, b) => a.at - b.at).map((item, i) => item.make(offset + i + 1));
  const order = (view: RunView) => view.result === "running" ? 0 : view.result === "queued" ? 1 : 2;
  return [...stored, ...numbered].toSorted((a, b) => order(a) - order(b) || time(b.at) - time(a.at) || b.number - a.number);
}

/** Success share 0–1, or null when not scored. */
export function runShare(view: Pick<RunView, "episodes" | "successes">): number | null {
  return view.successes === null || !view.episodes ? null : view.successes / view.episodes;
}

/** The newest finished eval with a score, if any. */
export function latestScored(views: readonly RunView[]): RunView | null {
  return views.filter(view => runShare(view) !== null && view.result !== "running" && view.result !== "queued").toSorted((a, b) => time(b.at) - time(a.at))[0] ?? null;
}

/* ---------- rollouts ---------- */

export interface RolloutView {
  /** `?rollout=` value: a stored rollout id or a control-plane episode id. */
  id: string;
  /** Replayable when set. */
  episodeId: string | null;
  seed: number | null;
  /** Task (stored rollouts), else null. */
  task: string | null;
  steps: number | null;
  /** Episode seconds: simulated for stored rollouts, wall clock for evaluation cases. */
  seconds: number | null;
  result: RolloutResult;
}

const OUTCOME: Record<RolloutOutcome, RolloutResult> = { succeeded: "passed", failed: "failed", timeout: "timeout", "safety-stop": "safety-stop" };

export function storedRollout(rollout: Rollout, suite: EvalSuite | null): RolloutView {
  return {
    id: rollout.id, episodeId: rollout.episodeId ?? null, seed: rollout.seed, task: suite?.tasks.find(task => task.id === rollout.taskId)?.name ?? rollout.taskId,
    steps: rollout.steps, seconds: rollout.durationS, result: OUTCOME[rollout.outcome],
  };
}

/** The cases of an evaluation: reported cases with their outcome, else the allocated cases while it runs. */
export function evaluationRollouts(evaluation: Pick<EvaluationRun, "state" | "report" | "cases">): RolloutView[] {
  const running = ACTIVE.has(evaluation.state);
  if (evaluation.report) {
    return evaluation.report.cases.map((item, i) => ({
      id: item.episode_id ?? item.mission_id ?? `seed-${item.seed}-${i}`, episodeId: item.episode_id ?? null, seed: item.seed, task: null,
      steps: item.steps ?? null, seconds: item.wall_duration_s ?? null,
      result: !item.evidence_valid ? "invalid" : item.passed ? "passed" : item.state === "cancelled" ? "cancelled" : "failed",
    }));
  }
  return evaluation.cases.map((item, i) => ({
    id: item.episode_id ?? item.mission_id ?? `seed-${item.seed}-${i}`, episodeId: item.episode_id ?? null, seed: item.seed, task: null,
    steps: null, seconds: null, result: running ? "running" : "unknown",
  }));
}

/** A standalone mission's one rollout (its episode). */
export function missionRollouts(mission: PlatformMission, episode: Episode | null | undefined): RolloutView[] {
  if (!mission.episode_id) return [];
  const result = missionResult(mission, episode);
  const summary = episode?.summary ?? {};
  const steps = typeof summary.steps === "number" ? summary.steps : null;
  return [{
    id: mission.episode_id, episodeId: mission.episode_id, seed: mission.seed, task: null, steps, seconds: null,
    result: result === "passed" ? "passed" : result === "failed" ? "failed" : result === "running" || result === "queued" ? "running" : result === "cancelled" ? "cancelled" : "unknown",
  }];
}

/**
 * A stored run's rollouts: its stored rollouts, then the cases of its recorded
 * evaluation and its recorded episode that no stored rollout already names.
 */
export function storedRunRollouts(ws: ConvoyWorkspace, run: EvalRun, suite: EvalSuite | null, evaluation: Pick<EvaluationRun, "state" | "report" | "cases"> | null): RolloutView[] {
  const rows = ws.rollouts.filter(rollout => rollout.runId === run.id).map(rollout => storedRollout(rollout, suite));
  const named = new Set(rows.map(row => row.episodeId).filter(Boolean));
  for (const row of evaluation ? evaluationRollouts(evaluation) : []) if (!row.episodeId || !named.has(row.episodeId)) { rows.push(row); if (row.episodeId) named.add(row.episodeId); }
  if (run.recordedEpisodeId && !named.has(run.recordedEpisodeId)) {
    const scored = run.counts.episodes === 1 && run.counts.successes !== null;
    rows.push({ id: run.recordedEpisodeId, episodeId: run.recordedEpisodeId, seed: null, task: null, steps: null, seconds: run.episodeTime?.medianS ?? null, result: scored ? (run.counts.successes ? "passed" : "failed") : "unknown" });
  }
  return rows;
}

/** Median steps of the rollouts that report them. */
export const medianSteps = (rows: readonly RolloutView[]) => median(rows.map(row => row.steps));

/* ---------- slices ---------- */

export interface SliceView { id: string; name: string; family: string; episodes: number; successes: number; share: number | null }
/** A stored run's slice results with names, in the suite's order. */
export function sliceViews(suite: EvalSuite | null, results: readonly SliceResult[]): SliceView[] {
  const byId = new Map(results.map(result => [result.sliceId, result]));
  const rows: SliceView[] = [];
  for (const family of suite?.sliceFamilies ?? []) {
    for (const slice of family.slices) {
      const result = byId.get(slice.id);
      if (result) rows.push({ id: slice.id, name: slice.name, family: family.name, episodes: result.episodes, successes: result.successes, share: result.episodes ? result.successes / result.episodes : null });
    }
  }
  for (const result of results) {
    if (!rows.some(row => row.id === result.sliceId)) rows.push({ id: result.sliceId, name: result.sliceId, family: "", episodes: result.episodes, successes: result.successes, share: result.episodes ? result.successes / result.episodes : null });
  }
  return rows;
}

/** Slices of a platform evaluation: one per seed when it ran more than one case. */
export function seedSlices(rows: readonly RolloutView[]): SliceView[] {
  const seeds = new Map<number, { episodes: number; successes: number }>();
  for (const row of rows) {
    if (row.seed === null || row.result === "running" || row.result === "unknown") continue;
    const entry = seeds.get(row.seed) ?? { episodes: 0, successes: 0 };
    entry.episodes += 1;
    if (row.result === "passed") entry.successes += 1;
    seeds.set(row.seed, entry);
  }
  if (rows.length < 2) return [];
  return [...seeds.entries()].toSorted((a, b) => a[0] - b[0]).map(([seed, entry]) => ({ id: `seed-${seed}`, name: `Seed ${seed}`, family: "Seed", ...entry, share: entry.episodes ? entry.successes / entry.episodes : null }));
}
