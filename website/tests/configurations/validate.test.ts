import test from "node:test";
import assert from "node:assert/strict";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";
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
  assert.deepEqual(issues(ws => { (ws.robots[3].telemetry!.latest as unknown as Record<string, unknown>).socTempC = "hot"; }),
    [{ path: "robots[3].telemetry.latest.socTempC", message: "expected a finite number or null" }]);
  assert.deepEqual(issues(ws => { (ws.configurations[0].revisions[1].edgeModels[0] as unknown as Record<string, unknown>).state = "running"; }),
    [{ path: "configurations[0].revisions[1].edgeModels[0].state", message: "expected one of \"active\", \"fallback-only\", \"proposed\", \"blocked\", \"not-deployed\"" }]);
  assert.deepEqual(issues(ws => { ws.runs[2].successCi95 = [0.9, 0.2]; }), [{ path: "runs[2].successCi95", message: "expected low ≤ high" }]);
  assert.deepEqual(issues(ws => { ws.traces[0].spans[1].group = "nowhere"; }), [{ path: "traces[0].spans[1].group", message: "\"nowhere\" is not one of spanGroups[].id" }]);
  assert.deepEqual(issues(ws => { delete (ws.suites[0] as unknown as Record<string, unknown>).gate; }), [{ path: "suites[0].gate", message: "is required" }]);
});

test("measured values are never stored", () => {
  const found = issues(ws => { (ws.robots[1].telemetry!.provenance as { kind: string }).kind = "measured"; });
  assert.equal(found[0].path, "robots[1].telemetry.provenance.kind");
  assert.match(found[0].message, /never stored/);
  assert.deepEqual(issues(ws => { ws.robots[0].telemetry = ws.robots[1].telemetry; }),
    [{ path: "robots[0].telemetry", message: "a robot with a live device binding gets telemetry from the device and must not store it" }]);
  assert.deepEqual(issues(ws => { ws.runs[5].provenance = { kind: "recorded" }; }), [{ path: "runs[5].provenance.at", message: "recorded evidence needs a date" }]);
});

test("references, uniqueness and reserved ids are checked", () => {
  assert.deepEqual(issues(ws => { ws.robots[2].rev = "r9"; }), [{ path: "robots[2].rev", message: "\"r9\" is not a revision of \"hybrid\"" }]);
  assert.deepEqual(issues(ws => { ws.runs[0].robotId = "ghost"; }), [{ path: "runs[0].robotId", message: "unknown robot \"ghost\"" }]);
  assert.deepEqual(issues(ws => { ws.rollouts[0].runId = "run-1"; }), [{ path: "rollouts[0].runId", message: "unknown run \"run-1\"" }]);
  assert.deepEqual(issues(ws => { ws.runs[2].slices[0].sliceId = "rain"; }), [{ path: "runs[2].slices[0].sliceId", message: "unknown slice \"rain\" in suite \"station-suite-v1\"" }]);
  assert.deepEqual(issues(ws => { ws.robots[7].id = ws.robots[6].id; }), [{ path: "robots[7].id", message: "duplicate id \"sim-01\"" }]);
  assert.deepEqual(issues(ws => { ws.runs[1].number = ws.runs[0].number; }), [{ path: "runs[1].number", message: "duplicate number \"25\"" }]);
  assert.deepEqual(issues(ws => { ws.configurations[2].id = "new"; }).map(issue => issue.path), ["configurations[2].id"]);
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

test("issue summaries name the first problem and count the rest", () => {
  assert.equal(summarizeIssues([]), "");
  assert.equal(summarizeIssues([{ path: "robots[0].rev", message: "is required" }, { path: "runs", message: "expected an array" }]), "robots[0].rev: is required (and 1 more)");
});
