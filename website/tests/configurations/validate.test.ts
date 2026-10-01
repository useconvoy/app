import test from "node:test";
import assert from "node:assert/strict";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import type { ActionTrace, ConvoyWorkspace, RobotTelemetry } from "../../src/lib/configurations/types";
import { summarizeIssues, validateWorkspace } from "../../src/lib/configurations/validate";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const sample = () => createSampleWorkspace(NOW);
function issues(change: (ws: ConvoyWorkspace) => unknown): Array<{ path: string; message: string }> {
  const ws = sample();
  const value = change(ws) ?? ws;
  const result = validateWorkspace(value);
  assert.equal(result.ok, false, "expected a validation failure");
  return result.ok ? [] : result.issues;
}
const telemetry = (): RobotTelemetry => ({
  provenance: { kind: "sample" }, clock: "device",
  latest: { at: "2026-10-01T09:41:00Z", cpuPct: 20, gpuPct: 5, memAvailableMiB: 3000, memTotalMiB: 7620, socTempC: 50, boardPowerW: 9 },
});
const trace = (): ActionTrace => ({
  id: "trc_000000000001", robotId: "sim-02", at: "2026-10-01T09:00:00Z", clock: "device", instruction: "Pick the cube", decision: { kind: "skill", skill: "pick" },
  path: "cloud", route: "Cloud planner", plannerMs: 140, policyMs: null, outcome: "succeeded", durationS: 12, escalated: false,
  spans: [{ id: "plan", name: "Plan", path: "cloud", startMs: 0, durationMs: 140 }, { id: "act", name: "Act", path: "edge", startMs: 140, durationMs: 900, group: "main" }],
  spanGroups: [{ id: "main", title: "Main", unit: "ms" }], facts: [], provenance: { kind: "sample" },
});

test("non-objects and wrong schema versions fail at the root with clear messages", () => {
  for (const value of [null, [], "text", 4]) {
    const result = validateWorkspace(value);
    assert.equal(result.ok, false);
    assert.deepEqual(!result.ok && result.issues, [{ path: "", message: "expected an object" }]);
  }
  assert.deepEqual(issues(ws => ({ ...ws, schemaVersion: 2 })), [{ path: "schemaVersion", message: "expected 1; this website reads schema version 1 only" }]);
  const missing = validateWorkspace({ schemaVersion: 1 });
  assert.equal(missing.ok, false);
  assert.deepEqual(!missing.ok && missing.issues.map(issue => issue.path), ["meta", "configurations", "robots", "suites", "runs", "rollouts", "traces", "logs", "activity"]);
});

test("error paths point at the exact field", () => {
  assert.deepEqual(issues(ws => { ws.robots[1].telemetry = telemetry(); (ws.robots[1].telemetry.latest as unknown as Record<string, unknown>).socTempC = "hot"; }),
    [{ path: "robots[1].telemetry.latest.socTempC", message: "expected a finite number or null" }]);
  assert.deepEqual(issues(ws => { (ws.configurations[0].revisions[0].edgeModels[0] as unknown as Record<string, unknown>).state = "running"; }),
    [{ path: "configurations[0].revisions[0].edgeModels[0].state", message: "expected one of \"active\", \"fallback-only\", \"proposed\", \"blocked\", \"not-deployed\"" }]);
  assert.deepEqual(issues(ws => { ws.runs[2].successCi95 = [0.9, 0.2]; }), [{ path: "runs[2].successCi95", message: "expected low ≤ high" }]);
  assert.deepEqual(issues(ws => { const item = trace(); item.spans[1].group = "nowhere"; ws.traces = [item]; }), [{ path: "traces[0].spans[1].group", message: "\"nowhere\" is not one of spanGroups[].id" }]);
  assert.deepEqual(issues(ws => { delete (ws.suites[0] as unknown as Record<string, unknown>).gate; }), [{ path: "suites[0].gate", message: "is required" }]);
});

test("a robot's offline evaluations are control-plane ids, each listed once", () => {
  const linked = sample();
  linked.robots[2].offlineEvaluationIds = ["oev_contract0001", "oev_contract0002"];
  assert.equal(validateWorkspace(linked).ok, true);
  assert.deepEqual(issues(ws => { ws.robots[2].offlineEvaluationIds = ["oev_contract0001", "oev_contract0001"]; }),
    [{ path: "robots[2].offlineEvaluationIds", message: "lists an offline evaluation twice" }]);
  assert.deepEqual(issues(ws => { ws.robots[2].offlineEvaluationIds = ["eva_contract0001", "oev_CONTRACT0001", "../x"]; }).map(issue => issue.path),
    ["robots[2].offlineEvaluationIds[0]", "robots[2].offlineEvaluationIds[1]", "robots[2].offlineEvaluationIds[2]"]);
  assert.deepEqual(issues(ws => { (ws.robots[2] as unknown as Record<string, unknown>).offlineEvaluationIds = "oev_contract0001"; }),
    [{ path: "robots[2].offlineEvaluationIds", message: "expected an array" }]);
  assert.deepEqual(issues(ws => { ws.robots[2].offlineEvaluationIds = Array.from({ length: 101 }, (_, i) => `oev_${String(i).padStart(12, "0")}`); }),
    [{ path: "robots[2].offlineEvaluationIds", message: "expected at most 100 items" }]);
});

test("measured values are never stored", () => {
  const found = issues(ws => { ws.robots[1].telemetry = telemetry(); (ws.robots[1].telemetry.provenance as { kind: string }).kind = "measured"; });
  assert.equal(found[0].path, "robots[1].telemetry.provenance.kind");
  assert.match(found[0].message, /never stored/);
  assert.deepEqual(issues(ws => { ws.robots[0].telemetry = telemetry(); }),
    [{ path: "robots[0].telemetry", message: "a robot with a live device binding gets telemetry from the device and must not store it" }]);
  assert.deepEqual(issues(ws => { ws.runs[2].provenance = { kind: "recorded" }; }), [{ path: "runs[2].provenance.at", message: "recorded evidence needs a date" }]);
});

test("platform links: a project and a platform robot are ids, and a platform robot needs its project", () => {
  const linked = sample();
  linked.robots[2].projectId = "prj_contract01";
  linked.robots[2].platformRobotId = "rob_contract01";
  linked.runs[2].recordedEvaluationId = "eva_contract01";
  linked.rollouts[0].episodeId = "epi_contract01";
  const result = validateWorkspace(linked);
  assert.equal(result.ok, true, result.ok ? "" : JSON.stringify(result.issues));
  assert.deepEqual(result.ok && result.warnings, []);
  assert.deepEqual(issues(ws => { ws.robots[2].platformRobotId = "rob_contract01"; }), [{ path: "robots[2].platformRobotId", message: "needs the projectId it belongs to" }]);
  assert.deepEqual(issues(ws => { ws.robots[2].projectId = "prj contract"; }).map(issue => issue.path), ["robots[2].projectId"]);
});

test("references, uniqueness and reserved ids are checked", () => {
  assert.deepEqual(issues(ws => { ws.robots[2].rev = "r9"; }), [{ path: "robots[2].rev", message: "\"r9\" is not a revision of \"edge-vla\"" }]);
  assert.deepEqual(issues(ws => { ws.runs[0].robotId = "ghost"; }), [{ path: "runs[0].robotId", message: "unknown robot \"ghost\"" }]);
  assert.deepEqual(issues(ws => { ws.rollouts[0].runId = "run-9"; }), [{ path: "rollouts[0].runId", message: "unknown run \"run-9\"" }]);
  assert.deepEqual(issues(ws => { ws.runs[2].slices[0].sliceId = "rain"; }), [{ path: "runs[2].slices[0].sliceId", message: "unknown slice \"rain\" in suite \"pick-place-v1\"" }]);
  assert.deepEqual(issues(ws => { ws.robots[2].id = ws.robots[1].id; ws.runs = []; ws.rollouts = []; }), [{ path: "robots[2].id", message: "duplicate id \"sim-02\"" }]);
  assert.deepEqual(issues(ws => { ws.runs[1].number = ws.runs[0].number; }), [{ path: "runs[1].number", message: "duplicate number \"1\"" }]);
  assert.deepEqual(issues(ws => { ws.configurations[2].id = "new"; }).map(issue => issue.path).includes("configurations[2].id"), true);
  assert.deepEqual(issues(ws => { ws.configurations[0].candidateRev = "r7"; }), [{ path: "configurations[0].candidateRev", message: "\"r7\" is not one of revisions[].rev" }]);
  assert.deepEqual(issues(ws => { ws.runs[2].status = "below-gate"; }), [{ path: "runs[2].status", message: "must agree with gate.passed" }]);
});

test("unknown keys are kept as warnings, not errors", () => {
  const ws = sample() as ConvoyWorkspace & { extra?: unknown };
  ws.extra = true;
  (ws.robots[0] as unknown as Record<string, unknown>).nickname = "bench";
  const result = validateWorkspace(ws);
  assert.equal(result.ok, true);
  assert.deepEqual(result.warnings.map(warning => warning.path), ["robots[0].nickname", "extra"]);
});

test("prototype keys and ids are refused, wherever they appear", () => {
  // JSON.parse keeps "__proto__" as an own key, exactly as a stored document would arrive.
  const parsed = (ws: ConvoyWorkspace, patch: (json: string) => string) => JSON.parse(patch(JSON.stringify(ws))) as unknown;
  const proto = validateWorkspace(parsed(sample(), json => json.replace('"schemaVersion":1', '"schemaVersion":1,"__proto__":{"polluted":true}')));
  assert.equal(proto.ok, false);
  assert.deepEqual(!proto.ok && proto.issues, [{ path: "__proto__", message: "is a reserved key and is not allowed" }]);
  assert.equal(({} as Record<string, unknown>).polluted, undefined);
  const inMeta = validateWorkspace(parsed(sample(), json => json.replace('"meta":{', '"meta":{"constructor":"x",')));
  assert.deepEqual(!inMeta.ok && inMeta.issues, [{ path: "meta.constructor", message: "is a reserved key and is not allowed" }]);
  assert.deepEqual(issues(ws => { ws.robots[1].id = "constructor"; }).filter(issue => issue.path === "robots[1].id"),
    [{ path: "robots[1].id", message: "\"constructor\" is reserved and cannot be an id" }]);
  assert.deepEqual(issues(ws => { ws.robots[0].deviceId = "__proto__"; }), [{ path: "robots[0].deviceId", message: "\"__proto__\" is reserved and cannot be an id" }]);
  // A key that only exists on Object.prototype is not mistaken for a field.
  const toString = validateWorkspace(parsed(sample(), json => json.replace('"schemaVersion":1', '"schemaVersion":1,"toString":"x"')));
  assert.deepEqual(toString.ok && toString.warnings.map(warning => warning.path), ["toString"]);
});

test("document links may only point inside the app", () => {
  const withAction = (href: string | null) => validateWorkspace((() => {
    const ws = sample();
    ws.configurations[0].revisions[0].compatibility = [{ id: "fit", verdict: "pass", title: "Fits", detail: "Fits.", evidence: { kind: "sample" }, action: { label: "Open", href } }];
    return ws;
  })());
  for (const href of [null, "/app/configurations/edge-vla", "/app/configurations/edge-vla/robots/sim-01?tab=details", "/app"]) assert.equal(withAction(href).ok, true, String(href));
  for (const href of ["https://example.test/", "javascript:alert(1)", "//example.test/app", "/app//example.test", "/app/../console", "/applications", "/app/x y", "/app\\x"]) {
    const result = withAction(href);
    assert.deepEqual(!result.ok && result.issues.map(issue => issue.message), ["expected a link inside this app, starting with /app/"], href);
  }
});

test("issue summaries name the first problem and count the rest", () => {
  assert.equal(summarizeIssues([]), "");
  assert.equal(summarizeIssues([{ path: "robots[0].rev", message: "is required" }, { path: "runs", message: "expected an array" }]), "robots[0].rev: is required (and 1 more)");
});
