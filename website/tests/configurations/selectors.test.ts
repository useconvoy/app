import test from "node:test";
import assert from "node:assert/strict";
import {
  attentionRobots, configurationCounts, configurationSummary, currentRevision, displayHealth, evaluateFlagRules, getRevision, healthSeverity, latestGateRun, listConfigurations,
  logsFor, nextRevision, recentActivity, resolveRobotRoute, resolveRunRoute, robotHref, robotReadings, robotsFor, rolloutsFor, runHref, runsFor,
  latencyRows, sliceInfo, spansForGroup, timeTicks, tracesFor, unassignedRobots,
} from "../../src/lib/configurations/selectors";
import type { ActiveFlag } from "../../src/lib/configurations/selectors";
import { flagRobot } from "../../src/lib/configurations/mutations";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";
import { fmtCi, fmtDateRange, fmtDateTime, fmtPct, fmtRelative, fmtUnit, fmtUpdated, median, percentile, provenanceLabel, wilson } from "../../src/lib/configurations/format";
import { mapSnapshot } from "../../src/lib/configurations/live";
import type { LiveBinding } from "../../src/lib/configurations/live";
import { routes } from "../../src/lib/configurations/routes";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import type { PortalSnapshot } from "../../src/lib/portal/types";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const ws = createSampleWorkspace(NOW);

function liveSnapshot(temp: number | null, online = true): PortalSnapshot {
  const at = (s: number) => new Date(NOW - s * 1000).toISOString();
  const sample = (s: number) => ({ ts: at(s), cpu_pct: 20, gpu_pct: 9, mem_total_mb: 7620, mem_available_mb: 3500, power_w: 8.1, temp_max_c: temp, disk_free_mb: 1, runtime_state: "running", clock_confidence: "unknown" });
  return {
    fetched_at: at(0), telemetry_stale_after_s: 90, heartbeat_interval_s: 15,
    device: { id: "dev_lab", name: "Lab Jetson", status: "online", live_at: at(5), observed_at: at(5), observed_health: "ok", observed_stage: "ready", agent_version: "1", observed_active_release_id: null, gateway_mode: "production", runtime_state: "running" },
    release: null, telemetry: [sample(30), sample(15), sample(0)], latest_telemetry: sample(0), usage: { from: "2026-09-01", to: "2026-09-30", metrics: null },
    recent_inference: [], chat: { eligible: online, online, reason: null, release_id: null, max_tokens: 128, context_window: null },
  };
}
const binding = (snapshot: PortalSnapshot): LiveBinding => ({ deviceKey: "configured-device", status: "fresh", data: mapSnapshot(snapshot), error: null, receivedAt: 0 });

test("configurations: search all words, filter by status, sort", () => {
  assert.deepEqual(listConfigurations(ws).map(config => config.id), ["hybrid", "cloud-only", "edge-only"]);
  assert.deepEqual(listConfigurations(ws, { sort: "name" }).map(config => config.id), ["cloud-only", "edge-only", "hybrid"]);
  assert.deepEqual(listConfigurations(ws, { sort: "eval" }).map(config => config.id), ["hybrid", "cloud-only", "edge-only"]);
  assert.deepEqual(listConfigurations(ws, { status: "draft" }).map(config => config.id), ["edge-only"]);
  assert.deepEqual(listConfigurations(ws, { status: "testing" }).map(config => config.id), ["hybrid", "cloud-only"]);
  assert.deepEqual(listConfigurations(ws, { query: "unit 16" }).map(config => config.id), ["hybrid"]);
  assert.deepEqual(listConfigurations(ws, { query: "VLM verifier" }).map(config => config.id), ["hybrid"]);
  assert.deepEqual(listConfigurations(ws, { query: "jetson cloud" }).map(config => config.id), ["hybrid", "cloud-only"]);
  assert.deepEqual(listConfigurations(ws, { query: "nothing like this" }), []);
  assert.deepEqual(configurationCounts(ws), { all: 3, testing: 2, production: 1, draft: 1 });
  const hybrid = ws.configurations[0];
  assert.equal(currentRevision(hybrid).rev, "r4");
  assert.equal(nextRevision(hybrid), "r5");
});

test("robots: bound first, test before production; unassigned listed separately", () => {
  assert.deepEqual(robotsFor(ws, "hybrid").map(robot => robot.id), ["lab-bench", "unit-02", "unit-07", "unit-08", "unit-13", "unit-16"]);
  assert.deepEqual(robotsFor(ws, "hybrid", { role: "test" }).map(robot => robot.id), ["lab-bench", "unit-02"]);
  assert.deepEqual(unassignedRobots(ws).map(robot => robot.id), ["unit-18"]);
  assert.equal(robotHref(ws.robots.find(robot => robot.id === "unit-18")!), null);
});

test("routes resolve only along the configuration → robot → run hierarchy", () => {
  assert.equal(resolveRobotRoute(ws, "hybrid", "unit-08")?.robot.name, "Unit 08");
  assert.equal(resolveRobotRoute(ws, "cloud-only", "unit-08"), null);
  const run = resolveRunRoute(ws, "hybrid", "lab-bench", "run-21");
  assert.equal(run?.run.number, 21);
  assert.equal(run?.runConfiguration?.id, "cloud-only", "a run can belong to another configuration than its robot");
  assert.equal(resolveRunRoute(ws, "hybrid", "unit-02", "run-21"), null);
  assert.equal(runHref(ws, run!.run), "/app/configurations/hybrid/robots/lab-bench/evals/run-21");
  assert.equal(routes.robot("hybrid", "unit-07", { trace: "trc_1" }), "/app/configurations/hybrid/robots/unit-07?trace=trc_1");
  assert.equal(routes.newConfiguration(null), "/app/configurations/new");
});

test("runs: running and queued first, newest first; rollouts, traces, logs and activity filter", () => {
  assert.deepEqual(runsFor(ws, { robotId: "lab-bench" }).map(run => run.number), [24, 25, 23, 22, 21, 20, 18, 19]);
  assert.equal(latestGateRun(ws, "hybrid")?.number, 23);
  assert.equal(latestGateRun(ws, "hybrid", "r3")?.number, 22);
  assert.equal(latestGateRun(ws, "edge-only"), null);
  assert.equal(rolloutsFor(ws, "run-23").length, 12);
  assert.deepEqual(rolloutsFor(ws, "run-23", { outcome: "safety" }).map(rollout => rollout.id), ["ep-23-012", "ep-23-058", "ep-23-095"]);
  assert.equal(rolloutsFor(ws, "run-23", { outcome: "failed" }).every(rollout => rollout.outcome !== "succeeded"), true);
  assert.deepEqual(rolloutsFor(ws, "run-23", { sliceId: "link-drop" }).map(rollout => rollout.id), ["ep-23-007", "ep-23-095"]);
  assert.deepEqual(sliceInfo(ws.suites[0], "link-drop"), { name: "Link drop mid-task", family: "Network" });
  const traces = tracesFor(ws, "unit-07");
  assert.equal(traces.length, 12);
  assert.ok(traces.every((trace, i) => i === 0 || Date.parse(traces[i - 1].at) >= Date.parse(trace.at)));
  assert.equal(tracesFor(ws, "unit-07", "escalated").length, 1);
  assert.equal(tracesFor(ws, "unit-07", "fallback").length, 1);
  assert.equal(tracesFor(ws, "unit-07", "failed").length, 1);
  assert.ok(logsFor(ws, { configId: "hybrid" }).every(line => line.configId === "hybrid"));
  assert.equal(logsFor(ws, { robotId: "unit-08" }).length, 2);
  assert.equal(logsFor(ws, {}, 3).length, 3);
  assert.equal(recentActivity(ws, { configId: "cloud-only" }).length, 1);
});

test("readings: stored sample values for unbound robots; measured values only from a live binding", () => {
  const hybrid = ws.configurations[0];
  const hotRobot = ws.robots.find(robot => robot.id === "unit-08")!;
  const stored = robotReadings(hotRobot, getRevision(hybrid, hotRobot.rev), null, NOW);
  assert.equal(stored.provenance.kind, "sample");
  assert.equal(stored.health, "attention");
  assert.equal(stored.day.socTempC?.length, 25);
  const bench = ws.robots.find(robot => robot.id === "lab-bench")!;
  const waiting = robotReadings(bench, getRevision(hybrid, bench.rev), { deviceKey: "configured-device", status: "loading", data: null, error: null, receivedAt: null }, NOW);
  assert.equal(waiting.provenance.kind, "not-reported");
  assert.equal(waiting.health, "not-reported");
  assert.equal(waiting.latest, null, "sample values never stand in for a bound robot");
  assert.equal(waiting.edgeProvenance.kind, "recorded", "the recorded soak stays labelled as recorded");
  const live = robotReadings(bench, getRevision(hybrid, bench.rev), binding(liveSnapshot(46)), NOW);
  assert.equal(live.provenance.kind, "measured");
  assert.equal(live.latest?.socTempC, 46);
  assert.equal(live.recent.socTempC?.length, 3);
  assert.equal(live.health, "healthy");
  const hot = robotReadings(bench, getRevision(hybrid, bench.rev), binding(liveSnapshot(99.4)), NOW);
  assert.equal(hot.health, "attention");
  assert.equal(hot.flags[0].label, "Thermal throttling", "at or above the device's software throttle point");
  assert.equal(hot.flags[0].provenance.kind, "measured");
  const warm = robotReadings(bench, getRevision(hybrid, bench.rev), binding(liveSnapshot(97.6)), NOW);
  assert.deepEqual([warm.health, warm.flags[0].label, warm.flags[0].detail], ["attention", "Near thermal throttle", "Jetson SoC 97.6 °C, at or above the 97 °C attention rule (software throttle at 99 °C)"]);
  const offline = robotReadings(bench, getRevision(hybrid, bench.rev), binding(liveSnapshot(46, false)), NOW);
  assert.deepEqual([offline.health, offline.baseHealth, offline.healthReason], ["offline", "offline", "No recent live contact"]);
  assert.deepEqual([hot.baseHealth, hot.healthFlag?.label], ["healthy", "Thermal throttling"], "the device is healthy; the flag sets the shown health");
});

test("flag rules and the robots that need attention, attention first", () => {
  const rules = currentRevision(ws.configurations[0]).flagRules;
  const reading = { at: new Date(NOW).toISOString(), cpuPct: 50, gpuPct: 50, memAvailableMiB: 500, memTotalMiB: 7620, socTempC: 93, boardPowerW: 24 };
  const flags = evaluateFlagRules(reading, rules, { capW: 25, fallbackPct: 18, lastSeenAt: new Date(NOW - 120_000).toISOString(), now: NOW, provenance: { kind: "measured" }, idPrefix: "x" });
  assert.deepEqual(flags.map(flag => [flag.rule, flag.severity]), [["soc-temp", "warning"], ["board-power", "warning"], ["memory", "warning"], ["fallback", "attention"], ["no-report", "attention"]]);
  assert.deepEqual(evaluateFlagRules(null, null, { provenance: { kind: "measured" }, idPrefix: "x" }), []);
  assert.deepEqual(attentionRobots(ws, "hybrid").map(entry => [entry.robot.id, entry.severity, entry.label]), [["unit-08", "attention", "Near thermal throttle"], ["unit-16", "attention", "Cloud link degraded"], ["unit-13", "warning", "Power peaks"]]);
  const bench = ws.robots.find(robot => robot.id === "lab-bench")!;
  const hot = attentionRobots(ws, "hybrid", { [bench.id]: binding(liveSnapshot(99.6)) }, NOW);
  assert.deepEqual(hot.map(entry => entry.robot.id), ["lab-bench", "unit-08", "unit-16", "unit-13"]);
  assert.deepEqual([hot[0].detail, hot[0].provenance.kind], ["Jetson SoC 99.6 °C, at or above the 99 °C software throttle point", "measured"]);
  assert.deepEqual(attentionRobots(ws).map(entry => entry.robot.id), ["unit-08", "unit-16", "unit-13"], "every configuration; unattached robots are left out");
});

const flag = (severity: ActiveFlag["severity"], label: string): ActiveFlag => ({ id: label, rule: "manual", severity, label, detail: `${label}.`, at: new Date(NOW).toISOString(), provenance: { kind: "recorded" }, origin: "stored" });

test("displayed health: one precedence for every page — attention, offline, degraded, then the base health", () => {
  assert.deepEqual(displayHealth({ health: "healthy", reason: null }, []), { health: "healthy", reason: null, flag: null });
  assert.deepEqual(displayHealth({ health: "healthy", reason: null }, [flag("warning", "Loose cable")]).health, "degraded");
  assert.deepEqual(displayHealth({ health: "not-reported", reason: "Waiting" }, [flag("warning", "Loose cable")]).reason, "Loose cable");
  assert.deepEqual(displayHealth({ health: "degraded", reason: "Power peaks" }, [flag("warning", "Other")]).reason, "Power peaks", "a stored reason stays");
  const attention = displayHealth({ health: "healthy", reason: null }, [flag("warning", "Loose cable"), flag("attention", "Gripper noise")]);
  assert.deepEqual([attention.health, attention.reason, attention.flag?.label], ["attention", "Gripper noise", "Gripper noise"]);
  assert.deepEqual(displayHealth({ health: "offline", reason: "No recent live contact" }, [flag("attention", "No recent report")]).health, "attention", "Needs attention outranks Offline");
  assert.deepEqual(displayHealth({ health: "offline", reason: "No recent live contact" }, [flag("warning", "Hot SoC")]), { health: "offline", reason: "No recent live contact", flag: null }, "a warning does not hide Offline");
  assert.deepEqual(displayHealth({ health: "attention", reason: "Declared" }, [flag("warning", "Hot SoC")]), { health: "attention", reason: "Declared", flag: null });
  assert.deepEqual(["attention", "degraded", "healthy", "offline", "not-reported"].map(health => healthSeverity(health as Parameters<typeof healthSeverity>[0])), ["attention", "warning", null, null, null]);
});

test("a manual flag changes the shown health, the attention list and the counts together", () => {
  const hybridOf = (workspace: ConvoyWorkspace) => workspace.configurations.find(config => config.id === "hybrid")!;
  const shown = (workspace: ConvoyWorkspace, id: string) => { const robot = workspace.robots.find(item => item.id === id)!; return robotReadings(robot, getRevision(hybridOf(workspace), robot.rev), null, NOW); };
  const flagged = flagRobot(ws, "unit-07", { label: "Gripper slipping", note: "Slipped twice on bin 4", severity: "attention", by: "operator@example.test" }, NOW);
  const unit07 = shown(flagged, "unit-07");
  assert.deepEqual([unit07.health, unit07.baseHealth, unit07.healthReason, unit07.healthFlag?.origin], ["attention", "healthy", "Gripper slipping", "stored"]);
  assert.deepEqual(attentionRobots(flagged, "hybrid").filter(entry => entry.severity === "attention").map(entry => entry.robot.id), ["unit-07", "unit-08", "unit-16"]);
  assert.equal(configurationSummary(flagged, hybridOf(flagged)).attention, 3);
  const warned = flagRobot(ws, "unit-02", { label: "Camera smudge", note: "Wrist camera needs cleaning" }, NOW);
  assert.equal(shown(warned, "unit-02").health, "degraded");
  assert.deepEqual(attentionRobots(warned, "hybrid").filter(entry => entry.severity === "warning").map(entry => [entry.robot.id, entry.label]), [["unit-02", "Camera smudge"], ["unit-13", "Power peaks"]]);
  // Every robot: the attention list holds exactly the robots whose shown health is Needs attention or Degraded.
  for (const workspace of [ws, flagged, warned]) {
    const listed = new Map(attentionRobots(workspace).map(entry => [entry.robot.id, entry.severity]));
    for (const robot of workspace.robots.filter(item => item.configId)) {
      const config = workspace.configurations.find(item => item.id === robot.configId)!;
      assert.equal(listed.get(robot.id) ?? null, healthSeverity(robotReadings(robot, getRevision(config, robot.rev), null, NOW).health), robot.id);
    }
  }
});

test("'no recent report' is judged on the server's clock, not the browser's", () => {
  const bench = ws.robots.find(robot => robot.id === "lab-bench")!;
  const revision = getRevision(ws.configurations[0], bench.rev);
  const ahead = 10 * 60_000; // this browser's clock runs 10 minutes fast
  const received = (data: ReturnType<typeof mapSnapshot>): LiveBinding => ({ deviceKey: "configured-device", status: "fresh", data, error: null, receivedAt: 0 });
  const offset = robotReadings(bench, revision, received(mapSnapshot(liveSnapshot(46), null, NOW + ahead)), NOW + ahead);
  assert.deepEqual([offset.health, offset.flags.map(flag => flag.rule)], ["healthy", []], "the device reported 5 s ago on the server's clock");
  const naive = robotReadings(bench, revision, received(mapSnapshot(liveSnapshot(46))), NOW + ahead);
  assert.deepEqual(naive.flags.map(flag => flag.rule), ["no-report"], "without the offset the fast browser clock would flag it");
});

test("a bound robot that stops reporting is flagged and needs attention; its base health stays offline", () => {
  const bench = ws.robots.find(robot => robot.id === "lab-bench")!;
  const silent = liveSnapshot(46, false);
  silent.device.live_at = new Date(NOW - 10 * 60_000).toISOString();
  const readings = robotReadings(bench, getRevision(ws.configurations[0], bench.rev), binding(silent), NOW);
  assert.deepEqual([readings.health, readings.healthReason, readings.baseHealth, readings.baseHealthReason], ["attention", "No recent report", "offline", "No recent live contact"]);
  assert.equal(attentionRobots(ws, "hybrid", { "lab-bench": binding(silent) }, NOW)[0].robot.id, "lab-bench");
});

test("a device's own health: ok is Healthy, unknown or missing is Not reported without a warning, failed is Degraded", () => {
  const bench = ws.robots.find(robot => robot.id === "lab-bench")!;
  const revision = getRevision(ws.configurations[0], bench.rev);
  const live = (health: string | null) => {
    const snapshot = liveSnapshot(46);
    (snapshot.device as { observed_health: string | null }).observed_health = health;
    return { "lab-bench": binding(snapshot) };
  };
  const shown = (health: string | null) => {
    const readings = robotReadings(bench, revision, live(health)["lab-bench"], NOW);
    return [readings.health, readings.healthReason];
  };
  assert.deepEqual(shown("ok"), ["healthy", null]);
  assert.deepEqual(shown("unknown"), ["not-reported", "Device health: unknown"]);
  assert.deepEqual(shown(null), ["not-reported", "Device health not reported"]);
  assert.deepEqual(shown("failed"), ["degraded", "Device health: failed"]);
  const listed = (health: string | null) => attentionRobots(ws, "hybrid", live(health), NOW).some(entry => entry.robot.id === "lab-bench");
  assert.equal(listed("unknown"), false, "an unknown device health is not a warning");
  assert.equal(listed("failed"), true);
});

test("configuration summary counts roles, attention and the latest gated run", () => {
  const summary = configurationSummary(ws, ws.configurations[0]);
  assert.deepEqual(summary.robots, { test: 2, production: 4, total: 6 });
  assert.equal(summary.attention, 2);
  assert.equal(summary.degraded, 1);
  assert.equal(summary.latestRun?.number, 23);
  assert.equal(summary.activeRun?.number, 24);
  assert.equal(configurationSummary(ws, ws.configurations[2]).latestRun, null);
});

test("formatting keeps units, dates, provenance and statistics in house style", () => {
  assert.equal(fmtUnit(46, "°C", 1, true), "46.0 °C");
  assert.equal(fmtUnit(null, "°C"), "Not reported");
  assert.equal(fmtUnit(0, "W"), "0 W", "a reported zero stays zero");
  assert.equal(fmtPct(27, 0), "27 %");
  assert.equal(fmtCi([0.735, 0.82]), "73.5–82.0 %");
  assert.equal(fmtDateTime("2026-10-01T09:41:20Z"), "Oct 1, 09:41:20 UTC");
  assert.equal(fmtDateTime("2026-10-01T18:25:40Z", { zone: "PDT", utcOffsetMinutes: -420 }), "Oct 1, 11:25:40 PDT");
  assert.equal(fmtDateRange("2026-09-13T08:00:00Z", "2026-09-14T01:00:00Z"), "Sep 13–14");
  assert.equal(fmtDateRange("2026-09-30T08:00:00Z", "2026-10-01T01:00:00Z"), "Sep 30 – Oct 1");
  assert.equal(fmtRelative(new Date(NOW - 9000).toISOString(), NOW), "9s ago");
  assert.equal(fmtRelative(new Date(NOW - 12 * 60000).toISOString(), NOW), "12 min ago");
  assert.equal(fmtUpdated("2026-10-01T09:41:20Z"), "Updated Oct 1, 09:41:20 UTC · Refreshes every 15 seconds");
  assert.equal(provenanceLabel({ kind: "measured", at: new Date(NOW - 9000).toISOString() }, NOW), "Measured · 9s ago");
  assert.equal(provenanceLabel({ kind: "recorded", at: "2026-09-13T08:07:00Z", until: "2026-09-14T01:35:08Z" }), "Recorded · Sep 13–14");
  assert.equal(provenanceLabel({ kind: "sample" }), "Sample");
  assert.equal(provenanceLabel({ kind: "not-reported" }), "Not reported");
  assert.equal(median([3, null, 1, 2]), 2);
  assert.equal(percentile([1, 2, 3, 4, 100], 0.95), 100, "nearest rank: p95 of a small sample can be the maximum");
  const [low, high] = wilson(281, 360)!;
  assert.ok(Math.abs(low - 0.7349) < 0.001 && Math.abs(high - 0.8202) < 0.001);
  assert.equal(wilson(1, 0), null);
});

test("chart helpers: trace waterfalls by group, latency rows with fallback events, short time ticks", () => {
  const escalated = tracesFor(ws, "unit-07", "escalated")[0];
  assert.deepEqual(spansForGroup(escalated).map(span => span.id), ["capture", "plan", "chunk-1", "verify", "attempt", "escalate"]);
  assert.deepEqual(spansForGroup(escalated, "recovery").map(span => span.id), ["hold", "teleop", "dataset", "resume"]);
  const plain = tracesFor(ws, "unit-07")[0];
  assert.equal(spansForGroup(plain).length, plain.spans.length);
  const rows = latencyRows(ws.configurations[0].production!.latency.cloud);
  assert.equal(rows.length, 25);
  assert.equal(rows.filter(row => row.event).length, 6);
  assert.deepEqual(latencyRows(null), []);
  assert.deepEqual(timeTicks("2026-09-30T10:00:00Z", 3600, 25), ["10:00", "16:00", "22:00", "04:00", "Now"]);
  assert.deepEqual(timeTicks("2026-09-30T10:00:00Z", 3600, 25, 3, null, -420), ["03:00", "15:00", "03:00"]);
});
