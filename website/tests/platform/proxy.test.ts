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
  assert.equal(allowedPlatformPath(["configurations"], "POST", new URLSearchParams()), "/api/v1/configurations");
  assert.equal(allowedPlatformPath(["applications", "app_one"], "GET", new URLSearchParams()), "/api/v1/applications/app_one");
  assert.equal(allowedPlatformPath(["applications", "app_one", "configuration-releases"], "POST", new URLSearchParams()), "/api/v1/applications/app_one/configuration-releases");
  assert.equal(allowedPlatformPath(["applications", "app_one", "configuration-releases"], "GET", new URLSearchParams()), null);
  assert.equal(allowedPlatformPath(["applications", "app_one", "releases", "apr_one", "setup"], "GET", new URLSearchParams()), "/api/v1/applications/app_one/releases/apr_one/setup");
  assert.equal(allowedPlatformPath(["applications", "app_one", "releases", "apr_one", "setup"], "POST", new URLSearchParams()), null);
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

const documentUrl = "http://127.0.0.1:8080/api/v1/workspace-documents/configurations";
const metadata = { name: "configurations", schema_version: 1, revision: 2, size_bytes: 2 * 1024 * 1024, updated_at: "2026-10-01T00:00:00Z" };

test("document writes forward session, key, precondition and the exact bytes of a full 2 MiB document", async () => {
  const fetch = globalThis.fetch;
  process.env.CONVOY_API_URL = "http://127.0.0.1:8080";
  const blob = "x".repeat(2 * 1024 * 1024 - 11); // {"blob":"…"} is exactly 2 MiB of compact JSON
  const body = JSON.stringify({ schema_version: 1, body: { blob } });
  const calls: { method?: string; body?: unknown; headers: Headers }[] = [];
  globalThis.fetch = async (input, init) => {
    assert.equal(String(input), documentUrl);
    const headers = new Headers(init?.headers);
    calls.push({ method: init?.method, body: init?.body, headers });
    assert.equal(headers.get("cookie"), session);
    assert.equal(headers.get("authorization"), null);
    assert.equal(headers.get("x-convoy-client"), "web");
    assert.equal(init?.redirect, "error");
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    return Response.json(metadata);
  };
  try {
    const created = await proxyPlatform(request("workspace-documents/configurations", { method: "PUT", body, headers: { "Idempotency-Key": "save-1", "If-None-Match": "*", Authorization: "Bearer attacker" } }), documentPath);
    assert.equal(created.status, 200);
    assert.match(created.headers.get("cache-control") ?? "", /no-store/);
    assert.equal(created.headers.get("content-type"), "application/json");
    assert.deepEqual(await created.json(), metadata);
    const replaced = await proxyPlatform(request("workspace-documents/configurations", { method: "PUT", body, headers: { "Idempotency-Key": "save-2", "If-Match": '"1"' } }), documentPath);
    assert.equal(replaced.status, 200);
    const removed = await proxyPlatform(request("workspace-documents/configurations", { method: "DELETE", headers: { "Idempotency-Key": "remove-1", "If-Match": '"2"' } }), documentPath);
    assert.equal(removed.status, 204);
    assert.equal(await removed.text(), "");
    assert.match(removed.headers.get("cache-control") ?? "", /no-store/);
    assert.deepEqual(calls.map(call => call.method), ["PUT", "PUT", "DELETE"]);
    assert.deepEqual(calls.map(call => [call.headers.get("if-none-match"), call.headers.get("if-match")]), [["*", null], [null, '"1"'], [null, '"2"']]);
    assert.ok(calls[0].body instanceof Uint8Array);
    assert.equal(new TextDecoder().decode(calls[0].body), body);
    assert.equal(calls[0].headers.get("content-type"), "application/json");
    assert.equal(calls[0].headers.get("idempotency-key"), "save-1");
    assert.equal(calls[2].body, undefined);
    assert.equal(calls[2].headers.get("content-type"), null);
    assert.equal(calls[2].headers.get("idempotency-key"), "remove-1");
  } finally { globalThis.fetch = fetch; }
});

test("document bodies pass through as bytes in both directions without JSON.parse", async () => {
  const fetch = globalThis.fetch;
  const parse = JSON.parse;
  // Formatting, non-ASCII text and even malformed JSON reach the API untouched: the API validates documents.
  const sent = new TextEncoder().encode('{"schema_version": 1, "body": {"name": "Bänk 東京 🤖"}}');
  const malformed = new TextEncoder().encode('{"schema_version":1,"body":{');
  const stored = '{"name":"configurations","schema_version":1,"revision":1,"body":{"name":"Bänk 東京 🤖", "spaced" : true},"size_bytes":42,"updated_at":"2026-10-01T00:00:00Z"}';
  const forwarded: Uint8Array[] = [];
  let contentType = "application/json";
  globalThis.fetch = async (_input, init) => {
    const headers = new Headers(init?.headers);
    if (init?.method === "PUT") { forwarded.push(init.body as Uint8Array); return Response.json(metadata); }
    // Reads are never made conditional.
    assert.equal(headers.get("if-none-match"), null);
    assert.equal(headers.get("if-match"), null);
    return new Response(stored, { headers: { "Content-Type": contentType } });
  };
  let parsed = 0;
  JSON.parse = ((...args: Parameters<typeof JSON.parse>) => { parsed++; return parse(...args); }) as typeof JSON.parse;
  let read: Response | undefined;
  try {
    for (const body of [sent, malformed]) {
      const saved = await proxyPlatform(request("workspace-documents/configurations", { method: "PUT", body, headers: { "Idempotency-Key": "save", "If-None-Match": "*" } }), documentPath);
      assert.equal(saved.status, 200);
    }
    read = await proxyPlatform(request("workspace-documents/configurations", { headers: { "If-None-Match": '"1"', "If-Match": "junk" } }), documentPath);
  } finally { JSON.parse = parse; globalThis.fetch = fetch; }
  assert.equal(parsed, 0);
  assert.deepEqual(forwarded.map(bytes => Buffer.from(bytes).toString("hex")), [sent, malformed].map(bytes => Buffer.from(bytes).toString("hex")));
  assert.equal(read.status, 200);
  assert.equal(read.headers.get("content-type"), "application/json");
  assert.equal(await read.text(), stored);
  // Only JSON passes through.
  globalThis.fetch = async () => new Response("<html>proxy error page</html>", { headers: { "Content-Type": contentType } });
  try {
    contentType = "text/html";
    const html = await proxyPlatform(request("workspace-documents/configurations"), documentPath);
    assert.equal(html.status, 503);
    assert.doesNotMatch(await html.text(), /proxy error page/);
  } finally { globalThis.fetch = fetch; }
});

test("document requests are CSRF, session, precondition and size checked before fetch", async () => {
  const fetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("must not reach upstream"); };
  const invalid = { error: "The workspace document or its save request is not valid." };
  try {
    for (const method of ["PUT", "DELETE"]) {
      const body = method === "PUT" ? '{"schema_version":1,"body":{}}' : undefined;
      for (const headers of [{ Origin: "https://other.test" }, { "X-Convoy-Client": "" }] as Record<string, string>[]) {
        assert.equal((await proxyPlatform(request("workspace-documents/configurations", { method, body, headers }), documentPath)).status, 403);
      }
      assert.equal((await proxyPlatform(request("workspace-documents/configurations", { method, body, headers: { Cookie: "unrelated=private" } }), documentPath)).status, 401);
      for (const ifMatch of ["1", 'W/"1"', '"01"', '"0"', '"one"', "*", '"1", "2"', `"${"9".repeat(19)}"`]) {
        const refused = await proxyPlatform(request("workspace-documents/configurations", { method, body, headers: { "If-Match": ifMatch } }), documentPath);
        assert.equal(refused.status, 422, ifMatch);
        assert.deepEqual(await refused.json(), invalid);
      }
    }
    const put = (body: string, headers: Record<string, string> = {}) => proxyPlatform(request("workspace-documents/configurations", { method: "PUT", body, headers }), documentPath);
    const tooLarge = await put("x".repeat(documentLimit + 1));
    assert.equal(tooLarge.status, 413);
    assert.deepEqual(await tooLarge.json(), { error: "The workspace document is larger than the 2 MiB limit." });
    assert.equal((await put("{}", { "Content-Length": String(documentLimit + 1) })).status, 413);
    for (const headers of [{ "Content-Type": "text/plain" }, { "Idempotency-Key": "has space" }, { "If-None-Match": '"1"' }, { "If-None-Match": "W/*" }] as Record<string, string>[]) {
      const refused = await put("{}", headers);
      assert.equal(refused.status, 422);
      assert.deepEqual(await refused.json(), invalid);
    }
    const conditionalDelete = await proxyPlatform(request("workspace-documents/configurations", { method: "DELETE", headers: { "If-None-Match": "*" } }), documentPath);
    assert.equal(conditionalDelete.status, 422);
  } finally { globalThis.fetch = fetch; }
});

test("document errors get accurate curated messages and keep Retry-After; other routes keep theirs", async () => {
  const fetch = globalThis.fetch;
  let status = 200;
  let error = "private-owner-and-database-detail";
  let headers: Record<string, string> = {};
  globalThis.fetch = async () => status === 200
    ? Response.json({ blob: "x".repeat(2 * 1024 * 1024) })
    : Response.json({ error }, { status, headers });
  const read = async (path = documentPath) => {
    const response = await proxyPlatform(request(path.join("/")), path);
    const text = await response.text();
    assert.doesNotMatch(text, /private-owner|database-detail|per account/);
    return { status: response.status, body: JSON.parse(text) as unknown, retry: response.headers.get("retry-after") };
  };
  try {
    assert.equal((await proxyPlatform(request("projects"), ["projects"])).status, 413);
    assert.equal((await proxyPlatform(request("workspace-documents/configurations"), documentPath)).status, 200);
    const expected: Record<number, string> = {
      404: "This workspace document does not exist.",
      409: "This save conflicts with the stored workspace documents. Reload before saving again.",
      412: "The workspace document changed since it was loaded. Reload it and apply your change again.",
      413: "The workspace document is larger than the 2 MiB limit.",
      422: "The workspace document or its save request is not valid.",
      428: "The save did not name the document revision it replaces. Reload and try again.",
      429: "Too many workspace saves. Wait a moment before trying again.",
    };
    for (status of [404, 409, 412, 413, 422, 428, 429]) {
      assert.deepEqual(await read(), { status, body: { error: expected[status] }, retry: null });
    }
    status = 429;
    headers = { "Retry-After": "120" };
    assert.equal((await read()).retry, "120");
    headers = { "Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT" };
    assert.equal((await read()).retry, null);
    headers = {};
    // The two reasons for a 409 on a document write, recognized from the API's text but never forwarded.
    status = 409;
    error = "workspace document limit reached (16 per account); delete one first";
    assert.deepEqual((await read()).body, { error: "Your account has reached its workspace document limit. Delete a document before creating another." });
    error = "Idempotency-Key was already used with a different payload";
    assert.deepEqual((await read()).body, { error: "This save was already submitted with different content. Reload before saving again." });
    // Other routes keep their own vocabulary, and statuses only documents use stay unavailable there.
    status = 422;
    assert.deepEqual((await read(["projects"])).body, { error: "Check the required fields and the supplied release manifest." });
    for (status of [412, 428]) assert.equal((await read(["projects"])).status, 503);
  } finally { globalThis.fetch = fetch; }
});

const oev = "oev_abcdefgh2345";
const oep = "oep_abcdefgh2345";
const episodeLimit = 16 * 1024 * 1024;

test("offline evaluations: reads, creation, episode uploads and deletion, with exact ids only", () => {
  const allowed = (path: string, method: string, search = "") => allowedPlatformPath(path.split("/"), method, new URLSearchParams(search));
  for (const path of ["offline-evaluations", `offline-evaluations/${oev}`, `offline-evaluations/${oev}/episodes/${oep}/replay`, `offline-evaluations/${oev}/episodes/${oep}/replay/frames/0`, `offline-evaluations/${oev}/episodes/${oep}/replay/frames/2048`]) {
    assert.equal(allowed(path, "GET"), `/api/v1/${path}`);
    assert.equal(allowed(path, "GET", "owner=usr_other"), null);
  }
  assert.equal(allowed("offline-evaluations", "POST"), "/api/v1/offline-evaluations");
  assert.equal(allowed(`offline-evaluations/${oev}/episodes`, "POST"), `/api/v1/offline-evaluations/${oev}/episodes`);
  for (const path of [`offline-evaluations/${oev}`, `offline-evaluations/${oev}/episodes/${oep}`]) assert.equal(allowed(path, "DELETE"), `/api/v1/${path}`);
  for (const [path, methods] of [
    ["offline-evaluations", ["PUT", "DELETE", "PATCH"]],
    [`offline-evaluations/${oev}`, ["POST", "PUT", "PATCH"]],
    [`offline-evaluations/${oev}/episodes`, ["GET", "PUT", "DELETE"]],
    [`offline-evaluations/${oev}/episodes/${oep}`, ["GET", "POST", "PUT"]],
    [`offline-evaluations/${oev}/episodes/${oep}/replay`, ["POST", "DELETE"]],
    ["offline-evaluations/eva_abcdefgh2345", ["GET", "DELETE"]],
    ["offline-evaluations/oev_ABCDEFGH2345", ["GET", "DELETE"]],
    ["offline-evaluations/oev_short", ["GET", "DELETE"]],
    [`offline-evaluations/${oev}/episodes/epi_abcdefgh2345/replay`, ["GET"]],
    [`offline-evaluations/${oev}/episodes/${oep}/replay/frames/10000`, ["GET"]],
    [`offline-evaluations/${oev}/episodes/${oep}/replay/frames/..`, ["GET"]],
    [`offline-evaluations/${oev}/extra`, ["GET", "POST", "DELETE"]],
  ] as const) {
    for (const method of methods) assert.equal(allowed(path, method), null, `${method} ${path}`);
  }
});

test("episode uploads pass through as bytes up to 16 MiB; other offline writes keep the ordinary bound", async () => {
  const fetch = globalThis.fetch;
  process.env.CONVOY_API_URL = "http://127.0.0.1:8080";
  const forwarded: { url: string; method?: string; body?: unknown; headers: Headers }[] = [];
  globalThis.fetch = async (input, init) => {
    forwarded.push({ url: String(input), method: init?.method, body: init?.body, headers: new Headers(init?.headers) });
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    return Response.json({ id: oep }, { status: 201 });
  };
  const path = ["offline-evaluations", oev, "episodes"];
  const exact = new Uint8Array(episodeLimit).fill(0x20);
  exact.set(new TextEncoder().encode('{"seed":'));
  try {
    for (const body of [exact, new TextEncoder().encode('{"frames": [], "seed": 1 ')]) {
      const sent = await proxyPlatform(request(path.join("/"), { method: "POST", body, headers: { "Idempotency-Key": "episode-1", Authorization: "Bearer attacker" } }), path);
      assert.equal(sent.status, 201);
      assert.deepEqual(await sent.json(), { id: oep });
    }
    const removed = await proxyPlatform(request(`offline-evaluations/${oev}`, { method: "DELETE", headers: { "Idempotency-Key": "delete-1" } }), ["offline-evaluations", oev]);
    assert.equal(removed.status, 204);
  } finally { globalThis.fetch = fetch; }
  assert.deepEqual(forwarded.map(call => [call.method, call.url]), [
    ["POST", `http://127.0.0.1:8080/api/v1/offline-evaluations/${oev}/episodes`],
    ["POST", `http://127.0.0.1:8080/api/v1/offline-evaluations/${oev}/episodes`],
    ["DELETE", `http://127.0.0.1:8080/api/v1/offline-evaluations/${oev}`],
  ]);
  assert.ok(forwarded[0].body instanceof Uint8Array && forwarded[0].body.byteLength === episodeLimit);
  assert.equal(Buffer.from(forwarded[1].body as Uint8Array).toString(), '{"frames": [], "seed": 1 '); // unparsed: the API validates
  for (const call of forwarded) {
    assert.equal(call.headers.get("cookie"), session);
    assert.equal(call.headers.get("authorization"), null);
    assert.equal(call.headers.get("x-convoy-client"), "web");
  }
  assert.equal(forwarded[0].headers.get("idempotency-key"), "episode-1");
  assert.equal(forwarded[2].headers.get("idempotency-key"), "delete-1");
  assert.equal(forwarded[2].body, undefined);

  globalThis.fetch = async () => { throw new Error("must not reach upstream"); };
  try {
    const tooLarge = await proxyPlatform(request(path.join("/"), { method: "POST", body: new Uint8Array(episodeLimit + 1) }), path);
    assert.equal(tooLarge.status, 413);
    assert.deepEqual(await tooLarge.json(), { error: "The episode is larger than the 16 MiB upload limit." });
    assert.equal((await proxyPlatform(request(path.join("/"), { method: "POST", body: "{}", headers: { "Content-Length": String(episodeLimit + 1) } }), path)).status, 413);
    // Creation keeps the ordinary 256 KiB bound and must be JSON.
    assert.equal((await proxyPlatform(request("offline-evaluations", { method: "POST", body: "x".repeat(256 * 1024 + 1) }), ["offline-evaluations"])).status, 413);
    assert.equal((await proxyPlatform(request("offline-evaluations", { method: "POST", body: "{" }), ["offline-evaluations"])).status, 422);
    for (const headers of [{ Origin: "https://other.test" }, { "X-Convoy-Client": "" }] as Record<string, string>[]) {
      assert.equal((await proxyPlatform(request(path.join("/"), { method: "POST", body: "{}", headers }), path)).status, 403);
    }
    assert.equal((await proxyPlatform(request(path.join("/"), { method: "POST", body: "{}", headers: { Cookie: "unrelated=private" } }), path)).status, 401);
    assert.equal((await proxyPlatform(request(path.join("/"), { method: "POST", body: "{}", headers: { "Content-Type": "text/plain" } }), path)).status, 422);
  } finally { globalThis.fetch = fetch; }
});

test("offline errors: curated messages, the storage limit, conflict reasons and the upload's own validation reason", async () => {
  const fetch = globalThis.fetch;
  let status = 422;
  let error = "invalid request: frames.37.action: Value error, expected 14 values, the width of frame 1's action";
  let headers: Record<string, string> = {};
  globalThis.fetch = async () => Response.json({ error, detail: [{ loc: ["body", "frames", 37, "action"], msg: "x", type: "value_error" }] }, { status, headers });
  const path = ["offline-evaluations", oev, "episodes"];
  const write = async () => {
    const response = await proxyPlatform(request(path.join("/"), { method: "POST", body: "{}" }), path);
    return { status: response.status, body: await response.json() as unknown, retry: response.headers.get("retry-after") };
  };
  try {
    assert.deepEqual(await write(), { status: 422, body: { error }, retry: null });
    error = `invalid request: \u0000${"x".repeat(400)}`;
    assert.deepEqual((await write()).body, { error: `invalid request:  ${"x".repeat(282)}` });
    status = 507;
    error = "Recording storage limit reached; delete offline evaluations to free space";
    assert.deepEqual(await write(), { status: 507, body: { error: "Offline evaluation storage is full. Delete offline evaluations to free space." }, retry: null });
    status = 409;
    for (const [text, expected] of [
      ["offline evaluation limit reached (100 per account); delete one first", "Your account has reached its offline evaluation limit. Delete one first."],
      ["offline episode limit reached (200 per evaluation)", "This offline evaluation has reached its episode limit."],
      ["Idempotency-Key was already used with a different payload", "This upload was already submitted with different content."],
      ["private detail", "This upload conflicts with stored offline evaluations."],
    ]) {
      error = text;
      assert.deepEqual((await write()).body, { error: expected });
    }
    status = 429;
    headers = { "Retry-After": "300" };
    assert.deepEqual(await write(), { status: 429, body: { error: "Too many offline evaluation writes. Wait a moment before trying again." }, retry: "300" });
    headers = {};
    status = 413;
    assert.deepEqual((await write()).body, { error: "The episode is larger than the 16 MiB upload limit." });
    // Reads keep curated messages; a 422 there is never the API's text.
    status = 404;
    const missing = await proxyPlatform(request(`offline-evaluations/${oev}`), ["offline-evaluations", oev]);
    assert.deepEqual([missing.status, await missing.json()], [404, { error: "This offline evaluation does not exist." }]);
    status = 507;
    assert.equal((await proxyPlatform(request("projects", { method: "POST", body: "{}" }), ["projects"])).status, 503);
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

test("registry reads require project scope and only explicit mutations are exposed", () => {
  for (const resource of ["robot-profiles", "fleets"]) {
    assert.equal(allowedPlatformPath([resource], "GET", new URLSearchParams()), null);
    assert.equal(allowedPlatformPath([resource], "GET", new URLSearchParams("project_id=prj_one")), `/api/v1/${resource}?project_id=prj_one`);
  }
  for (const path of ["robot-profiles", "robot-registrations", "fleets", "fleets/flt_one/members", "fleets/flt_one/members/rob_one/remove", "robots/rob_one/qualification"]) {
    assert.equal(allowedPlatformPath(path.split("/"), "POST", new URLSearchParams()), `/api/v1/${path}`);
    assert.equal(allowedPlatformPath(path.split("/"), "PUT", new URLSearchParams()), null);
  }
  assert.equal(allowedPlatformPath(["robots", "rob_one"], "GET", new URLSearchParams()), "/api/v1/robots/rob_one");
  assert.equal(allowedPlatformPath(["robots", "rob_one", "qualification"], "GET", new URLSearchParams()), "/api/v1/robots/rob_one/qualification");
  assert.equal(allowedPlatformPath(["missions"], "GET", new URLSearchParams("project_id=prj_one&robot_id=rob_one")), "/api/v1/missions?project_id=prj_one&robot_id=rob_one");
  assert.equal(allowedPlatformPath(["missions"], "GET", new URLSearchParams("robot_id=rob_one")), null);
  assert.equal(allowedPlatformPath(["qualifications", "rqc_one", "report"], "POST", new URLSearchParams()), null);
  assert.equal(allowedPlatformPath(["robot-profiles", "rpf_one"], "DELETE", new URLSearchParams()), null);
});
