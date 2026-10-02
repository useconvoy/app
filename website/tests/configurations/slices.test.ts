import test from "node:test";
import assert from "node:assert/strict";
import { linkOfflineEvaluations } from "../../src/lib/configurations/mutations";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { offlineProvenance, provenanceItems } from "../../src/lib/configurations/selectors";
import {
  evalSlices, isSliced, NO_SLICE_KEY, pickSlice, plannerCalls, seedText, sliceLabel, sliceResultsText, sliceSummary,
} from "../../src/lib/configurations/slices";
import type { SliceEpisode } from "../../src/lib/configurations/slices";
import type { ConvoyWorkspace, EvalProvenance } from "../../src/lib/configurations/types";
import { MAX_PROVENANCE_TEXT, validateWorkspace } from "../../src/lib/configurations/validate";
import type { OfflineEpisode } from "../../src/lib/platform/client";
import { PLANNER_EPISODES } from "./evidence-fixture";
import type { FixtureEpisode } from "./evidence-fixture";
import { SECOND_RUN_EPISODES } from "./slices-fixture";

const NOW = Date.parse("2026-10-01T09:41:20Z");
type Episode = SliceEpisode & { id: string };
const episodes = (rows: readonly FixtureEpisode[] = SECOND_RUN_EPISODES): Episode[] => rows.map(row => ({
  id: row.id, seed: row.seed, outcome: row.outcome, steps: row.steps, sim_seconds: row.simSeconds, wall_seconds: null, metrics: { ...row.metrics },
}));
const ok =(metrics: Record<string, unknown>, outcome: OfflineEpisode["outcome"] = "success"): SliceEpisode => ({ outcome, metrics: metrics as OfflineEpisode["metrics"] });

/* ---------- slices ---------- */

test("an eval's episodes by slice: in the order the slices first appear, each with its own successes and seeds", () => {
  const slices = evalSlices(episodes());
  assert.equal(isSliced(slices), true);
  assert.deepEqual(slices.map(slice => [slice.key, slice.label, slice.episodes.length, slice.successes, seedText(slice.seeds)]), [
    ["nominal", "Nominal", 5, 3, "0–4"],
    ["more_items", "More items", 5, 0, "200–204"],
  ]);
  assert.equal(sliceResultsText(slices), "Nominal 3/5 · More items 0/5", "never one pooled rate (3/10)");
  // The first eval's fixture: the same slices, its own results.
  assert.equal(sliceResultsText(evalSlices(episodes(PLANNER_EPISODES))), "Nominal 4/5 · More items 0/5");
  // Episodes keep upload order within their slice, and a slice keeps the place it first appeared.
  const mixed = evalSlices([ok({ slice: "b" }), ok({ slice: "a" }, "timeout"), ok({ slice: "b" }, "failure")]);
  assert.deepEqual(mixed.map(slice => [slice.id, slice.episodes.length, slice.successes]), [["b", 2, 1], ["a", 1, 0]]);
});

test("slice labels come from the recorded text; episodes without one form their own group, and one group is not sliced", () => {
  assert.equal(sliceLabel("pill_count_30"), "Pill count 30");
  assert.equal(sliceLabel("  wide__scatter "), "Wide scatter");
  assert.equal(sliceLabel("network-outage"), "Network-outage", "hyphens are kept");
  assert.equal(sliceLabel(null), "No slice");
  const groups = evalSlices([ok({ slice: "nominal" }), ok({}), ok({ slice: "   " }), ok({ slice: 30 }), ok({ slice: " nominal " })]);
  assert.deepEqual(groups.map(slice => [slice.id, slice.key, slice.label, slice.episodes.length]), [
    ["nominal", "nominal", "Nominal", 2], [null, NO_SLICE_KEY, "No slice", 3],
  ], "blank or non-text slices report none; recorded text is trimmed");
  assert.equal(isSliced(evalSlices([ok({ slice: "nominal" }), ok({ slice: "nominal" })])), false, "one slice: shown as the whole eval");
  assert.equal(isSliced(evalSlices([ok({}), ok({})])), false);
  assert.deepEqual(evalSlices([]), []);
  // The panels' choice: the slice the key names, else the first.
  assert.equal(pickSlice(groups, NO_SLICE_KEY)?.label, "No slice");
  assert.equal(pickSlice(groups, "rain")?.label, "Nominal");
  assert.equal(pickSlice(groups, null)?.label, "Nominal");
  assert.equal(pickSlice([], "nominal"), null);
  assert.deepEqual([seedText([]), seedText([7]), seedText([0, 2, 7]), seedText([200, 201, 202])], [null, "7", "0, 2, 7", "200–202"]);
});

test("a slice's results come from its own episodes: success, items placed, failed decisions, calls, latency and time", () => {
  const [nominal, more] = evalSlices(episodes()).map(sliceSummary);
  assert.deepEqual([nominal.placed, nominal.failedDecisions, nominal.medianSteps, nominal.medianSeconds, nominal.clock], [{ placed: 118, total: 120 }, 9, 296, 147.6, "simulated"]);
  assert.deepEqual([more.placed, more.failedDecisions, more.medianSteps, more.medianSeconds, more.clock], [{ placed: 124, total: 150 }, 10, 299, 150, "simulated"]);
  // Latency per decision: the median, across the slice's episodes, of each episode's own percentile.
  const latency = (summary: typeof nominal) => [summary.latency?.p50.deviceMs, summary.latency?.p95.deviceMs, summary.latency?.p50.e2eMs, summary.latency?.p95.e2eMs];
  assert.deepEqual(latency(nominal), [1332.2, 1397.2, 2410.7, 2858.9]);
  assert.deepEqual(latency(more), [1379.1, 1457.8, 2427.9, 2895.2]);
  assert.equal(nominal.latency?.n, 5);
  // Items placed need both counts from every episode.
  const partial = episodes().slice(0, 2);
  delete partial[1].metrics!.pills_total;
  assert.equal(sliceSummary(evalSlices(partial)[0]).placed, null);
});

/* ---------- planner calls ---------- */

test("planner calls by result: refused replies apart from device or transport failures, a 0 kept as 0", () => {
  const all = plannerCalls(episodes())!;
  assert.deepEqual([all.calls, all.valid.calls, all.refused.calls, all.failed.calls], [415, 301, 114, 0], "every call is valid or refused; none failed");
  assert.deepEqual(all.refused.parts, [{ name: "planner_invalid_choice", calls: 114 }, { name: "planner_invalid_json", calls: 0 }, { name: "planner_invalid_schema", calls: 0 }]);
  assert.deepEqual(all.failed.parts, [{ name: "planner_device_errors", calls: 0 }, { name: "planner_http_errors", calls: 0 }, { name: "planner_timeouts", calls: 0 }]);
  assert.deepEqual([all.refused.episodes, all.failed.episodes], [10, 0]);
  const [nominal, more] = evalSlices(episodes()).map(slice => plannerCalls(slice.episodes)!);
  assert.deepEqual([nominal.calls, nominal.valid.calls, nominal.refused.calls, nominal.failed.calls], [196, 142, 54, 0]);
  assert.deepEqual([more.calls, more.valid.calls, more.refused.calls, more.failed.calls], [219, 159, 60, 0]);
  // A device or transport failure counts there, never as a refusal.
  const failing = plannerCalls([ok({ planner_calls: 6, planner_valid_replies: 3, planner_invalid_choice: 1, planner_invalid_json: 0, planner_invalid_schema: 0, planner_device_errors: 1, planner_http_errors: 0, planner_timeouts: 1 })])!;
  assert.deepEqual([failing.refused.calls, failing.failed.calls, failing.failed.episodes], [1, 2, 1]);
});

test("planner calls: a count an episode does not report is Not reported, never 0; an eval without call results has none", () => {
  const missing = episodes();
  delete missing[3].metrics!.planner_invalid_json;
  const calls = plannerCalls(missing)!;
  assert.deepEqual([calls.refused.calls, calls.refused.episodes, calls.failed.calls, calls.valid.calls], [null, null, 0, 301]);
  assert.deepEqual(calls.refused.parts.map(part => part.calls), [114, null, 0], "the parts that every episode reports stay");
  // The first fixture records invalid choices only: its other parts are not reported.
  const first = plannerCalls(episodes(PLANNER_EPISODES))!;
  assert.deepEqual([first.calls, first.valid.calls, first.refused.calls, first.failed.calls], [378, 293, null, null]);
  // Not reports: wrong types, negative or fractional counts.
  const odd = plannerCalls([ok({ planner_invalid_choice: -1, planner_invalid_json: 0.5, planner_invalid_schema: "0", planner_device_errors: 0, planner_http_errors: 0, planner_timeouts: 0 })])!;
  assert.deepEqual([odd.refused.calls, odd.failed.calls], [null, 0]);
  assert.equal(plannerCalls([ok({ planner_calls: 4, planner_valid_replies: 4 })]), null, "no refused or failed counts: nothing to show");
  assert.equal(plannerCalls([]), null);
});

/* ---------- declared provenance ---------- */

const LINKED = "oev_contract0009", OTHER = "oev_contract0010";
const DECLARED: EvalProvenance = {
  run: "MuJoCo planner run", perception: "Simulator-state perception", control: "Scripted IK",
  planner: "Qwen on Jetson (real calls)", runner: "Laptop", transport: "Portal relay",
};
function withProvenance(value: unknown, ids: string[] = [LINKED, OTHER]): ConvoyWorkspace {
  const ws = createSampleWorkspace(NOW);
  ws.robots[2].offlineEvaluationIds = ids;
  (ws.robots[2] as unknown as Record<string, unknown>).offlineEvaluationProvenance = value;
  return ws;
}
const at = "robots[2].offlineEvaluationProvenance";
function issues(value: unknown): string[] {
  const result = validateWorkspace(withProvenance(value));
  return result.ok ? [] : result.issues.map(issue => `${issue.path}: ${issue.message}`);
}

test("declared provenance is short text per field, keyed by an offline evaluation the robot links", () => {
  const valid = validateWorkspace(withProvenance({ [LINKED]: DECLARED, [OTHER]: { runner: "Cloud runner" } }));
  assert.equal(valid.ok, true, valid.ok ? "" : JSON.stringify(valid.issues));
  assert.deepEqual(valid.ok && valid.warnings, [], "a known key");
  assert.equal(validateWorkspace(withProvenance({})).ok, true, "nothing declared yet");
  const absent = createSampleWorkspace(NOW);
  assert.equal(validateWorkspace(absent).ok, true, "optional");
  assert.deepEqual(issues([DECLARED]), [`${at}: expected an object keyed by offline evaluation id`]);
  assert.deepEqual(issues({ eva_contract01: DECLARED }), [`${at}.eva_contract01: expected an offline evaluation id (oev_ and 12 letters or digits) as the key`]);
  assert.deepEqual(issues({ [LINKED]: "Mac" }), [`${at}.${LINKED}: expected an object`]);
  assert.deepEqual(issues({ [LINKED]: { runner: "" } }), [`${at}.${LINKED}.runner: expected a non-empty string`]);
  assert.deepEqual(issues({ [LINKED]: { runner: "  " } }), [`${at}.${LINKED}.runner: expected a non-empty string`]);
  assert.deepEqual(issues({ [LINKED]: { runner: 7 } }), [`${at}.${LINKED}.runner: expected a non-empty string`]);
  assert.deepEqual(issues({ [LINKED]: { transport: "x".repeat(MAX_PROVENANCE_TEXT + 1) } }), [`${at}.${LINKED}.transport: expected at most ${MAX_PROVENANCE_TEXT} characters`]);
  assert.deepEqual(issues({ [LINKED]: { planner: "Qwen\non Jetson" } }), [`${at}.${LINKED}.planner: expected one line of text`]);
  assert.equal(validateWorkspace(withProvenance({ [LINKED]: { transport: "x".repeat(MAX_PROVENANCE_TEXT) } })).ok, true);
  // Prototype keys are refused, as everywhere in the document.
  const parsed = JSON.parse(JSON.stringify(withProvenance({ [LINKED]: DECLARED })).replace(`"${LINKED}":{`, `"__proto__":{"polluted":true},"${LINKED}":{`));
  const proto = validateWorkspace(parsed);
  assert.deepEqual(!proto.ok && proto.issues, [{ path: `${at}.__proto__`, message: "is a reserved key and is not allowed" }]);
  assert.equal(({} as Record<string, unknown>).polluted, undefined);
});

test("declared provenance: an unknown field or an evaluation the robot does not link is kept as a warning and ignored", () => {
  const result = validateWorkspace(withProvenance({ [LINKED]: { ...DECLARED, release: "r1" }, oev_contract0011: { runner: "Laptop" } }));
  assert.equal(result.ok, true);
  assert.deepEqual(result.ok && result.warnings, [
    { path: `${at}.${LINKED}.release`, message: "is not part of schema version 1 and is ignored" },
    { path: `${at}.oev_contract0011`, message: "describes an offline evaluation this robot does not link and is ignored" },
  ]);
  const robot = withProvenance({ [LINKED]: DECLARED, oev_contract0011: { runner: "Laptop" } }).robots[2];
  assert.equal(offlineProvenance(robot, "oev_contract0011"), null, "not linked: not shown");
  assert.equal(offlineProvenance(robot, OTHER), null, "linked, none declared");
  assert.deepEqual(offlineProvenance(robot, LINKED), DECLARED);
  assert.equal(offlineProvenance({ offlineEvaluationIds: [LINKED] }, LINKED), null);
});

test("declared provenance reads as one line in a fixed order; runner and transport are named", () => {
  assert.deepEqual(provenanceItems(DECLARED).map(item => item.text), [
    "MuJoCo planner run", "Simulator-state perception", "Scripted IK", "Qwen on Jetson (real calls)", "Runner: Laptop", "Transport: Portal relay",
  ]);
  assert.deepEqual(provenanceItems({ transport: " Portal relay ", runner: "Laptop", run: "  " }).map(item => [item.field, item.label, item.value]), [
    ["runner", "Runner", "Laptop"], ["transport", "Transport", "Portal relay"],
  ], "trimmed, in order, blanks left out");
  assert.deepEqual(provenanceItems(null), []);
  assert.deepEqual(provenanceItems({}), []);
});

test("relinking a robot's offline evaluations keeps the provenance of the links it keeps and drops the others'", () => {
  const ws = withProvenance({ [LINKED]: DECLARED, [OTHER]: { runner: "Cloud runner" } });
  const kept = linkOfflineEvaluations(ws, ws.robots[2].id, [LINKED], NOW);
  assert.deepEqual(kept.robots[2].offlineEvaluationProvenance, { [LINKED]: DECLARED });
  const result = validateWorkspace(kept);
  assert.equal(result.ok, true);
  assert.deepEqual(result.ok && result.warnings, []);
  const none = linkOfflineEvaluations(ws, ws.robots[2].id, [], NOW);
  assert.equal(none.robots[2].offlineEvaluationProvenance, undefined);
  assert.equal(none.robots[2].offlineEvaluationIds, undefined);
  assert.deepEqual(ws.robots[2].offlineEvaluationProvenance, { [LINKED]: DECLARED, [OTHER]: { runner: "Cloud runner" } }, "the input is not changed");
});
