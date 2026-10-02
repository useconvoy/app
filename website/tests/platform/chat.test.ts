import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import { CHAT_CONTRACT, chatInput, curateChat, deviceChatRoute, proxyDeviceChat, resetChatLimits, upstreamRequestId, type DeviceChatRoute } from "../../src/lib/platform/chat";
import { consoleOrigin, ownCookie, platformOrigin, proxyPlatform } from "../../src/lib/platform/proxy";

/*
 * The public device chat contract (platform-chat-v1) with a mocked control plane: authentication,
 * Origin and client checks, strict bodies and UTF-8 limits, per-session request ids, the per-session
 * and per-site rate limits with Retry-After, curated results and errors, and the device list. The
 * real control plane, website and device agent run together in
 * integrations/simulation/tests/test_platform_chat_contract.py.
 */

process.env.CONVOY_API_URL = "http://127.0.0.1:18085";
delete process.env.CONVOY_CONSOLE_ORIGIN;
delete process.env.PORTAL_PUBLIC_ORIGIN;
const origin = "https://console.example.test";
const cookie = (n = 0) => `convoy_session=cvs_${String(n).padStart(4, "0")}abcdefghijklmnopqrst`;
const DEVICE = "dev_board1";
const CLIENT_ID = "fe630a10-82b0-47f2-8b90-9c829bd71556";
const RELEASE = "rel_live";

type Call = { method: string; path: string; headers: Headers; body: unknown; init: RequestInit };
type Handler = (method: string, path: string, body: unknown) => Response | Promise<Response>;

/** Replaces fetch with `handler`; returns the recorded upstream calls. */
function mockUpstream(handler: Handler): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input));
    assert.equal(url.origin, "http://127.0.0.1:18085", "only the configured API");
    const method = init.method ?? "GET";
    const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path: url.pathname + url.search, headers: new Headers(init.headers), body, init });
    return handler(method, url.pathname, body);
  }) as typeof fetch;
  return calls;
}
const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; resetChatLimits(); });

const physical = {
  id: DEVICE, simulated: false, name: "Board", status: "online", profile_id: "jetson-orin-nano-8gb", agent_version: "0.9.0",
  runtime_evidence: { backend: "cuda", build_info: "b6550-5266f24d", n_ctx: 2048, gpu_offloaded_layers: 29, gpu_total_layers: 29,
    model_path: "/var/lib/convoy/private/model.gguf", argv: ["--api-key-file", "/private/key"], binary_sha256: "a".repeat(64) },
  observed: { gateway: { mode: "production", stats: { private: true } } }, settings: { private: true },
};
const release = {
  id: RELEASE, simulated: false, name: "Edge model", version: "3", digest: "d".repeat(64), notes: "private note", created_by: "usr_private",
  provenance: { argv_preview: ["--api-key-file", "<key>"] },
  model: { source: "hf", repo: "example-org/Example-1B-GGUF", revision: "main", commit: "c".repeat(40),
    file: { path: "weights/example-1b-q4_k_m.gguf", size: 1, sha256: "e".repeat(64), url: "https://models.example.test/private?token=abc", auth: "token" } },
  runtime: { name: "llama.cpp", tag: "b6550", commit: "5266f24da75dc449bd56cbed7addb9c8e4a6a73e", backend: "cuda", artifact_files: ["private"] },
  config: { ctx_size: 2048, n_predict: 128, temperature: 0, seed: 42, threads: 6 },
  spec: { model: { gguf: { file_type: "Q4_K_M", name: "Example 1B", architecture: "qwen2", counts: { private: 1 } } }, template: { text: "private template" } },
};
const availability = (extra: Record<string, unknown> = {}) => ({
  id: DEVICE, name: "Board", simulated: false, online: true, eligible: true, reason: null, release_id: RELEASE,
  release_name: "Edge model", max_tokens: 128, context_window: 2048, chat_supported: true, ...extra,
});
function chatRow(id: string, extra: Record<string, unknown> = {}) {
  return { id, device_id: DEVICE, release_id: RELEASE, status: "queued", created_at: "2026-10-02T00:00:00Z", expires_at: "2026-10-02T00:02:00Z",
    content: null, finish_reason: null, usage: null, metrics: null, trace_id: null, error: null, ...extra };
}
/** A healthy control plane: one physical device, its release, and chat requests stored by upstream id. */
function controlPlane(overrides: Partial<Record<string, Handler>> = {}): Handler {
  const stored = new Map<string, Record<string, unknown>>();
  return (method, path, body) => {
    const key = `${method} ${path}`;
    for (const [pattern, handler] of Object.entries(overrides)) if (handler && new RegExp(`^${pattern}$`).test(key)) return handler(method, path, body);
    if (key === "GET /api/v1/chat/devices") return Response.json({ devices: [availability()] });
    if (key === `GET /api/v1/devices/${DEVICE}`) return Response.json(physical);
    if (key === `GET /api/v1/releases/${RELEASE}`) return Response.json(release);
    if (key === `POST /api/v1/devices/${DEVICE}/chat`) {
      const id = (body as { request_id: string }).request_id;
      const row = stored.get(id) ?? chatRow(id);
      stored.set(id, row);
      return Response.json(row, { status: 202 });
    }
    const read = key.match(new RegExp(`^GET /api/v1/devices/${DEVICE}/chat/([0-9a-f-]{36})$`));
    if (read) {
      const row = stored.get(read[1]);
      return row ? Response.json({ ...row, status: "succeeded", content: '{"arm": "L", "skill": "wait"}', finish_reason: "stop",
        usage: { prompt_tokens: 900, completion_tokens: 13, total_tokens: 913 }, metrics: { latency_ms: 812.4, ttft_ms: 455, queue_ms: 0.2 },
        trace_id: "tr_00ab", private: "internal" }) : Response.json({ error: "chat request not found" }, { status: 404 });
    }
    throw new Error(`unexpected upstream call ${key}`);
  };
}

const body = (extra: Record<string, unknown> = {}) => ({
  request_id: CLIENT_ID, expected_release_id: RELEASE, max_tokens: 32,
  messages: [{ role: "user", content: "scene" }, { role: "assistant", content: '{"arm": "R", "skill": "wait"}' }, { role: "user", content: "next scene" }],
  ...extra,
});
function send(payload: unknown = body(), options: { session?: string | null; headers?: Record<string, string>; device?: string } = {}) {
  const headers: Record<string, string> = { Origin: origin, "X-Convoy-Client": "web", "Content-Type": "application/json", ...options.headers };
  if (options.session !== null) headers.Cookie = `${options.session ?? cookie()}; unrelated=private`;
  return new Request(`${origin}/api/platform/devices/${options.device ?? DEVICE}/chat`, { method: "POST", headers,
    body: typeof payload === "string" || payload instanceof Uint8Array ? payload as BodyInit : JSON.stringify(payload) });
}
function get(path: string, session: string | null = cookie()) {
  return new Request(`${origin}/api/platform/${path}`, { headers: session === null ? {} : { Cookie: session } });
}
const parts = (request: Request) => new URL(request.url).pathname.replace(/^\/api\/platform\//, "").split("/");
const call = (request: Request) => proxyPlatform(request, parts(request));
/** With a controllable clock (the limits' windows). */
function at(now: number, request: Request) {
  const route = deviceChatRoute(parts(request), request.method) as DeviceChatRoute;
  return proxyDeviceChat(request, route, { consoleOrigin, ownCookie, platformOrigin: () => platformOrigin(), now: () => now });
}
async function failure(response: Response) {
  const data = await response.json() as Record<string, unknown>;
  assert.deepEqual(Object.keys(data).sort(), ["code", "error"], "errors are {error, code} only");
  return { status: response.status, code: data.code, retryAfter: response.headers.get("retry-after"), message: String(data.error) };
}

test("chat paths and methods: only the three routes; anything else falls through to the generic allowlist", async () => {
  assert.deepEqual(deviceChatRoute(["chat", "devices"], "GET"), { kind: "devices" });
  assert.deepEqual(deviceChatRoute(["devices", DEVICE, "chat"], "POST"), { kind: "send", deviceId: DEVICE });
  assert.deepEqual(deviceChatRoute(["devices", DEVICE, "chat", CLIENT_ID], "GET"), { kind: "read", deviceId: DEVICE, requestId: CLIENT_ID });
  for (const [path, method] of [["chat/devices", "POST"], [`devices/${DEVICE}/chat`, "GET"], [`devices/${DEVICE}/chat`, "DELETE"], [`devices/${DEVICE}/chat/${CLIENT_ID}`, "POST"],
    [`devices/${DEVICE}/chat/${CLIENT_ID}/x`, "GET"], ["chat", "GET"]] as const) {
    assert.equal(deviceChatRoute(path.split("/"), method), null, `${method} ${path}`);
  }
  const calls = mockUpstream(() => { throw new Error("must not reach upstream"); });
  const deleted = await proxyPlatform(new Request(`${origin}/api/platform/devices/${DEVICE}/chat`, { method: "DELETE", headers: { Cookie: cookie(), Origin: origin, "X-Convoy-Client": "web" } }), ["devices", DEVICE, "chat"]);
  assert.equal(deleted.status, 404);
  assert.equal(calls.length, 0);
});

test("anonymous, duplicate-session and unverified requests are refused before the API", async () => {
  const calls = mockUpstream(() => { throw new Error("must not reach upstream"); });
  for (const request of [get("chat/devices", null), get(`devices/${DEVICE}/chat/${CLIENT_ID}`, null), send(body(), { session: null }),
    get("chat/devices", `${cookie()}; ${cookie(1)}`), get("chat/devices", "convoy_session=not-a-session")]) {
    const refused = await failure(await call(request));
    assert.deepEqual([refused.status, refused.code], [401, "authentication_required"]);
  }
  for (const headers of [{ Origin: "https://evil.test" }, { Origin: "null" }, { "X-Convoy-Client": "" }, { Origin: `${origin}.evil.test` }] as Record<string, string>[]) {
    const refused = await failure(await call(send(body(), { headers })));
    assert.deepEqual([refused.status, refused.code], [403, "invalid_origin"]);
  }
  const noOrigin = new Request(`${origin}/api/platform/devices/${DEVICE}/chat`, { method: "POST", headers: { Cookie: cookie(), "X-Convoy-Client": "web", "Content-Type": "application/json" }, body: JSON.stringify(body()) });
  assert.equal((await call(noOrigin)).status, 403);
  assert.equal(calls.length, 0);
});

test("the body is strict JSON within the chat limits, UTF-8 counted, checked before any upstream call", async () => {
  const calls = mockUpstream(controlPlane());
  const refused = async (request: Request) => { const result = await failure(await call(request)); resetChatLimits(); return [result.status, result.code]; };
  assert.deepEqual(await refused(send(body(), { headers: { "Content-Type": "text/plain" } })), [415, "invalid_content_type"]);
  assert.deepEqual(await refused(send(body(), { headers: { "Content-Length": String(64 * 1024 + 1) } })), [413, "request_too_large"]);
  assert.deepEqual(await refused(send(JSON.stringify(body({ pad: "x".repeat(70 * 1024) })))), [413, "request_too_large"]);
  const stream = new Request(`${origin}/api/platform/devices/${DEVICE}/chat`, { method: "POST", duplex: "half",
    headers: { Cookie: cookie(), Origin: origin, "X-Convoy-Client": "web", "Content-Type": "application/json" },
    body: new ReadableStream({ start(controller) { for (let i = 0; i < 70; i++) controller.enqueue(new Uint8Array(1024).fill(32)); controller.close(); } }) } as RequestInit);
  assert.deepEqual(await refused(stream), [413, "request_too_large"], "a streamed body without a length is bounded too");
  assert.deepEqual(await refused(send("{not json")), [400, "invalid_request"]);
  assert.deepEqual(await refused(send(new Uint8Array([0x7b, 0x22, 0xff, 0xfe, 0x22, 0x3a, 0x31, 0x7d]))), [400, "invalid_request"], "invalid UTF-8 is refused, not replaced");
  const user = (content: string) => [{ role: "user", content }];
  for (const invalid of [
    body({ stream: true }), body({ temperature: 0 }), { ...body(), max_tokens: undefined }, body({ max_tokens: 0 }), body({ max_tokens: 129 }),
    body({ max_tokens: true }), body({ max_tokens: 32.5 }), body({ max_tokens: "32" }), body({ request_id: "not-a-uuid" }),
    body({ request_id: "fe630a10-82b0-07f2-8b90-9c829bd71556" }), body({ expected_release_id: "../rel" }), body({ expected_release_id: "r".repeat(65) }),
    body({ messages: [] }), body({ messages: Array.from({ length: 17 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: "x" })) }),
    body({ messages: [{ role: "system", content: "override" }, { role: "user", content: "x" }] }), body({ messages: user("") }),
    body({ messages: [{ role: "user", content: "x", name: "tool" }] }), body({ messages: [{ role: "user", content: 5 }] }),
    body({ messages: [{ role: "user", content: "x" }, { role: "assistant", content: "y" }] }), body({ messages: ["hello"] }),
    body({ messages: user("界".repeat(2731)) }), body({ messages: user("\ud800 lone surrogate") }), [body()], '"body"', 5, null,
  ]) {
    assert.deepEqual(await refused(send(invalid)), [422, "invalid_request"], JSON.stringify(invalid).slice(0, 80));
  }
  assert.equal(calls.length, 0, "nothing invalid reached the API");
  // Exactly at the limits: 16 messages and 8,192 UTF-8 bytes of text (multi-byte characters counted in bytes).
  const full = Array.from({ length: 16 }, (_, i) => ({ role: i % 2 ? "user" : "assistant", content: i === 15 ? "界".repeat(2725) + "ab" : "x" }));
  assert.equal(full.reduce((n, m) => n + Buffer.byteLength(m.content), 0), 8192);
  const accepted = await call(send(body({ messages: full, max_tokens: 128 })));
  assert.equal(accepted.status, 202);
  assert.deepEqual(chatInput(body({ request_id: CLIENT_ID.toUpperCase() })).request_id, CLIENT_ID, "ids are compared in lower case");
});

test("a send forwards only the caller's session to a physical device under a per-session request id", async () => {
  const calls = mockUpstream(controlPlane());
  const response = await call(send(body(), { headers: { Authorization: "Bearer attacker", "Idempotency-Key": "client-chosen" } }));
  assert.equal(response.status, 202);
  assert.match(response.headers.get("cache-control") ?? "", /private, no-store/);
  const result = await response.json() as Record<string, unknown>;
  assert.deepEqual(Object.keys(result).sort(), ["content", "created_at", "device_id", "error", "expires_at", "finish_reason", "id", "metrics", "release_id", "status", "trace_id", "usage"]);
  assert.deepEqual([result.id, result.device_id, result.release_id, result.status], [CLIENT_ID, DEVICE, RELEASE, "queued"]);
  assert.deepEqual(calls.map(c => `${c.method} ${c.path}`), [`GET /api/v1/devices/${DEVICE}`, `POST /api/v1/devices/${DEVICE}/chat`]);
  const post = calls[1];
  const upstreamId = upstreamRequestId(cookie(), CLIENT_ID);
  assert.notEqual(upstreamId, CLIENT_ID);
  assert.deepEqual(post.body, { ...body(), request_id: upstreamId }, "the body is forwarded unchanged but for the namespaced id");
  for (const c of calls) {
    assert.equal(c.headers.get("cookie"), cookie(), "the caller's session only");
    assert.equal(c.headers.get("authorization"), null);
    assert.equal(c.headers.get("idempotency-key"), null);
    assert.equal(c.headers.get("x-convoy-client"), "web");
    assert.equal(c.init.redirect, "error");
    assert.equal(c.init.cache, "no-store");
    assert.ok(c.init.signal, "every upstream call has a deadline");
  }
  // Another session of the same account gets another upstream id, so it cannot read this request.
  assert.notEqual(upstreamRequestId(cookie(1), CLIENT_ID), upstreamId);
  assert.equal(upstreamRequestId(cookie(), CLIENT_ID.toUpperCase()), upstreamId);
  const other = await failure(await call(get(`devices/${DEVICE}/chat/${CLIENT_ID}`, cookie(1))));
  assert.deepEqual([other.status, other.code], [404, "not_found"]);
  // A simulated device is refused before anything is sent.
  const simulated = mockUpstream(controlPlane({ [`GET /api/v1/devices/${DEVICE}`]: () => Response.json({ ...physical, simulated: true }) }));
  const refused = await failure(await call(send()));
  assert.deepEqual([refused.status, refused.code], [409, "device_not_ready"]);
  assert.deepEqual(simulated.map(c => c.method), ["GET"]);
  // Path ids: an unknown device shape is not found; an invalid request id is invalid.
  assert.deepEqual(Object.values(await failure(await call(send(body(), { device: "DEV_UPPER" })))).slice(0, 2), [404, "not_found"]);
  assert.deepEqual(Object.values(await failure(await call(get(`devices/${DEVICE}/chat/not-a-uuid`)))).slice(0, 2), [422, "invalid_request"]);
});

test("a read returns the curated request with its trace id and release id, nothing else", async () => {
  const calls = mockUpstream(controlPlane());
  assert.equal((await call(send())).status, 202);
  const response = await call(get(`devices/${DEVICE}/chat/${CLIENT_ID.toUpperCase()}`));
  assert.equal(response.status, 200);
  const result = await response.json() as Record<string, unknown>;
  assert.deepEqual(result, {
    id: CLIENT_ID, device_id: DEVICE, release_id: RELEASE, status: "succeeded", created_at: "2026-10-02T00:00:00Z", expires_at: "2026-10-02T00:02:00Z",
    content: '{"arm": "L", "skill": "wait"}', finish_reason: "stop", usage: { prompt_tokens: 900, completion_tokens: 13, total_tokens: 913 },
    metrics: { latency_ms: 812.4, ttft_ms: 455, queue_ms: 0.2 }, trace_id: "tr_00ab", error: null,
  });
  assert.deepEqual(calls.slice(2).map(c => c.path).sort(), [`/api/v1/devices/${DEVICE}`, `/api/v1/devices/${DEVICE}/chat/${upstreamRequestId(cookie(), CLIENT_ID)}`].sort());
  // Results are verified and curated: a foreign id or device is refused; unknown codes and malformed trace ids are not passed on.
  const upstreamId = upstreamRequestId(cookie(), CLIENT_ID);
  assert.throws(() => curateChat(chatRow("another"), CLIENT_ID, upstreamId, DEVICE));
  assert.throws(() => curateChat({ ...chatRow(upstreamId), device_id: "dev_other" }, CLIENT_ID, upstreamId, DEVICE));
  assert.throws(() => curateChat({ ...chatRow(upstreamId), status: "cancelled" }, CLIENT_ID, upstreamId, DEVICE));
  const failed = curateChat({ ...chatRow(upstreamId), status: "failed", trace_id: "trace with spaces", error: { code: "runtime_error", message: "private model detail" } }, CLIENT_ID, upstreamId, DEVICE);
  assert.deepEqual([failed.trace_id, failed.error], [null, { code: "request_failed", message: "The device could not complete this request." }]);
  const expired = curateChat({ ...chatRow(upstreamId), status: "expired", error: { code: "request_expired", message: "x" } }, CLIENT_ID, upstreamId, DEVICE);
  assert.equal(expired.error?.code, "request_expired");
  assert.equal(JSON.stringify(failed).includes("private"), false);
});

test("sends are limited to 6 per session and 20 per site a minute, reads to 180 and 1,200, with Retry-After to the window's end", async () => {
  mockUpstream(controlPlane());
  const t0 = 1_000_000;
  for (let i = 0; i < 6; i++) assert.equal((await at(t0 + i, send(body({ request_id: crypto.randomUUID() })))).status, 202, `send ${i + 1}`);
  const limited = await failure(await at(t0 + 15_000, send(body({ request_id: crypto.randomUUID() }))));
  assert.deepEqual([limited.status, limited.code, limited.retryAfter], [429, "rate_limited", "45"], "45 s left in the session's window");
  assert.equal((await at(t0 + 60_001, send(body({ request_id: crypto.randomUUID() })))).status, 202, "a new window");
  resetChatLimits();
  // The site's 20 sends a minute hold across sessions (a new sign-in does not reset them).
  for (let i = 0; i < 20; i++) assert.equal((await at(t0 + i, send(body({ request_id: crypto.randomUUID() }), { session: cookie(Math.floor(i / 5) + 10) }))).status, 202);
  const site = await failure(await at(t0 + 30_000, send(body({ request_id: crypto.randomUUID() }), { session: cookie(99) })));
  assert.deepEqual([site.status, site.code, site.retryAfter], [429, "rate_limited", "30"]);
  resetChatLimits();
  // An invalid send still counts: probing does not get free attempts.
  for (let i = 0; i < 6; i++) await at(t0, send(body({ max_tokens: 0 })));
  assert.equal((await failure(await at(t0, send()))).code, "rate_limited");
  resetChatLimits();
  assert.equal((await at(t0, send())).status, 202);
  const read = get(`devices/${DEVICE}/chat/${CLIENT_ID}`);
  for (let i = 0; i < 180; i++) assert.equal((await at(t0 + i, read.clone())).status, 200);
  const reads = await failure(await at(t0 + 59_000, read.clone()));
  assert.deepEqual([reads.status, reads.retryAfter], [429, "1"]);
  resetChatLimits();
  for (let s = 0; s < 7; s++) for (let i = 0; i < (s < 6 ? 180 : 120); i++) await at(t0, get(`devices/${DEVICE}/chat/${CLIENT_ID}`, cookie(s)));
  assert.equal((await failure(await at(t0, get(`devices/${DEVICE}/chat/${CLIENT_ID}`, cookie(8))))).code, "rate_limited", "1,200 reads a minute per site");
  resetChatLimits();
  for (let i = 0; i < 30; i++) assert.equal((await at(t0, get("chat/devices"))).status, 200);
  assert.equal((await failure(await at(t0, get("chat/devices")))).status, 429, "30 device lists a minute per session");
});

test("upstream failures are curated by status, 409 reasons by code, and Retry-After is passed through", async () => {
  const cases: Array<[number, Record<string, string>, unknown, number, string, string | null]> = [
    [401, {}, { error: "authentication required" }, 401, "authentication_required", null],
    [403, {}, { error: "requires role operator" }, 403, "forbidden", null],
    [404, {}, { error: "device not found" }, 404, "not_found", null],
    [409, {}, { error: "active release changed; refresh device before sending" }, 409, "release_changed", null],
    [409, {}, { error: "device already has a chat request in progress" }, 409, "device_busy", null],
    [409, {}, { error: "request_id was already used with different content" }, 409, "request_conflict", null],
    [409, {}, { error: "chat request belongs to an earlier device binding or control plane restore" }, 409, "device_changed", null],
    [409, {}, { error: "Device is offline or has no recent live report." }, 409, "device_offline", null],
    [409, {}, { error: "Device needs a healthy active model in production mode." }, 409, "device_not_ready", null],
    [409, {}, { error: "something private and new" }, 409, "device_busy", null],
    [413, {}, { error: "chat request body too large" }, 413, "request_too_large", null],
    [422, {}, { error: "invalid request: messages.0.content: private" }, 422, "invalid_request", null],
    [429, { "Retry-After": "17" }, { error: "chat relay capacity reached" }, 429, "rate_limited", "17"],
    [429, {}, { error: "chat relay capacity reached" }, 429, "rate_limited", "60"],
    [429, { "Retry-After": "soon" }, {}, 429, "rate_limited", "60"],
    [500, { "Retry-After": "5" }, { error: "Traceback private/path.py" }, 503, "upstream_unavailable", null],
    [503, { "Retry-After": "1" }, { error: "database busy; retry" }, 503, "upstream_unavailable", "1"],
    [502, {}, "<html>private proxy page</html>", 503, "upstream_unavailable", null],
  ];
  for (const [status, headers, data, expected, code, retry] of cases) {
    mockUpstream(controlPlane({ [`POST /api/v1/devices/${DEVICE}/chat`]: () => typeof data === "string" ? new Response(data, { status, headers }) : Response.json(data, { status, headers }) }));
    const response = await call(send());
    const result = await failure(response);
    assert.deepEqual([result.status, result.code, result.retryAfter], [expected, code, retry], `${status} ${JSON.stringify(data)}`);
    assert.equal(/private|Traceback|operator|capacity reached|relay capacity|refresh device/.test(result.message), false, "the API's text is never forwarded");
    resetChatLimits();
  }
  mockUpstream(() => { throw new TypeError("connect ECONNREFUSED 10.0.0.5:8080"); });
  const down = await failure(await call(send()));
  assert.deepEqual([down.status, down.code], [503, "upstream_unavailable"]);
  assert.equal(down.message.includes("10.0.0.5"), false);
  mockUpstream(controlPlane({ [`GET /api/v1/devices/${DEVICE}/chat/.*`]: () => Response.json({ error: "chat request not found" }, { status: 404 }) }));
  assert.equal((await failure(await call(get(`devices/${DEVICE}/chat/${CLIENT_ID}`)))).code, "not_found");
  // A misconfigured API origin is a curated 503 too.
  process.env.CONVOY_API_URL = "http://external.example.test";
  try { assert.deepEqual(Object.values(await failure(await call(get("chat/devices")))).slice(0, 2), [503, "unavailable"]); }
  finally { process.env.CONVOY_API_URL = "http://127.0.0.1:18085"; }
});

test("the device list: physical devices with the release the platform records and the runtime the device reports", async () => {
  const calls = mockUpstream(controlPlane({
    "GET /api/v1/chat/devices": () => Response.json({ devices: [
      { ...availability({ id: "dev_simulator", name: "Simulator", simulated: true, release_id: "rel_sim" }) },
      availability({ max_tokens: 4096, private: "x" }),
      availability({ id: "dev_spare", name: "Spare", online: false, eligible: false, reason: "Device is offline or has no recent live report.", release_id: null }),
      availability({ id: "dev_other", name: "Other", eligible: false, reason: "An unexpected private reason" }),
      availability({ id: "../escape" }), { id: "dev_nosim", name: "No flag", online: true, eligible: true },
    ] }),
    "GET /api/v1/devices/dev_spare": () => Response.json({ error: "device not found" }, { status: 404 }),
    "GET /api/v1/devices/dev_other": () => Response.json({ ...physical, id: "dev_other", runtime_evidence: {} }),
  }));
  const response = await call(get("chat/devices"));
  assert.equal(response.status, 200);
  const data = await response.json() as { contract: string; fetched_at: string; limits: Record<string, number>; devices: Array<Record<string, unknown>> };
  assert.equal(data.contract, CHAT_CONTRACT);
  assert.deepEqual(data.limits, {
    window_s: 60, sends_per_session: 6, sends_per_site: 20, reads_per_session: 180, reads_per_site: 1200, lists_per_session: 30, lists_per_site: 120,
    max_messages: 16, max_text_bytes: 8192, max_tokens: 128, request_ttl_s: 120, result_ttl_s: 300, in_progress_per_device: 1,
  });
  assert.deepEqual(data.devices.map(d => d.id), [DEVICE, "dev_spare", "dev_other"], "simulated, malformed and unflagged devices are not listed");
  assert.deepEqual(data.devices[0], {
    id: DEVICE, name: "Board", status: "online", online: true, eligible: true, reason: null, reason_code: null, release_id: RELEASE,
    max_tokens: 128, context_window: 2048, agent_version: "0.9.0", hardware_profile: "jetson-orin-nano-8gb",
    release: {
      id: RELEASE, name: "Edge model", version: "3", digest: "d".repeat(64),
      model: { repo: "example-org/Example-1B-GGUF", revision: "c".repeat(40), file: "example-1b-q4_k_m.gguf", sha256: "e".repeat(64), quantization: "Q4_K_M", name: "Example 1B", architecture: "qwen2" },
      runtime: { name: "llama.cpp", backend: "cuda", version: "b6550" }, decoding: { temperature: 0, seed: 42 }, context_window: 2048, output_limit: 128,
    },
    runtime: { backend: "cuda", build: "b6550-5266f24d", context_window: 2048, gpu_layers: 29, gpu_layers_total: 29 },
  });
  assert.deepEqual([data.devices[1].eligible, data.devices[1].reason_code, data.devices[1].reason, data.devices[1].release, data.devices[1].runtime, data.devices[1].status],
    [false, "device_offline", "The device is offline or has no recent live report.", null, null, "offline"]);
  assert.deepEqual([data.devices[2].reason_code, data.devices[2].reason, data.devices[2].runtime], ["not_ready", "The device is not ready for chat. Refresh its status before sending.", null]);
  const text = JSON.stringify(data);
  for (const leak of ["private", "token=", "/var/lib", "--api-key", "template", "usr_", "artifact", "rel_sim", "provenance", "Simulator"]) {
    assert.equal(text.includes(leak), false, `no ${leak}`);
  }
  assert.equal(calls.filter(c => c.path === `/api/v1/releases/${RELEASE}`).length, 1, "each release is read once");
  // The portal's check: an eligible device whose release the platform cannot show (missing, simulated, another id) is not eligible.
  for (const changed of [() => Response.json({ error: "release not found" }, { status: 404 }), () => Response.json({ ...release, simulated: true }), () => Response.json({ ...release, id: "rel_other" })]) {
    resetChatLimits();
    mockUpstream(controlPlane({ [`GET /api/v1/releases/${RELEASE}`]: changed }));
    const device = (await (await call(get("chat/devices"))).json() as { devices: Array<Record<string, unknown>> }).devices[0];
    assert.deepEqual([device.eligible, device.reason_code, device.release], [false, "model_unavailable", null]);
  }
  // A release without header metadata reports what is known and null for the rest (never a guess).
  resetChatLimits();
  mockUpstream(controlPlane({ [`GET /api/v1/releases/${RELEASE}`]: () => Response.json({ ...release, spec: { model: { gguf: {} } }, runtime: null, config: null, model: { repo: "example-org/Example-1B-GGUF" } }) }));
  const bare = (await (await call(get("chat/devices"))).json() as { devices: Array<{ release: Record<string, Record<string, unknown>> }> }).devices[0].release;
  assert.deepEqual(bare.model, { repo: "example-org/Example-1B-GGUF", revision: null, file: null, sha256: null, quantization: null, name: null, architecture: null });
  assert.deepEqual(bare.runtime, { name: null, backend: null, version: null });
  // A named device comes alone, with its details; anything else in the query is refused.
  resetChatLimits();
  const named = mockUpstream(controlPlane({
    "GET /api/v1/chat/devices": () => Response.json({ devices: [availability({ id: "dev_spare", name: "Spare" }), availability()] }),
    "GET /api/v1/devices/dev_spare": () => Response.json({ ...physical, id: "dev_spare" }),
  }));
  const one = await (await call(get(`chat/devices?device_id=${DEVICE}`))).json() as { devices: Array<Record<string, unknown>> };
  assert.deepEqual(one.devices.map(d => d.id), [DEVICE]);
  assert.ok(one.devices[0].release && one.devices[0].runtime);
  assert.deepEqual(named.filter(c => c.path.startsWith("/api/v1/devices/")).map(c => c.path), [`/api/v1/devices/${DEVICE}`], "details only for the named device");
  for (const query of ["device_id=dev_UPPER", "device_id=a&device_id=b", "other=1", `device_id=${DEVICE}&x=1`]) {
    const refused = await failure(await call(get(`chat/devices?${query}`)));
    assert.deepEqual([refused.status, refused.code], [422, "invalid_request"], query);
  }
  // Beyond the first eight, devices keep the control plane's own eligibility, without details.
  resetChatLimits();
  const many = Array.from({ length: 10 }, (_, i) => availability({ id: `dev_board${i}`, name: `Board ${i}` }));
  const reads = mockUpstream(controlPlane({
    "GET /api/v1/chat/devices": () => Response.json({ devices: many }),
    "GET /api/v1/devices/dev_board[0-9]": (_method, path) => Response.json({ ...physical, id: path.split("/").at(-1) }),
  }));
  const ten = (await (await call(get("chat/devices"))).json() as { devices: Array<Record<string, unknown>> }).devices;
  assert.deepEqual(ten.map(d => [d.eligible, d.release !== null]), [...Array(8).fill([true, true]), [true, false], [true, false]]);
  assert.equal(reads.length, 1 + 8 + 1, "the list, eight devices and their one release");
  // The session is the caller's: a refused session or role stays 401/403.
  for (const status of [401, 403]) {
    resetChatLimits();
    mockUpstream(controlPlane({ "GET /api/v1/chat/devices": () => Response.json({ error: "x" }, { status }) }));
    assert.equal((await call(get("chat/devices"))).status, status);
  }
});
