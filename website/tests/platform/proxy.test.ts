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
  assert.equal(allowedPlatformPath(["applications", "app_one", "qualification"], "GET", new URLSearchParams("release_id=rel_one")), "/api/v1/applications/app_one/qualification?release_id=rel_one");
  assert.equal(allowedPlatformPath(["applications", "app_one", "qualification"], "GET", new URLSearchParams()), null);
  assert.equal(allowedPlatformPath(["evaluations", "eva_one"], "GET", new URLSearchParams()), "/api/v1/evaluations/eva_one");
  assert.equal(allowedPlatformPath(["evaluations", "eva_one"], "GET", new URLSearchParams("baseline_id=eva_two")), "/api/v1/evaluations/eva_one?baseline_id=eva_two");
  assert.equal(allowedPlatformPath(["evaluations", "eva_one"], "GET", new URLSearchParams("baseline_id=a&baseline_id=b")), null);
  assert.equal(allowedPlatformPath(["evaluations", "eva_one", "promote"], "POST", new URLSearchParams()), "/api/v1/evaluations/eva_one/promote");
  assert.equal(allowedPlatformPath(["evaluations", "eva_one", "promote"], "GET", new URLSearchParams()), null);
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
    assert.match(cookie, /Path=\/api; HttpOnly; SameSite=Strict; Secure; Max-Age=600/);
    assert.doesNotMatch(cookie, /Domain=|internal/);
    assert.ok(logged.headers.getSetCookie().some(value => value.includes("Path=/api/platform;") && value.includes("Max-Age=0")));
    assert.ok(logged.headers.getSetCookie().some(value => value.startsWith("__Host-convoy_portal=;") && value.includes("Max-Age=0")));
    const migrated = await proxyPlatform(request("auth/me"), ["auth", "me"]);
    assert.equal(migrated.status, 200);
    assert.ok(migrated.headers.getSetCookie().some(value => value.startsWith(session + "; Path=/api;")));
    failure = true;
    const failed = await proxyPlatform(request("projects"), ["projects"]);
    assert.equal(failed.status, 503);
    assert.doesNotMatch(await failed.text(), /private-token|database-path/);
  } finally { globalThis.fetch = fetch; }
});

test("replay is read-only and accepts only bounded frame paths without path or URL inputs", () => {
  for (const path of ["episodes/epi_one/replay", "episodes/epi_one/replay/frames/54"]) {
    assert.equal(allowedPlatformPath(path.split("/"), "GET", new URLSearchParams()), `/api/v1/${path}`);
    assert.equal(allowedPlatformPath(path.split("/"), "POST", new URLSearchParams()), null);
    assert.equal(allowedPlatformPath(path.split("/"), "GET", new URLSearchParams("path=/private/file")), null);
  }
  for (const frame of ["-1", "1.2", "10000", "..", "anything"]) {
    assert.equal(allowedPlatformPath(["episodes", "epi_one", "replay", "frames", frame], "GET", new URLSearchParams()), null);
  }
});

const documentPath = ["workspace-documents", "configurations"];
const documentLimit = 2 * 1024 * 1024 + 64 * 1024;

test("workspace documents are the only PUT/DELETE routes and accept only contract names", () => {
  assert.equal(allowedPlatformPath(["workspace-documents"], "GET", new URLSearchParams()), "/api/v1/workspace-documents");
  assert.equal(allowedPlatformPath(["workspace-documents"], "GET", new URLSearchParams("owner=usr_other")), null);
  for (const method of ["GET", "PUT", "DELETE"]) {
    assert.equal(allowedPlatformPath(documentPath, method, new URLSearchParams()), "/api/v1/workspace-documents/configurations");
    assert.equal(allowedPlatformPath(documentPath, method, new URLSearchParams("owner=usr_other")), null);
    for (const name of ["Configurations", "-leading", "under_score", "a".repeat(65), ".."]) {
      assert.equal(allowedPlatformPath(["workspace-documents", name], method, new URLSearchParams()), null);
    }
  }
  assert.equal(allowedPlatformPath(["workspace-documents", "a".repeat(64)], "PUT", new URLSearchParams()), `/api/v1/workspace-documents/${"a".repeat(64)}`);
  assert.equal(allowedPlatformPath(["workspace-documents", "0-sample-2"], "DELETE", new URLSearchParams()), "/api/v1/workspace-documents/0-sample-2");
  assert.equal(allowedPlatformPath(documentPath, "POST", new URLSearchParams()), null);
  assert.equal(allowedPlatformPath(documentPath, "PATCH", new URLSearchParams()), null);
  assert.equal(allowedPlatformPath([...documentPath, "extra"], "GET", new URLSearchParams()), null);
  for (const method of ["PUT", "DELETE"]) {
    for (const path of [["workspace-documents"], ["projects"], ["devices", "dev_one"], ["robots", "rob_one"], ["auth", "me"], ["tokens", "tok_one"]]) {
      assert.equal(allowedPlatformPath(path, method, new URLSearchParams()), null);
    }
  }
});

test("document writes forward session, method, key and a full 2 MiB document; deletes forward no body", async () => {
  const fetch = globalThis.fetch;
  process.env.CONVOY_API_URL = "http://127.0.0.1:8080";
  const blob = "x".repeat(2 * 1024 * 1024 - 11); // {"blob":"…"} is exactly 2 MiB of compact JSON
  const body = JSON.stringify({ schema_version: 1, body: { blob } });
  const calls: { method?: string; body?: unknown; headers: Headers }[] = [];
  globalThis.fetch = async (input, init) => {
    assert.equal(String(input), "http://127.0.0.1:8080/api/v1/workspace-documents/configurations");
    const headers = new Headers(init?.headers);
    calls.push({ method: init?.method, body: init?.body, headers });
    assert.equal(headers.get("cookie"), session);
    assert.equal(headers.get("authorization"), null);
    assert.equal(headers.get("x-convoy-client"), "web");
    assert.equal(init?.redirect, "error");
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    return Response.json({ name: "configurations", schema_version: 1, body: { blob }, size_bytes: 2 * 1024 * 1024, updated_at: "2026-10-01T00:00:00Z" });
  };
  try {
    const saved = await proxyPlatform(request("workspace-documents/configurations", { method: "PUT", body, headers: { "Idempotency-Key": "save-1", Authorization: "Bearer attacker" } }), documentPath);
    assert.equal(saved.status, 200);
    assert.match(saved.headers.get("cache-control") ?? "", /no-store/);
    assert.equal((await saved.json()).body.blob, blob); // larger than the ordinary 2 MiB response cap
    const removed = await proxyPlatform(request("workspace-documents/configurations", { method: "DELETE", headers: { "Idempotency-Key": "remove-1" } }), documentPath);
    assert.equal(removed.status, 204);
    assert.equal(await removed.text(), "");
    assert.match(removed.headers.get("cache-control") ?? "", /no-store/);
    assert.deepEqual(calls.map(call => call.method), ["PUT", "DELETE"]);
    assert.equal(calls[0].body, body);
    assert.equal(calls[0].headers.get("content-type"), "application/json");
    assert.equal(calls[0].headers.get("idempotency-key"), "save-1");
    assert.equal(calls[1].body, undefined);
    assert.equal(calls[1].headers.get("content-type"), null);
    assert.equal(calls[1].headers.get("idempotency-key"), "remove-1");
  } finally { globalThis.fetch = fetch; }
});

test("document writes are CSRF, session and size checked before fetch", async () => {
  const fetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("must not reach upstream"); };
  try {
    for (const method of ["PUT", "DELETE"]) {
      const body = method === "PUT" ? '{"schema_version":1,"body":{}}' : undefined;
      for (const headers of [{ Origin: "https://other.test" }, { "X-Convoy-Client": "" }] as Record<string, string>[]) {
        assert.equal((await proxyPlatform(request("workspace-documents/configurations", { method, body, headers }), documentPath)).status, 403);
      }
      assert.equal((await proxyPlatform(request("workspace-documents/configurations", { method, body, headers: { Cookie: "unrelated=private" } }), documentPath)).status, 401);
    }
    const put = (body: string, headers: Record<string, string> = {}) => proxyPlatform(request("workspace-documents/configurations", { method: "PUT", body, headers }), documentPath);
    assert.equal((await put("x".repeat(documentLimit + 1))).status, 413);
    assert.equal((await put("{}", { "Content-Length": String(documentLimit + 1) })).status, 413);
    assert.equal((await put("{}", { "Content-Type": "text/plain" })).status, 422);
    assert.equal((await put("{")).status, 422);
    assert.equal((await put("{}", { "Idempotency-Key": "has space" })).status, 422);
  } finally { globalThis.fetch = fetch; }
});

test("only document routes get the larger response allowance and upstream errors stay curated", async () => {
  const fetch = globalThis.fetch;
  let status = 200;
  globalThis.fetch = async () => status === 200
    ? Response.json({ blob: "x".repeat(2 * 1024 * 1024) })
    : Response.json({ error: "private-owner-and-database-detail" }, { status });
  try {
    assert.equal((await proxyPlatform(request("projects"), ["projects"])).status, 413);
    assert.equal((await proxyPlatform(request("workspace-documents/configurations"), documentPath)).status, 200);
    for (status of [404, 409, 413, 422]) {
      const failed = await proxyPlatform(request("workspace-documents/configurations"), documentPath);
      assert.equal(failed.status, status);
      assert.doesNotMatch(await failed.text(), /private-owner|database-detail/);
    }
  } finally { globalThis.fetch = fetch; }
});

 test("private HTTP is explicit and restricted to the existing Compose service", () => {
  const previous = process.env.CONVOY_API_INTERNAL_HTTP;
  try {
    delete process.env.CONVOY_API_INTERNAL_HTTP;
    assert.throws(() => platformOrigin("http://control-plane:8080"));
    process.env.CONVOY_API_INTERNAL_HTTP = "1";
    assert.equal(platformOrigin("http://control-plane:8080"), "http://control-plane:8080");
    assert.throws(() => platformOrigin("http://external.test"));
    assert.throws(() => platformOrigin("http://control-plane:8081"));
  } finally {
    if (previous === undefined) delete process.env.CONVOY_API_INTERNAL_HTTP;
    else process.env.CONVOY_API_INTERNAL_HTTP = previous;
  }
});
