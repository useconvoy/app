import test from "node:test";
import assert from "node:assert/strict";
import {
  attentionRobots, currentRevision, displayHealth, evaluateFlagRules, getRevision, healthSeverity, listConfigurations, resolveRobotRoute, robotReadings, robotsFor, tracesFor,
} from "../../src/lib/configurations/selectors";
import type { ActiveFlag } from "../../src/lib/configurations/selectors";
import type { ConvoyWorkspace, Flag } from "../../src/lib/configurations/types";
import { fmtDate, fmtPct, fmtUnit, fmtWhen, median, percentile, wilson } from "../../src/lib/configurations/format";
import { mapSnapshot } from "../../src/lib/configurations/live";
import type { LiveBinding } from "../../src/lib/configurations/live";
import { routes } from "../../src/lib/configurations/routes";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { robotStatus, robotType } from "../../src/lib/configurations/status";
import type { PortalSnapshot } from "../../src/lib/portal/types";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const ws = createSampleWorkspace(NOW);
const bench = ws.robots.find(robot => robot.id === "bench-01")!;
const edge = ws.configurations.find(config => config.id === "edge-planner")!;
const revision = getRevision(edge, bench.rev);

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
const waiting: LiveBinding = { deviceKey: "configured-device", status: "loading", data: null, error: null, receivedAt: null };

test("configurations in the order they were created; robots live-bound first; routes along the hierarchy", () => {
  assert.deepEqual(listConfigurations(ws).map(config => config.id), ["edge-planner", "cloud-planner", "edge-vla"]);
  assert.equal(currentRevision(edge).rev, "r1");
  assert.deepEqual(robotsFor(ws, "edge-planner").map(robot => robot.id), ["bench-01"]);
  assert.deepEqual(ws.robots.filter(robot => robot.deviceId).map(robot => robot.id), ["bench-01"]);
  assert.equal(resolveRobotRoute(ws, "edge-vla", "sim-01")?.robot.name, "Sim 01");
  assert.equal(resolveRobotRoute(ws, "edge-planner", "sim-01"), null, "a robot resolves only under its own configuration");
  assert.deepEqual(tracesFor(ws, "bench-01"), []);
  assert.equal(routes.run("edge-vla", "sim-01", "eva_contract01", "epi_contract01"), "/app/configurations/edge-vla/robots/sim-01/evals/eva_contract01?rollout=epi_contract01");
  assert.equal(routes.robot("edge-vla", "sim-01", "details"), "/app/configurations/edge-vla/robots/sim-01?tab=details");
  assert.equal(routes.newConfiguration(), "/app/configurations/new");
});

test("readings: measured values only from a live binding, never stored ones", () => {
  const before = robotReadings(bench, revision, waiting, NOW);
  assert.deepEqual([before.provenance.kind, before.health, before.latest], ["not-reported", "not-reported", null]);
  const live = robotReadings(bench, revision, binding(liveSnapshot(46)), NOW);
  assert.deepEqual([live.provenance.kind, live.latest?.socTempC, live.recent.socTempC?.length, live.health], ["measured", 46, 3, "healthy"]);
  const hot = robotReadings(bench, revision, binding(liveSnapshot(99.4)), NOW);
  assert.deepEqual([hot.health, hot.flags[0].label, hot.flags[0].provenance.kind, hot.baseHealth], ["attention", "Thermal throttling", "measured", "healthy"]);
  const warm = robotReadings(bench, revision, binding(liveSnapshot(97.6)), NOW);
  assert.deepEqual([warm.health, warm.flags[0].detail], ["attention", "Jetson SoC 97.6 °C, at or above the 97 °C attention rule (software throttle at 99 °C)"]);
  const offline = robotReadings(bench, revision, binding(liveSnapshot(46, false)), NOW);
  assert.deepEqual([offline.health, offline.healthReason], ["offline", "No recent live contact"]);
});

test("robot status: a live robot's health, else whether an eval runs on it", () => {
  const status = (live: LiveBinding | null) => robotStatus(bench, robotReadings(bench, revision, live, NOW), live, []);
  assert.equal(status(waiting), "connecting");
  assert.equal(status(binding(liveSnapshot(46))), "online");
  assert.equal(status(binding(liveSnapshot(99.4))), "attention");
  assert.equal(status(binding(liveSnapshot(46, false))), "offline");
  assert.equal(status({ ...waiting, status: "unavailable" }), "no-data");
  const sim = ws.robots.find(robot => robot.id === "sim-02")!;
  const simReadings = robotReadings(sim, null, null, NOW);
  assert.equal(robotStatus(sim, simReadings, null, [{ result: "running" }]), "running");
  assert.equal(robotStatus(sim, simReadings, null, [{ result: "passed" }]), "idle");
  assert.deepEqual([robotType(bench), robotType(sim)], ["Live device", "Simulator"]);
});

test("flag rules, and the robots that need attention", () => {
  const rules = currentRevision(edge).flagRules;
  const reading = { at: new Date(NOW).toISOString(), cpuPct: 50, gpuPct: 50, memAvailableMiB: 500, memTotalMiB: 7620, socTempC: 93, boardPowerW: 24 };
  const flags = evaluateFlagRules(reading, rules, { capW: 25, fallbackPct: 18, lastSeenAt: new Date(NOW - 120_000).toISOString(), now: NOW, provenance: { kind: "measured" }, idPrefix: "x" });
  assert.deepEqual(flags.map(flag => [flag.rule, flag.severity]), [["soc-temp", "warning"], ["board-power", "warning"], ["memory", "warning"], ["fallback", "attention"], ["no-report", "attention"]]);
  assert.deepEqual(evaluateFlagRules(null, null, { provenance: { kind: "measured" }, idPrefix: "x" }), []);
  assert.deepEqual(attentionRobots(ws), [], "nothing in the sample needs attention");
  const hot = attentionRobots(ws, "edge-planner", { "bench-01": binding(liveSnapshot(99.6)) }, NOW);
  assert.deepEqual(hot.map(entry => [entry.robot.id, entry.severity, entry.detail]), [["bench-01", "attention", "Jetson SoC 99.6 °C, at or above the 99 °C software throttle point"]]);
});

const flag = (severity: ActiveFlag["severity"], label: string): ActiveFlag => ({ id: label, rule: "manual", severity, label, detail: `${label}.`, at: new Date(NOW).toISOString(), provenance: { kind: "recorded" }, origin: "stored" });

test("displayed health: one precedence for every page — attention, offline, degraded, then the base health", () => {
  assert.deepEqual(displayHealth({ health: "healthy", reason: null }, []), { health: "healthy", reason: null, flag: null });
  assert.equal(displayHealth({ health: "healthy", reason: null }, [flag("warning", "Loose cable")]).health, "degraded");
  assert.equal(displayHealth({ health: "not-reported", reason: "Waiting" }, [flag("warning", "Loose cable")]).reason, "Loose cable");
  assert.equal(displayHealth({ health: "degraded", reason: "Power peaks" }, [flag("warning", "Other")]).reason, "Power peaks", "a stored reason stays");
  const attention = displayHealth({ health: "healthy", reason: null }, [flag("warning", "Loose cable"), flag("attention", "Gripper noise")]);
  assert.deepEqual([attention.health, attention.reason, attention.flag?.label], ["attention", "Gripper noise", "Gripper noise"]);
  assert.equal(displayHealth({ health: "offline", reason: "No recent live contact" }, [flag("attention", "No recent report")]).health, "attention", "Needs attention outranks Offline");
  assert.deepEqual(displayHealth({ health: "offline", reason: "No recent live contact" }, [flag("warning", "Hot SoC")]), { health: "offline", reason: "No recent live contact", flag: null }, "a warning does not hide Offline");
  assert.deepEqual(["attention", "degraded", "healthy", "offline", "not-reported"].map(health => healthSeverity(health as Parameters<typeof healthSeverity>[0])), ["attention", "warning", null, null, null]);
});

test("a stored flag changes the shown health and the attention list together", () => {
  const stored = (severity: Flag["severity"]): Flag => ({ id: `sim-02-${severity}`, rule: "manual", severity, label: "Gripper slipping", detail: "Slipped twice.", at: new Date(NOW).toISOString(), provenance: { kind: "recorded", at: new Date(NOW).toISOString() } });
  const flagged: ConvoyWorkspace = { ...ws, robots: ws.robots.map(robot => robot.id === "sim-02" ? { ...robot, flags: [stored("attention")] } : robot) };
  const sim = flagged.robots.find(robot => robot.id === "sim-02")!;
  const readings = robotReadings(sim, null, null, NOW);
  assert.deepEqual([readings.health, readings.healthReason, readings.healthFlag?.origin], ["attention", "Gripper slipping", "stored"]);
  assert.deepEqual(attentionRobots(flagged).map(entry => [entry.robot.id, entry.severity]), [["sim-02", "attention"]]);
  const warned: ConvoyWorkspace = { ...ws, robots: ws.robots.map(robot => robot.id === "sim-02" ? { ...robot, flags: [stored("warning")] } : robot) };
  assert.deepEqual(attentionRobots(warned).map(entry => [entry.robot.id, entry.severity, entry.label]), [["sim-02", "warning", "Gripper slipping"]]);
});

test("'no recent report' is judged on the server's clock, not the browser's", () => {
  const ahead = 10 * 60_000; // this browser's clock runs 10 minutes fast
  const received = (data: ReturnType<typeof mapSnapshot>): LiveBinding => ({ deviceKey: "configured-device", status: "fresh", data, error: null, receivedAt: 0 });
  const offset = robotReadings(bench, revision, received(mapSnapshot(liveSnapshot(46), null, NOW + ahead)), NOW + ahead);
  assert.deepEqual([offset.health, offset.flags.map(item => item.rule)], ["healthy", []], "the device reported 5 s ago on the server's clock");
  const naive = robotReadings(bench, revision, received(mapSnapshot(liveSnapshot(46))), NOW + ahead);
  assert.deepEqual(naive.flags.map(item => item.rule), ["no-report"], "without the offset the fast browser clock would flag it");
});

test("a device's own health: ok is Healthy, unknown or missing is Not reported without a warning, failed is Degraded", () => {
  const shown = (health: string | null) => {
    const snapshot = liveSnapshot(46);
    (snapshot.device as { observed_health: string | null }).observed_health = health;
    const readings = robotReadings(bench, revision, binding(snapshot), NOW);
    return [readings.health, readings.healthReason];
  };
  assert.deepEqual(shown("ok"), ["healthy", null]);
  assert.deepEqual(shown("unknown"), ["not-reported", "Device health: unknown"]);
  assert.deepEqual(shown(null), ["not-reported", "Device health not reported"]);
  assert.deepEqual(shown("failed"), ["degraded", "Device health: failed"]);
});

test("formatting keeps units, dates and statistics in house style", () => {
  assert.equal(fmtUnit(46, "°C", 1, true), "46.0 °C");
  assert.equal(fmtUnit(null, "°C"), "Not reported");
  assert.equal(fmtUnit(0, "W"), "0 W", "a reported zero stays zero");
  assert.equal(fmtPct(27, 0), "27 %");
  assert.equal(fmtWhen("2026-10-01T03:59:38Z"), "Oct 1, 03:59");
  assert.equal(fmtWhen(null), "Not reported");
  assert.equal(fmtWhen("2026-10-01T23:05:00Z"), "Oct 1, 23:05", "24-hour UTC");
  assert.equal(fmtDate("2026-09-14T06:29:51Z"), "Sep 14");
  assert.equal(fmtDate(undefined), "Not reported");
  assert.equal(median([3, null, 1, 2]), 2);
  assert.equal(percentile([1, 2, 3, 4, 100], 0.95), 100, "nearest rank: p95 of a small sample can be the maximum");
  const [low, high] = wilson(281, 360)!;
  assert.ok(Math.abs(low - 0.7349) < 0.001 && Math.abs(high - 0.8202) < 0.001);
  assert.equal(wilson(1, 0), null);
});
