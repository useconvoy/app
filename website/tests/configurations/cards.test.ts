import test from "node:test";
import assert from "node:assert/strict";
import { ACTIVITY_LABEL, activitySubject, attentionCounts, cardVerdict, noResultsTitle, parseFilter, parseSort, robotCountsLabel, stackRows } from "../../src/lib/configurations/cards";
import { mapSnapshot } from "../../src/lib/configurations/live";
import type { LiveBinding } from "../../src/lib/configurations/live";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { flaggedRobots } from "../../src/lib/configurations/selectors";
import type { PortalSnapshot } from "../../src/lib/portal/types";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const ws = createSampleWorkspace(NOW);
const config = (id: string) => ws.configurations.find(item => item.id === id)!;

function hotSnapshot(temp: number): PortalSnapshot {
  const at = (s: number) => new Date(NOW - s * 1000).toISOString();
  const sample = (s: number) => ({ ts: at(s), cpu_pct: 40, gpu_pct: 60, mem_total_mb: 7620, mem_available_mb: 3000, power_w: 9, temp_max_c: temp, disk_free_mb: 1, runtime_state: "running", clock_confidence: "unknown" });
  return {
    fetched_at: at(0), telemetry_stale_after_s: 90, heartbeat_interval_s: 15,
    device: { id: "dev_lab", name: "Lab Jetson", status: "online", live_at: at(5), observed_at: at(5), observed_health: "ok", observed_stage: "ready", agent_version: "1", observed_active_release_id: null, gateway_mode: "production", runtime_state: "running" },
    release: null, telemetry: [sample(15), sample(0)], latest_telemetry: sample(0), usage: { from: "2026-09-01", to: "2026-09-30", metrics: null },
    recent_inference: [], chat: { eligible: true, online: true, reason: null, release_id: null, max_tokens: 128, context_window: null },
  };
}

test("filters come from the URL and fall back to all / last activity", () => {
  assert.deepEqual([parseFilter("draft"), parseFilter("production"), parseFilter("bogus"), parseFilter(null)], ["draft", "production", "all", "all"]);
  assert.deepEqual([parseSort("eval"), parseSort("name"), parseSort("x"), parseSort(undefined)], ["eval", "name", "activity", "activity"]);
  assert.equal(noResultsTitle(" spot ", "all"), "No configurations match “spot”.");
  assert.equal(noResultsTitle("", "production"), "No configurations in production.");
  assert.equal(noResultsTitle("arm", "draft"), "No draft configurations match “arm”.");
});

test("a card describes the revision under test: robot, edge hardware with its power mode, edge and cloud models", () => {
  assert.deepEqual(stackRows(config("hybrid")), [
    { label: "Robot", value: "Bimanual mobile manipulator · 2 × 7-DOF arms · omnidirectional base" },
    { label: "Edge hardware", value: "Jetson Orin Nano Super 8 GB · 25 W mode" },
    { label: "Edge models", value: "Qwen2.5-1.5B planner · SmolVLA fallback · Skill pack (proposed)" },
    { label: "Cloud models", value: "Cloud VLA policy v3.1 · Cloud VLM verifier" },
  ]);
  assert.deepEqual(stackRows(config("edge-only")).slice(2).map(row => row.value), ["Qwen2.5-1.5B planner · SmolVLA policy", "None (edge only)"]);
});

test("the verdict line is the stored highlight, else the newest gate result with its provenance", () => {
  assert.deepEqual(cardVerdict(ws, config("hybrid")), config("hybrid").highlight);
  assert.deepEqual(cardVerdict(ws, { ...config("hybrid"), highlight: undefined }), { tone: "good", lead: "Passed gate.", text: "r4 passed the Bimanual station suite v1 gate in Run 23.", provenance: { kind: "sample" } });
  assert.deepEqual(cardVerdict(ws, { ...config("cloud-only"), highlight: undefined }), { tone: "warning", lead: "Below gate.", text: "r2 missed “Each slice family ≥ 55 %” in Run 21: Network 48.3 %.", provenance: { kind: "sample" } });
  assert.equal(cardVerdict(ws, { ...config("edge-only"), highlight: undefined }), null, "a timing run is not a gate result");
});

test("robot counts name a role's only robot", () => {
  assert.equal(robotCountsLabel(ws, "hybrid"), "Test 2 · Production 4");
  assert.equal(robotCountsLabel(ws, "cloud-only"), "Test 1 (Sim 01) · Production 0");
  assert.equal(robotCountsLabel(ws, "edge-only"), "Test 0 · Production 0");
});

test("the attention summary counts stored flags and rules raised on live readings", () => {
  const stored = attentionCounts(flaggedRobots(ws, "hybrid", {}, NOW));
  assert.deepEqual(stored, { attention: 2, warning: 1, attentionLabel: "2 need attention", warningLabel: "1 warning", detail: "Unit 08: Near thermal throttle · Unit 16: Cloud link degraded · Unit 13: Power peaks" });
  const live: Record<string, LiveBinding> = { "lab-bench": { deviceKey: "configured-device", status: "fresh", data: mapSnapshot(hotSnapshot(98.2)), error: null, receivedAt: 0 } };
  const measured = attentionCounts(flaggedRobots(ws, "hybrid", live, NOW));
  assert.deepEqual([measured.attention, measured.attentionLabel, measured.warning], [3, "3 need attention", 1]);
  assert.match(measured.detail, /^Lab bench: Near thermal throttle/);
  assert.deepEqual(attentionCounts(flaggedRobots(ws, "cloud-only", {}, NOW)), { attention: 0, warning: 0, attentionLabel: "0 need attention", warningLabel: "0 warnings", detail: "" });
});

test("activity subjects link to the robot, the run under its robot, or the configuration", () => {
  const event = (id: string) => ws.activity.find(item => item.id === id)!;
  assert.deepEqual(activitySubject(ws, event("act-01")), { label: "Unit 08", href: "/app/configurations/hybrid/robots/unit-08" });
  assert.deepEqual(activitySubject(ws, event("act-02")), { label: "Run 24", href: "/app/configurations/hybrid/robots/lab-bench/evals/run-24" });
  assert.deepEqual(activitySubject(ws, event("act-05")), { label: "Run 21", href: "/app/configurations/hybrid/robots/lab-bench/evals/run-21" });
  assert.deepEqual(activitySubject(ws, event("act-06")), { label: "Unit 18", href: null }, "an unattached robot has no page");
  assert.deepEqual(activitySubject(ws, event("act-07")), { label: "Bimanual station · Edge only", href: "/app/configurations/edge-only" });
  assert.deepEqual(activitySubject(ws, { subject: { type: "run", id: "run-404" } }), { label: "run-404", href: null });
  assert.deepEqual(Object.values(ACTIVITY_LABEL).filter(item => item.flag).map(item => item.label), ["Flagged"]);
});
