import test from "node:test";
import assert from "node:assert/strict";
import { addRevision, attachRobot, clearFlag, createConfiguration, flagRobot, nextRunNumber, promoteRevision, queueEvaluation, setRobotRole, slugify, uniqueId } from "../../src/lib/configurations/mutations";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { validateWorkspace } from "../../src/lib/configurations/validate";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const LATER = NOW + 60_000;
const valid = (value: unknown) => { const result = validateWorkspace(value); assert.equal(result.ok, true, result.ok ? "" : JSON.stringify(result.issues)); };

test("ids are route-safe and unique", () => {
  assert.equal(slugify("Bimanual station · Edge (v2)"), "bimanual-station-edge-v2");
  assert.equal(uniqueId("hybrid", ["hybrid", "hybrid-2"]), "hybrid-3");
  assert.equal(uniqueId("new", []), "new-2", "the new-configuration route is reserved");
  assert.equal(uniqueId("constructor", []), "constructor-2", "prototype names are never ids");
  assert.equal(nextRunNumber(createSampleWorkspace(NOW)), 26);
  // A 64-character id stays within 64 characters with any suffix.
  const long = "a".repeat(64);
  const taken = [long, ...Array.from({ length: 1200 }, (_, i) => `${"a".repeat(64 - String(i + 2).length - 1)}-${i + 2}`)];
  const next = uniqueId(long, taken);
  assert.equal(next, `${"a".repeat(59)}-1202`);
  assert.ok(next.length <= 64 && /^[A-Za-z0-9_-]{1,64}$/.test(next));
  // A flag id built from a long robot id is still a valid id.
  const ws = createSampleWorkspace(NOW);
  const robot = ws.robots.find(item => item.id === "unit-07")!;
  const renamed = { ...ws, robots: ws.robots.map(item => item === robot ? { ...item, id: "r".repeat(64) } : item), logs: ws.logs.filter(line => line.robotId !== "unit-07"), traces: ws.traces.filter(trace => trace.robotId !== "unit-07"), activity: ws.activity.filter(event => event.subject.id !== "unit-07") };
  valid(flagRobot(renamed, "r".repeat(64), { label: "Check", note: "Long id" }, LATER));
});

test("the activity feed keeps the newest 200 events", () => {
  let ws = createSampleWorkspace(NOW);
  for (let i = 0; i < 205; i++) ws = flagRobot(ws, "unit-07", { label: `Check ${i}`, note: "Repeated" }, LATER + i * 1000);
  valid(ws);
  assert.equal(ws.activity.length, 200);
  assert.equal(ws.activity[0].message, "Unit 07 flagged: Check 204");
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

test("adding a revision puts it under test; production, robots and other configurations keep theirs", () => {
  const ws = createSampleWorkspace(NOW);
  const before = JSON.stringify(ws);
  const hybrid = ws.configurations.find(config => config.id === "hybrid")!;
  const revision = { ...hybrid.revisions[1], rev: "r5", note: "Lighter skill pack", createdAt: new Date(LATER).toISOString() };
  const added = addRevision(ws, "hybrid", { revision, purpose: "Plans on the edge and acts from a cloud policy.", from: "r4" }, LATER);
  valid(added);
  assert.equal(JSON.stringify(ws), before, "inputs are not mutated");
  const updated = added.configurations.find(config => config.id === "hybrid")!;
  assert.deepEqual([updated.revisions.map(item => item.rev), updated.candidateRev, updated.productionRev, updated.status, updated.name], [["r3", "r4", "r5"], "r5", "r3", "production", "Bimanual station · Hybrid"]);
  assert.deepEqual([updated.purpose, updated.updatedAt], ["Plans on the edge and acts from a cloud policy.", new Date(LATER).toISOString()]);
  assert.deepEqual(added.robots.map(robot => robot.rev), ws.robots.map(robot => robot.rev));
  assert.deepEqual(added.configurations.filter(config => config.id !== "hybrid"), ws.configurations.filter(config => config.id !== "hybrid"));
  assert.deepEqual([added.activity[0].kind, added.activity[0].message, added.activity[0].provenance.kind, added.meta.updatedAt], ["configuration-created", "Bimanual station · Hybrid r5 created from r4", "recorded", new Date(LATER).toISOString()]);
  assert.equal(addRevision(ws, "hybrid", { revision, name: "  Bimanual station · Hybrid v2 " }, LATER).configurations.find(config => config.id === "hybrid")!.name, "Bimanual station · Hybrid v2");
  assert.throws(() => addRevision(added, "hybrid", { revision }, LATER), /already has a revision r5/);
  assert.throws(() => addRevision(ws, "nope", { revision }, LATER), /Unknown configuration "nope"/);
});

test("clearing the flag that explains a stored health clears that health too; other health stays", () => {
  const ws = createSampleWorkspace(NOW);
  const unit13 = ws.robots.find(robot => robot.id === "unit-13")!;
  assert.deepEqual([unit13.health, unit13.healthReason, unit13.flags.map(flag => flag.label)], ["degraded", "Power peaks", ["Power peaks"]]);
  const cleared = clearFlag(ws, "unit-13", unit13.flags[0].id, LATER);
  valid(cleared);
  assert.deepEqual((({ health, healthReason, flags }) => ({ health, healthReason, flags }))(cleared.robots.find(robot => robot.id === "unit-13")!), { health: "healthy", healthReason: null, flags: [] });
  // Two flags: clearing the one behind the health hands it to the other.
  const twice = flagRobot(ws, "unit-13", { label: "Fan noise", note: "Rattle at idle", severity: "attention" }, LATER);
  const next = clearFlag(twice, "unit-13", unit13.flags[0].id, LATER + 1000).robots.find(robot => robot.id === "unit-13")!;
  assert.deepEqual([next.health, next.healthReason], ["attention", "Fan noise"]);
  // A health with its own reason is declared, not explained by the flag: it stays.
  const declared = { ...ws, robots: ws.robots.map(robot => robot.id === "unit-13" ? { ...robot, healthReason: "Board swapped, burn-in" } : robot) };
  assert.equal(clearFlag(declared, "unit-13", unit13.flags[0].id, LATER).robots.find(robot => robot.id === "unit-13")!.health, "degraded");
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
