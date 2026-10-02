/**
 * The public device chat contract, `platform-chat-v1`: the signed-in user's own session forwarded to the
 * control plane's chat relay (`control-plane/server/convoy_server/routers/chat.py`). `proxyPlatform`
 * serves three routes from here:
 *
 * - `GET  /api/platform/chat/devices[?device_id=dev_…]`: the physical devices (or the one named) with
 *   their chat availability, the active release as the platform records it (model repo, file,
 *   quantization, runtime, decoding), the runtime the device itself reports, and the limits below.
 *   Simulated devices are not listed. Release and runtime details come with the first eight devices
 *   listed, and always with a named one.
 * - `POST /api/platform/devices/{device}/chat`: `{request_id, expected_release_id, messages, max_tokens}`
 *   answers 202 with the request.
 * - `GET  /api/platform/devices/{device}/chat/{request_id}`: the request, until `succeeded`, `failed`
 *   or `expired`, with its trace id and release id.
 *
 * What it keeps from the device portal routes it replaces (`/api/portal/chat`, `/api/portal/snapshot`):
 * the `convoy_session` cookie only (never a shared token); exact Origin and `X-Convoy-Client: web` on
 * the POST; the backend's roles and ownership (viewers list and read, operators send; the control plane
 * lets a request's creator or an administrator read it); a device must be physical; strict body and id
 * validation with UTF-8 limits; the per-session and per-site rate limits; request ids namespaced per
 * session (an HMAC keyed by the session credential), which narrows reads to the session that sent the
 * request: another session, of the same account or an administrator, gets 404;
 * bounded bodies and eight-second deadlines; no redirects or retried writes; curated errors with
 * `Retry-After` on 429. The control plane enforces one request in progress per device (409
 * `device_busy`) and expires a request 120 s after it is made; a result stays readable for 300 s.
 *
 * Errors are `{"error": "<curated message>", "code": "<code>"}`; upstream error text, headers and
 * internal ids never reach the caller.
 */
import { createHash, createHmac } from "node:crypto";

if (typeof window !== "undefined") throw new Error("Device chat is server-only");

export const CHAT_CONTRACT = "platform-chat-v1";
/** Fixed one-minute windows, per website process (the portal's numbers). Scale-out needs a shared limiter. */
export const CHAT_LIMITS = {
  windowS: 60,
  sendsPerSession: 6, sendsPerSite: 20,
  readsPerSession: 180, readsPerSite: 1200,
  listsPerSession: 30, listsPerSite: 120,
  maxMessages: 16, maxTextBytes: 8192, maxTokens: 128, maxBodyBytes: 64 * 1024,
  requestTtlS: 120, resultTtlS: 300, inProgressPerDevice: 1,
} as const;

const BUCKETS = 4096;
const BODY_MS = 8000;
const UPSTREAM_MS = 8000;
const RESPONSE_LIMIT = 2 * 1024 * 1024;
const ERROR_LIMIT = 4 * 1024;
/** Devices listed, and how many of them get their release and runtime details (one upstream read each). */
const MAX_DEVICES = 64;
const MAX_DETAILED = 8;
const DEVICE = /^dev_[a-z0-9]{1,60}$/;
const RELEASE = /^[A-Za-z0-9_-]{1,64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TRACE = /^tr_[a-f0-9]{1,61}$/;
const HEX64 = /^[a-f0-9]{64}$/;
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
const STATUSES = ["queued", "running", "succeeded", "failed", "expired"] as const;

export type DeviceChatRoute =
  | { kind: "devices" }
  | { kind: "send"; deviceId: string }
  | { kind: "read"; deviceId: string; requestId: string };

export interface ChatRelease {
  id: string; name: string | null; version: string | null; digest: string | null;
  model: { repo: string | null; revision: string | null; file: string | null; sha256: string | null; quantization: string | null; name: string | null; architecture: string | null };
  runtime: { name: string | null; backend: string | null; version: string | null };
  decoding: { temperature: number | null; seed: number | null };
  context_window: number | null; output_limit: number | null;
}
export interface ChatDeviceRuntime { backend: string | null; build: string | null; context_window: number | null; gpu_layers: number | null; gpu_layers_total: number | null }
export interface ChatDevice {
  id: string; name: string; status: string; online: boolean; eligible: boolean;
  reason: string | null; reason_code: string | null; release_id: string | null;
  max_tokens: number; context_window: number | null;
  agent_version: string | null; hardware_profile: string | null;
  release: ChatRelease | null; runtime: ChatDeviceRuntime | null;
}
export interface ChatDeviceList { contract: typeof CHAT_CONTRACT; fetched_at: string; limits: Record<string, number>; devices: ChatDevice[] }
export interface ChatInput { request_id: string; expected_release_id: string; messages: Array<{ role: "user" | "assistant"; content: string }>; max_tokens: number }
export interface ChatRequestResult {
  id: string; device_id: string; release_id: string; status: typeof STATUSES[number];
  created_at: string | null; expires_at: string | null;
  content: string | null; finish_reason: "stop" | "length" | null;
  usage: null | { prompt_tokens: number | null; completion_tokens: number | null; total_tokens: number | null };
  metrics: null | { latency_ms: number | null; ttft_ms: number | null; queue_ms: number | null };
  trace_id: string | null; error: null | { code: string; message: string };
}
/** What the chat routes need from the platform proxy (passed in, so this module does not import it). */
export interface ChatDeps {
  consoleOrigin(request: Request): string;
  ownCookie(request: Request): string | null;
  platformOrigin(): string;
  now?(): number;
}

class ChatFailure extends Error {
  constructor(public status: number, public code: string, message: string, public retryAfter?: number) { super(message); }
}

const UNAVAILABLE = "Device chat is temporarily unavailable.";
const UNREACHABLE = "The device connection is temporarily unavailable. Check the same request before sending another message.";
const INVALID = "Use up to 16 messages, 8 KiB of text, and 1–128 output tokens.";
const NOT_READY = "The device is not ready for chat. Refresh its status before sending.";
const UPSTREAM: Record<number, [string, string]> = {
  401: ["authentication_required", "Your session ended. Sign in to your Convoy workspace."],
  403: ["forbidden", "Your account does not have permission for this device action."],
  404: ["not_found", "This device or request is not available. The device may not have received it yet."],
  413: ["request_too_large", "The conversation is too large."],
  422: ["invalid_request", "The request could not be accepted. Check the messages and the output limit."],
  429: ["rate_limited", "The chat relay is at capacity. Wait before sending another request."],
};
/** The control plane's reasons a device cannot chat (its `reason` text and 409 detail), by their start. */
const READINESS: Array<[string, string, string]> = [
  ["Chat requires a physical", "not_physical", "Chat requires a physical device."],
  ["Device credentials are inactive", "credentials_inactive", "The device's credentials are inactive."],
  ["Device agent needs the chat relay update", "agent_update_required", "The device agent needs the chat relay update."],
  ["Device is offline", "device_offline", "The device is offline or has no recent live report."],
  ["Control plane dispatch is paused", "dispatch_paused", "Dispatch is paused or restoring. Try again later."],
  ["Device has a deployment or evaluation in progress", "operation_in_progress", "The device has a deployment or evaluation in progress."],
  ["Device needs a healthy active model", "model_not_ready", "The device needs a healthy active model in production mode."],
];
/** The control plane's other 409 reasons on these routes, by their start. */
const CONFLICTS: Array<[string, string, string]> = [
  ["active release changed", "release_changed", "The device's active model changed. Refresh the device before sending."],
  ["device already has a chat request in progress", "device_busy", "The device is answering another request. Wait for it to finish."],
  ["request_id was already used with different content", "request_conflict", "This request identifier was already used with different content."],
  ["chat request belongs to an earlier device binding", "device_changed", "The device or the control plane changed since this request was made."],
];
const RESULT_ERRORS: Record<string, string> = {
  request_expired: "The request expired. It will not be run again automatically.",
  device_changed: "The active device or model changed during this request.",
  context_length_exceeded: "The conversation exceeds the model context. Start a new conversation.",
  timeout: "The model did not finish within its deadline.",
};

// ---- routing -------------------------------------------------------------------------------------------

/** The chat route for a platform path and method, or null (the generic allowlist then decides). */
export function deviceChatRoute(parts: readonly string[], method: string): DeviceChatRoute | null {
  if (method === "GET" && parts.length === 2 && parts[0] === "chat" && parts[1] === "devices") return { kind: "devices" };
  if (parts.length >= 3 && parts[0] === "devices" && parts[2] === "chat") {
    if (method === "POST" && parts.length === 3) return { kind: "send", deviceId: parts[1] };
    if (method === "GET" && parts.length === 4) return { kind: "read", deviceId: parts[1], requestId: parts[3] };
  }
  return null;
}

export async function proxyDeviceChat(request: Request, route: DeviceChatRoute, deps: ChatDeps): Promise<Response> {
  try {
    const now = deps.now?.() ?? Date.now();
    if (route.kind === "send" && (request.headers.get("origin") !== deps.consoleOrigin(request) || request.headers.get("x-convoy-client") !== "web")) {
      throw new ChatFailure(403, "invalid_origin", "This request could not be verified. Reload the workspace and try again.");
    }
    const cookie = deps.ownCookie(request);
    if (!cookie) throw new ChatFailure(401, "authentication_required", "Sign in to your Convoy workspace.");
    const key = createHash("sha256").update(cookie).digest("hex");
    if (route.kind === "devices") {
      take("lists-site", CHAT_LIMITS.listsPerSite, now);
      take(`lists:${key}`, CHAT_LIMITS.listsPerSession, now);
      return reply(await listDevices({ origin: deps.platformOrigin(), cookie }, now, deviceQuery(new URL(request.url).searchParams)));
    }
    if (route.kind === "send") {
      take("sends-site", CHAT_LIMITS.sendsPerSite, now);
      take(`sends:${key}`, CHAT_LIMITS.sendsPerSession, now);
      const deviceId = device(route.deviceId);
      const input = chatInput(await jsonBody(request));
      const api = { origin: deps.platformOrigin(), cookie };
      assertPhysical(await upstream(api, `/api/v1/devices/${deviceId}`), deviceId);
      const upstreamId = upstreamRequestId(cookie, input.request_id);
      const result = await upstream(api, `/api/v1/devices/${deviceId}/chat`, { ...input, request_id: upstreamId });
      return reply(curateChat(result, input.request_id, upstreamId, deviceId), 202);
    }
    take("reads-site", CHAT_LIMITS.readsPerSite, now);
    take(`reads:${key}`, CHAT_LIMITS.readsPerSession, now);
    const deviceId = device(route.deviceId);
    const upstreamId = upstreamRequestId(cookie, route.requestId);
    const api = { origin: deps.platformOrigin(), cookie };
    const [state, result] = await Promise.all([
      upstream(api, `/api/v1/devices/${deviceId}`),
      upstream(api, `/api/v1/devices/${deviceId}/chat/${upstreamId}`),
    ]);
    assertPhysical(state, deviceId);
    return reply(curateChat(result, route.requestId, upstreamId, deviceId));
  } catch (error) {
    const failure = error instanceof ChatFailure ? error : new ChatFailure(503, "unavailable", UNAVAILABLE);
    return reply({ error: failure.message, code: failure.code }, failure.status, failure.retryAfter ? { "Retry-After": String(failure.retryAfter) } : {});
  }
}

const PRIVATE_HEADERS = { "Cache-Control": "private, no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff" };
function reply(value: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return Response.json(value, { status, headers: { ...PRIVATE_HEADERS, ...headers } });
}

// ---- limits --------------------------------------------------------------------------------------------

const buckets = new Map<string, { count: number; until: number }>();
/** Counts one request against a fixed one-minute window; 429 with the seconds to the window's end. */
function take(key: string, limit: number, now: number) {
  for (const [name, entry] of buckets) if (entry.until <= now) buckets.delete(name);
  let entry = buckets.get(key);
  if (!entry) {
    if (buckets.size >= BUCKETS) throw new ChatFailure(429, "rate_limited", "Device chat is busy. Please wait a minute and try again.", CHAT_LIMITS.windowS);
    entry = { count: 0, until: now + CHAT_LIMITS.windowS * 1000 };
    buckets.set(key, entry);
  }
  if (++entry.count > limit) {
    throw new ChatFailure(429, "rate_limited", "Too many requests. Wait before trying again.", Math.max(1, Math.ceil((entry.until - now) / 1000)));
  }
}
/** Tests only: forget every rate-limit window. */
export function resetChatLimits() { buckets.clear(); }

// ---- input ---------------------------------------------------------------------------------------------

/** The upstream request UUID: the session credential keys an HMAC of the caller's UUID (the portal's derivation). */
export function upstreamRequestId(cookie: string, clientId: string): string {
  if (!UUID.test(clientId)) throw new ChatFailure(422, "invalid_request", "The request identifier is invalid.");
  const bytes = createHmac("sha256", cookie).update(`chat\0${clientId.toLowerCase()}`).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 15) | 0x40;
  bytes[8] = (bytes[8] & 63) | 0x80;
  const h = bytes.toString("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function device(id: string): string {
  if (!DEVICE.test(id)) throw new ChatFailure(404, "not_found", "This device is not available.");
  return id;
}

/** The device list's only query: one `device_id`, or none. */
function deviceQuery(search: URLSearchParams): string | null {
  if (!search.size) return null;
  const ids = search.getAll("device_id");
  if (search.size !== 1 || ids.length !== 1 || !DEVICE.test(ids[0])) throw new ChatFailure(422, "invalid_request", "Name one device with device_id=dev_….");
  return ids[0];
}

/** Exactly `{request_id, expected_release_id, messages, max_tokens}`; user and assistant text only. */
export function chatInput(value: unknown): ChatInput {
  const bad = (): never => { throw new ChatFailure(422, "invalid_request", INVALID); };
  const v = record(value);
  const keys = ["request_id", "expected_release_id", "messages", "max_tokens"];
  if (value === null || typeof value !== "object" || Array.isArray(value) || Object.keys(v).length !== keys.length || !keys.every(key => Object.hasOwn(v, key))) return bad();
  if (typeof v.request_id !== "string" || !UUID.test(v.request_id) || typeof v.expected_release_id !== "string" || !RELEASE.test(v.expected_release_id)) return bad();
  if (typeof v.max_tokens !== "number" || !Number.isInteger(v.max_tokens) || v.max_tokens < 1 || v.max_tokens > CHAT_LIMITS.maxTokens) return bad();
  if (!Array.isArray(v.messages) || v.messages.length < 1 || v.messages.length > CHAT_LIMITS.maxMessages) return bad();
  let bytes = 0;
  const messages = v.messages.map(item => {
    const m = record(item);
    if (item === null || typeof item !== "object" || Array.isArray(item) || Object.keys(m).length !== 2 || (m.role !== "user" && m.role !== "assistant")
      || typeof m.content !== "string" || !m.content.length || LONE_SURROGATE.test(m.content)) return bad();
    bytes += Buffer.byteLength(m.content as string, "utf8");
    return { role: m.role as "user" | "assistant", content: m.content as string };
  });
  if (bytes > CHAT_LIMITS.maxTextBytes || messages.at(-1)?.role !== "user") return bad();
  return { request_id: v.request_id.toLowerCase(), expected_release_id: v.expected_release_id, messages, max_tokens: v.max_tokens };
}

async function jsonBody(request: Request): Promise<unknown> {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get("content-type") ?? "")) {
    throw new ChatFailure(415, "invalid_content_type", "Send a JSON request.");
  }
  const tooLarge = () => new ChatFailure(413, "request_too_large", "The request is too large.");
  const length = request.headers.get("content-length");
  if (length !== null && (!/^\d{1,12}$/.test(length) || Number(length) > CHAT_LIMITS.maxBodyBytes)) throw tooLarge();
  if (!request.body) throw new ChatFailure(400, "invalid_request", "The request body is missing.");
  const bytes = await bounded(request.body, CHAT_LIMITS.maxBodyBytes, BODY_MS, tooLarge,
    () => new ChatFailure(408, "request_timeout", "The request took too long. Please try again."));
  let text: string;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { throw new ChatFailure(400, "invalid_request", "The request must be UTF-8 JSON."); }
  try { return JSON.parse(text); } catch { throw new ChatFailure(400, "invalid_request", "The request must contain valid JSON."); }
}

async function bounded(body: ReadableStream<Uint8Array>, maximum: number, timeoutMs: number, tooLarge: () => ChatFailure, late: () => ChatFailure): Promise<Uint8Array> {
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(late()), timeoutMs); });
  try {
    while (true) {
      const { done, value } = await Promise.race([reader.read(), expired]);
      if (done) break;
      size += value.byteLength;
      if (size > maximum) throw tooLarge();
      chunks.push(value);
    }
    return Buffer.concat(chunks);
  } catch (error) {
    void reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    clearTimeout(timer);
    reader.releaseLock();
  }
}

// ---- upstream ------------------------------------------------------------------------------------------

interface Api { origin: string; cookie: string }

/** One control-plane call with the caller's session; JSON in and out, bounded, never redirected or retried. */
async function upstream(api: Api, path: string, body?: unknown): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${api.origin}${path}`, {
      method: body === undefined ? "GET" : "POST", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(UPSTREAM_MS),
      headers: { Cookie: api.cookie, "X-Convoy-Client": "web", Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    throw new ChatFailure(503, "upstream_unavailable", UNREACHABLE);
  }
  if (!response.ok) throw await upstreamFailure(response);
  try {
    if (!response.body) throw new Error("missing body");
    const bytes = await bounded(response.body, RESPONSE_LIMIT, UPSTREAM_MS, () => new ChatFailure(503, "upstream_unavailable", UNREACHABLE),
      () => new ChatFailure(503, "upstream_unavailable", UNREACHABLE));
    return JSON.parse(Buffer.from(bytes).toString("utf8"));
  } catch {
    throw new ChatFailure(503, "upstream_unavailable", UNREACHABLE);
  }
}

/** The same call, where a missing resource (404) is null rather than an error. */
async function optional(api: Api, path: string): Promise<unknown> {
  try { return await upstream(api, path); } catch (error) {
    if (error instanceof ChatFailure && error.status === 404) return null;
    throw error;
  }
}

/** A curated failure for an upstream error status. The API's own text is read only to pick a known reason. */
async function upstreamFailure(response: Response): Promise<ChatFailure> {
  const retry = retryAfter(response.headers.get("retry-after"));
  if (response.status === 409) {
    const detail = await errorText(response);
    const known = [...CONFLICTS, ...READINESS].find(([start]) => detail?.startsWith(start));
    if (!known) return new ChatFailure(409, "device_busy", "The device or its active model changed, or another request is running. Refresh before sending again.");
    const [, code, message] = known;
    return new ChatFailure(409, READINESS.includes(known) ? (code === "device_offline" ? "device_offline" : "device_not_ready") : code, message);
  }
  await response.body?.cancel().catch(() => undefined);
  const known = UPSTREAM[response.status];
  if (response.status === 429) return new ChatFailure(429, known[0], known[1], retry ?? CHAT_LIMITS.windowS);
  if (known) return new ChatFailure(response.status, known[0], known[1]);
  return new ChatFailure(503, "upstream_unavailable", UNREACHABLE, response.status === 503 ? retry : undefined);
}

function retryAfter(value: string | null): number | undefined {
  return value !== null && /^\d{1,4}$/.test(value) && Number(value) >= 1 && Number(value) <= 3600 ? Number(value) : undefined;
}

async function errorText(response: Response): Promise<string | null> {
  try {
    if (!response.body) return null;
    const bytes = await bounded(response.body, ERROR_LIMIT, UPSTREAM_MS, () => new ChatFailure(503, "upstream_unavailable", UNREACHABLE),
      () => new ChatFailure(503, "upstream_unavailable", UNREACHABLE));
    const error = record(JSON.parse(Buffer.from(bytes).toString("utf8"))).error;
    return typeof error === "string" ? error : null;
  } catch {
    return null;
  }
}

function assertPhysical(value: unknown, deviceId: string) {
  const d = record(value);
  if (d.id !== deviceId) throw new ChatFailure(503, "upstream_unavailable", "The device response could not be verified.");
  if (d.simulated !== false) throw new ChatFailure(409, "device_not_ready", "Chat requires a physical device.");
}

// ---- curation ------------------------------------------------------------------------------------------

const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const finite = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const count = (value: unknown, min = 0): number | null => Number.isInteger(value) && (value as number) >= min ? value as number : null;
/** A short printable string, else null (not reported). Overlong values are not truncated into something else. */
function text(value: unknown, max: number): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value) ? value : null;
}
const hex64 = (value: unknown): string | null => typeof value === "string" && HEX64.test(value) ? value : null;

export function curateChat(value: unknown, clientId: string, upstreamId: string, deviceId: string): ChatRequestResult {
  const v = record(value);
  if (v.id !== upstreamId || v.device_id !== deviceId || typeof v.release_id !== "string" || !RELEASE.test(v.release_id)
    || !STATUSES.includes(v.status as typeof STATUSES[number])) {
    throw new ChatFailure(503, "upstream_unavailable", "The device response could not be verified.");
  }
  const usage = record(v.usage), metrics = record(v.metrics), error = record(v.error);
  const errorCode = typeof error.code === "string" ? error.code : null;
  return {
    id: clientId.toLowerCase(), device_id: deviceId, release_id: v.release_id, status: v.status as ChatRequestResult["status"],
    created_at: text(v.created_at, 40), expires_at: text(v.expires_at, 40),
    content: typeof v.content === "string" ? v.content.slice(0, 16384) : null,
    finish_reason: v.finish_reason === "stop" || v.finish_reason === "length" ? v.finish_reason : null,
    usage: v.usage ? { prompt_tokens: count(usage.prompt_tokens), completion_tokens: count(usage.completion_tokens), total_tokens: count(usage.total_tokens) } : null,
    metrics: v.metrics ? { latency_ms: finite(metrics.latency_ms), ttft_ms: finite(metrics.ttft_ms), queue_ms: finite(metrics.queue_ms) } : null,
    trace_id: typeof v.trace_id === "string" && TRACE.test(v.trace_id) ? v.trace_id : null,
    error: errorCode ? { code: RESULT_ERRORS[errorCode] ? errorCode : "request_failed", message: RESULT_ERRORS[errorCode] ?? "The device could not complete this request." } : null,
  };
}

/** The release as the platform records it: model identity, quantization (from the GGUF header), runtime and decoding. */
export function curateRelease(value: unknown, releaseId: string): ChatRelease | null {
  const r = record(value);
  if (r.id !== releaseId || r.simulated !== false) return null;
  const model = record(r.model), file = record(model.file), gguf = record(record(record(r.spec).model).gguf);
  const runtime = record(r.runtime), config = record(r.config);
  const path = text(file.path, 512);
  const commit = text(runtime.commit, 64);
  return {
    id: releaseId, name: text(r.name, 128), version: text(r.version, 64), digest: hex64(r.digest),
    model: {
      repo: text(model.repo, 160), revision: text(model.commit, 64) ?? text(model.revision, 64),
      file: path ? text(path.split("/").at(-1), 160) : null, sha256: hex64(file.sha256),
      quantization: text(gguf.file_type, 32), name: text(gguf.name, 128), architecture: text(gguf.architecture, 32),
    },
    runtime: { name: text(runtime.name, 64), backend: text(runtime.backend, 32), version: text(runtime.tag, 64) ?? (commit ? commit.slice(0, 12) : null) },
    decoding: { temperature: finite(config.temperature), seed: count(config.seed) },
    context_window: count(config.ctx_size, 1), output_limit: count(config.n_predict, 1),
  };
}

/** The runtime the device reports (its live runtime evidence); no paths, arguments or hashes. */
export function curateRuntime(value: unknown): ChatDeviceRuntime | null {
  const r = record(value);
  if (!Object.keys(r).length) return null;
  return {
    backend: text(r.backend, 32), build: text(r.build_info, 128), context_window: count(r.n_ctx, 1),
    gpu_layers: count(r.gpu_offloaded_layers), gpu_layers_total: count(r.gpu_total_layers),
  };
}

function readiness(reason: unknown): [string, string] {
  const known = READINESS.find(([start]) => typeof reason === "string" && reason.startsWith(start));
  return known ? [known[1], known[2]] : ["not_ready", NOT_READY];
}

async function listDevices(api: Api, now: number, only: string | null): Promise<ChatDeviceList> {
  const listed = list(record(await upstream(api, "/api/v1/chat/devices")).devices).map(record)
    .filter(d => d.simulated === false && typeof d.id === "string" && DEVICE.test(d.id) && (only === null || d.id === only))
    .slice(0, MAX_DEVICES);
  const detailed = listed.slice(0, MAX_DETAILED);
  const releaseIds = [...new Set(detailed.map(d => d.release_id).filter((id): id is string => typeof id === "string" && RELEASE.test(id)))];
  const [releases, details] = await Promise.all([
    Promise.all(releaseIds.map(id => optional(api, `/api/v1/releases/${id}`))),
    Promise.all(detailed.map(d => optional(api, `/api/v1/devices/${d.id as string}`))),
  ]);
  const releaseById = new Map(releaseIds.map((id, i) => [id, releases[i]]));
  const devices = listed.map((chat, i): ChatDevice => {
    const id = chat.id as string;
    const releaseId = typeof chat.release_id === "string" && RELEASE.test(chat.release_id) ? chat.release_id : null;
    const detailed = i < MAX_DETAILED;
    const release = releaseId && detailed ? curateRelease(releaseById.get(releaseId), releaseId) : null;
    const detail = detailed && details[i] && record(details[i]).id === id ? record(details[i]) : null;
    // The portal's check, where the release was read: the active model must be the physical release the
    // platform records. (The control plane's own eligibility already requires one.)
    const eligible = chat.eligible === true && (!detailed || release !== null);
    const [reasonCode, reason] = eligible ? [null, null]
      : chat.eligible === true ? ["model_unavailable", "The device's active model is unavailable."] : readiness(chat.reason);
    return {
      id, name: text(chat.name, 128) ?? id,
      status: (detail && text(detail.status, 32)) ?? (chat.online === true ? "online" : "offline"),
      online: chat.online === true, eligible, reason, reason_code: reasonCode, release_id: releaseId,
      max_tokens: Math.min(CHAT_LIMITS.maxTokens, Math.max(1, count(chat.max_tokens, 1) ?? CHAT_LIMITS.maxTokens)),
      context_window: count(chat.context_window, 1),
      agent_version: detail ? text(detail.agent_version, 64) : null, hardware_profile: detail ? text(detail.profile_id, 64) : null,
      release, runtime: detail ? curateRuntime(detail.runtime_evidence) : null,
    };
  });
  return {
    contract: CHAT_CONTRACT, fetched_at: new Date(now).toISOString(),
    limits: {
      window_s: CHAT_LIMITS.windowS, sends_per_session: CHAT_LIMITS.sendsPerSession, sends_per_site: CHAT_LIMITS.sendsPerSite,
      reads_per_session: CHAT_LIMITS.readsPerSession, reads_per_site: CHAT_LIMITS.readsPerSite,
      lists_per_session: CHAT_LIMITS.listsPerSession, lists_per_site: CHAT_LIMITS.listsPerSite,
      max_messages: CHAT_LIMITS.maxMessages, max_text_bytes: CHAT_LIMITS.maxTextBytes, max_tokens: CHAT_LIMITS.maxTokens,
      request_ttl_s: CHAT_LIMITS.requestTtlS, result_ttl_s: CHAT_LIMITS.resultTtlS, in_progress_per_device: CHAT_LIMITS.inProgressPerDevice,
    },
    devices,
  };
}
