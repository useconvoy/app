import test from "node:test";
import assert from "node:assert/strict";
import { scryptSync } from "node:crypto";
import { checkOrigin, COOKIE, issueSession, limit, readSession, sessionCookie, upstreamRequestId, verifyLogin } from "../../src/lib/portal/auth";
import { boundedJson, handled, json } from "../../src/lib/portal/http";
import { chatInput, curateChat, curateTelemetry, curateUsage, snapshot } from "../../src/lib/portal/server";
import { allowedPath, assertDevice, upstream } from "../../src/lib/portal/upstream";

process.env.PORTAL_SESSION_SECRET = "test-secret-".repeat(5);
process.env.PORTAL_PUBLIC_ORIGIN = "https://portal.example.test";
process.env.PORTAL_DEMO_EMAIL = "demo@example.test";
const salt = "ab".repeat(16);
process.env.PORTAL_DEMO_PASSWORD_HASH = `scrypt$${salt}$${scryptSync("correct-password", Buffer.from(salt, "hex"), 64).toString("hex")}`;
process.env.CONVOY_UPSTREAM_URL = "http://127.0.0.1:18083";
process.env.CONVOY_UPSTREAM_TOKEN = "cva_test-only";
process.env.CONVOY_DEVICE_ID = "dev_board1";
const clientId = "fe630a10-82b0-47f2-8b90-9c829bd71556";
const cookieRequest = (cookie: string) => new Request("https://portal.example.test/api/portal/snapshot", { headers: { cookie: `${COOKIE}=${cookie}` } });

test("signed sessions reject tampering, expiry and credential rotation; cookie is hardened", () => {
  const now = Date.now();
  const { cookie, session } = issueSession(now);
  assert.deepEqual(readSession(cookieRequest(cookie), now), session);
  assert.equal(readSession(cookieRequest(cookie.slice(0, -1) + (cookie.endsWith("A") ? "B" : "A")), now), null);
  assert.equal(readSession(cookieRequest(cookie), now + 8 * 3600000), null);
  const before = process.env.PORTAL_DEMO_EMAIL;
  process.env.PORTAL_DEMO_EMAIL = "changed@example.test";
  assert.equal(readSession(cookieRequest(cookie), now), null);
  process.env.PORTAL_DEMO_EMAIL = before;
  assert.match(sessionCookie(cookie), /HttpOnly; Secure; SameSite=Strict; Max-Age=28800/);
  assert.match(sessionCookie("", 0), /Max-Age=0/);
});
test("login checks password and account; origins require exact HTTPS origin", async () => {
  assert.equal(await verifyLogin("DEMO@example.test", "correct-password"), true);
  assert.equal(await verifyLogin("demo@example.test", "wrong"), false);
  assert.equal(await verifyLogin("other@example.test", "correct-password"), false);
  checkOrigin(new Request("https://portal.example.test", { headers: { origin: "https://portal.example.test" } }));
  for (const origin of ["https://evil.test", "null", "https://portal.example.test.evil.test", "http://portal.example.test"]) {
    assert.throws(() => checkOrigin(new Request("https://portal.example.test", { headers: { origin } })), /verified/);
  }
  assert.throws(() => checkOrigin(new Request("https://portal.example.test")), /verified/);
});
test("upstream UUID is stable for one session and isolates other sessions", () => {
  const a = issueSession().session, b = issueSession().session;
  const id = upstreamRequestId(a, clientId);
  assert.equal(upstreamRequestId({ ...a }, clientId.toUpperCase()), id);
  assert.notEqual(upstreamRequestId(b, clientId), id);
  assert.notEqual(id, clientId);
  assert.throws(() => upstreamRequestId(a, "../admin"));
});
test("path allowlist excludes unrelated devices, writes, credentials and redirects", () => {
  assert.equal(allowedPath("/api/v1/devices/dev_board1/chat", "POST", "dev_board1"), true);
  for (const path of ["/api/v1/devices/dev_other/chat", "/api/v1/devices/dev_board1/deploy", "/api/v1/admin", "https://evil.test", "/api/v1/devices/dev_board1/../dev_other", "/api/v1/devices/dev_board1/chat?x=1"]) {
    assert.equal(allowedPath(path, "POST", "dev_board1"), false);
  }
  assert.equal(allowedPath("/api/v1/devices/dev_board1/chat", "DELETE", "dev_board1"), false);
});
test("physical validation rejects missing flags and mismatches; usage excludes fleet sums and missing data remains null", () => {
  assertDevice({ id: "dev_board1", simulated: false }, "dev_board1");
  for (const value of [{ id: "dev_board1", simulated: true }, { id: "dev_board1" }, { id: "other", simulated: false }]) assert.throws(() => assertDevice(value, "dev_board1"));
  const usage = curateUsage({ totals: { inference_requests: 999 }, days: [{ inference_requests: 888 }], devices: [{ device_id: "dev_sim", simulated: true, metrics: { inference_requests: 900 } }, { device_id: "dev_board1", simulated: false, metrics: { inference_requests: 4, tokens_out: 0 } }] }, "dev_board1");
  assert.equal(usage?.inference_requests, 4);
  assert.equal(usage?.tokens_out, 0);
  assert.equal(usage?.runtime_up_minutes, null);
  assert.equal(curateUsage({ devices: [] }, "dev_board1"), null);
  const telemetry = curateTelemetry({ ts: "2026-09-13T00:00:00Z", gpu_pct: 0, power_w: Infinity, extra: { secret: "not public" } });
  assert.equal(telemetry.gpu_pct, 0);
  assert.equal(telemetry.cpu_pct, null);
  assert.equal(telemetry.power_w, null);
  assert.equal("extra" in telemetry, false);
});
test("chat enforces UTF-8 limits and roles; result identifiers and errors are curated", () => {
  const input = { request_id: clientId, expected_release_id: "rel_test", max_tokens: 64, messages: [{ role: "user", content: "hello" }] };
  assert.equal(chatInput(input).messages.length, 1);
  for (const invalid of [{ ...input, max_tokens: true }, { ...input, stream: true }, { ...input, messages: [{ role: "system", content: "hi" }] }, { ...input, messages: [{ role: "user", content: "🙂".repeat(2049) }] }]) assert.throws(() => chatInput(invalid));
  const value = { id: clientId, device_id: "dev_board1", release_id: "rel_test", status: "failed", error: { code: "private_internal_code", message: "private internal secret" }, attrs: { private: true } };
  const result = curateChat(value, clientId, clientId, "dev_board1");
  assert.equal(JSON.stringify(result).includes("private"), false);
  assert.throws(() => curateChat(value, clientId, "wrong-id", "dev_board1"));
});
test("bounded requests, rate limiting and safe error handling", async () => {
  await assert.rejects(boundedJson(new Request("https://portal.example.test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: "x".repeat(30) }) }), 20));
  await assert.rejects(boundedJson(new Request("https://portal.example.test", { method: "POST", headers: { "Content-Type": "text/plain" }, body: "{}" }), 20));
  limit("test-bucket", 1, 60, 1000);
  assert.throws(() => limit("test-bucket", 1, 60, 1001));
  limit("test-bucket", 1, 60, 61000);
  const response = await handled(async () => { throw new Error("sensitive token"); });
  assert.equal(response.status, 503);
  assert.equal((await response.text()).includes("sensitive"), false);
  assert.match(json({}).headers.get("cache-control") ?? "", /private, no-store/);
});
test("snapshot uses only selected physical data and upstream fetch never retries failed writes", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (input, init) => {
    calls++;
    assert.equal(init?.redirect, "error");
    assert.equal(init?.cache, "no-store");
    const url = new URL(String(input));
    const data = url.pathname === "/api/v1/devices/dev_board1" ? { id: "dev_board1", simulated: false, name: "Board", observed_active_release_id: "rel_test", observed: { gateway: { mode: "production", stats: { secret: true } } } }
      : url.pathname.endsWith("/telemetry") ? [{ ts: "2026-09-13T00:00:00Z", gpu_pct: 5 }]
      : url.pathname.endsWith("/spans") ? [{ name: "gateway.chat_completion", kind: "inference", trace_id: "tr_abcd", attrs: { tokens_out: 3, secret: "hidden" } }]
      : url.pathname.endsWith("/usage") ? { totals: { tokens_out: 999 }, devices: [{ device_id: "dev_board1", simulated: false, metrics: { tokens_out: 3 } }] }
      : url.pathname.endsWith("/rel_test") ? { id: "rel_test", simulated: false, name: "Real", spec: { secret: "hidden" } }
      : url.pathname.endsWith("/settings") ? { offline_after_s: 90, heartbeat_interval_s: 15, public_url: "private" }
      : { devices: [{ id: "dev_board1", simulated: false, eligible: true, online: true, release_id: "rel_test" }] };
    return Response.json(data);
  };
  try {
    const result = await snapshot();
    assert.equal(result.usage.metrics?.tokens_out, 3);
    assert.equal(result.telemetry_stale_after_s, 90);
    assert.equal(result.chat.eligible, true);
    assert.equal(JSON.stringify(result).includes("secret"), false);
    assert.equal(calls, 7);
    calls = 0;
    globalThis.fetch = async () => { calls++; return new Response("sensitive raw error", { status: 500 }); };
    await assert.rejects(upstream("/api/v1/devices/dev_board1/chat", "POST", {}), /temporarily unavailable/);
    assert.equal(calls, 1);
  } finally { globalThis.fetch = originalFetch; }
});
