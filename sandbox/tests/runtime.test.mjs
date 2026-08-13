import assert from "node:assert/strict";
import test from "node:test";
import { createTestRuntime, loadFixture, providerRequest } from "./helpers.mjs";

test("sandbox lifecycle is deterministic across snapshot, restore, reset, and fork", async () => {
  const { runtime } = await createTestRuntime();
  const fixture = await loadFixture();
  await runtime.provision({ sandboxId: "lifecycle", fixture });

  await runtime.invoke("lifecycle", "slack", providerRequest({
    method: "POST",
    path: "/api/chat.postMessage",
    token: "xoxb-convoy-sandbox",
    body: { channel: "C_OPERATIONS", text: "First update" },
  }));
  const snapshot = await runtime.snapshot("lifecycle", { label: "after-first-update" });
  await runtime.invoke("lifecycle", "slack", providerRequest({
    method: "POST",
    path: "/api/chat.postMessage",
    token: "xoxb-convoy-sandbox",
    body: { channel: "C_OPERATIONS", text: "Second update" },
  }));
  // The fixture seeds two operations messages; counts below are fixture + writes.
  assert.equal((await runtime.inspect("lifecycle")).providers.slack.messages.length, 4);

  await runtime.restore("lifecycle", snapshot.snapshotId);
  assert.equal((await runtime.inspect("lifecycle")).providers.slack.messages.length, 3);

  const forked = await runtime.fork("lifecycle", snapshot.snapshotId, "forked");
  assert.equal(forked.providers.slack.messages.length, 3);
  assert.equal(forked.sandboxId, "forked");

  await runtime.reset("lifecycle");
  assert.equal((await runtime.inspect("lifecycle")).providers.slack.messages.length, 2);
});

test("webhooks are signed, queued, delivered, and audited on simulated time", async () => {
  const { runtime, deliveries } = await createTestRuntime();
  await runtime.provision({ sandboxId: "webhooks", fixture: await loadFixture() });
  await runtime.invoke("webhooks", "github", providerRequest({
    method: "POST",
    path: "/repos/sandbox/example-service/issues",
    token: "github-convoy-sandbox",
    body: { title: "Export regression" },
  }));
  assert.equal((await runtime.inspect("webhooks")).webhooks.queue.length, 1);
  await runtime.advanceClock("webhooks", 1);
  assert.equal(deliveries.length, 1);
  assert.match(deliveries[0].headers["x-convoy-sandbox-signature"], /^v1=[a-f0-9]{64}$/);
  const state = await runtime.inspect("webhooks");
  assert.equal(state.webhooks.queue.length, 0);
  assert.equal(state.webhooks.deliveries[0].succeeded, true);
  assert.equal(state.clock.tick, 1);
});

test("idempotency keys prevent promoted writes from executing twice", async () => {
  const { runtime } = await createTestRuntime();
  await runtime.provision({ sandboxId: "idempotency", fixture: await loadFixture() });
  const request = providerRequest({
    method: "POST",
    path: "/repos/sandbox/example-service/issues",
    token: "github-convoy-sandbox",
    body: { title: "Create exactly once" },
  });
  request.headers["x-convoy-idempotency-key"] = "run-1:activity-1";
  const [first, second] = await Promise.all([
    runtime.invoke("idempotency", "github", request),
    runtime.invoke("idempotency", "github", request),
  ]);
  assert.deepEqual(second.body, first.body);
  const state = await runtime.inspect("idempotency");
  // Two fixture issues plus exactly one write despite the duplicate call.
  assert.equal(state.providers.github.repositories[0].issues.length, 3);
  assert.equal(state.webhooks.queue.length, 1);
  assert.equal(state.auditLog.at(-1).event, "provider.request_replayed");
});

test("destroy is recoverable by default", async () => {
  const { runtime } = await createTestRuntime();
  await runtime.provision({ sandboxId: "temporary", fixture: await loadFixture() });
  const result = await runtime.destroy("temporary");
  assert.deepEqual(result, { sandboxId: "temporary", status: "destroyed", purged: false });
  await assert.rejects(() => runtime.inspect("temporary"), /Sandbox not found/);
});
