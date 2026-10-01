import test from "node:test";
import assert from "node:assert/strict";
import { createSampleWorkspace, seededRandom } from "../../src/lib/configurations/sample";
import { CONFIGURED_DEVICE } from "../../src/lib/configurations/types";
import { validateWorkspace } from "../../src/lib/configurations/validate";

const NOW = Date.parse("2026-10-01T09:41:20Z");

test("the sample workspace is valid and deterministic for a given time", () => {
  const ws = createSampleWorkspace(NOW);
  const result = validateWorkspace(ws);
  assert.equal(result.ok, true, result.ok ? "" : JSON.stringify(result.issues));
  assert.deepEqual(result.ok ? result.warnings : [], [], "no unknown keys");
  assert.deepEqual(createSampleWorkspace(NOW), ws);
  assert.notDeepEqual(createSampleWorkspace(NOW + 3_600_000).runs[0].startedAt, ws.runs[0].startedAt, "times are relative to now");
});

test("three configurations, each with one test robot: a live device and two simulators", () => {
  const ws = createSampleWorkspace(NOW);
  assert.deepEqual(ws.configurations.map(config => [config.name, config.status]), [["Edge planner", "testing"], ["Cloud planner", "testing"], ["Edge VLA", "testing"]]);
  assert.deepEqual(ws.robots.map(robot => [robot.name, robot.configId, robot.role]), [["Bench 01", "edge-planner", "test"], ["Sim 02", "cloud-planner", "test"], ["Sim 01", "edge-vla", "test"]]);
  assert.ok(ws.robots.every(robot => robot.role === "test"), "no production robots");
  assert.deepEqual(ws.robots.filter(robot => robot.deviceId).map(robot => [robot.id, robot.deviceId]), [["bench-01", CONFIGURED_DEVICE]]);
  assert.ok(ws.robots.every(robot => !robot.telemetry && !robot.latency), "no stored telemetry: the live device measures");
  assert.deepEqual(ws.configurations.map(config => config.revisions[0].routing.mode), ["edge-only", "cloud-only", "edge-only"]);
  assert.deepEqual(ws.runs.map(run => [run.number, run.robotId, run.status]), [[1, "sim-01", "below-gate"], [2, "sim-01", "passed-gate"], [3, "sim-01", "passed-gate"], [4, "sim-02", "passed-gate"], [5, "sim-02", "running"]]);
  for (const run of ws.runs) {
    for (const family of ws.suites[0].sliceFamilies) {
      const total = run.slices.filter(slice => family.slices.some(item => item.id === slice.sliceId)).reduce((sum, slice) => sum + slice.successes, 0);
      assert.equal(total, run.counts.successes, `${run.id}: each slice family covers every success once`);
    }
  }
});

test("no sample rollout or run names a control-plane episode: the sample offers no replay", () => {
  const ws = createSampleWorkspace(NOW);
  assert.ok(ws.rollouts.length > 0);
  assert.ok(ws.rollouts.every(rollout => !rollout.episodeId));
  assert.ok(ws.runs.every(run => !run.recordedEpisodeId && !run.recordedEvaluationId));
});

test("the sample is generic: marked sample, no account ids, no hex, no stored measurements", () => {
  const ws = createSampleWorkspace(NOW);
  const text = JSON.stringify(ws);
  assert.equal(ws.meta.sample, true);
  assert.doesNotMatch(text, /\b(prj|eva|epi|mis|rob|dev|usr|apr|esu)_[a-z0-9]{6,}/, "no control-plane ids");
  assert.doesNotMatch(text, /ROBO-T|customer/i, "no customer names");
  assert.doesNotMatch(text, /#[0-9a-fA-F]{3,8}\b/, "no hex-like values");
  const kinds = new Set(Array.from(text.matchAll(/"kind":"(measured|recorded|sample|not-reported)"/g), match => match[1]));
  assert.ok(!kinds.has("measured"), "no stored measured values");
});

test("the generator is seeded: same seed, same sequence", () => {
  const a = seededRandom(7), b = seededRandom(7);
  assert.deepEqual([a(), a(), a()], [b(), b(), b()]);
});
