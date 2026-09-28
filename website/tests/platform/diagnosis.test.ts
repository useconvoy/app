import test from "node:test";
import assert from "node:assert/strict";
import { executionDiagnosis } from "../../src/lib/platform/diagnosis";

test("episode diagnosis displays known labels and observed expiry without interpreting remote errors", () => {
  const failure = { schema_version: 1, component: "action_policy", phase: "policy_inference",
    category: "authorization", authorization_elapsed: false, http_status: 401,
    message: "private remote error", request_id: "req_1", sequence: 3 };
  assert.deepEqual(executionDiagnosis(failure), {
    component: "Action policy", stage: "Requesting the next action", category: "Authorization check failed",
    authorizationNotice: null,
  });
  assert.equal(executionDiagnosis({ ...failure, authorization_elapsed: true })?.authorizationNotice,
    "The original mission authorization had elapsed on the robot.");
  for (const invalid of [undefined, null, [], {}, { ...failure, schema_version: 2 },
    { ...failure, component: "remote-error-text" }, { ...failure, phase: "future_phase" },
    { ...failure, category: "constructor" }, { ...failure, authorization_elapsed: "true" }]) {
    assert.equal(executionDiagnosis(invalid), null);
  }
});
