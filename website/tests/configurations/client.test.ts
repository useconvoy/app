import test from "node:test";
import assert from "node:assert/strict";
import { WorkspaceStore } from "../../src/lib/configurations/client";
import { attachRobot } from "../../src/lib/configurations/mutations";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { onSessionExpired } from "../../src/lib/configurations/session-events";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";

const NOW = Date.parse("2026-10-01T09:41:20Z");
interface Call { method: string; url: string; headers: Record<string, string>; body: unknown }
type Reply = { status: number; body?: unknown } | Error | Promise<{ status: number; body?: unknown }>;

function server(replies: Array<Reply | ((call: Call) => Reply)>) {
  const calls: Call[] = [];
  const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = { method: init?.method ?? "GET", url: String(input), headers: Object.fromEntries(Object.entries(init?.headers ?? {})), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const next = replies.shift();
    if (!next) throw new Error(`unexpected ${call.method}`);
    const reply = await (typeof next === "function" ? next(call) : next);
    if (reply instanceof Error) throw reply;
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json" } });
  }) as typeof globalThis.fetch;
  let key = 0;
  const store = new WorkspaceStore({ fetch, now: () => NOW, createKey: () => `key-${++key}` });
  return { store, calls };
}
const envelope = (body: unknown, updated = "2026-10-01T09:40:00Z") => ({ status: 200, body: { name: "configurations", schema_version: 1, body, size_bytes: 100, updated_at: updated } });
const echo = (call: Call) => envelope((call.body as { body: unknown }).body, "2026-10-01T09:42:00Z");
const document = (label = "Lab workspace"): ConvoyWorkspace => { const ws = createSampleWorkspace(NOW); ws.meta = { ...ws.meta, label, sample: false }; return ws; };
const attach = (current: ConvoyWorkspace) => attachRobot(current, "unit-18", { configId: "hybrid", role: "test", site: "Lab · Staging", rev: "r4" }, NOW);

test("a missing document shows the sample and allows the first save", async () => {
  const { store, calls } = server([{ status: 404, body: { error: "missing" } }]);
  assert.equal(store.getSnapshot().status, "loading");
  await store.load();
  await store.load();
  const snapshot = store.getSnapshot();
  assert.equal(calls.length, 1, "load is idempotent");
  assert.equal(calls[0].url, "/api/platform/workspace-documents/configurations");
  assert.equal(calls[0].headers["X-Convoy-Client"], "web");
  assert.equal(snapshot.status, "ready");
  assert.equal(snapshot.source, "sample");
  assert.equal(snapshot.reason, "missing");
  assert.equal(snapshot.canSave, true);
  assert.equal(snapshot.workspace?.meta.sample, true);
});

test("a valid document is used; an invalid one falls back with its problems and blocks saves", async () => {
  const valid = server([envelope(document())]);
  await valid.store.load();
  assert.equal(valid.store.getSnapshot().source, "document");
  assert.equal(valid.store.getSnapshot().workspace?.meta.label, "Lab workspace");
  assert.equal(valid.store.getSnapshot().documentUpdatedAt, "2026-10-01T09:40:00Z");

  const invalid = server([envelope({ ...document(), robots: "none" })]);
  await invalid.store.load();
  const snapshot = invalid.store.getSnapshot();
  assert.equal(snapshot.source, "sample");
  assert.equal(snapshot.reason, "invalid");
  assert.deepEqual(snapshot.issues, [{ path: "robots", message: "expected an array" }]);
  assert.equal(snapshot.canSave, false);
  const result = await invalid.store.save(attach);
  assert.equal(result.ok, false);
  assert.equal(invalid.calls.length, 1, "no PUT over an unreadable document");
});

test("an unreadable document falls back to the sample; a later failed refresh keeps the document shown", async () => {
  const { store } = server([new TypeError("offline"), envelope(document()), { status: 503, body: { error: "The management service is unavailable." } }]);
  await store.load();
  assert.equal(store.getSnapshot().reason, "unavailable");
  await store.refresh();
  assert.equal(store.getSnapshot().source, "document");
  await store.refresh();
  assert.equal(store.getSnapshot().source, "document");
  assert.match(store.getSnapshot().error ?? "", /Showing the last version read/);
});

test("save writes with PUT and an Idempotency-Key, shows the change at once and confirms it", async () => {
  let release: (reply: { status: number; body?: unknown }) => void = () => undefined;
  const { store, calls } = server([envelope(document()), new Promise(resolve => { release = resolve; })]);
  await store.load();
  const pending = store.save(attach);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(store.getSnapshot().saving, true);
  assert.equal(store.getSnapshot().workspace?.robots.find(robot => robot.id === "unit-18")?.configId, "hybrid", "optimistic");
  const put = calls[1];
  assert.equal(put.method, "PUT");
  assert.equal(put.headers["Idempotency-Key"], "key-1");
  assert.equal(put.headers["X-Convoy-Client"], "web");
  assert.equal((put.body as { schema_version: number }).schema_version, 1);
  release(echo(put));
  const result = await pending;
  assert.equal(result.ok, true);
  assert.equal(store.getSnapshot().saving, false);
  assert.equal(store.getSnapshot().source, "document");
  assert.equal(store.getSnapshot().documentUpdatedAt, "2026-10-01T09:42:00Z");
});

test("a failed save rolls back; retrying the identical change reuses its key", async () => {
  const { store, calls } = server([envelope(document()), new TypeError("connection reset"), { status: 500, body: { error: "The management service is unavailable." } }]);
  await store.load();
  const first = await store.save(attach);
  assert.equal(first.ok, false);
  assert.equal(store.getSnapshot().workspace?.robots.find(robot => robot.id === "unit-18")?.configId, null, "rolled back");
  const second = await store.save(attach);
  assert.equal(second.ok, false);
  assert.equal(calls[1].headers["Idempotency-Key"], calls[2].headers["Idempotency-Key"]);
  assert.equal(!second.ok && second.error, "The management service is unavailable.");
});

test("a 409 re-reads the document and re-applies an updater once", async () => {
  const theirs = document("Changed elsewhere");
  const { store, calls } = server([envelope(document()), { status: 409, body: { error: "The state changed." } }, envelope(theirs), echo]);
  await store.load();
  const result = await store.save(attach);
  assert.equal(result.ok, true);
  assert.equal(calls.map(call => call.method).join(" "), "GET PUT GET PUT");
  assert.notEqual(calls[1].headers["Idempotency-Key"], calls[3].headers["Idempotency-Key"]);
  const saved = (calls[3].body as { body: ConvoyWorkspace }).body;
  assert.equal(saved.meta.label, "Changed elsewhere", "the change applies on top of the newer document");
  assert.equal(saved.robots.find(robot => robot.id === "unit-18")?.configId, "hybrid");
  const replacement = server([envelope(document()), { status: 409, body: { error: "The state changed." } }, envelope(theirs)]);
  await replacement.store.load();
  const conflict = await replacement.store.save(document("Mine"));
  assert.equal(conflict.ok, false);
  assert.equal(!conflict.ok && conflict.conflict, true, "a full replacement is not re-applied over someone else's change");
  assert.equal(replacement.store.getSnapshot().workspace?.meta.label, "Changed elsewhere");
});

test("invalid changes are not sent; 401 ends the session", async () => {
  const { store, calls } = server([envelope(document()), { status: 401, body: { error: "Sign in" } }]);
  await store.load();
  const invalid = await store.save(current => ({ ...current, robots: [...current.robots, { ...current.robots[1] }] }));
  assert.equal(invalid.ok, false);
  assert.match(!invalid.ok ? invalid.error : "", /duplicate id/);
  assert.equal(calls.length, 1);
  let ended = 0;
  const stop = onSessionExpired(() => { ended++; });
  const result = await store.save(attach);
  stop();
  assert.equal(result.ok, false);
  assert.equal(ended, 1);
});

test("import validates the file first and can replace an invalid stored document", async () => {
  const { store, calls } = server([envelope({ schemaVersion: 7 }), echo]);
  await store.load();
  assert.equal(store.getSnapshot().reason, "invalid");
  const rejected = await store.importDocument({ schemaVersion: 1 });
  assert.equal(rejected.ok, false);
  assert.equal(calls.length, 1);
  const imported = await store.importDocument(document("Imported"));
  assert.equal(imported.ok, true);
  assert.equal(store.getSnapshot().source, "document");
  assert.equal(store.getSnapshot().workspace?.meta.label, "Imported");
});
