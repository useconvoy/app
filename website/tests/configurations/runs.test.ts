import test from "node:test";
import assert from "node:assert/strict";
import {
  belongsTo, evaluationResult, evaluationRollouts, evaluationView, latestScored, medianSteps, missionResult, missionRollouts, platformPaths, robotRuns, runShare, seedSlices, sliceViews,
  standaloneMissions, storedRunRollouts, storedRunView,
} from "../../src/lib/configurations/runs";
import type { PlatformEvaluation, PlatformMission, PlatformProject } from "../../src/lib/configurations/runs";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import type { ConvoyWorkspace, Robot } from "../../src/lib/configurations/types";
import type { Episode } from "../../src/lib/platform/client";

// Contract fixtures with the control plane's shapes; ids are made up.
const NOW = Date.parse("2026-10-01T09:41:20Z");
const ws = createSampleWorkspace(NOW);

function evaluation(id: string, created: string, cases: Array<{ seed: number; episode: string; passed: boolean; valid?: boolean }>, options: { robot?: string; state?: string; report?: boolean } = {}): PlatformEvaluation {
  const reported = options.report ?? true;
  return {
    id, project_id: "prj_contract", suite_id: "esu_contract", release_id: "apr_contract", robot_id: options.robot ?? "rob_contract", state: options.state ?? "completed",
    detail: "all allocated cases have terminal outcomes", created_at: created, updated_at: created,
    cases: cases.map(item => ({ seed: item.seed, mission_id: `mis_${item.episode}`, episode_id: item.episode })),
    report: reported ? {
      passed: cases.filter(item => item.passed).length >= 1, successes: cases.filter(item => item.passed).length, min_successes: 1, case_count: cases.length, median_wall_s: 41.2,
      suite_digest: "suite", release_digest: "release",
      cases: cases.map(item => ({ seed: item.seed, mission_id: `mis_${item.episode}`, episode_id: item.episode, state: "completed", passed: item.passed, evidence_valid: item.valid ?? true, steps: 54, wall_duration_s: 41.2 })),
    } : null,
  };
}
const mission = (id: string, episode: string | null, created: string, state = "completed", robot = "rob_contract"): PlatformMission => ({
  id, project_id: "prj_contract", robot_id: robot, deployment_id: "dep_contract", release_id: "apr_contract", state, detail: "", episode_id: episode, seed: 0, expires_at: 0, created_at: created, updated_at: created,
});
const episode = (id: string, success: boolean | null): Episode => ({ id, mission_id: "mis", state: "completed", detail: "", release_digest: "release", summary: success === null ? {} : { final_success: success, steps: 54 } });

const project: PlatformProject = {
  evaluations: [
    evaluation("eva_second", "2026-10-01T03:59:38Z", [{ seed: 0, episode: "epi_b", passed: true }]),
    evaluation("eva_first", "2026-10-01T03:58:49Z", [{ seed: 0, episode: "epi_a", passed: true }]),
    evaluation("eva_other_robot", "2026-10-01T04:30:00Z", [{ seed: 0, episode: "epi_x", passed: false }], { robot: "rob_other" }),
  ],
  missions: [
    mission("mis_epi_a", "epi_a", "2026-10-01T03:58:50Z"),
    mission("mis_epi_b", "epi_b", "2026-10-01T03:59:57Z"),
    mission("mis_alone_1", "epi_c", "2026-10-01T03:56:50Z"),
    mission("mis_alone_2", "epi_d", "2026-10-01T04:02:48Z"),
    mission("mis_running", null, "2026-10-01T04:10:00Z", "running"),
  ],
  episodes: { epi_c: episode("epi_c", true), epi_d: episode("epi_d", false) },
};
const linked = (extra: Partial<Robot> = {}): Robot => ({ ...ws.robots.find(robot => robot.id === "sim-01")!, projectId: "prj_contract", ...extra });

test("an evaluation's result comes from its report, else its state; a mission's from its episode", () => {
  assert.equal(evaluationResult(project.evaluations[0]), "passed");
  assert.equal(evaluationResult(project.evaluations[2]), "failed");
  assert.equal(evaluationResult({ state: "queued", report: null }), "queued");
  assert.equal(evaluationResult({ state: "running", report: null }), "running");
  assert.equal(evaluationResult({ state: "cancelled", report: null }), "cancelled");
  assert.equal(evaluationResult({ state: "unknown", report: null }), "unknown");
  assert.equal(missionResult({ state: "completed" }, episode("e", true)), "passed");
  assert.equal(missionResult({ state: "completed" }, episode("e", false)), "failed");
  assert.equal(missionResult({ state: "completed" }, null), "completed", "without the episode the outcome is not claimed");
  assert.equal(missionResult({ state: "running" }, null), "running");
});

test("missions outside every evaluation are the project's standalone episodes", () => {
  assert.deepEqual(standaloneMissions(project).map(item => item.id), ["mis_alone_1", "mis_alone_2", "mis_running"]);
});

test("a robot's evals: stored runs, then the project's evaluations and standalone missions, numbered by creation", () => {
  const robot = linked();
  const views = robotRuns(ws, robot, project);
  // Stored runs 1–3 keep their numbers; platform evals follow in creation order: alone_1, eva_first, eva_second, alone_2, running, eva_other_robot.
  // Shown running first, then newest first.
  assert.deepEqual(views.map(view => [view.id, view.label, view.result]), [
    ["mis_running", "Eval 8", "running"],
    ["run-3", "Eval 3", "passed"],
    ["eva_other_robot", "Eval 9", "failed"],
    ["mis_alone_2", "Eval 7", "failed"],
    ["eva_second", "Eval 6", "passed"],
    ["eva_first", "Eval 5", "passed"],
    ["mis_alone_1", "Eval 4", "passed"],
    ["run-2", "Eval 2", "passed"],
    ["run-1", "Eval 1", "below-gate"],
  ]);
  const only = robotRuns(ws, linked({ platformRobotId: "rob_other" }), project);
  assert.deepEqual(only.filter(view => view.source !== "document").map(view => view.id), ["eva_other_robot"], "a platform robot narrows the evals to its own");
  assert.deepEqual(robotRuns(ws, ws.robots.find(item => item.id === "sim-01")!, project).map(view => view.source), ["document", "document", "document"], "no project, no platform evals");
  assert.deepEqual(robotRuns(ws, robot, null).map(view => view.id), ["run-3", "run-2", "run-1"], "while the project loads, the stored runs show");
});

test("eval views carry counts, success and median time", () => {
  const view = evaluationView(project.evaluations[0], 5);
  assert.deepEqual([view.label, view.episodes, view.successes, view.medianS, runShare(view)], ["Eval 5", 1, 1, 41.2, 1]);
  const stored = storedRunView(ws.runs.find(run => run.id === "run-5")!);
  assert.deepEqual([stored.result, stored.progress, stored.episodes, stored.successes], ["running", { done: 24, total: 60 }, 24, 21]);
  assert.equal(latestScored([stored, view])?.id, "eva_second", "a running eval is not the latest result");
  assert.equal(belongsTo(linked(), { project_id: "prj_elsewhere", robot_id: "rob_contract" }), false);
  assert.equal(belongsTo(linked({ platformRobotId: "rob_contract" }), { project_id: "prj_contract", robot_id: "rob_contract" }), true);
});

test("rollouts: evaluation cases replay their episodes; stored rollouts without an episode do not", () => {
  const cases = evaluationRollouts(evaluation("eva_three", "2026-10-01T05:00:00Z", [{ seed: 0, episode: "epi_1", passed: true }, { seed: 1, episode: "epi_2", passed: false }, { seed: 1, episode: "epi_3", passed: false, valid: false }]));
  assert.deepEqual(cases.map(row => [row.id, row.episodeId, row.seed, row.result]), [["epi_1", "epi_1", 0, "passed"], ["epi_2", "epi_2", 1, "failed"], ["epi_3", "epi_3", 1, "invalid"]]);
  assert.equal(medianSteps(cases), 54);
  assert.deepEqual(seedSlices(cases).map(slice => [slice.name, slice.successes, slice.episodes]), [["Seed 0", 1, 1], ["Seed 1", 0, 2]]);
  assert.deepEqual(seedSlices(cases.slice(0, 1)), [], "one case is not a slice breakdown");
  const running = evaluationRollouts(evaluation("eva_run", "2026-10-01T05:00:00Z", [{ seed: 4, episode: "epi_9", passed: false }], { state: "running", report: false }));
  assert.deepEqual(running.map(row => row.result), ["running"]);
  assert.deepEqual(missionRollouts(project.missions[2], project.episodes.epi_c).map(row => [row.episodeId, row.steps, row.result]), [["epi_c", 54, "passed"]]);
  assert.deepEqual(missionRollouts(project.missions[4], null), [], "no episode yet, nothing to replay");
  const sample = storedRunRollouts(ws, ws.runs.find(run => run.id === "run-3")!, ws.suites[0], null);
  assert.equal(sample.length, 8);
  assert.ok(sample.every(row => row.episodeId === null && row.task !== null), "sample rollouts never offer a replay");
});

test("a stored run linked to a recorded evaluation and episode lists each real episode once", () => {
  const document: ConvoyWorkspace = structuredClone(ws);
  const run = document.runs.find(item => item.id === "run-3")!;
  run.recordedEvaluationId = "eva_three";
  run.recordedEpisodeId = "epi_4";
  document.rollouts.find(item => item.id === "run-3-ep-01")!.episodeId = "epi_1";
  const rows = storedRunRollouts(document, run, document.suites[0], evaluation("eva_three", "2026-10-01T05:00:00Z", [{ seed: 0, episode: "epi_1", passed: true }, { seed: 1, episode: "epi_2", passed: false }]));
  assert.deepEqual(rows.filter(row => row.episodeId).map(row => row.episodeId), ["epi_1", "epi_2", "epi_4"]);
  assert.equal(rows.length, 10);
});

test("slices keep the suite's order and names; proxy paths encode ids", () => {
  const run = ws.runs.find(item => item.id === "run-3")!;
  assert.deepEqual(sliceViews(ws.suites[0], run.slices).map(slice => [slice.family, slice.name, slice.successes]), [
    ["Lighting", "Normal light", 26], ["Lighting", "Low light", 21], ["Clutter", "Clear table", 27], ["Clutter", "Cluttered table", 20], ["Objects", "Seen objects", 28], ["Objects", "Unseen objects", 19],
  ]);
  assert.equal(platformPaths.evaluations("prj_contract01"), "evaluations?project_id=prj_contract01");
  assert.equal(platformPaths.frame("epi_a b", 7), "episodes/epi_a%20b/replay/frames/7");
});
