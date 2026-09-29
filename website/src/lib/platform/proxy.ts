/** The console forwards the caller's own session to an operator-configured API. */
if (typeof window !== "undefined") throw new Error("Platform proxy is server-only");

const REQUEST_LIMIT = 256 * 1024;
const RESPONSE_LIMIT = 2 * 1024 * 1024;
const ID = "[A-Za-z0-9_-]{1,64}";
const COOKIE = "convoy_session";

class ProxyFailure extends Error {
  constructor(public status: number, message: string) { super(message); }
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
    allowed = /^(auth\/me|projects|devices)$/.test(path)
      || new RegExp(`^(devices|deployments|missions|episodes)/${ID}$`).test(path)
      || new RegExp(`^applications/${ID}/(releases|evaluation-suites|evaluation-gate)$`).test(path)
      || new RegExp(`^evaluation-suites/${ID}$`).test(path)
      || new RegExp(`^episodes/${ID}/replay(?:/frames/[0-9]{1,4})?$`).test(path);
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
  }
  if (!allowed) return null;
  for (const [key, value] of search) {
    if (!keys.includes(key) || search.getAll(key).length !== 1 || !new RegExp(`^${ID}$`).test(value)) return null;
  }
  if (required.some(key => !search.has(key))) return null;
  return `/api/v1/${path}${search.size ? `?${search.toString()}` : ""}`;
}

function consoleOrigin(request: Request): string {
  const configured = process.env.CONVOY_CONSOLE_ORIGIN ?? process.env.PORTAL_PUBLIC_ORIGIN;
  const actual = new URL(request.url).origin;
  if (!configured) return actual;
  const parsed = new URL(configured);
  if (parsed.origin !== configured || parsed.username || parsed.password) throw new ProxyFailure(503, "Console origin is not configured correctly.");
  return parsed.origin;
}

function ownCookie(request: Request): string | null {
  const entry = request.headers.get("cookie")?.split(";").map(value => value.trim()).find(value => value.startsWith(`${COOKIE}=`));
  if (!entry) return null;
  return /^convoy_session=cvs_[A-Za-z0-9_-]{16,128}$/.test(entry) ? entry : null;
}

async function boundedBody(body: ReadableStream<Uint8Array> | null, maximum: number): Promise<string> {
  if (!body) return "";
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
      if (bytes > maximum) throw new ProxyFailure(413, messages[413]);
      chunks.push(value);
    }
    return Buffer.concat(chunks).toString("utf8");
  } catch (error) {
    void reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    clearTimeout(timeout);
    reader.releaseLock();
  }
}

function responseCookie(value: string, secure: boolean): string | null {
  const segments = value.split(";").map(part => part.trim());
  const pair = segments[0];
  if (!/^convoy_session=(?:cvs_[A-Za-z0-9_-]{16,128}|""|)$/.test(pair)) return null;
  const duration = segments.find(part => /^max-age=\d+$/i.test(part));
  const expires = segments.find(part => /^expires=/i.test(part));
  // Scope to the BFF. Provider Domain/Path attributes and unrelated cookies never reach the browser.
  return [pair, "Path=/api/platform", "HttpOnly", "SameSite=Strict", secure ? "Secure" : "",
    duration ?? "", expires ?? ""].filter(Boolean).join("; ");
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: { "Cache-Control": "private, no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff" } });
}

export async function proxyPlatform(request: Request, parts: string[]): Promise<Response> {
  try {
    const origin = consoleOrigin(request);
    const path = allowedPlatformPath(parts, request.method, new URL(request.url).searchParams);
    if (!path) throw new ProxyFailure(404, "This console action is unavailable.");
    const mutation = request.method === "POST";
    if (mutation && (request.headers.get("origin") !== origin || request.headers.get("x-convoy-client") !== "web")) {
      throw new ProxyFailure(403, messages[403]);
    }
    const login = parts.join("/") === "auth/login";
    const logout = parts.join("/") === "auth/logout";
    const cookie = ownCookie(request);
    if (!cookie && !login && !logout) throw new ProxyFailure(401, messages[401]);
    let body: string | undefined;
    if (mutation) {
      if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get("content-type") ?? "")) throw new ProxyFailure(422, messages[422]);
      const length = request.headers.get("content-length");
      if (length && (!/^\d+$/.test(length) || Number(length) > REQUEST_LIMIT)) throw new ProxyFailure(413, messages[413]);
      body = await boundedBody(request.body, REQUEST_LIMIT);
      try { JSON.parse(body); } catch { throw new ProxyFailure(422, messages[422]); }
    }
    const headers = new Headers({ Accept: "application/json" });
    if (cookie) headers.set("Cookie", cookie);
    if (mutation) { headers.set("Content-Type", "application/json"); headers.set("X-Convoy-Client", "web"); }
    const key = request.headers.get("idempotency-key");
    if (key) {
      if (!/^[\x21-\x7e]{1,128}$/.test(key)) throw new ProxyFailure(422, messages[422]);
      headers.set("Idempotency-Key", key);
    }
    const upstream = await fetch(`${platformOrigin()}${path}`, {
      method: request.method, headers, body, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000),
    });
    if (!upstream.ok) {
      await upstream.body?.cancel();
      const status = messages[upstream.status] ? upstream.status : 503;
      throw new ProxyFailure(status, login && status === 401 ? "The email or password was not accepted." : messages[status] ?? "The management service is unavailable. Refresh before retrying an action.");
    }
    const data: unknown = JSON.parse(await boundedBody(upstream.body, RESPONSE_LIMIT));
    const response = json(data, upstream.status);
    if (login || logout) {
      const cookies = upstream.headers.getSetCookie();
      const ours = cookies.map(value => responseCookie(value, origin.startsWith("https:"))).find(Boolean);
      if (!ours) throw new ProxyFailure(503, "The session response could not be verified.");
      response.headers.set("Set-Cookie", ours);
    }
    return response;
  } catch (error) {
    if (error instanceof ProxyFailure) return json({ error: error.message }, error.status);
    return json({ error: "The management service is unavailable. Refresh before retrying an action." }, 503);
  }
}
