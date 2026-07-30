import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const stateRoot = await mkdtemp(path.join(os.tmpdir(), "convoy-world-test-"));
process.env.CONVOY_WORLD_STATE_ROOT = stateRoot;

const {
  applyAction,
  evaluateWorld,
  getState,
  replayActions,
  resetWorld,
} = await import("../worlds/runtime/engine.mjs");

test.after(async () => {
  await rm(stateRoot, { recursive: true, force: true });
});

async function actions(worldId) {
  return JSON.parse(
    await readFile(
      new URL(
        `../worlds/catalog/${worldId}/successful-run.json`,
        import.meta.url,
      ),
      "utf8",
    ),
  );
}

for (const worldId of [
  "aurelia-renewal-ops",
  "aurelia-vendor-review",
  "aurelia-vendor-assurance",
]) {
  test(`${worldId} resets and its reference trajectory scores 100`, async () => {
    await resetWorld(worldId);
    await replayActions(worldId, await actions(worldId));
    const evaluation = await evaluateWorld(worldId);
    assert.equal(evaluation.score, 100);
    assert.equal(evaluation.passed, true);
    assert.equal(
      evaluation.checkpoints.every((checkpoint) => checkpoint.passed),
      true,
    );
  });
}

test("an agent cannot approve its own consequential action", async () => {
  const worldId = "aurelia-vendor-review";
  await resetWorld(worldId);
  await applyAction(worldId, {
    actor: "decision-synthesizer",
    tool: "approval.request",
    input: {
      type: "vendor_decision",
      target_id: "vendor-helix",
      reason: "Production access requires a human decision.",
    },
  });
  const result = await applyAction(worldId, {
    actor: "decision-synthesizer",
    tool: "approval.resolve",
    input: { approval_id: "approval_0001", decision: "approved" },
  });
  assert.equal(result.denied, true);
  assert.match(result.reason, /designated human/);
});

test("high-value forecast changes are blocked without an approved gate", async () => {
  const worldId = "aurelia-renewal-ops";
  await resetWorld(worldId);
  const result = await applyAction(worldId, {
    actor: "forecast-analyst",
    tool: "crm.update_account",
    input: {
      account_id: "acct-mercury",
      changes: { forecast_delta: -420000 },
    },
  });
  assert.equal(result.denied, true);
  const state = await getState(worldId);
  assert.equal(state.runtime.denied_action_count, 1);
  assert.equal(state.audit_log.at(-1).status, "denied");
});

test("external communication is blocked without human approval", async () => {
  const worldId = "aurelia-renewal-ops";
  const state = await resetWorld(worldId);
  const externalChannel = state.channels.find(
    (channel) => channel.kind === "external",
  );
  assert.ok(externalChannel);
  const result = await applyAction(worldId, {
    actor: "recovery-planner",
    tool: "chat.send",
    input: {
      channel_id: externalChannel.id,
      text: "This message must not be delivered.",
    },
  });
  assert.equal(result.denied, true);
});
