import test from "node:test";
import assert from "node:assert/strict";
import { attachRobot, clearFlag, createConfiguration, flagRobot, nextRunNumber, promoteRevision, queueEvaluation, setRobotRole, slugify, uniqueId } from "../../src/lib/configurations/mutations";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { validateWorkspace } from "../../src/lib/configurations/validate";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const LATER = NOW + 60_000;
const valid = (value: unknown) => { const result = validateWorkspace(value); assert.equal(result.ok, true, result.ok ? "" : JSON.stringify(result.issues)); };

test("ids are route-safe and unique", () => {
  assert.equal(slugify("Bimanual station · Edge (v2)"), "bimanual-station-edge-v2");
  assert.equal(uniqueId("hybrid", ["hybrid", "hybrid-2"]), "hybrid-3");
  assert.equal(uniqueId("new", []), "new-2", "the new-configuration route is reserved");
  assert.equal(nextRunNumber(createSampleWorkspace(NOW)), 26);
});

test("each change returns a new valid workspace and records an activity event", () => {
  const ws = createSampleWorkspace(NOW);
  const before = JSON.stringify(ws);
  const attached = attachRobot(ws, "unit-18", { configId: "hybrid", role: "production", site: "Site 5 · Line 2", rev: "r3" }, LATER);
  valid(attached);
  assert.equal(JSON.stringify(ws), before, "inputs are not mutated");
  assert.deepEqual((({ configId, role, rev, site }) => ({ configId, role, rev, site }))(attached.robots.find(robot => robot.id === "unit-18")!), { configId: "hybrid", role: "production", rev: "r3", site: "Site 5 · Line 2" });
  assert.equal(attached.activity[0].kind, "robot-added");
  assert.equal(attached.activity[0].provenance.kind, "recorded");
  assert.throws(() => attachRobot(ws, "unit-18", { configId: "hybrid", role: "test", site: "", rev: "r9" }, LATER), /Unknown configuration revision/);

  const flagged = flagRobot(attached, "unit-07", { label: "Gripper noise", note: "Clicking on close; check before the next shift", by: "operator@example.test" }, LATER);
  valid(flagged);
  const flag = flagged.robots.find(robot => robot.id === "unit-07")!.flags[0];
  assert.deepEqual([flag.rule, flag.severity, flag.note], ["manual", "warning", "Clicking on close; check before the next shift"]);
  const cleared = clearFlag(flagged, "unit-07", flag.id, LATER);
  valid(cleared);
  assert.equal(cleared.robots.find(robot => robot.id === "unit-07")!.flags.length, 0);

  const { workspace: queued, run } = queueEvaluation(cleared, { configId: "hybrid", rev: "r4", robotId: "lab-bench", suiteId: "station-suite-v1", variant: "Hybrid", purpose: "Confirmation" }, LATER);
  valid(queued);
  assert.deepEqual([run.number, run.status, run.progress?.total, run.provenance.kind], [26, "queued", 360, "not-reported"]);

  const base = ws.configurations[0].revisions[1];
  const { workspace: created, configuration } = createConfiguration(queued, { name: "Bimanual station · Hybrid", purpose: "A copy for a lighter edge pack.", revision: { ...base, rev: "r1", createdAt: new Date(LATER).toISOString() } }, LATER);
  valid(created);
  assert.equal(configuration.id, "bimanual-station-hybrid");
  assert.deepEqual([configuration.status, configuration.candidateRev, configuration.productionRev], ["draft", "r1", null]);
});

test("promotion moves production robots to the revision; roles can change", () => {
  const ws = createSampleWorkspace(NOW);
  const promoted = promoteRevision(ws, "hybrid", "r4", LATER);
  valid(promoted);
  const hybrid = promoted.configurations.find(config => config.id === "hybrid")!;
  assert.deepEqual([hybrid.productionRev, hybrid.candidateRev, hybrid.status], ["r4", null, "production"]);
  assert.ok(promoted.robots.filter(robot => robot.configId === "hybrid" && robot.role === "production").every(robot => robot.rev === "r4"));
  assert.equal(promoted.activity[0].kind, "promoted");
  const moved = setRobotRole(ws, "unit-02", "production", LATER);
  valid(moved);
  assert.equal(moved.robots.find(robot => robot.id === "unit-02")!.role, "production");
  assert.equal(setRobotRole(ws, "unit-02", "test", LATER), ws, "no change, no new document");
  assert.throws(() => setRobotRole(ws, "unit-18", "production", LATER), /not attached/);
});
