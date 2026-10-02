import test from "node:test";
import assert from "node:assert/strict";
import {
  autonomyFromEpisodes, breakdown, EVIDENCE_METRICS, latencyBudgetFromEpisodes, latencyBudgetFromInference, latencyScale, latencyTargets,
} from "../../src/lib/configurations/evidence";
import type { Autonomy, LatencyBreakdown } from "../../src/lib/configurations/evidence";
import type { LiveInference } from "../../src/lib/configurations/live";
import { createSampleWorkspace, SAMPLE_LATENCY_TARGETS } from "../../src/lib/configurations/sample";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";
import { MAX_LATENCY_TARGETS, validateWorkspace } from "../../src/lib/configurations/validate";
import type { OfflineEpisode } from "../../src/lib/platform/client";
import { PLANNER_EPISODES } from "./evidence-fixture";
import type { FixtureEpisode } from "./evidence-fixture";

const NOW = Date.parse("2026-10-01T09:41:20Z");
type Episode = Pick<OfflineEpisode, "outcome" | "metrics">;
const episodes = (): Episode[] => PLANNER_EPISODES.map((episode: FixtureEpisode) => ({ outcome: episode.outcome, metrics: { ...episode.metrics } }));
/** Equal to 1e-9: the budget subtracts measured floats. */
function close(actual: number | null | undefined, expected: number, label = "") {
  assert.ok(typeof actual === "number" && Math.abs(actual - expected) < 1e-9, `${label} ${String(actual)} ≠ ${expected}`);
}
function closeRow(actual: LatencyBreakdown, expected: Record<keyof LatencyBreakdown, number | null>, label: string) {
  for (const key of Object.keys(expected) as Array<keyof LatencyBreakdown>) {
    const value = expected[key];
    if (value === null) assert.equal(actual[key], null, `${label}.${key}`); else close(actual[key], value, `${label}.${key}`);
  }
}
const ok = (episode: Partial<Record<string, number>> = {}, outcome: OfflineEpisode["outcome"] = "success"): Episode => ({
  outcome, metrics: { planner_valid_replies: 10, planner_failed_decisions: 0, protective_stops: 0, arm_arm_contacts: 0, ...episode },
});

/* ---------- latency budget ---------- */

test("an eval's latency budget: medians of the episodes' percentiles, split by the budget formulas", () => {
  const budget = latencyBudgetFromEpisodes(episodes())!;
  assert.equal(budget.source, "eval");
  assert.equal(budget.n, 10);
  // p50: prefill = first token, decode = device − first token, network = end to end − device.
  closeRow(budget.p50, { prefillMs: 800.7, decodeMs: 561.55, deviceMs: 1362.25, networkMs: 1321.55, e2eMs: 2683.8 }, "p50");
  close((budget.p50.prefillMs ?? 0) + (budget.p50.decodeMs ?? 0) + (budget.p50.networkMs ?? 0), 2683.8, "segments add up to end to end");
  // p95: no first-token p95 is recorded, so on-device time is not split.
  closeRow(budget.p95, { prefillMs: null, decodeMs: null, deviceMs: 1429.9, networkMs: 1986.15, e2eMs: 3416.05 }, "p95");
  assert.deepEqual([budget.tokensIn, budget.tokensOut], [896, 22]);
  close(budget.prefillShare, 800.7 / 1362.25, "prefill share");
  assert.equal(Math.round((budget.prefillShare ?? 0) * 100), 59);
  assert.deepEqual(budget.worst, { e2eMs: 17821.7, deviceMs: 1452 }, "the relay stall shows in the slowest episode's p95, not on the device");
});

test("an eval's latency budget reads only what episodes report: missing parts are null, never 0", () => {
  assert.equal(latencyBudgetFromEpisodes([]), null);
  assert.equal(latencyBudgetFromEpisodes([{ outcome: "success", metrics: { reward_sum: 4.5, grasped: true } }]), null, "no planner latency: no panel");
  const deviceOnly = latencyBudgetFromEpisodes([{ outcome: "success", metrics: { planner_device_p50_ms: 1200, planner_device_p95_ms: 1300 } }])!;
  closeRow(deviceOnly.p50, { prefillMs: null, decodeMs: null, deviceMs: 1200, networkMs: null, e2eMs: null }, "device only");
  assert.deepEqual([deviceOnly.tokensIn, deviceOnly.tokensOut, deviceOnly.prefillShare, deviceOnly.worst], [null, null, null, null]);
  // Each figure is the median of the episodes that report it; n counts episodes reporting any latency.
  const partial = latencyBudgetFromEpisodes([
    { outcome: "success", metrics: { planner_device_p50_ms: 1000, planner_ttft_p50_ms: 600, planner_e2e_p50_ms: 2000 } },
    { outcome: "success", metrics: { planner_device_p50_ms: 1400 } },
    { outcome: "timeout", metrics: { planner_device_p50_ms: "slow", planner_e2e_p50_ms: -5, reward_sum: 1 } },
  ])!;
  assert.equal(partial.n, 2, "an episode with only invalid values reports nothing");
  closeRow(partial.p50, { prefillMs: 600, decodeMs: 600, deviceMs: 1200, networkMs: 800, e2eMs: 2000 }, "partial");
  // Inconsistent values are not drawn: a first token after the device time, or a round trip shorter than it.
  assert.deepEqual(breakdown(1500, 1200, 1000), { prefillMs: 1500, decodeMs: null, deviceMs: 1200, networkMs: null, e2eMs: 1000 });
  assert.deepEqual(breakdown(null, null, null), { prefillMs: null, decodeMs: null, deviceMs: null, networkMs: null, e2eMs: null });
  const share = latencyBudgetFromEpisodes([{ outcome: "success", metrics: { planner_ttft_p50_ms: 1500, planner_device_p50_ms: 1200 } }])!;
  assert.equal(share.prefillShare, null, "no share above 100 %");
  const p95 = latencyBudgetFromEpisodes([{ outcome: "success", metrics: { [EVIDENCE_METRICS.ttftP95]: 900, planner_device_p95_ms: 1400, planner_e2e_p95_ms: 3400 } }])!;
  closeRow(p95.p95, { prefillMs: 900, decodeMs: 500, deviceMs: 1400, networkMs: 2000, e2eMs: 3400 }, "a first-token p95, when reported, splits p95 too");
});

test("a live device's budget: median and nearest-rank p95 of its spans, with no network figure", () => {
  const span = (latencyMs: number | null, ttftMs: number | null, tokensIn: number | null = 900, tokensOut: number | null = 22): LiveInference =>
    ({ traceId: `tr_${latencyMs}`, at: null, status: "ok", latencyMs, ttftMs, queueMs: 0, tokensIn, tokensOut, tokensPerS: null });
  assert.equal(latencyBudgetFromInference([]), null);
  assert.equal(latencyBudgetFromInference([span(null, 100)]), null, "no latency: nothing to draw");
  const budget = latencyBudgetFromInference([span(1355, 783, 837, 22), span(1012, 199, 185, 28), span(1361, 790), span(null, 500)])!;
  assert.equal(budget.source, "live");
  assert.equal(budget.n, 3, "spans with a latency");
  closeRow(budget.p50, { prefillMs: 783, decodeMs: 572, deviceMs: 1355, networkMs: null, e2eMs: null }, "p50");
  closeRow(budget.p95, { prefillMs: 790, decodeMs: 571, deviceMs: 1361, networkMs: null, e2eMs: null }, "p95 (nearest rank)");
  assert.deepEqual([budget.tokensIn, budget.tokensOut, budget.worst], [837, 22, null]);
  const noFirstToken = latencyBudgetFromInference([span(1200, null), span(1300, null)])!;
  closeRow(noFirstToken.p50, { prefillMs: null, decodeMs: null, deviceMs: 1250, networkMs: null, e2eMs: null }, "no first token");
});

test("latency targets: well-formed ones, shortest first; the scale is linear from 0 with round ticks", () => {
  assert.deepEqual(latencyTargets(null), []);
  assert.deepEqual(latencyTargets({}), []);
  assert.deepEqual(latencyTargets({ latencyTargets: [{ label: "Far", ms: 120 }, { label: " ", ms: 80 }, { label: "Near", ms: 60 }, { label: "Zero", ms: 0 }] }).map(target => target.label), ["Near", "Far"]);
  assert.deepEqual(latencyScale([2683.8, 3416.05, 60, 120]), { maxMs: 4000, ticks: [0, 1000, 2000, 3000, 4000] });
  assert.deepEqual(latencyScale([2683.8, 60, 120]), { maxMs: 3000, ticks: [0, 1000, 2000, 3000] });
  assert.deepEqual(latencyScale([1355, 1361, 60, 120]), { maxMs: 1500, ticks: [0, 500, 1000, 1500] });
  assert.deepEqual(latencyScale([650]), { maxMs: 800, ticks: [0, 200, 400, 600, 800] });
  assert.deepEqual(latencyScale([210.25, 60, 120]), { maxMs: 300, ticks: [0, 100, 200, 300] }, "at most four intervals");
  assert.deepEqual(latencyScale([4000]), { maxMs: 4000, ticks: [0, 1000, 2000, 3000, 4000] }, "a value on a tick ends the axis there");
  assert.deepEqual(latencyScale([null, undefined, Number.NaN]), { maxMs: 1, ticks: [0] });
  assert.deepEqual(latencyScale([]), { maxMs: 1, ticks: [0] });
});

/* ---------- autonomy ---------- */

test("autonomy across the eval: interventions are failed decisions, protective stops and unfinished episodes", () => {
  const autonomy = autonomyFromEpisodes(episodes())!;
  assert.deepEqual(
    [autonomy.episodes, autonomy.autonomous, autonomy.autonomousShare, autonomy.interventions, autonomy.decisions, autonomy.firstCallAccepted],
    [10, 0, 0, 29, 308, null],
    "every episode has an intervention; first-call acceptance is not recorded",
  );
  close(autonomy.perEpisode, 2.9, "per episode");
  close(autonomy.per100Decisions, 29 / 308 * 100, "per 100 decisions");
  close(autonomy.decisionsBetween, 308 / 29, "decisions between interventions");
  assert.deepEqual(autonomy.breakdown, [
    { type: "failed-decision", counted: true, events: 15, episodes: 7 },
    { type: "protective-stop", counted: true, events: 8, episodes: 1 },
    { type: "unfinished", counted: true, events: 6, episodes: 6 },
    { type: "arm-arm-contact", counted: false, events: 140, episodes: 1 },
  ], "arm–arm contacts are shown, not counted: recorded at any force");
  assert.deepEqual([autonomy.reported, autonomy.calls, autonomy.validReplies], [10, 378, 293], "recorded context: every episode reports every count");
});

test("autonomy: a missing count is Not reported (null), never 0, and only where it matters", () => {
  // A success that does not report its failed decisions: its autonomy is unknown, so are the totals.
  const missing = episodes();
  delete missing[1].metrics![EVIDENCE_METRICS.failedDecisions];
  const unknown = autonomyFromEpisodes(missing)!;
  assert.deepEqual([unknown.autonomous, unknown.autonomousShare, unknown.interventions, unknown.perEpisode, unknown.decisions, unknown.per100Decisions, unknown.decisionsBetween],
    [null, null, null, null, null, null, null]);
  assert.deepEqual(unknown.breakdown.map(row => [row.type, row.events, row.episodes]), [
    ["failed-decision", null, null], ["protective-stop", 8, 1], ["unfinished", 6, 6], ["arm-arm-contact", 140, 1],
  ]);
  assert.deepEqual([unknown.reported, unknown.calls, unknown.validReplies], [9, 378, 293], "9 of 10 report every count; calls are still complete");
  // A timeout that does not report one: still not autonomous, so the autonomous count stands.
  const timeout = autonomyFromEpisodes([ok(), { outcome: "timeout", metrics: { planner_valid_replies: 5, protective_stops: 0 } }])!;
  assert.deepEqual([timeout.autonomous, timeout.interventions, timeout.decisions], [1, null, null]);
  // Wrong types and negative or fractional counts are not reports.
  const invalid = autonomyFromEpisodes([ok({ planner_failed_decisions: -1 }), ok({ protective_stops: 1.5 }), { outcome: "success", metrics: { planner_failed_decisions: "0" as unknown as number, protective_stops: 0 } }])!;
  assert.deepEqual(invalid.breakdown.slice(0, 2).map(row => row.events), [null, null]);
  // An outcome this website does not know is not counted either way.
  const odd = autonomyFromEpisodes([ok({}, "cancelled" as OfflineEpisode["outcome"])])!;
  assert.deepEqual([odd.autonomous, odd.breakdown[2].events], [null, null]);
});

test("autonomy edge cases: no interventions, first-call acceptance when recorded, no decisions, no planner metrics", () => {
  const clean = autonomyFromEpisodes([ok(), ok({ planner_valid_replies: 20 })])!;
  assert.deepEqual([clean.autonomous, clean.autonomousShare, clean.interventions, clean.perEpisode, clean.decisions, clean.per100Decisions], [2, 1, 0, 0, 30, 0]);
  assert.equal(clean.decisionsBetween, null, "no interventions: nothing between them");
  const firstCall = autonomyFromEpisodes([ok({ planner_first_call_valid: 9 }), ok({ planner_first_call_valid: 6, planner_failed_decisions: 1, planner_valid_replies: 9 })])!;
  close(firstCall.firstCallAccepted, 15 / 20, "first call");
  assert.equal(autonomyFromEpisodes([ok({ planner_first_call_valid: 9 }), ok()])!.firstCallAccepted, null, "every episode must report it");
  const none = autonomyFromEpisodes([ok({ planner_valid_replies: 0 })])!;
  assert.deepEqual([none.decisions, none.per100Decisions, none.decisionsBetween, none.firstCallAccepted], [0, null, null, null]);
  assert.equal(autonomyFromEpisodes([]), null);
  assert.equal(autonomyFromEpisodes([{ outcome: "timeout", metrics: { reward_sum: 1.5 } }]), null, "an eval without planner or safety counts has no panel");
  const noContacts = autonomyFromEpisodes([{ outcome: "success", metrics: { planner_failed_decisions: 0, protective_stops: 0, planner_valid_replies: 3 } }]) as Autonomy;
  assert.deepEqual(noContacts.breakdown.map(row => row.type), ["failed-decision", "protective-stop", "unfinished"], "no arm–arm row without the field");
});

/* ---------- the document ---------- */

test("the sample declares neutral latency targets on every configuration and stays valid", () => {
  const ws = createSampleWorkspace(NOW);
  assert.ok(ws.configurations.every(config => JSON.stringify(config.latencyTargets) === JSON.stringify(SAMPLE_LATENCY_TARGETS)));
  assert.deepEqual(SAMPLE_LATENCY_TARGETS.map(target => [target.label, target.ms]), [["Teleop · near", 60], ["Teleop · far", 120]]);
  const result = validateWorkspace(ws);
  assert.equal(result.ok, true);
  assert.deepEqual(result.ok && result.warnings, [], "a known key");
});

test("the validator accepts latency targets only as short labels with positive milliseconds", () => {
  const withTargets = (targets: unknown): ConvoyWorkspace => {
    const ws = createSampleWorkspace(NOW);
    (ws.configurations[0] as unknown as Record<string, unknown>).latencyTargets = targets;
    return ws;
  };
  const issues = (targets: unknown) => {
    const result = validateWorkspace(withTargets(targets));
    return result.ok ? [] : result.issues.map(issue => `${issue.path}: ${issue.message}`);
  };
  for (const targets of [[], [{ label: "Teleop", ms: 60 }], [{ label: "Motor loop", ms: 1 }, { label: "Teleop", ms: 100.5 }]]) {
    assert.equal(validateWorkspace(withTargets(targets)).ok, true, JSON.stringify(targets));
  }
  const absent = createSampleWorkspace(NOW);
  delete absent.configurations[0].latencyTargets;
  assert.equal(validateWorkspace(absent).ok, true, "optional");
  const at = "configurations[0].latencyTargets";
  assert.deepEqual(issues({ label: "Teleop", ms: 60 }), [`${at}: expected an array`]);
  assert.deepEqual(issues(Array.from({ length: MAX_LATENCY_TARGETS + 1 }, (_, i) => ({ label: `T${i}`, ms: i + 1 }))), [`${at}: expected at most ${MAX_LATENCY_TARGETS} items`]);
  assert.deepEqual(issues([{ label: "", ms: 60 }]), [`${at}[0].label: expected a non-empty string`]);
  assert.deepEqual(issues([{ label: "   ", ms: 60 }]), [`${at}[0].label: expected a non-empty string`]);
  assert.deepEqual(issues([{ label: "x".repeat(41), ms: 60 }]), [`${at}[0].label: expected at most 40 characters`]);
  assert.deepEqual(issues([{ label: 60, ms: 60 }]), [`${at}[0].label: expected a non-empty string`]);
  assert.deepEqual(issues([{ ms: 60 }]), [`${at}[0].label: is required`]);
  assert.deepEqual(issues([{ label: "Teleop" }]), [`${at}[0].ms: is required`]);
  assert.deepEqual(issues([{ label: "Teleop", ms: 0 }]), [`${at}[0].ms: expected a number > 0`]);
  assert.deepEqual(issues([{ label: "Teleop", ms: -60 }]), [`${at}[0].ms: expected a number > 0`]);
  assert.deepEqual(issues([{ label: "Teleop", ms: 600_001 }]), [`${at}[0].ms: expected a number ≤ 600000`]);
  assert.deepEqual(issues([{ label: "Teleop", ms: "60" }]), [`${at}[0].ms: expected a finite number`]);
  assert.deepEqual(issues([{ label: "Teleop", ms: 60 }, { label: "teleop ", ms: 120 }]), [`${at}[1].label: duplicate label "teleop "`]);
  assert.deepEqual(issues(["Teleop 60"]), [`${at}[0]: expected an object`]);
  assert.deepEqual(issues([JSON.parse("{\"label\":\"Teleop\",\"ms\":60,\"__proto__\":{}}")]), [`${at}[0].__proto__: is a reserved key and is not allowed`]);
  const extra = validateWorkspace(withTargets([{ label: "Teleop", ms: 60, color: "red" }]));
  assert.equal(extra.ok, true, "an unknown key is kept");
  assert.deepEqual(extra.ok && extra.warnings, [{ path: `${at}[0].color`, message: "is not part of schema version 1 and is ignored" }]);
});
