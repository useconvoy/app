import test from "node:test";
import assert from "node:assert/strict";
import { buildRevision, EMPTY_INPUT, HARDWARE, inputErrors, routeMode } from "../../src/lib/configurations/create";
import { createConfiguration, emptyWorkspace } from "../../src/lib/configurations/mutations";
import { validateWorkspace } from "../../src/lib/configurations/validate";

const NOW = Date.parse("2026-10-01T09:41:20Z");

test("the form needs a unique name, the robot and at least one model", () => {
  assert.deepEqual(inputErrors(EMPTY_INPUT, []), { name: "Enter a name.", robot: "Enter the robot.", models: "Add an edge or a cloud model." });
  const ready = { ...EMPTY_INPUT, name: "Arm · Edge planner", robot: "Arm", edgeModel: "Qwen2.5-1.5B-Instruct Q4_K_M" };
  assert.deepEqual(inputErrors(ready, []), {});
  assert.deepEqual(inputErrors({ ...ready, edgeModel: "", cloudModel: "Hosted planner" }, []), {});
  assert.deepEqual(inputErrors(ready, [" arm · EDGE planner"]), { name: "A configuration has this name." });
  assert.deepEqual(inputErrors({ ...ready, name: "x".repeat(81) }, []), { name: "Use at most 80 characters." });
});

test("the route follows from the models", () => {
  assert.equal(routeMode(true, false), "edge-only");
  assert.equal(routeMode(false, true), "cloud-only");
  assert.equal(routeMode(true, true), "hybrid");
});

test("revision r1: a known edge model brings its runtime; a typed one is declared as typed; the document stays valid", () => {
  const known = buildRevision({ ...EMPTY_INPUT, name: "A", robot: "Arm", edgeModel: "SmolVLA (450M)", edgeRole: "policy" }, NOW);
  assert.deepEqual([known.rev, known.routing.mode, known.edgeModels[0].runtime, known.edgeModels[0].role, known.cloudModels.length], ["r1", "edge-only", "PyTorch CUDA", "policy", 0]);
  assert.equal(known.edgeHardware.name, HARDWARE[0].hardware.name);
  const cloud = buildRevision({ ...EMPTY_INPUT, name: "B", robot: "Arm", cloudModel: "Hosted planner", hardwareId: "orin-nx-16gb" }, NOW);
  assert.deepEqual([cloud.routing.mode, cloud.edgeModels.length, cloud.cloudModels[0].name, cloud.cloudModels[0].role, cloud.edgeHardware.name], ["cloud-only", 0, "Hosted planner", "planner", "Jetson Orin NX 16 GB"]);
  const typed = buildRevision({ ...EMPTY_INPUT, name: "C", robot: "Arm", edgeModel: "My planner", cloudModel: "Hosted policy", cloudRole: "policy" }, NOW);
  assert.deepEqual([typed.routing.mode, typed.edgeModels[0].shortName, typed.edgeModels[0].runtime], ["hybrid", "My planner", "Not specified"]);
  let workspace = emptyWorkspace(NOW);
  for (const [name, revision] of [["A", known], ["B", cloud], ["C", typed]] as const) workspace = createConfiguration(workspace, { name, revision }, NOW).workspace;
  const result = validateWorkspace(workspace);
  assert.equal(result.ok, true, result.ok ? "" : JSON.stringify(result.issues));
});
