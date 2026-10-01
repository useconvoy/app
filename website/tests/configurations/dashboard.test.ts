import test from "node:test";
import assert from "node:assert/strict";
import {
  ALL_LOGS, attentionSummary, cadenceLabel, DEFAULT_ROBOT_SORT, filterLogs, filterRobotRows, isSilent, latencyTicks, logFacets, needsAttention, nextRobotSort, niceTicks,
  parseRobotFilter, productionKpis, revisionGate, robotFilterCounts, robotRows, robotSortValue, seriesWindowLabel, sortRobotRows, sparkReferences, sparkY,
  safetyCaption, sparkZone, telemetryDomain, triageRank, weakestProvenance,
} from "../../src/lib/configurations/dashboard";
import { flagRobot } from "../../src/lib/configurations/mutations";
import { mapSnapshot } from "../../src/lib/configurations/live";
import type { LiveBinding } from "../../src/lib/configurations/live";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { attentionRobots, getConfiguration, logsFor } from "../../src/lib/configurations/selectors";
import { sortRows } from "../../src/lib/configurations/table";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";
import type { PortalSnapshot } from "../../src/lib/portal/types";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const fresh = () => createSampleWorkspace(NOW);
const ws = fresh();
const hybrid = getConfiguration(ws, "hybrid")!;
const ids = (rows: ReadonlyArray<{ robot: { id: string } }>) => rows.map(row => row.robot.id);

function snapshot(options: { temp?: number | null; online?: boolean; latencies?: number[] } = {}): PortalSnapshot {
  const at = (s: number) => new Date(NOW - s * 1000).toISOString();
  const sample = (s: number) => ({ ts: at(s), cpu_pct: 21, gpu_pct: 9, mem_total_mb: 7620, mem_available_mb: 3533, power_w: 8.1, temp_max_c: options.temp === undefined ? 46 : options.temp, disk_free_mb: 1, runtime_state: "running", clock_confidence: "unknown" });
  return {
    fetched_at: at(0), telemetry_stale_after_s: 90, heartbeat_interval_s: 15,
    device: { id: "dev_lab", name: "Lab Jetson", status: options.online === false ? "offline" : "online", live_at: at(5), observed_at: at(5), observed_health: "ok", observed_stage: "ready", agent_version: "1", observed_active_release_id: null, gateway_mode: "production", runtime_state: "running" },
    release: null, telemetry: [sample(30), sample(15), sample(0)], latest_telemetry: sample(0), usage: { from: "2026-09-01", to: "2026-09-30", metrics: null },
    recent_inference: (options.latencies ?? [180, 210, 650]).map((latency, i) => ({ trace_id: `tr_${i}`, start_ts: at(i * 20), status: "ok", latency_ms: latency, ttft_ms: 60, queue_ms: 0, tokens_in: 100, tokens_out: 8, tok_s: 55 })),
    chat: { eligible: options.online !== false, online: options.online !== false, reason: null, release_id: null, max_tokens: 128, context_window: null },
  };
}
const binding = (snap: PortalSnapshot): LiveBinding => ({ deviceKey: "configured-device", status: "fresh", data: mapSnapshot(snap), error: null, receivedAt: 0 });
const unavailable: LiveBinding = { deviceKey: "configured-device", status: "unavailable", data: null, error: "The device could not be reached.", receivedAt: null };

test("rows: one per attached robot, bound first; links resolve under the configuration", () => {
  const rows = robotRows(ws, hybrid, {}, NOW);
  assert.deepEqual(ids(rows), ["lab-bench", "unit-02", "unit-07", "unit-08", "unit-13", "unit-16"]);
  assert.deepEqual(rows.map(row => row.bound), [true, false, false, false, false, false]);
  assert.equal(rows[2].href, "/app/configurations/hybrid/robots/unit-07");
  const bench = rows[0];
  assert.equal(bench.reporting, false, "no live data yet");
  assert.equal(bench.current, null);
  assert.equal(bench.edge.ms, null, "the recorded soak never stands in for the live reading");
  assert.equal(bench.edge.provenance.kind, "not-reported");
  assert.equal(bench.readings.edgeProvenance.kind, "recorded", "the readings still carry the recorded figures for their own panel");
  assert.equal(isSilent(bench), true);
  assert.equal(isSilent(rows[1]), false);
});

test("rows: a bound robot shows measured values only while it reports", () => {
  const live = robotRows(ws, hybrid, { "lab-bench": binding(snapshot()) }, NOW)[0];
  assert.equal(live.reporting, true);
  assert.equal(live.current?.socTempC, 46);
  assert.equal(live.readings.provenance.kind, "measured");
  assert.deepEqual([live.edge.ms?.p50, live.edge.ms?.p95, live.edge.provenance.kind], [210, 650, "measured"]);
  const offline = robotRows(ws, hybrid, { "lab-bench": binding(snapshot({ online: false })) }, NOW)[0];
  assert.equal(offline.readings.health, "offline");
  assert.equal(offline.readings.baseHealth, "offline");
  assert.equal(offline.reporting, false);
  assert.equal(offline.current, null, "an offline device's last values are not shown as current");
  assert.equal(offline.edge.ms, null);
  const missing = robotRows(ws, hybrid, { "lab-bench": unavailable }, NOW)[0];
  assert.deepEqual([missing.reporting, missing.current, missing.readings.health], [false, null, "not-reported"]);
});

test("filters: counts per chip, Needs attention is the attention health only, unknown query values read as all", () => {
  const rows = robotRows(ws, hybrid, {}, NOW);
  assert.deepEqual(robotFilterCounts(rows), { all: 6, test: 2, production: 4, attention: 2 });
  assert.deepEqual(ids(filterRobotRows(rows, "production")), ["unit-07", "unit-08", "unit-13", "unit-16"]);
  assert.deepEqual(ids(filterRobotRows(rows, "test")), ["lab-bench", "unit-02"]);
  assert.deepEqual(ids(filterRobotRows(rows, "attention")), ["unit-08", "unit-16"], "Unit 13 is Degraded (a warning), not Needs attention");
  assert.equal(parseRobotFilter("attention"), "attention");
  assert.equal(parseRobotFilter("bogus"), "all");
  assert.equal(parseRobotFilter(null), "all");
});

test("sort: bound robot pinned first, missing values last in both directions, ties by name", () => {
  const rows = robotRows(ws, hybrid, {}, NOW);
  assert.deepEqual(ids(sortRobotRows(rows, DEFAULT_ROBOT_SORT)), ["lab-bench", "unit-08", "unit-16", "unit-13", "unit-02", "unit-07"]);
  assert.deepEqual(ids(sortRobotRows(rows, { key: "temp", dir: "desc" })), ["lab-bench", "unit-08", "unit-16", "unit-02", "unit-07", "unit-13"]);
  assert.deepEqual(ids(sortRobotRows(rows, { key: "temp", dir: "asc" })), ["lab-bench", "unit-13", "unit-07", "unit-02", "unit-16", "unit-08"]);
  assert.deepEqual(ids(sortRobotRows(rows, { key: "name", dir: "desc" })), ["lab-bench", "unit-16", "unit-13", "unit-08", "unit-07", "unit-02"]);
  // A robot that reports nothing (attached for the first time) sorts after the others either way.
  const attached: ConvoyWorkspace = { ...ws, robots: ws.robots.map(robot => robot.id === "unit-18" ? { ...robot, configId: "hybrid", rev: "r4" } : robot) };
  const withSilent = robotRows(attached, getConfiguration(attached, "hybrid")!, {}, NOW);
  assert.equal(ids(sortRobotRows(withSilent, { key: "power", dir: "desc" })).at(-1), "unit-18");
  assert.equal(ids(sortRobotRows(withSilent, { key: "power", dir: "asc" })).at(-1), "unit-18");
  // The table re-sorts with the column's plain value (no sentinel): missing stays last both ways, and the order matches.
  assert.equal(robotSortValue(withSilent.find(row => row.robot.id === "unit-18")!, "fallback"), null);
  for (const dir of ["asc", "desc"] as const) {
    const sorted = sortRobotRows(withSilent, { key: "fallback", dir });
    assert.deepEqual(ids(sortRows(sorted, row => robotSortValue(row, "fallback"), dir, row => row.bound)), ids(sorted), dir);
    assert.equal(ids(sorted).at(-1), "unit-18", dir);
  }
  assert.deepEqual(nextRobotSort(DEFAULT_ROBOT_SORT, "health"), { key: "health", dir: "asc" });
  assert.deepEqual(nextRobotSort(DEFAULT_ROBOT_SORT, "name"), { key: "name", dir: "asc" });
  assert.deepEqual(nextRobotSort(DEFAULT_ROBOT_SORT, "temp"), { key: "temp", dir: "desc" });
});

test("a manual flag raises the shown health; filter, rank, row and banner follow it, the stored health stays", () => {
  const flagged = flagRobot(fresh(), "unit-07", { label: "Gripper slipping", note: "Slipped twice on bin 4", severity: "attention", by: "operator@example.test" }, NOW);
  const rows = robotRows(flagged, getConfiguration(flagged, "hybrid")!, {}, NOW);
  const unit07 = rows.find(row => row.robot.id === "unit-07")!;
  assert.deepEqual([unit07.readings.health, unit07.readings.baseHealth], ["attention", "healthy"], "shown health follows the flag; the document's health is kept as the base");
  assert.equal(unit07.reporting, true, "a flag does not stop a robot from reporting");
  assert.equal(needsAttention(unit07), true);
  assert.equal(triageRank(unit07), 3);
  assert.deepEqual(ids(filterRobotRows(rows, "attention")), ["unit-07", "unit-08", "unit-16"]);
  assert.deepEqual(ids(sortRobotRows(rows, DEFAULT_ROBOT_SORT)).slice(0, 4), ["lab-bench", "unit-07", "unit-08", "unit-16"]);
  const banner = attentionSummary(attentionRobots(flagged, "hybrid", {}, NOW));
  assert.deepEqual(banner.attention.map(line => line.robot.id), ids(filterRobotRows(rows, "attention")), "the banner lists exactly the Needs attention rows");
  const warned = flagRobot(fresh(), "unit-02", { label: "Camera smudge", note: "Wrist camera needs cleaning" }, NOW);
  const unit02 = robotRows(warned, getConfiguration(warned, "hybrid")!, {}, NOW).find(row => row.robot.id === "unit-02")!;
  assert.deepEqual([unit02.readings.health, needsAttention(unit02), triageRank(unit02)], ["degraded", false, 2], "a warning shows as Degraded");
});

test("KPIs: counts from the production robots, pooled figures from the stored summary", () => {
  const kpis = productionKpis(hybrid, robotRows(ws, hybrid, {}, NOW));
  assert.deepEqual(ids(kpis.robots), ["unit-07", "unit-08", "unit-13", "unit-16"], "production robots only, never the test robots");
  assert.deepEqual(kpis.excluded, []);
  assert.deepEqual([kpis.window, kpis.provenance.kind], ["24 h", "sample"]);
  assert.deepEqual([kpis.reporting.count, kpis.reporting.of, kpis.reporting.provenance.kind], [4, 4, "sample"]);
  assert.deepEqual([kpis.edge?.basis, kpis.edge?.p50, kpis.edge?.p95, kpis.edge?.n], ["summary", 167, 388, 4]);
  assert.deepEqual([kpis.cloud?.p50, kpis.cloud?.p95], [119, 442]);
  assert.deepEqual([kpis.fallback?.pct, kpis.fallback?.basis], [6.1, "summary"]);
  assert.deepEqual([kpis.interventions?.mean, kpis.interventions?.min, kpis.interventions?.max], [3.2, 1.2, 5.2]);
  assert.deepEqual([kpis.safety?.critical, kpis.safety?.major, kpis.safety?.minor, kpis.safety?.note], [0, 0, 3, "S4 dropped object"]);
});

test("KPIs: without a stored summary the robots are aggregated with their basis stated", () => {
  const doc = fresh();
  const config = { ...getConfiguration(doc, "hybrid")!, production: undefined };
  const kpis = productionKpis(config, robotRows(doc, config, {}, NOW));
  // Median of the per-robot p50 values (162, 214, 171, 169); a p95 cannot be pooled, so only its range is given.
  assert.deepEqual([kpis.edge?.basis, kpis.edge?.p50, kpis.edge?.p95, kpis.edge?.p95Range, kpis.edge?.n], ["robots", 170, null, [351, 498], 4]);
  assert.deepEqual([kpis.cloud?.p50, kpis.cloud?.p95Range], [126, [236, 1410]]);
  assert.equal(kpis.fallback?.basis, "robots");
  assert.equal(Math.round((kpis.fallback?.pct ?? 0) * 10) / 10, 6.1, "mean of 0.6, 3.9, 0.9 and 19 %");
  assert.deepEqual([kpis.interventions?.basis, kpis.interventions?.min, kpis.interventions?.max, kpis.interventions?.n], ["robots", 1.2, 5.2, 4]);
  assert.equal(Math.round((kpis.interventions?.mean ?? 0) * 100) / 100, 3.2);
  assert.equal(kpis.safety, null, "safety events exist only in a stored summary");
  assert.equal(kpis.window, "24 h", "the robots' common latency window");
  assert.equal(kpis.provenance.kind, "sample");
});

test("KPIs: a live-bound production robot is excluded from every aggregate and a new silent robot counts as not reporting", () => {
  const doc = fresh();
  doc.robots = doc.robots.map(robot => robot.id === "lab-bench" ? { ...robot, role: "production" as const, rev: "r3" }
    : robot.id === "unit-18" ? { ...robot, configId: "hybrid", role: "production" as const, rev: "r3" } : robot);
  const config = getConfiguration(doc, "hybrid")!;
  const kpis = productionKpis(config, robotRows(doc, config, { "lab-bench": binding(snapshot({ temp: 99.5 })) }, NOW));
  assert.deepEqual(ids(kpis.excluded), ["lab-bench"]);
  assert.ok(!ids(kpis.robots).includes("lab-bench"));
  assert.deepEqual([kpis.reporting.count, kpis.reporting.of], [4, 5], "Unit 18 has not reported yet");
  assert.equal(kpis.reporting.provenance.kind, "sample", "the measured robot's provenance is not part of the aggregate");
});

test("provenance of an aggregate is the weakest input", () => {
  assert.deepEqual(weakestProvenance([{ kind: "sample" }, { kind: "recorded", at: "2026-09-13T00:00:00Z" }]), { kind: "sample" });
  assert.deepEqual(weakestProvenance([{ kind: "recorded", at: "2026-09-14T00:00:00Z", n: 10, source: "Soak" }, { kind: "recorded", at: "2026-09-13T00:00:00Z", until: "2026-09-13T06:00:00Z", n: 5, source: "Soak" }]),
    { kind: "recorded", at: "2026-09-13T00:00:00Z", until: "2026-09-14T00:00:00Z", n: 15, source: "Soak" });
  assert.deepEqual(weakestProvenance([{ kind: "not-reported" }, null, undefined]), { kind: "not-reported" });
  assert.deepEqual(weakestProvenance([{ kind: "recorded", at: "2026-09-13T00:00:00Z" }, { kind: "not-reported" }]), { kind: "recorded", at: "2026-09-13T00:00:00Z" });
});

test("chart domains, reference lines and ticks", () => {
  assert.deepEqual(telemetryDomain([35.1, 61.2, 97.6], [90, 99]), [30, 105], "every SoC card shares 30–105 °C");
  assert.deepEqual(telemetryDomain([6.5, 24.1], [25], { zero: true }), [0, 30], "board input from 0 to 30 W");
  assert.deepEqual(telemetryDomain([50, 50], []), [45, 55]);
  assert.equal(telemetryDomain([null, undefined], []), null);
  assert.equal(sparkY(99, [30, 105]), 18.16);
  assert.equal(sparkY(30, [30, 105]), 66);
  assert.equal(sparkY(200, [30, 105]), 14, "clamped to the domain");
  assert.equal(sparkReferences([90, null, 99], [30, 105]), "M12,24.4H288M12,18.16H288");
  assert.equal(sparkZone(90, 99, [30, 105]), "M12,18.16H288V24.4H12Z");
  assert.equal(sparkZone(null, 99, [30, 105]), "");
  assert.deepEqual(niceTicks(442), [0, 200, 400, 600]);
  assert.deepEqual(niceTicks(760), [0, 200, 400, 600, 800]);
  assert.deepEqual(niceTicks(1410), [0, 500, 1000, 1500]);
  assert.deepEqual(niceTicks(9), [0, 2.5, 5, 7.5, 10]);
  assert.deepEqual(niceTicks(0), [0, 1]);
  const edge = hybrid.production!.latency.edge!;
  assert.deepEqual(latencyTicks(edge, NOW), ["09:00", "15:00", "21:00", "03:00", "Now"]);
  assert.deepEqual(latencyTicks(edge, NOW + 3 * 86_400_000), ["09:00", "15:00", "21:00", "03:00", "09:00"], "an older series ends on its own time, not on Now");
  assert.equal(seriesWindowLabel(edge), "Sep 30 09:00 – Oct 1 09:00 UTC");
  assert.deepEqual([cadenceLabel(3600), cadenceLabel(900), cadenceLabel(60), cadenceLabel(30)], ["hourly", "every 15 min", "per minute", "every 30 s"]);
});

test("promotion gate: the latest gated suite run of the revision decides", () => {
  const passed = revisionGate(ws, hybrid, "r4");
  assert.deepEqual([passed.passed, passed.run?.number, passed.reason], [true, 23, "r4 passed gate on Run 23"]);
  assert.deepEqual(passed.lines[0], { label: "Overall success", target: "≥ 70 %", actual: "78.1 %", passed: true });
  assert.equal(revisionGate(ws, hybrid, "r3").run?.number, 22);
  const below = revisionGate(ws, getConfiguration(ws, "cloud-only")!, "r2");
  assert.equal(below.passed, false);
  assert.equal(below.reason, "r2 is below gate on Run 21 (Each slice family: Network 48.3 %, needs ≥ 55 %)");
  const none = revisionGate(ws, getConfiguration(ws, "cloud-only")!, "r1");
  assert.deepEqual([none.passed, none.run, none.reason], [false, null, "r1 has no gated suite run yet"]);
  assert.equal(revisionGate(ws, getConfiguration(ws, "edge-only")!, "r1").passed, false, "a timing run is not a gate decision");
});

test("attention banner: needs attention first, warnings apart, one leading reason per robot", () => {
  const summary = attentionSummary(attentionRobots(ws, "hybrid", {}, NOW));
  assert.deepEqual(summary.attention.map(line => [line.robot.id, line.label, line.more]), [["unit-08", "Near thermal throttle", 0], ["unit-16", "Cloud link degraded", 0]]);
  assert.deepEqual(summary.warning.map(line => [line.robot.id, line.label]), [["unit-13", "Power peaks"]]);
  assert.equal(summary.attention[0].detail, "Jetson SoC 97.6 °C, at or above the 97 °C attention rule (software throttle at 99 °C)");
  const hot = attentionSummary(attentionRobots(ws, "hybrid", { "lab-bench": binding(snapshot({ temp: 99.4 })) }, NOW));
  assert.equal(hot.attention[0].robot.id, "lab-bench", "a measured flag joins the banner");
  assert.equal(hot.attention[0].provenance.kind, "measured");
  // A robot whose stored health needs attention without a flag is listed with its health reason.
  const declared: ConvoyWorkspace = { ...ws, robots: ws.robots.map(robot => robot.id === "unit-02" ? { ...robot, health: "attention", healthReason: "Bin sensor misread" } : robot) };
  const line = attentionSummary(attentionRobots(declared, "hybrid", {}, NOW)).attention.find(item => item.robot.id === "unit-02")!;
  assert.deepEqual([line.label, line.detail, line.more], ["Bin sensor misread", "Bin sensor misread", 0]);
});

test("safety checks caption: the scope once, then the envelope's own note", () => {
  assert.equal(safetyCaption(undefined), "Checked in every evaluation episode");
  assert.equal(safetyCaption("  "), "Checked in every evaluation episode");
  assert.equal(safetyCaption("Enforced by the robot's safety PLC"), "Checked in every evaluation episode · Enforced by the robot's safety PLC");
  assert.equal(safetyCaption("Checked in every evaluation episode. S5 is enforced by the safety MCU."), "Checked in every evaluation episode. S5 is enforced by the safety MCU.", "a note that states the scope is not repeated");
  assert.equal(safetyCaption(hybrid.revisions[0].safety.note), hybrid.revisions[0].safety.note, "the sample's note already states the scope");
});

test("log filters: level, source and robot combine", () => {
  const lines = logsFor(ws, { configId: "hybrid" });
  const facets = logFacets(lines);
  assert.deepEqual(facets.levels, { all: 13, info: 6, warn: 6, error: 1 });
  assert.deepEqual(facets.sources, ["agent", "cloud", "dataset", "planner", "router", "verifier"]);
  assert.equal(filterLogs(lines, ALL_LOGS).length, 13);
  assert.deepEqual(filterLogs(lines, { ...ALL_LOGS, level: "error" }).map(line => line.id), ["log-04"]);
  assert.deepEqual(filterLogs(lines, { ...ALL_LOGS, robotId: "unit-08" }).map(line => line.id), ["log-03", "log-04"]);
  assert.deepEqual(filterLogs(lines, { level: "warn", source: "router", robotId: null }).map(line => line.id), ["log-01", "log-09"]);
  assert.deepEqual(filterLogs(lines, { level: "info", source: "router", robotId: "unit-08" }), []);
});
