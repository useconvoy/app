import test from "node:test";
import assert from "node:assert/strict";
import type { LiveInference } from "../../src/lib/configurations/live";
import { flagRobot, setRobotRole } from "../../src/lib/configurations/mutations";
import {
  activeRunOn, clockText, edgeSpanStats, evaluationChoices, fmtStarted, gateShortfall, gateSummary, latencyBarChart, niceAxis, policyLabel, promotionState,
  runRowFacts, sampleStat, timeWindowLabel, traceCounts, traceFactGroups, traceWaterfalls,
} from "../../src/lib/configurations/robot";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { getConfiguration, getRevision, getRobot, getRun, robotReadings, tracesFor } from "../../src/lib/configurations/selectors";
import type { ConvoyWorkspace, EvalRun } from "../../src/lib/configurations/types";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const ws = createSampleWorkspace(NOW);
const iso = (offsetS: number) => new Date(NOW + offsetS * 1000).toISOString();
const span = (offsetS: number, latencyMs: number | null, extra: Partial<LiveInference> = {}): LiveInference => ({
  traceId: `tr_${offsetS}`, at: iso(offsetS), status: "ok", latencyMs, ttftMs: 60, queueMs: 0, tokensIn: 110, tokensOut: 8, tokensPerS: 57.5, ...extra,
});
const robot = (id: string) => getRobot(ws, id)!;
const hybrid = getConfiguration(ws, "hybrid")!;
const run = (id: string) => getRun(ws, id)!;

test("edge span statistics: median and nearest-rank p95 with their own n, window from the span times", () => {
  // Newest first, as the device snapshot sends them; one span without a latency, one without a TTFT.
  const inference = [span(-1, 650, { ttftMs: 80 }), span(-20, 210.25), span(-40, null, { ttftMs: null }), span(-60, 180.5)];
  const stats = edgeSpanStats(inference);
  assert.equal(stats.received, 4);
  assert.deepEqual(stats.latency, { p50: 210.25, p95: 650, n: 3 });
  assert.deepEqual(stats.ttft, { p50: 60, p95: 80, n: 3 });
  assert.deepEqual(stats.throughput, { p50: 57.5, p95: 57.5, n: 4 });
  assert.equal(stats.from, iso(-60));
  assert.equal(stats.to, iso(-1));
  assert.deepEqual(edgeSpanStats([]), { received: 0, latency: { p50: null, p95: null, n: 0 }, ttft: { p50: null, p95: null, n: 0 }, queue: { p50: null, p95: null, n: 0 }, throughput: { p50: null, p95: null, n: 0 }, outputTokens: { p50: null, p95: null, n: 0 }, from: null, to: null });
  assert.deepEqual(sampleStat([5, null, Number.NaN, 1, 3]), { p50: 3, p95: 5, n: 3 }, "missing values are not zeros");
});

test("latency bars: round axis in thirds, oldest bar first, dashed p95 line", () => {
  assert.deepEqual(niceAxis(702.4), { end: 750, step: 250 });
  assert.deepEqual(niceAxis(650), { end: 750, step: 250 });
  assert.deepEqual(niceAxis(120), { end: 150, step: 50 });
  assert.deepEqual(niceAxis(1000), { end: 1000, step: 500 });
  assert.deepEqual(niceAxis(0.7), { end: 0.75, step: 0.25 });
  assert.deepEqual(niceAxis(0), { end: 3, step: 1 });
  const chart = latencyBarChart([span(-1, 600), span(-20, null), span(-40, 150)]);
  assert.equal(chart.count, 2);
  assert.deepEqual([chart.low, chart.high, chart.p95, chart.end], [150, 600, 600, 750]);
  assert.deepEqual(chart.ticks.map(tick => [tick.label, tick.top]), [["0", "100%"], ["250", "66.67%"], ["500", "33.33%"], ["750", "0%"]]);
  // The older 150 ms request is the left bar (x 9–41), the newer 600 ms one the right bar (x 59–91).
  assert.equal(chart.bars, "M9 100V80H41V100ZM59 100V20H91V100Z");
  assert.equal(chart.ref, "M0 20H100");
  assert.equal(chart.refTop, "20%");
  const empty = latencyBarChart([span(-1, null)]);
  assert.deepEqual([empty.count, empty.bars, empty.ref, empty.refTop, empty.p95], [0, "", "", null, null]);
});

test("promotion: bench, device-bound and simulated robots are blocked; others need the revision's latest gate run to pass", () => {
  assert.deepEqual(promotionState(ws, robot("lab-bench"), hybrid), { kind: "blocked", reason: "Bench hardware runs evaluations; promote a revision, not a bench.", run: null });
  assert.equal(promotionState(ws, robot("unit-07"), hybrid).kind, "not-test");
  const allowed = promotionState(ws, robot("unit-02"), hybrid);
  assert.equal(allowed.kind, "allowed");
  assert.equal(allowed.kind === "allowed" && allowed.run.number, 23, "Run 24 is still running, so Run 23 holds the gate decision for r4");
  const cloudOnly = getConfiguration(ws, "cloud-only")!;
  const sim = promotionState(ws, robot("sim-01"), cloudOnly);
  assert.equal(sim.kind === "blocked" && sim.reason, "A simulated robot runs suites only; it cannot serve production.");
  // A physical test robot on the cloud-only configuration: r2's latest gate run is below the gate.
  const onCloudOnly: ConvoyWorkspace = { ...ws, robots: ws.robots.map(item => item.id === "unit-02" ? { ...item, configId: "cloud-only", rev: "r2" } : item) };
  const below = promotionState(onCloudOnly, getRobot(onCloudOnly, "unit-02")!, cloudOnly);
  assert.deepEqual(below.kind === "blocked" && [below.reason, below.run?.number], ["r2 is below the gate on Run 21: Each slice family: Network 48.3 % (gate ≥ 55 %).", 21]);
  const noGate: ConvoyWorkspace = { ...ws, robots: ws.robots.map(item => item.id === "unit-02" ? { ...item, configId: "edge-only", rev: "r1" } : item) };
  const none = promotionState(noGate, getRobot(noGate, "unit-02")!, getConfiguration(noGate, "edge-only")!);
  assert.equal(none.kind === "blocked" && none.reason, "r1 has no gate decision yet; run the evaluation suite first.");
  const bound: ConvoyWorkspace = { ...ws, robots: ws.robots.map(item => item.id === "unit-02" ? { ...item, deviceId: "dev_other", telemetry: undefined } : item) };
  assert.equal(promotionState(bound, getRobot(bound, "unit-02")!, hybrid).kind, "blocked");
  assert.equal(gateShortfall(ws.suites[0], run("run-23")), null);
});

test("queuing choices: the configuration's suite and the robot's revision come first", () => {
  const choices = evaluationChoices(ws, hybrid, robot("lab-bench"));
  assert.equal(choices.suiteId, "station-suite-v1");
  assert.deepEqual(choices.suites.map(suite => [suite.label, suite.detail]), [["Bimanual station suite v1", "360 episodes per run · 5 seeds per cell"]]);
  assert.deepEqual(choices.revisions.map(item => item.label), ["r4 · testing · Cloud policy v3.1", "r3 · in production · First production revision"]);
  assert.equal(choices.rev, "r4");
  assert.equal(evaluationChoices(ws, hybrid, robot("unit-07")).rev, "r3");
  assert.match(gateSummary(ws.suites[0]) ?? "", /^Overall success ≥ 70 % · Each slice family ≥ 55 % · Critical safety events: None · /);
  assert.equal(gateSummary(null), null);
  assert.equal(activeRunOn(ws, "lab-bench")?.number, 24);
  assert.equal(activeRunOn(ws, "unit-02"), null);
});

test("evaluation rows: running, gated, recorded and queued runs read honestly", () => {
  const running = runRowFacts(ws, run("run-24"));
  assert.deepEqual(running.success, { value: "78.8 %", detail: "167 / 212 · so far", missing: false });
  assert.deepEqual(running.safety, { value: "0.9", detail: "0 critical · so far", missing: false });
  assert.deepEqual(running.median, { value: "15.4 s", detail: "simulated · so far", missing: false });
  assert.equal(running.note, null);
  const passed = runRowFacts(ws, run("run-23"));
  assert.equal(passed.success.detail, "281 / 360 · CI 73.5–82.0 %");
  assert.equal(passed.subject, "Bimanual station · Hybrid r4");
  assert.equal(passed.subjectDetail, "Candidate · seeds 1–5", "a suite run's variant repeats its configuration");
  assert.equal(runRowFacts(ws, run("run-19")).subjectDetail, "Qwen2.5-1.5B planner, llama.cpp CUDA · Diagnostic text fixture, not a robot task");
  const below = runRowFacts(ws, run("run-21"));
  assert.equal(below.subject, "Bimanual station · Cloud only r2", "a run names its own configuration");
  assert.equal(below.note, "Each slice family: Network 48.3 % (gate ≥ 55 %)");
  const timing = runRowFacts(ws, run("run-20"));
  assert.deepEqual([timing.started, timing.success.value, timing.safety.value, timing.safety.missing, timing.median.value, timing.median.detail, timing.note],
    ["Sep 29", "0 / 3", "Not reported", true, "6.2 s", "wall-clock", "Truncated at the 500-step horizon"]);
  const soak = runRowFacts(ws, run("run-19"));
  assert.deepEqual([soak.started, soak.success.value, soak.success.detail, soak.median.value, soak.median.detail], ["Sep 14, 01:19", "97.1 %", "1,617 / 1,666 requests", "122 ms", "p95 635 ms · on-device gateway"]);
  assert.equal(runRowFacts(ws, run("run-18")).success.detail, "One episode");
  const queued = runRowFacts(ws, run("run-25"));
  assert.deepEqual([queued.started, queued.success.value, queued.success.detail, queued.safety.value, queued.median.value, queued.waiting, queued.note],
    ["Not started", "Pending", "0 / 360", "Pending", "Pending", true, "Runner not connected"]);
  const linked: EvalRun = { ...run("run-25"), recordedEvaluationId: "eva_contract01" };
  assert.deepEqual([runRowFacts(ws, linked).waiting, runRowFacts(ws, linked).note, runRowFacts(ws, linked).evaluationId], [false, null, "eva_contract01"], "a run linked to a control-plane evaluation is not described as waiting for a runner");
  assert.equal(fmtStarted("2026-09-29T00:00:00.000Z"), "Sep 29");
  assert.equal(fmtStarted(null), "Not started");
});

test("traces: filter counts, waterfalls per span group and the drawer's fact groups", () => {
  assert.deepEqual(traceCounts(ws, "unit-07"), { all: 12, fallback: 1, escalated: 1, failed: 1 });
  assert.deepEqual(traceCounts(ws, "unit-08"), { all: 0, fallback: 0, escalated: 0, failed: 0 });
  const traces = tracesFor(ws, "unit-07");
  const escalated = traces.find(trace => trace.escalated)!;
  const waterfalls = traceWaterfalls(escalated);
  assert.deepEqual(waterfalls.map(item => [item.title, item.unit, item.spans.length, item.paths.join(" ")]), [
    ["Autonomy · first 5 seconds", "ms", 6, "edge cloud"],
    ["Escalation and recovery · whole trace", "s", 4, "edge operator"],
  ]);
  const navigate = traces[0];
  assert.deepEqual(traceWaterfalls(navigate).map(item => [item.title, item.spans.length, item.paths.join(" ")]), [["Spans · whole trace", 3, "edge"]]);
  const revision = getRevision(hybrid, "r3");
  // Stored groups keep their content; model versions move before safety.
  assert.deepEqual(traceFactGroups(escalated, { revision, configurationName: hybrid.name }).map(group => group.title), ["Inputs and outputs", "Recovery", "Model versions", "Safety"]);
  const built = traceFactGroups(navigate, { revision, configurationName: hybrid.name });
  assert.deepEqual(built.map(group => group.title), ["Inputs and outputs", "Model versions", "Safety"]);
  assert.deepEqual(built[0].facts.map(fact => [fact.label, fact.value, fact.detail ?? null]), [
    ["Instruction", "Move to staging area 2", null],
    ["Planner decision", "navigate", "staging area 2 · Planned in 147 ms"],
    ["Route", "Base controller, no policy call", "Edge path"],
    ["Policy p50 / p95", "No policy call", null],
  ]);
  assert.deepEqual(built[2].facts[0], { label: "Safety record", value: "Not reported", detail: "No safety facts are stored with this trace." });
  const declined = traces.find(trace => trace.decision.kind === "declined")!;
  assert.deepEqual(traceFactGroups(declined)[0].facts[1], { label: "Planner decision", value: "Declined", detail: "No object or place named · Decided in 205 ms" });
  const bare = { ...navigate, facts: [] };
  assert.deepEqual(traceFactGroups(bare).map(group => group.title), ["Inputs and outputs", "Safety"], "no revision: model versions are not guessed");
  const declared = traceFactGroups(bare, { revision, configurationName: hybrid.name })[1];
  assert.deepEqual(declared.facts.map(fact => fact.label), ["Planner · edge", "Fallback policy · edge", "Policy · cloud", "Verifier · cloud", "Configuration"]);
  assert.equal(policyLabel({ p50: 117, p95: 243, n: 31 }), "117 / 243 ms");
  assert.equal(policyLabel({ p50: 131, p95: 131, n: 1 }), "131 ms");
  assert.equal(policyLabel(null), "No policy call");
  assert.equal(policyLabel({ p50: null, p95: null }), "Not reported");
});

test("clock labels and time windows", () => {
  assert.equal(clockText(undefined), "UTC");
  assert.equal(clockText({ zone: "PDT", utcOffsetMinutes: -420 }), "PDT · UTC−7");
  assert.equal(clockText({ zone: "IST", utcOffsetMinutes: 330 }), "IST · UTC+5:30");
  assert.equal(timeWindowLabel(["2026-10-01T09:39:00Z", null, "2026-10-01T09:13:00Z"]), "Oct 1 · 09:13 – 09:39 UTC");
  assert.equal(timeWindowLabel(["2026-10-01T06:30:00Z", "2026-10-01T07:10:00Z"], { zone: "PDT", utcOffsetMinutes: -420 }), "Sep 30, 23:30 – Oct 1, 00:10 PDT");
  assert.equal(timeWindowLabel([]), null);
});

test("the robot page's health is the shared displayed health: it follows the flags in effect", () => {
  // The page renders `robotReadings(…).health`, the rule every page uses (selectors.displayHealth).
  const readings = (workspace: ConvoyWorkspace, id: string) => robotReadings(getRobot(workspace, id)!, getRevision(hybrid, getRobot(workspace, id)!.rev), null, NOW);
  assert.deepEqual([readings(ws, "unit-07").health, readings(ws, "unit-07").healthReason], ["healthy", null]);
  assert.deepEqual([readings(ws, "unit-08").health, readings(ws, "unit-08").healthReason], ["attention", "Near thermal throttle"]);
  const attention = flagRobot(ws, "unit-07", { label: "Gripper noise", note: "Clicking on close", severity: "attention" }, NOW);
  assert.deepEqual([readings(attention, "unit-07").health, readings(attention, "unit-07").healthReason, readings(attention, "unit-07").baseHealth], ["attention", "Gripper noise", "healthy"]);
  const warning = flagRobot(ws, "unit-07", { label: "Loose cable", note: "Check the wrist camera cable" }, NOW);
  assert.equal(readings(warning, "unit-07").health, "degraded");
});

test("role changes keep the promotion rules consistent", () => {
  const moved = setRobotRole(ws, "unit-02", "production", NOW + 60_000);
  assert.equal(promotionState(moved, getRobot(moved, "unit-02")!, hybrid).kind, "not-test");
});
