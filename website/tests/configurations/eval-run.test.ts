import test from "node:test";
import assert from "node:assert/strict";
import {
  clockLabel, clockName, comparisonRun, countNoun, failedEpisodes, failureShares, familyOf, familyResults, fmtDuration, gateRows, gateThreshold, hilRunFor,
  keySteps, largestSliceGap, lastAtOrBefore, otherConfigurationRun, outcomeCounts, paginate, pathAt, pathSegments, pathTotals, pointsDelta, replayClock,
  rolloutNote, rowInterval, rowShare, safetyRows, scrubMarkers, secondsBetween, seriesWithin, signed, sortRollouts, successInterval, valueDelta, windowAround,
} from "../../src/lib/configurations/eval-run";
import { wilson } from "../../src/lib/configurations/format";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { getRun, getRollout, rolloutsFor } from "../../src/lib/configurations/selectors";
import type { EvalRun, EvalSuite, Rollout, RolloutEvent } from "../../src/lib/configurations/types";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const ws = createSampleWorkspace(NOW);
const suite = ws.suites[0];
const run = (id: string) => getRun(ws, id)!;
const rollout = (id: string) => getRollout(ws, id)!;
const near = (actual: number | null, expected: number, digits = 3) => assert.equal(actual === null ? null : Number(actual.toFixed(digits)), Number(expected.toFixed(digits)));

test("success interval: the stored interval, else Wilson from the counts, else none", () => {
  const stored = successInterval(run("run-23"));
  assert.deepEqual(stored, { ci: run("run-23").successCi95, computed: false });
  const computed = successInterval({ counts: { episodes: 3, successes: 0 }, successCi95: null });
  assert.equal(computed.computed, true);
  assert.deepEqual(computed.ci, wilson(0, 3));
  near(computed.ci![1], 0.56, 2);
  assert.deepEqual(successInterval({ counts: { episodes: 0, successes: null }, successCi95: null }), { ci: null, computed: false });
  assert.deepEqual(successInterval({ counts: { episodes: 12, successes: null } }), { ci: null, computed: false }, "not scored");
  assert.deepEqual(rowInterval({ successes: 21, episodes: 30, ci95: [0.5, 0.8] }), [0.5, 0.8]);
  assert.deepEqual(rowInterval({ successes: 21, episodes: 30 }), wilson(21, 30));
  assert.equal(rowInterval({ successes: 0, episodes: 0 }), null);
  assert.equal(rowShare({ successes: 0, episodes: 0 }), null);
});

test("deltas are in points or plain units, missing values stay missing, signs are typographic", () => {
  assert.equal(pointsDelta(281 / 360, 271 / 360), 2.8);
  assert.equal(pointsDelta(0.5, null), null);
  assert.equal(valueDelta(0.8, 1.1), -0.3);
  assert.equal(valueDelta(null, 1.1), null);
  assert.equal(signed(2.8), "+2.8");
  assert.equal(signed(-0.9), "−0.9");
  assert.equal(signed(0), "0.0");
});

test("durations and clocks read as labelled text", () => {
  assert.equal(fmtDuration(41.21), "41.2 s");
  assert.equal(fmtDuration(196.8), "3 min 17 s");
  assert.equal(fmtDuration(120), "2 min");
  assert.equal(fmtDuration(23400), "6 h 30 min");
  assert.equal(fmtDuration(3600), "1 h");
  assert.equal(fmtDuration(null), null);
  assert.equal(fmtDuration(-1), null);
  assert.equal(secondsBetween("2026-10-01T00:00:00Z", "2026-10-01T00:01:30Z"), 90);
  assert.equal(secondsBetween("2026-10-01T00:00:00Z", Date.parse("2026-10-01T00:00:10Z")), 10);
  assert.equal(secondsBetween(null, "2026-10-01T00:00:00Z"), null);
  assert.equal(secondsBetween("2026-10-01T00:01:00Z", "2026-10-01T00:00:00Z"), null, "never negative");
  assert.equal(clockLabel("simulated"), "simulated");
  assert.equal(clockName("wall"), "wall clock");
  assert.deepEqual(countNoun(run("run-19")), { one: "request", many: "requests" });
  assert.deepEqual(countNoun(run("run-23")), { one: "episode", many: "episodes" });
});

test("gate thresholds come from the suite's stated percentages", () => {
  assert.equal(gateThreshold(suite, "overall"), 0.7);
  assert.equal(gateThreshold(suite, "slice"), 0.55);
  assert.equal(gateThreshold(null, "overall"), null);
  const noPercent: EvalSuite = { ...suite, gate: [{ id: "critical", label: "Critical safety events", target: "None" }, { id: "families", label: "Every slice family", target: "Better than production" }] };
  assert.equal(gateThreshold(noPercent, "overall"), null);
  assert.equal(gateThreshold(noPercent, "slice"), null);
  const renamed: EvalSuite = { ...suite, gate: [{ id: "nominal", label: "Nominal success", target: "≥ 80 %" }, { id: "g1", label: "Success across all slices", target: "≥ 72.5 %" }] };
  assert.equal(gateThreshold(renamed, "overall"), 0.725, "a nominal-slice criterion is not the overall gate");
});

test("gate rows: decided, below gate, pending, and results the suite does not define", () => {
  const passed = gateRows(suite, run("run-23"));
  assert.equal(passed.length, 6);
  assert.ok(passed.every(row => row.state === "pass"));
  assert.deepEqual(passed[0], { id: "overall", label: "Overall success", target: "≥ 70 %", state: "pass", actual: "78.1 %" });
  const below = gateRows(suite, run("run-21"));
  assert.deepEqual(below.filter(row => row.state === "fail").map(row => [row.id, row.actual]), [["slice-families", "Network 48.3 %"]]);
  const pending = gateRows(suite, run("run-24"));
  assert.ok(pending.every(row => row.state === "pending" && row.actual === null));
  const extra = gateRows(suite, { gate: { passed: false, results: [{ criterionId: "custom", passed: false, actual: "2 of 3" }] } });
  assert.deepEqual(extra.at(-1), { id: "custom", label: "custom", target: null, state: "fail", actual: "2 of 3" });
  assert.equal(extra.filter(row => row.state === "pending").length, 6);
  assert.deepEqual(gateRows(null, run("run-19")), []);
});

test("slice families sum their slices; the largest gap names the slice that differs most", () => {
  const families = familyResults(suite, run("run-23").slices);
  assert.deepEqual(families.map(family => family.id), ["lighting", "clutter", "objects", "placement", "sensors", "network"]);
  const network = families.find(family => family.id === "network")!;
  assert.deepEqual([network.successes, network.episodes], [48, 60]);
  near(network.share, 0.8);
  assert.deepEqual(network.ci, wilson(48, 60));
  assert.equal(network.safetyPer100, 1.7, "episode-weighted: (0 × 30 + 3.3 × 30) / 60");
  const cloud = familyResults(suite, run("run-21").slices).find(family => family.id === "network")!;
  near(cloud.share, 29 / 60);
  assert.deepEqual(familyOf(suite, "link-drop"), { id: "network", name: "Network" });
  assert.equal(familyOf(suite, "unknown"), null);
  assert.deepEqual(familyResults(suite, []), []);
  const gap = largestSliceGap(run("run-23").slices, run("run-21").slices)!;
  assert.equal(gap.sliceId, "link-drop");
  assert.equal(gap.points, 53.3);
  assert.equal(largestSliceGap(run("run-23").slices, []), null);
});

test("safety rows keep the suite's order with zeros; failures are shares of the failed episodes", () => {
  const rows = safetyRows(suite, run("run-23").safety);
  assert.deepEqual(rows.map(row => [row.id, row.count]), [["S1", 0], ["S2", 1], ["S3", 0], ["S4", 2], ["S5", 0], ["S6", 0]]);
  assert.equal(rows[4].severity, "critical");
  const unknown = safetyRows(suite, { per100: 1, episodesWithViolations: 1, critical: 0, major: 1, minor: 0, byCheck: [{ checkId: "S9", count: 1 }] });
  assert.deepEqual(unknown.at(-1), { id: "S9", name: "S9", severity: null, count: 1 });
  assert.equal(safetyRows(null, null).length, 0);
  assert.equal(failedEpisodes(run("run-23")), 79);
  assert.equal(failedEpisodes(run("run-25")), null, "a queued run is not scored yet");
  const shares = failureShares(run("run-23").failureModes, 79);
  assert.deepEqual(shares[0], { label: "Grasp lost", count: 27, pct: 34 });
  assert.equal(shares.reduce((sum, item) => sum + item.count, 0), 79);
  assert.equal(failureShares([{ label: "Timed out", count: 2 }], null)[0].pct, null);
});

test("comparison runs: the baseline, else the production revision's newest gated run", () => {
  assert.equal(comparisonRun(ws, run("run-23"))?.id, "run-22");
  assert.equal(comparisonRun(ws, run("run-24"))?.id, "run-22");
  assert.equal(comparisonRun(ws, run("run-22")), null, "the production revision has nothing to compare with");
  assert.equal(comparisonRun(ws, run("run-21")), null, "a configuration without a production revision");
  assert.equal(comparisonRun(ws, run("run-19")), null, "not a suite run");
  const withoutBaseline: EvalRun = { ...run("run-23"), id: "run-30", number: 30, baselineRunId: null };
  assert.equal(comparisonRun({ ...ws, runs: [...ws.runs, withoutBaseline] }, withoutBaseline)?.id, "run-22");
  assert.equal(otherConfigurationRun(ws, run("run-23"))?.id, "run-21");
  assert.equal(otherConfigurationRun(ws, run("run-21"))?.id, "run-23");
  assert.equal(otherConfigurationRun(ws, run("run-19")), null);
  assert.equal(hilRunFor(ws, "lab-bench")?.id, "run-19");
  assert.equal(hilRunFor(ws, "unit-02"), null);
});

test("rollouts: sort, count by outcome, page, and a one-line note", () => {
  const all = rolloutsFor(ws, "run-23");
  assert.equal(all.length, 12);
  const byEvents = sortRollouts(all, "events");
  assert.ok(byEvents[0].events.length + byEvents[0].violations.length >= byEvents[1].events.length + byEvents[1].violations.length);
  assert.deepEqual(sortRollouts(all, "episode").map(item => item.id).slice(0, 3), ["ep-23-007", "ep-23-012", "ep-23-031"]);
  assert.deepEqual(sortRollouts(all, "seed").map(item => item.seed), all.map(item => item.seed).toSorted((a, b) => a - b));
  assert.equal(sortRollouts(all, "duration")[0].durationS, 60);
  assert.deepEqual(outcomeCounts(all), { all: 12, failed: 6, safety: 3 });
  const first = paginate(all, 0, 10), second = paginate(all, 1, 10);
  assert.deepEqual([first.items.length, first.from, first.to, first.pages, first.total], [10, 1, 10, 2, 12]);
  assert.deepEqual([second.items.length, second.from, second.to, second.page], [2, 11, 12, 1]);
  assert.equal(paginate(all, 9, 10).page, 1, "clamped to the last page");
  assert.equal(paginate(all, -3, 10).page, 0);
  assert.deepEqual(paginate([], 0, 10), { items: [], page: 0, pages: 1, from: 0, to: 0, total: 0 });
  assert.equal(rolloutNote(rollout("ep-23-012")), "Insertion off-axis");
  assert.equal(rolloutNote(rollout("ep-23-007")), "Link lost, edge takes over");
  assert.equal(rolloutNote(rollout("ep-23-044")), null);
});

test("replay clock, markers and the event under the playhead", () => {
  const linkDrop = rollout("ep-23-007");
  const clock = replayClock(linkDrop);
  assert.deepEqual([clock.steps, clock.rate], [960, 25]);
  assert.equal(clock.stepAt(9), 225);
  assert.equal(clock.atStep(233), 9.32);
  assert.equal(clock.stepAt(99), 960, "clamped to the last step");
  assert.equal(replayClock({ steps: 0, durationS: 1, rateHz: null }).steps, 1);
  assert.equal(lastAtOrBefore(linkDrop.events, 8.9), -1);
  assert.equal(lastAtOrBefore(linkDrop.events, 9), 0);
  assert.equal(lastAtOrBefore(linkDrop.events, 20), 1);
  const markers = scrubMarkers(linkDrop.events, linkDrop.durationS);
  assert.deepEqual(markers.map(marker => [marker.label, marker.tone, marker.edge, marker.labelled]), [
    ["Link lost, edge takes over", "warning", null, true], ["Cloud link restored", "info", null, true], ["Task complete", "good", "end", true],
  ]);
  const stop = rollout("ep-23-095");
  const merged = scrubMarkers(stop.events, stop.durationS);
  assert.equal(merged.length, 2, "events 0.2 s apart share one marker");
  assert.equal(merged[1].label, "Joint at its soft limit +1");
  assert.equal(merged[1].events.length, 2);
  assert.equal(merged[1].edge, "end");
  const events: RolloutEvent[] = [{ atS: 0.5, label: "Start", tone: "info" }, { atS: 3, label: "Grasp", tone: "info" }, { atS: 4.2, label: "Slip", tone: "warning" }, { atS: 10, label: "Done", tone: "good" }];
  const crowded = scrubMarkers(events, 10);
  assert.deepEqual(crowded.map(marker => [marker.edge, marker.labelled]), [["start", true], [null, true], [null, false], ["end", true]], "a label that would overlap its neighbour is left off; its tick stays");
  assert.deepEqual(scrubMarkers([], 10), []);
});

test("path segments: policy time per path from the key steps; planner decisions are not policy time", () => {
  const linkDrop = rollout("ep-23-007");
  const segments = pathSegments(linkDrop.stepDetail, linkDrop.durationS);
  assert.deepEqual(segments.map(segment => [segment.path, segment.from, segment.to]), [["cloud", 0.2, 9.3], ["fallback", 9.3, 17.2], ["cloud", 17.2, 38.4]]);
  assert.deepEqual(pathTotals(segments), [{ path: "cloud", seconds: 30.3 }, { path: "fallback", seconds: 7.9 }]);
  assert.equal(pathAt(segments, 0.1), null, "planning, before the first chunk");
  assert.equal(pathAt(segments, 9.3), "fallback");
  assert.equal(pathAt(segments, 30), "cloud");
  assert.equal(pathAt(segments, 38.4), null, "the episode has ended");
  assert.deepEqual(pathSegments(undefined, 10), []);
  const edgeOnly = pathSegments([{ step: 0, atS: 0, planner: null, action: "Policy chunk", latencyMs: 400, path: "edge" }], 5);
  assert.deepEqual(edgeOnly, [{ path: "edge", from: 0, to: 5 }], "an edge policy without a planner decision is policy time");
});

test("key steps, the window around the playhead and synced series", () => {
  const linkDrop = rollout("ep-23-007");
  const keys = keySteps(linkDrop);
  assert.equal(keys[0], 0);
  assert.equal(keys.at(-1), 960);
  for (const step of [225, 233, 430]) assert.ok(keys.includes(step), String(step));
  assert.deepEqual(keys, keys.toSorted((a, b) => a - b));
  const rows = Array.from({ length: 10 }, (_, i) => i);
  assert.deepEqual(windowAround(rows, 0), { start: 0, rows: [0, 1, 2, 3, 4] });
  assert.deepEqual(windowAround(rows, 5), { start: 3, rows: [3, 4, 5, 6, 7] });
  assert.deepEqual(windowAround(rows, 9), { start: 5, rows: [5, 6, 7, 8, 9] });
  assert.deepEqual(windowAround([1, 2], 1), { start: 0, rows: [1, 2] });
  const speed = seriesWithin(linkDrop.signals?.eeSpeed, linkDrop.durationS)!;
  assert.equal(speed.values.length, 39, "values at 0 … 38 s of a 38.4 s episode");
  assert.equal(speed.spanS, 38);
  assert.equal(seriesWithin(undefined, 10), null);
  assert.equal(seriesWithin({ stepS: 1, values: [1] }, 10), null, "one point is not a line");
  const plain: Rollout = { ...linkDrop, signals: undefined };
  assert.equal(seriesWithin(plain.signals?.gripperAperture, plain.durationS), null);
});
