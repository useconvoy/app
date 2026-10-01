import test from "node:test";
import assert from "node:assert/strict";
import { createSampleWorkspace, hourlySeries, recentSeries, seededRandom } from "../../src/lib/configurations/sample";
import { CONFIGURED_DEVICE } from "../../src/lib/configurations/types";
import { validateWorkspace } from "../../src/lib/configurations/validate";

const NOW = Date.parse("2026-10-01T09:41:20Z");

test("the sample workspace is valid and deterministic for a given time", () => {
  const first = createSampleWorkspace(NOW);
  const result = validateWorkspace(first);
  assert.equal(result.ok, true, result.ok ? "" : JSON.stringify(result.issues, null, 2));
  assert.deepEqual(result.warnings, []);
  assert.equal(JSON.stringify(createSampleWorkspace(NOW)), JSON.stringify(first));
  assert.notEqual(JSON.stringify(createSampleWorkspace(NOW + 3600_000)), JSON.stringify(first));
});

test("the sample covers every screen: statuses, roles, a live binding, runs, rollouts, traces and logs", () => {
  const ws = createSampleWorkspace(NOW);
  assert.deepEqual(ws.configurations.map(config => config.status).toSorted(), ["draft", "production", "testing"]);
  assert.ok(ws.configurations.some(config => config.recommended && config.productionRev && config.candidateRev));
  const attached = ws.robots.filter(robot => robot.configId !== null);
  assert.equal(attached.length, 7);
  assert.ok(ws.robots.some(robot => robot.configId === null), "an unattached robot for Add robot");
  const bound = ws.robots.filter(robot => robot.deviceId === CONFIGURED_DEVICE);
  assert.equal(bound.length, 1);
  assert.equal(bound[0].telemetry, undefined, "a bound robot stores no telemetry");
  for (const role of ["test", "production"] as const) assert.ok(attached.some(robot => robot.role === role));
  for (const health of ["healthy", "degraded", "attention", "not-reported"] as const) assert.ok(ws.robots.some(robot => robot.health === health), health);
  for (const status of ["queued", "running", "passed-gate", "below-gate", "completed", "did-not-qualify"] as const) assert.ok(ws.runs.some(run => run.status === status), status);
  assert.ok(ws.runs.some(run => run.provenance.kind === "recorded"));
  assert.ok(ws.rollouts.some(rollout => rollout.events.length >= 3 && rollout.stepDetail && rollout.signals?.eeSpeed));
  assert.equal(ws.traces.length, 12);
  assert.ok(ws.traces.every(trace => trace.spans.length >= 2));
  assert.ok(ws.traces.some(trace => trace.escalated && trace.spanGroups?.length === 2));
  assert.deepEqual([...new Set(ws.traces.map(trace => trace.path))].toSorted(), ["cloud", "edge", "fallback"]);
  assert.ok(ws.logs.length >= 10 && ws.activity.length >= 5);
  const suite = ws.suites[0];
  assert.equal(suite.tasks.length * suite.sliceFamilies.flatMap(family => family.slices).length * suite.seedsPerCell, suite.episodesPerRun);
});

test("stored sample series are finite, sized and anchored before now", () => {
  const ws = createSampleWorkspace(NOW);
  for (const robot of ws.robots) {
    for (const [window, length] of [["day", 25], ["recent", 30]] as const) {
      for (const series of Object.values(robot.telemetry?.[window] ?? {})) {
        assert.equal(series.values.length, length, `${robot.id} ${window}`);
        assert.ok(series.values.every(value => value === null || Number.isFinite(value)));
        assert.ok(Date.parse(series.start) < NOW);
      }
    }
  }
  const latest = ws.robots.find(robot => robot.id === "unit-08")!.telemetry!;
  assert.equal(latest.day!.socTempC!.values.at(-1), 97.6, "the hourly max is never below the latest reading");
});

test("generators are seeded: same input, same output", () => {
  const a = seededRandom(7), b = seededRandom(7);
  assert.deepEqual([a(), a(), a()], [b(), b(), b()]);
  assert.deepEqual(hourlySeries({ base: 50, swing: 2, noise: 1, seed: 3, latest: 52 }), hourlySeries({ base: 50, swing: 2, noise: 1, seed: 3, latest: 52 }));
  const recent = recentSeries({ level: 10, noise: 2, seed: 4, latest: 11.5 });
  assert.equal(recent.length, 30);
  assert.equal(recent.at(-1), 11.5);
});

test("the sample is marked as the sample, labels every value and passes the token rule", () => {
  const ws = createSampleWorkspace(NOW);
  const text = JSON.stringify(ws);
  assert.equal(ws.meta.sample, true);
  assert.doesNotMatch(text, /#[0-9a-fA-F]{3,8}\b/, "no hex-like ids");
  assert.doesNotMatch(text, /real-time|\bfleet\b/i, "house copy rules");
  const kinds = new Set(Array.from(text.matchAll(/"kind":"(measured|recorded|sample|not-reported)"/g), match => match[1]));
  assert.deepEqual([...kinds].toSorted(), ["not-reported", "recorded", "sample"], "no stored measured values");
});
