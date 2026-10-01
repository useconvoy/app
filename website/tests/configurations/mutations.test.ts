import test from "node:test";
import assert from "node:assert/strict";
import { buildRevision, EMPTY_INPUT } from "../../src/lib/configurations/create";
import { ACTIVITY_LIMIT, addRobot, createConfiguration, deleteConfiguration, emptyWorkspace, linkOfflineEvaluations, removeRobot, slugify, uniqueId } from "../../src/lib/configurations/mutations";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { CONFIGURED_DEVICE } from "../../src/lib/configurations/types";
import { validateWorkspace } from "../../src/lib/configurations/validate";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const LATER = NOW + 60_000;
const valid = (value: unknown) => { const result = validateWorkspace(value); assert.equal(result.ok, true, result.ok ? "" : JSON.stringify(result.issues)); };
const revision = (edgeModel = "Qwen2.5-1.5B-Instruct Q4_K_M") => buildRevision({ ...EMPTY_INPUT, name: "Arm A", robot: "Arm", edgeModel }, NOW);

test("ids are route-safe and unique", () => {
  assert.equal(slugify("Arm · Edge planner (v2)"), "arm-edge-planner-v2");
  assert.equal(uniqueId("edge-planner", ["edge-planner", "edge-planner-2"]), "edge-planner-3");
  assert.equal(uniqueId("new", []), "new-2", "the new-configuration route is reserved");
  assert.equal(uniqueId("constructor", []), "constructor-2", "prototype names are never ids");
  // A 64-character id stays within 64 characters with any suffix.
  const long = "a".repeat(64);
  const taken = [long, ...Array.from({ length: 1200 }, (_, i) => `${"a".repeat(64 - String(i + 2).length - 1)}-${i + 2}`)];
  const next = uniqueId(long, taken);
  assert.equal(next, `${"a".repeat(59)}-1202`);
  assert.ok(next.length <= 64 && /^[A-Za-z0-9_-]{1,64}$/.test(next));
});

test("an account's workspace starts empty; a new configuration is under test at r1", () => {
  const empty = emptyWorkspace(NOW);
  valid(empty);
  assert.equal(empty.meta.sample, undefined);
  const { workspace, configuration } = createConfiguration(empty, { name: "  Arm · Edge planner ", revision: revision() }, LATER);
  valid(workspace);
  assert.deepEqual([configuration.id, configuration.name, configuration.status, configuration.candidateRev, configuration.productionRev], ["arm-edge-planner", "Arm · Edge planner", "testing", "r1", null]);
  assert.deepEqual(workspace.activity.map(event => event.kind), ["configuration-created"]);
  assert.equal(empty.configurations.length, 0, "the input is not changed");
  assert.equal(createConfiguration(workspace, { name: "Arm · Edge planner", revision: revision() }, LATER).configuration.id, "arm-edge-planner-2");
});

test("a robot is added for testing with its live device and its evals' project", () => {
  const { workspace: start } = createConfiguration(emptyWorkspace(NOW), { name: "Arm A", revision: revision() }, NOW);
  const live = addRobot(start, { configId: "arm-a", name: "Bench 01", deviceId: "dev_contract01" }, LATER);
  valid(live.workspace);
  assert.deepEqual([live.robot.id, live.robot.role, live.robot.rev, live.robot.deviceId, live.robot.kind, live.robot.projectId], ["bench-01", "test", "r1", "dev_contract01", "bench", undefined]);
  const sim = addRobot(live.workspace, { configId: "arm-a", name: "Sim runner", projectId: "prj_contract", platformRobotId: "rob_contract" }, LATER);
  valid(sim.workspace);
  assert.deepEqual([sim.robot.kind, sim.robot.deviceId, sim.robot.projectId, sim.robot.platformRobotId], ["simulator", undefined, "prj_contract", "rob_contract"]);
  const workspaceDevice = addRobot(start, { configId: "arm-a", name: "Bench", deviceId: CONFIGURED_DEVICE, platformRobotId: "rob_ignored" }, LATER);
  assert.equal(workspaceDevice.robot.platformRobotId, undefined, "a platform robot needs its project");
  valid(workspaceDevice.workspace);
  assert.throws(() => addRobot(start, { configId: "missing", name: "X" }, LATER), /Unknown configuration/);
  assert.throws(() => addRobot(start, { configId: "arm-a", name: "  " }, LATER), /needs a name/);
});

test("a simulator can show offline evaluations; its links can change later", () => {
  const { workspace: start } = createConfiguration(emptyWorkspace(NOW), { name: "Arm A", revision: revision() }, NOW);
  const added = addRobot(start, { configId: "arm-a", name: "Offline", offlineEvaluationIds: ["oev_contract0001", "oev_contract0001"] }, LATER);
  valid(added.workspace);
  assert.deepEqual([added.robot.kind, added.robot.offlineEvaluationIds, added.robot.projectId], ["simulator", ["oev_contract0001"], undefined]);
  assert.equal(addRobot(start, { configId: "arm-a", name: "None", offlineEvaluationIds: [] }, LATER).robot.offlineEvaluationIds, undefined);
  const relinked = linkOfflineEvaluations(added.workspace, added.robot.id, ["oev_contract0001", "oev_contract0002"], LATER + 1000);
  valid(relinked);
  assert.deepEqual(relinked.robots[0].offlineEvaluationIds, ["oev_contract0001", "oev_contract0002"]);
  assert.equal(relinked.configurations[0].updatedAt, new Date(LATER + 1000).toISOString());
  const unlinked = linkOfflineEvaluations(relinked, added.robot.id, [], LATER + 2000);
  valid(unlinked);
  assert.equal("offlineEvaluationIds" in unlinked.robots[0], false, "no links, no field");
  assert.equal(added.workspace.robots[0].offlineEvaluationIds?.length, 1, "the input is not changed");
  assert.throws(() => linkOfflineEvaluations(start, "missing", [], LATER), /Unknown robot/);
});

test("removing a robot removes what is stored about it; deleting a configuration removes its robots and runs", () => {
  const ws = createSampleWorkspace(NOW);
  const removed = removeRobot(ws, "sim-01", LATER);
  valid(removed);
  assert.equal(removed.robots.some(robot => robot.id === "sim-01"), false);
  assert.deepEqual([removed.runs.some(run => run.robotId === "sim-01"), removed.rollouts.some(rollout => rollout.runId === "run-3")], [false, false]);
  assert.equal(removed.configurations.length, 3, "its configuration stays");
  const deleted = deleteConfiguration(ws, "cloud-planner", LATER);
  valid(deleted);
  assert.deepEqual(deleted.configurations.map(config => config.id), ["edge-planner", "edge-vla"]);
  assert.deepEqual(deleted.robots.map(robot => robot.id), ["bench-01", "sim-01"]);
  assert.deepEqual(deleted.runs.map(run => run.id), ["run-1", "run-2", "run-3"]);
  assert.ok(deleted.rollouts.every(rollout => deleted.runs.some(run => run.id === rollout.runId)));
  assert.throws(() => deleteConfiguration(ws, "missing", LATER), /Unknown configuration/);
  assert.throws(() => removeRobot(ws, "missing", LATER), /Unknown robot/);
});

test("the activity feed keeps the newest events only", () => {
  let { workspace } = createConfiguration(emptyWorkspace(NOW), { name: "Arm A", revision: revision() }, NOW);
  for (let i = 0; i < ACTIVITY_LIMIT + 5; i++) workspace = addRobot(workspace, { configId: "arm-a", name: `Sim ${i}` }, LATER + i * 1000).workspace;
  valid(workspace);
  assert.equal(workspace.activity.length, ACTIVITY_LIMIT);
  assert.equal(workspace.activity[0].message, `Sim ${ACTIVITY_LIMIT + 4} added to Arm A`);
});
