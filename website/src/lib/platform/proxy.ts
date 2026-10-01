/** The console forwards the caller's own session to an operator-configured API. */
if (typeof window !== "undefined") throw new Error("Platform proxy is server-only");

const REQUEST_LIMIT = 256 * 1024;
const RESPONSE_LIMIT = 2 * 1024 * 1024;
// A workspace document is at most 2 MiB of compact JSON; the margin covers its request/response envelope.
const DOCUMENT_LIMIT = 2 * 1024 * 1024 + 64 * 1024;
const ID = "[A-Za-z0-9_-]{1,64}";
const DOCUMENT = "workspace-documents/[a-z0-9][a-z0-9-]{0,63}";
const MUTATIONS = ["POST", "PUT", "DELETE"];
const COOKIE = "convoy_session";
// A conditional document write names the revision it replaces (If-Match) or creates (If-None-Match: *).
const REVISION = /^"[1-9][0-9]{0,17}"$/;
const ERROR_LIMIT = 4 * 1024;
const UNAVAILABLE = "The management service is unavailable. Refresh before retrying an action.";

class ProxyFailure extends Error {
  constructor(public status: number, message: string, public headers: Record<string, string> = {}) { super(message); }
}

const messages: Record<number, string> = {
  401: "Sign in with your Convoy account to continue.",
  403: "Your account cannot perform this action, or the request could not be verified.",
  404: "This resource is unavailable in your project.",
  409: "The state changed or this request conflicts with existing work. Refresh before continuing.",
  410: "This request has expired. Refresh to inspect its outcome.",
  413: "The request is too large.",
  422: "Check the required fields and the supplied release manifest.",
  429: "Too many requests. Wait a moment before trying again.",
};

// Workspace documents: conditional saves, a per-account write budget and a document size limit.
const documentMessages: Record<number, string> = {
  ...messages,
  404: "This workspace document does not exist.",
  409: "This save conflicts with the stored workspace documents. Reload before saving again.",
  412: "The workspace document changed since it was loaded. Reload it and apply your change again.",
  413: "The workspace document is larger than the 2 MiB limit.",
  422: "The workspace document or its save request is not valid.",
  428: "The save did not name the document revision it replaces. Reload and try again.",
  429: "Too many workspace saves. Wait a moment before trying again.",
};
// The API's 409 reasons for a document write, by the start of its (otherwise unforwarded) error text.
const documentConflicts: [string, string][] = [
  ["workspace document limit reached", "Your account has reached its workspace document limit. Delete a document before creating another."],
  ["Idempotency-Key was already used", "This save was already submitted with different content. Reload before saving again."],
];

export function platformOrigin(value = process.env.CONVOY_API_URL ?? "http://127.0.0.1:8080"): string {
  const url = new URL(value);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  // Opt-in for the existing single-host Compose network; never browser supplied.
  const compose = process.env.CONVOY_API_INTERNAL_HTTP === "1" && url.origin === "http://control-plane:8080";
  if (url.username || url.password || url.search || url.hash || url.pathname !== "/"
      || (url.protocol !== "https:" && !((local || compose) && url.protocol === "http:"))) {
    throw new ProxyFailure(503, "The management connection is not configured correctly.");
  }
  return url.origin;
}

export function allowedPlatformPath(parts: string[], method: string, search: URLSearchParams): string | null {
  if (!parts.length || parts.some(part => !/^[A-Za-z0-9_-]{1,64}$/.test(part))) return null;
  const path = parts.join("/");
  let keys: string[] = [];
  let required: string[] = [];
  let allowed = false;
  if (method === "GET") {
    allowed = /^(auth\/me|projects|devices|workspace-documents)$/.test(path)
      || new RegExp(`^(devices|deployments|missions|episodes)/${ID}$`).test(path)
      || new RegExp(`^applications/${ID}/(releases|evaluation-suites|evaluation-gate)$`).test(path)
      || new RegExp(`^evaluation-suites/${ID}$`).test(path)
      || new RegExp(`^episodes/${ID}/replay(?:/frames/[0-9]{1,4})?$`).test(path)
      || new RegExp(`^${DOCUMENT}$`).test(path);
    if (["robots", "applications", "missions", "evaluations"].includes(path)) { allowed = true; keys = ["project_id"]; required = keys; }
    if (path === "deployments") { allowed = true; keys = ["project_id", "robot_id"]; required = ["project_id"]; }
    if (path === "episodes") { allowed = true; keys = ["mission_id"]; required = keys; }
    if (new RegExp(`^evaluations/${ID}$`).test(path)) { allowed = true; keys = ["baseline_id"]; }
    if (new RegExp(`^applications/${ID}/qualification$`).test(path)) { allowed = true; keys = ["release_id"]; required = keys; }
  } else if (method === "POST") {
    allowed = /^(auth\/(login|logout)|projects|robots|applications|deployments|enrollments|evaluations)$/.test(path)
      || new RegExp(`^applications/${ID}/(releases|evaluation-suites|evaluation-gate)$`).test(path)
      || new RegExp(`^robots/${ID}/missions$`).test(path)
      || new RegExp(`^missions/${ID}/cancel$`).test(path)
      || new RegExp(`^evaluations/${ID}/(cancel|promote)$`).test(path);
  } else if (method === "PUT" || method === "DELETE") {
    // The caller's own workspace documents are the only replaceable or deletable resources.
    allowed = new RegExp(`^${DOCUMENT}$`).test(path);
  }
  if (!allowed) return null;
  for (const [key, value] of search) {
    if (!keys.includes(key) || search.getAll(key).length !== 1 || !new RegExp(`^${ID}$`).test(value)) return null;
  }
  if (required.some(key => !search.has(key))) return null;
  return `/api/v1/${path}${search.size ? `?${search.toString()}` : ""}`;
}

export function consoleOrigin(request: Request): string {
  const configured = process.env.CONVOY_CONSOLE_ORIGIN ?? process.env.PORTAL_PUBLIC_ORIGIN;
  const actual = new URL(request.url).origin;
  if (!configured) return actual;
  const parsed = new URL(configured);
  if (parsed.origin !== configured || parsed.username || parsed.password) throw new ProxyFailure(503, "Console origin is not configured correctly.");
  return parsed.origin;
}

export function ownCookie(request: Request): string | null {
  const entries = request.headers.get("cookie")?.split(";").map(value => value.trim()).filter(value => value.startsWith(`${COOKIE}=`));
  if (entries?.length !== 1) return null;
  const entry = entries[0];
  return /^convoy_session=cvs_[A-Za-z0-9_-]{16,128}$/.test(entry) ? entry : null;
}

async function boundedBody(body: ReadableStream<Uint8Array> | null, maximum: number, tooLarge = messages[413]): Promise<Uint8Array<ArrayBuffer>> {
  if (!body) return new Uint8Array(0);
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<never>((_, reject) => { timeout = setTimeout(() => reject(new ProxyFailure(408, "The request timed out.")), 10000); });
  try {
    while (true) {
      const { done, value } = await Promise.race([reader.read(), expired]);
      if (done) break;
      bytes += value.byteLength;
      if (bytes > maximum) throw new ProxyFailure(413, tooLarge);
      chunks.push(value);
    }
    const joined = new Uint8Array(bytes);
    let offset = 0;
    for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.byteLength; }
    return joined;
  } catch (error) {
    void reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    clearTimeout(timeout);
    reader.releaseLock();
  }
}

function utf8(bytes: Uint8Array<ArrayBuffer>): string {
  return Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength).toString("utf8");
}

/** The curated message for a document write's 409, from the API's short error text. */
async function conflictMessage(upstream: Response): Promise<string | undefined> {
  try {
    const data: unknown = JSON.parse(utf8(await boundedBody(upstream.body, ERROR_LIMIT)));
    const error = data !== null && typeof data === "object" ? (data as { error?: unknown }).error : undefined;
    return typeof error === "string" ? documentConflicts.find(([start]) => error.startsWith(start))?.[1] : undefined;
  } catch {
    return undefined;
  }
}

function responseCookie(value: string, secure: boolean): string | null {
  const segments = value.split(";").map(part => part.trim());
  const pair = segments[0];
  if (!/^convoy_session=(?:cvs_[A-Za-z0-9_-]{16,128}|""|)$/.test(pair)) return null;
  const duration = segments.find(part => /^max-age=\d+$/i.test(part));
  const expires = segments.find(part => /^expires=/i.test(part));
  // Scope to the BFF. Provider Domain/Path attributes and unrelated cookies never reach the browser.
  return [pair, "Path=/api", "HttpOnly", "SameSite=Strict", secure ? "Secure" : "",
    duration ?? "", expires ?? ""].filter(Boolean).join("; ");
}

const PRIVATE_HEADERS = { "Cache-Control": "private, no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff" };

function json(value: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return Response.json(value, { status, headers: { ...PRIVATE_HEADERS, ...headers } });
}

export async function proxyPlatform(request: Request, parts: string[]): Promise<Response> {
  try {
    const origin = consoleOrigin(request);
    const path = allowedPlatformPath(parts, request.method, new URL(request.url).searchParams);
    if (!path) throw new ProxyFailure(404, "This console action is unavailable.");
    const document = parts[0] === "workspace-documents";
    const text = document ? documentMessages : messages;
    const mutation = MUTATIONS.includes(request.method);
    if (mutation && (request.headers.get("origin") !== origin || request.headers.get("x-convoy-client") !== "web")) {
      throw new ProxyFailure(403, messages[403]);
    }
    const login = parts.join("/") === "auth/login";
    const logout = parts.join("/") === "auth/logout";
    const cookie = ownCookie(request);
    if (!cookie && !login && !logout) throw new ProxyFailure(401, messages[401]);
    const requestLimit = document ? DOCUMENT_LIMIT : REQUEST_LIMIT;
    const responseLimit = document ? DOCUMENT_LIMIT : RESPONSE_LIMIT;
    const headers = new Headers({ Accept: "application/json" });
    if (cookie) headers.set("Cookie", cookie);
    if (mutation) headers.set("X-Convoy-Client", "web");
    const key = request.headers.get("idempotency-key");
    if (key) {
      if (!/^[\x21-\x7e]{1,128}$/.test(key)) throw new ProxyFailure(422, text[422]);
      headers.set("Idempotency-Key", key);
    }
    if (document && mutation) {
      // Only well-formed document preconditions reach the API; reads are never made conditional.
      const ifMatch = request.headers.get("if-match");
      const ifNoneMatch = request.headers.get("if-none-match");
      if (ifMatch !== null) {
        if (!REVISION.test(ifMatch)) throw new ProxyFailure(422, text[422]);
        headers.set("If-Match", ifMatch);
      }
      if (ifNoneMatch !== null) {
        if (request.method !== "PUT" || ifNoneMatch !== "*") throw new ProxyFailure(422, text[422]);
        headers.set("If-None-Match", "*");
      }
    }
    let body: Uint8Array<ArrayBuffer> | string | undefined;
    if (mutation && request.method !== "DELETE") {
      if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get("content-type") ?? "")) throw new ProxyFailure(422, text[422]);
      const length = request.headers.get("content-length");
      if (length && (!/^\d+$/.test(length) || Number(length) > requestLimit)) throw new ProxyFailure(413, text[413]);
      const bytes = await boundedBody(request.body, requestLimit, text[413]);
      if (document) {
        // Up to 2 MiB pass through as received: the API decodes and validates a document only after
        // authenticating the caller, so the console does not parse it as well.
        body = bytes;
      } else {
        body = utf8(bytes);
        try { JSON.parse(body); } catch { throw new ProxyFailure(422, messages[422]); }
      }
      headers.set("Content-Type", "application/json");
    }
    const upstream = await fetch(`${platformOrigin()}${path}`, {
      method: request.method, headers, body, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000),
    });
    if (!upstream.ok) {
      const status = text[upstream.status] ? upstream.status : 503;
      let message = login && status === 401 ? "The email or password was not accepted." : text[status] ?? UNAVAILABLE;
      if (document && status === 409) message = await conflictMessage(upstream) ?? message;
      else await upstream.body?.cancel();
      const retry = upstream.headers.get("retry-after");
      throw new ProxyFailure(status, message, status === 429 && retry && /^\d{1,6}$/.test(retry) ? { "Retry-After": retry } : {});
    }
    if (upstream.status === 204) {
      await upstream.body?.cancel();
      return new Response(null, { status: 204, headers: PRIVATE_HEADERS });
    }
    if (document) {
      // The API's JSON passes through unparsed; it is bounded like any other response.
      if (!/^application\/json(?:\s*;|$)/i.test(upstream.headers.get("content-type") ?? "")) {
        await upstream.body?.cancel();
        throw new ProxyFailure(503, UNAVAILABLE);
      }
      const bytes = await boundedBody(upstream.body, responseLimit);
      return new Response(bytes, { status: upstream.status, headers: { ...PRIVATE_HEADERS, "Content-Type": "application/json" } });
    }
    const data: unknown = JSON.parse(utf8(await boundedBody(upstream.body, responseLimit)));
    const response = json(data, upstream.status);
    if (login || logout || (parts.join("/") === "auth/me" && cookie)) {
      // A validated legacy session can move to the shared BFF scope without a
      // second login. This does not renew its control-plane expiry.
      const cookies = login || logout ? upstream.headers.getSetCookie() : [cookie!];
      const ours = cookies.map(value => responseCookie(value, origin.startsWith("https:"))).find(Boolean);
      if (!ours) throw new ProxyFailure(503, "The session response could not be verified.");
      response.headers.append("Set-Cookie", ours);
      // Retire both old paths so browsers cannot send ambiguous sessions.
      response.headers.append("Set-Cookie", `convoy_session=; Path=/api/platform; HttpOnly; SameSite=Strict; Max-Age=0${origin.startsWith("https:") ? "; Secure" : ""}`);
      response.headers.append("Set-Cookie", "__Host-convoy_portal=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0");
    }
    return response;
  } catch (error) {
    if (error instanceof ProxyFailure) return json({ error: error.message }, error.status, error.headers);
    return json({ error: UNAVAILABLE }, 503);
  }
}
