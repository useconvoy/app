import test from "node:test";
import assert from "node:assert/strict";
import { allowedPlatformPath, platformOrigin, proxyPlatform } from "../../src/lib/platform/proxy";

const session = "convoy_session=cvs_abcdefghijklmnopqrstuvwx";
const origin = "https://console.example.test";
function request(path: string, options: RequestInit = {}) {
  return new Request(`${origin}/api/platform/${path}`, { ...options,
    headers: { Cookie: `${session}; unrelated=private`, Origin: origin, "X-Convoy-Client": "web",
      "Content-Type": "application/json", ...options.headers } });
}

test("allowlist excludes controller/device writes, path traversal, unknown query keys and duplicate scope", () => {
  assert.equal(allowedPlatformPath(["robots"], "GET", new URLSearchParams("project_id=prj_one")), "/api/v1/robots?project_id=prj_one");
  assert.equal(allowedPlatformPath(["deployments"], "GET", new URLSearchParams("project_id=prj_one&robot_id=rob_one")), "/api/v1/deployments?project_id=prj_one&robot_id=rob_one");
  for (const path of [["admin"], ["devices", "dev_one", "deploy"], ["..", "admin"], ["auth", "tokens"], ["https:", "evil.test"]]) {
    assert.equal(allowedPlatformPath(path, "POST", new URLSearchParams()), null);
  }
  assert.equal(allowedPlatformPath(["robots"], "GET", new URLSearchParams("project_id=a&project_id=b")), null);
  assert.equal(allowedPlatformPath(["projects"], "GET", new URLSearchParams("url=http://other")), null);
  assert.equal(allowedPlatformPath(["robots"], "GET", new URLSearchParams()), null);
  assert.equal(allowedPlatformPath(["projects"], "DELETE", new URLSearchParams()), null);
  assert.equal(platformOrigin("http://localhost:8080"), "http://localhost:8080");
  for (const url of ["http://external.test", "https://user:pass@example.test", "https://example.test/base", "https://example.test?url=other"]) assert.throws(() => platformOrigin(url));
});

test("proxy forwards only caller session, preserves idempotency, and never follows redirects or retries writes", async () => {
  const fetch = globalThis.fetch;
  process.env.CONVOY_API_URL = "http://127.0.0.1:8080";
  let calls = 0;
  globalThis.fetch = async (input, init) => {
    calls++;
    assert.equal(String(input), "http://127.0.0.1:8080/api/v1/projects");
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("cookie"), session);
    assert.equal(headers.get("authorization"), null);
    assert.equal(headers.get("idempotency-key"), "same-attempt");
    assert.equal(headers.get("x-convoy-client"), "web");
    assert.equal(init?.redirect, "error");
    assert.equal(init?.cache, "no-store");
    return Response.json({ id: "prj_real" }, { status: 201 });
  };
  try {
    const response = await proxyPlatform(request("projects", { method: "POST", body: '{"name":"real"}', headers: { "Idempotency-Key": "same-attempt", Authorization: "Bearer attacker" } }), ["projects"]);
    assert.equal(response.status, 201);
    assert.equal(calls, 1);
    assert.match(response.headers.get("cache-control") ?? "", /no-store/);
    assert.deepEqual(await response.json(), { id: "prj_real" });
  } finally { globalThis.fetch = fetch; }
});

test("cross-origin, missing CSRF header, missing session and oversized bodies are rejected before fetch", async () => {
  const fetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("must not reach upstream"); };
  try {
    for (const headers of [{ Origin: "https://other.test" }, { "X-Convoy-Client": "" }] as Record<string, string>[]) {
      assert.equal((await proxyPlatform(request("auth/login", { method: "POST", body: "{}", headers }), ["auth", "login"])).status, 403);
    }
    assert.equal((await proxyPlatform(request("projects", { headers: { Cookie: "unrelated=private" } }), ["projects"])).status, 401);
    assert.equal((await proxyPlatform(request("projects", { method: "POST", body: "x".repeat(256 * 1024 + 1) }), ["projects"])).status, 413);
  } finally { globalThis.fetch = fetch; }
});

test("session forwarding hardens cookies, strips unrelated cookies, and redacts upstream errors", async () => {
  const fetch = globalThis.fetch;
  let failure = false;
  globalThis.fetch = async () => failure
    ? Response.json({ error: "private-token-and-database-path" }, { status: 500 })
    : Response.json({ user: { email: "me@example.test" } }, { headers: {
      "Set-Cookie": `${session}; Domain=internal.test; Path=/; Max-Age=600; SameSite=Lax`,
    } });
  try {
    const logged = await proxyPlatform(request("auth/login", { method: "POST", body: "{}" }), ["auth", "login"]);
    assert.equal(logged.status, 200);
    const cookie = logged.headers.get("set-cookie") ?? "";
    assert.match(cookie, /Path=\/api\/platform; HttpOnly; SameSite=Strict; Secure; Max-Age=600/);
    assert.doesNotMatch(cookie, /Domain=|internal/);
    failure = true;
    const failed = await proxyPlatform(request("projects"), ["projects"]);
    assert.equal(failed.status, 503);
    assert.doesNotMatch(await failed.text(), /private-token|database-path/);
  } finally { globalThis.fetch = fetch; }
});
