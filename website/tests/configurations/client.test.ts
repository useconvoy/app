import test from "node:test";
import assert from "node:assert/strict";
import { REFRESH_MIN_GAP_MS, saveErrorMessage, WorkspaceStore } from "../../src/lib/configurations/client";
import { attachRobot } from "../../src/lib/configurations/mutations";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { onSessionExpired } from "../../src/lib/configurations/session-events";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";

// Contract v2 of the workspace documents API: documents carry an integer `revision`; PUT needs
// `If-Match: "<revision>"` (replace) or `If-None-Match: *` (create), answers 412 on a mismatch and
// returns metadata only.
const NOW = Date.parse("2026-10-01T09:41:20Z");
interface Call { method: string; url: string; headers: Record<string, string>; body: unknown }
type Reply = { status: number; body?: unknown; headers?: Record<string, string> } | Error | Promise<{ status: number; body?: unknown; headers?: Record<string, string> }>;

function server(replies: Array<Reply | ((call: Call) => Reply)>, clock: { now: number } = { now: 0 }) {
  const calls: Call[] = [];
  const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = { method: init?.method ?? "GET", url: String(input), headers: Object.fromEntries(Object.entries(init?.headers ?? {})), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const next = replies.shift();
    if (!next) throw new Error(`unexpected ${call.method}`);
    const reply = await (typeof next === "function" ? next(call) : next);
    if (reply instanceof Error) throw reply;
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json", ...reply.headers } });
  }) as typeof globalThis.fetch;
  let key = 0;
  const store = new WorkspaceStore({ fetch, now: () => NOW, clock: () => clock.now, createKey: () => `key-${++key}` });
  return { store, calls, clock };
}
const envelope = (body: unknown, revision = 3, updated = "2026-10-01T09:40:00Z") => ({ status: 200, body: { name: "configurations", schema_version: 1, revision, body, size_bytes: 100, updated_at: updated } });
/** A v2 PUT answer: metadata only. */
const stored = (revision: number, updated = "2026-10-01T09:42:00Z") => ({ status: 200, body: { name: "configurations", schema_version: 1, revision, size_bytes: 100, updated_at: updated } });
const sent = (call: Call) => (call.body as { body: ConvoyWorkspace }).body;
const document = (label = "Lab workspace"): ConvoyWorkspace => { const ws = createSampleWorkspace(NOW); ws.meta = { ...ws.meta, label, sample: false }; return ws; };
const attach = (current: ConvoyWorkspace) => attachRobot(current, "unit-18", { configId: "hybrid", role: "test", site: "Lab · Staging", rev: "r4" }, NOW);

test("a missing document shows the sample and allows the first save, which may only create", async () => {
  const { store, calls } = server([{ status: 404, body: { error: "missing" } }, stored(1)]);
  assert.equal(store.getSnapshot().status, "loading");
  await store.load();
  await store.load();
  const snapshot = store.getSnapshot();
  assert.equal(calls.length, 1, "load is idempotent");
  assert.equal(calls[0].url, "/api/platform/workspace-documents/configurations");
  assert.equal(calls[0].headers["X-Convoy-Client"], "web");
  assert.deepEqual([snapshot.status, snapshot.source, snapshot.reason, snapshot.canSave, snapshot.documentExists, snapshot.documentRevision], ["ready", "sample", "missing", true, false, null]);
  assert.equal(snapshot.workspace?.meta.sample, true);
  const result = await store.save(attach);
  assert.equal(result.ok, true);
  assert.equal(calls[1].headers["If-None-Match"], "*", "creating never writes over a document stored meanwhile");
  assert.equal(calls[1].headers["If-Match"], undefined);
  assert.deepEqual([store.getSnapshot().source, store.getSnapshot().documentRevision, store.getSnapshot().documentExists], ["document", 1, true]);
});

test("a valid document is used with its revision; an invalid one falls back with its problems and blocks saves", async () => {
  const valid = server([envelope(document(), 7)]);
  await valid.store.load();
  assert.deepEqual([valid.store.getSnapshot().source, valid.store.getSnapshot().workspace?.meta.label, valid.store.getSnapshot().documentRevision], ["document", "Lab workspace", 7]);
  assert.equal(valid.store.getSnapshot().documentUpdatedAt, "2026-10-01T09:40:00Z");

  const invalid = server([envelope({ ...document(), robots: "none" }, 4)]);
  await invalid.store.load();
  const snapshot = invalid.store.getSnapshot();
  assert.deepEqual([snapshot.source, snapshot.reason, snapshot.canSave, snapshot.documentExists, snapshot.documentRevision], ["sample", "invalid", false, true, 4]);
  assert.deepEqual(snapshot.issues, [{ path: "robots", message: "expected an array" }]);
  const result = await invalid.store.save(attach);
  assert.equal(result.ok, false);
  assert.equal(invalid.calls.length, 1, "no PUT over an unreadable document");
});

test("an unreadable document falls back to the sample; a later failed refresh keeps the document shown", async () => {
  const { store } = server([new TypeError("offline"), envelope(document()), { status: 503, body: { error: "The management service is unavailable." } }]);
  await store.load();
  assert.deepEqual([store.getSnapshot().reason, store.getSnapshot().documentExists], ["unavailable", null]);
  await store.refresh();
  assert.equal(store.getSnapshot().source, "document");
  await store.refresh();
  assert.equal(store.getSnapshot().source, "document");
  assert.match(store.getSnapshot().error ?? "", /Showing the last version read/);
});

test("save replaces exactly the revision read: If-Match, Idempotency-Key, optimistic, then the new revision", async () => {
  let release: (reply: { status: number; body?: unknown }) => void = () => undefined;
  const { store, calls } = server([envelope(document(), 3), new Promise(resolve => { release = resolve; })]);
  await store.load();
  const pending = store.save(attach);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(store.getSnapshot().saving, true);
  assert.equal(store.getSnapshot().workspace?.robots.find(robot => robot.id === "unit-18")?.configId, "hybrid", "optimistic");
  const put = calls[1];
  assert.equal(put.method, "PUT");
  assert.equal(put.headers["If-Match"], "\"3\"");
  assert.equal(put.headers["If-None-Match"], undefined);
  assert.equal(put.headers["Idempotency-Key"], "key-1");
  assert.equal(put.headers["X-Convoy-Client"], "web");
  assert.equal((put.body as { schema_version: number }).schema_version, 1);
  release(stored(4));
  const result = await pending;
  assert.equal(result.ok, true);
  const snapshot = store.getSnapshot();
  assert.deepEqual([snapshot.saving, snapshot.source, snapshot.documentRevision, snapshot.documentUpdatedAt], [false, "document", 4, "2026-10-01T09:42:00Z"]);
  assert.equal(snapshot.workspace?.robots.find(robot => robot.id === "unit-18")?.configId, "hybrid", "the document sent is the confirmed one (no body echo)");
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
  assert.equal(calls[1].headers["If-Match"], calls[2].headers["If-Match"]);
  assert.equal(!second.ok && second.error, "The management service is unavailable.");
});

test("a 412 re-reads the document and re-applies an updater once, on the newer revision", async () => {
  const theirs = document("Changed elsewhere");
  const { store, calls } = server([envelope(document(), 3), { status: 412, body: { error: "precondition failed" } }, envelope(theirs, 5), stored(6)]);
  await store.load();
  const result = await store.save(attach);
  assert.equal(result.ok, true);
  assert.equal(calls.map(call => call.method).join(" "), "GET PUT GET PUT");
  assert.deepEqual([calls[1].headers["If-Match"], calls[3].headers["If-Match"]], ["\"3\"", "\"5\""]);
  assert.notEqual(calls[1].headers["Idempotency-Key"], calls[3].headers["Idempotency-Key"]);
  assert.equal(sent(calls[3]).meta.label, "Changed elsewhere", "the change applies on top of the newer document");
  assert.equal(sent(calls[3]).robots.find(robot => robot.id === "unit-18")?.configId, "hybrid");
  assert.equal(store.getSnapshot().documentRevision, 6);
});

test("a second 412, or a full replacement, is reported as a conflict and shows the latest version", async () => {
  const theirs = document("Changed elsewhere");
  const twice = server([envelope(document(), 3), { status: 412, body: {} }, envelope(theirs, 5), { status: 412, body: {} }, envelope(document("Changed again"), 8)]);
  await twice.store.load();
  const conflict = await twice.store.save(attach);
  assert.equal(conflict.ok, false);
  assert.equal(!conflict.ok && conflict.conflict, true);
  assert.match(!conflict.ok ? conflict.error : "", /changed in another tab or session/);
  assert.deepEqual([twice.store.getSnapshot().workspace?.meta.label, twice.store.getSnapshot().documentRevision], ["Changed again", 8]);
  assert.equal(twice.store.getSnapshot().workspace?.robots.find(robot => robot.id === "unit-18")?.configId, null, "nothing of the refused change remains");

  const replacement = server([envelope(document(), 3), { status: 412, body: {} }, envelope(theirs, 5)]);
  await replacement.store.load();
  const refused = await replacement.store.save(document("Mine"));
  assert.equal(!refused.ok && refused.conflict, true, "a full replacement is not re-applied over someone else's change");
  assert.equal(replacement.store.getSnapshot().workspace?.meta.label, "Changed elsewhere");
});

test("the sample is never written over a document that appeared meanwhile: the change moves to that document", async () => {
  const theirs = document("Created in another tab");
  const { store, calls } = server([{ status: 404, body: { error: "missing" } }, { status: 412, body: {} }, envelope(theirs, 1), stored(2)]);
  await store.load();
  const result = await store.save(attach);
  assert.equal(result.ok, true);
  assert.deepEqual([calls[1].headers["If-None-Match"], calls[3].headers["If-Match"]], ["*", "\"1\""]);
  assert.equal(sent(calls[3]).meta.label, "Created in another tab", "re-applied to the stored document, not the sample");
  assert.deepEqual([store.getSnapshot().source, store.getSnapshot().documentRevision], ["document", 2]);
});

test("refused writes say why: limit, reused key, conflict, missing precondition, too many, too large", () => {
  const reply = (status: number, error: unknown = null, retryAfter: string | null = null) => ({ status, data: error === null ? null : { error }, retryAfter });
  assert.match(saveErrorMessage(reply(409, "workspace document limit reached (16 per account); delete one first")), /most workspace documents allowed/);
  assert.match(saveErrorMessage(reply(409, "Idempotency-Key was already used with a different payload")), /reused the request key/);
  // The website proxy's curated wording for the same two 409s.
  assert.match(saveErrorMessage(reply(409, "Your account has reached its workspace document limit. Delete a document before creating another.")), /most workspace documents allowed/);
  assert.match(saveErrorMessage(reply(409, "This save was already submitted with different content. Reload before saving again.")), /reused the request key/);
  assert.match(saveErrorMessage(reply(409, "The state changed or this request conflicts with existing work.")), /conflicted with another request/);
  assert.match(saveErrorMessage(reply(412)), /changed in another tab or session/);
  assert.match(saveErrorMessage(reply(428, { message: "precondition required" })), /did not name the version it replaces/);
  assert.equal(saveErrorMessage(reply(429, null, "37")), "Too many saves in a short time. Wait 37 s, then try again.");
  assert.equal(saveErrorMessage(reply(429)), "Too many saves in a short time. Wait a moment, then try again.");
  assert.match(saveErrorMessage(reply(413)), /too large/);
  assert.equal(saveErrorMessage(reply(500, "The management service is unavailable.")), "The management service is unavailable.");
});

test("a 429 on save keeps the document, says when to retry and rolls the change back", async () => {
  const { store } = server([envelope(document(), 3), { status: 429, body: { error: "Too many requests." }, headers: { "Retry-After": "12" } }]);
  await store.load();
  const result = await store.save(attach);
  assert.equal(!result.ok && result.error, "Too many saves in a short time. Wait 12 s, then try again.");
  assert.equal(store.getSnapshot().workspace?.robots.find(robot => robot.id === "unit-18")?.configId, null);
  assert.equal(store.getSnapshot().documentRevision, 3);
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

test("import creates a document with If-None-Match, and replaces a stored one only when confirmed, with If-Match", async () => {
  const fresh = server([{ status: 404, body: {} }, stored(1)]);
  await fresh.store.load();
  const rejected = await fresh.store.importDocument({ schemaVersion: 1 });
  assert.equal(rejected.ok, false);
  assert.equal(fresh.calls.length, 1, "an invalid file is not sent");
  const created = await fresh.store.importDocument(document("Imported"));
  assert.equal(created.ok, true);
  assert.equal(fresh.calls[1].headers["If-None-Match"], "*");
  assert.equal(fresh.store.getSnapshot().workspace?.meta.label, "Imported");

  const { store, calls } = server([envelope({ schemaVersion: 7 }, 9), stored(10)]);
  await store.load();
  assert.deepEqual([store.getSnapshot().reason, store.getSnapshot().documentExists], ["invalid", true]);
  const unconfirmed = await store.importDocument(document("Imported"));
  assert.equal(unconfirmed.ok, false);
  assert.match(!unconfirmed.ok ? unconfirmed.error : "", /Confirm to replace it/);
  assert.equal(calls.length, 1, "a stored document is not replaced without confirmation");
  const replaced = await store.importDocument(document("Imported"), { replace: true });
  assert.equal(replaced.ok, true);
  assert.equal(calls[1].headers["If-Match"], "\"9\"");
  assert.deepEqual([store.getSnapshot().source, store.getSnapshot().workspace?.meta.label, store.getSnapshot().documentRevision], ["document", "Imported", 10]);
});

test("an import that would create over a document stored meanwhile is refused and shows that document", async () => {
  const { store } = server([{ status: 404, body: {} }, { status: 412, body: {} }, envelope(document("Stored meanwhile"), 2)]);
  await store.load();
  const result = await store.importDocument(document("Imported"));
  assert.equal(!result.ok && result.conflict, true);
  assert.match(!result.ok ? result.error : "", /stored for this account meanwhile/);
  assert.deepEqual([store.getSnapshot().workspace?.meta.label, store.getSnapshot().documentExists], ["Stored meanwhile", true]);
});

test("focus re-reads are spaced out, skipped while saving, and never undo a newer save", async () => {
  let release: (reply: { status: number; body?: unknown }) => void = () => undefined;
  const clock = { now: 0 };
  const { store, calls } = server([envelope(document(), 3), envelope(document("Elsewhere"), 4), new Promise(resolve => { release = resolve; }), stored(5)], clock);
  await store.load();
  await store.refreshIfIdle();
  assert.equal(calls.length, 1, "right after a read: skipped");
  clock.now += REFRESH_MIN_GAP_MS;
  await store.refreshIfIdle();
  assert.deepEqual([calls.length, store.getSnapshot().workspace?.meta.label, store.getSnapshot().documentRevision], [2, "Elsewhere", 4]);
  // A read that started before a save finished is older than that save: it is dropped.
  clock.now += REFRESH_MIN_GAP_MS;
  const reading = store.refresh();
  const saving = store.save(attach);
  await new Promise(resolve => setImmediate(resolve));
  clock.now += REFRESH_MIN_GAP_MS;
  await store.refreshIfIdle();
  assert.equal(calls.map(call => call.method).join(" "), "GET GET GET PUT", "no further re-read while one is in flight or a save is pending");
  release(envelope(document("Stale read"), 4));
  await reading;
  await saving;
  assert.deepEqual([store.getSnapshot().workspace?.meta.label, store.getSnapshot().documentRevision], ["Elsewhere", 5]);
});
