import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { request as httpRequest } from "node:http";
import os from "node:os";
import path from "node:path";
import { createControlPlane } from "../apps/sandbox-control-plane/src/server.mjs";
import { runHttpContracts } from "../packages/contract-testkit/src/index.mjs";
import { loadFixture } from "./helpers.mjs";

function request(url, options = {}) {
  return new Promise((resolve, reject) => {
    const body = options.body == null ? null : String(options.body);
    const headers = { ...(options.headers ?? {}) };
    if (body != null) headers["content-length"] = Buffer.byteLength(body);
    const outgoing = httpRequest(url, { method: options.method ?? "GET", headers }, (incoming) => {
      const chunks = [];
      incoming.on("data", (chunk) => chunks.push(chunk));
      incoming.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf8");
        resolve({ status: incoming.statusCode, json: async () => JSON.parse(text) });
      });
    });
    outgoing.on("error", reject);
    if (body != null) outgoing.write(body);
    outgoing.end();
  });
}

const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "convoy-control-plane-test-"));
const { server } = await createControlPlane({ runtimeRoot });
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
const baseUrl = `http://127.0.0.1:${address.port}`;

try {
  const provision = await request(`${baseUrl}/v1/sandboxes`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ sandboxId: "http-contract", fixture: await loadFixture() }),
  });
  assert.equal(provision.status, 201);

  const results = await runHttpContracts({
    baseUrl,
    fetchImpl: request,
    variables: { slackToken: "xoxb-convoy-sandbox" },
    contracts: [{
      name: "Slack conversations.list",
      request: {
        method: "GET",
        path: "/s/http-contract/slack/api/conversations.list?limit=100&types=public_channel",
        headers: { authorization: "Bearer {{slackToken}}" },
      },
      expect: { status: 200, requiredFields: ["ok", "channels"], equals: { ok: true } },
    }],
  });
  assert.deepEqual(results, [{ name: "Slack conversations.list", status: "passed" }]);

  const jwtPayload = Buffer.from(JSON.stringify({ iss: "convoy-agent@sandbox.invalid" })).toString("base64url");
  const tokenResponse = await request(`${baseUrl}/s/http-contract/google/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion: `e30.${jwtPayload}.signature` }),
  });
  assert.equal(tokenResponse.status, 200);
  const token = (await tokenResponse.json()).access_token;
  const driveResponse = await request(`${baseUrl}/s/http-contract/google/drive/v3/files?q=${encodeURIComponent("'folder-shared' in parents and trashed = false")}`, {
    headers: { authorization: `Bearer ${token}` },
  });
  // The unfiltered folder listing sees both fixture files: the tracker
  // spreadsheet and the runbook document.
  assert.deepEqual((await driveResponse.json()).files.map((file) => file.id), ["sheet-operations", "doc-runbook"]);

  const issueResponse = await request(`${baseUrl}/s/http-contract/github/repos/sandbox/example-service/issues`, {
    method: "POST",
    headers: { authorization: "Bearer github-convoy-sandbox", "content-type": "application/json" },
    body: JSON.stringify({ title: "HTTP contract issue" }),
  });
  assert.equal(issueResponse.status, 201);

  const snapshotResponse = await request(`${baseUrl}/v1/sandboxes/http-contract/snapshots`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ label: "contract-complete" }),
  });
  assert.equal((await snapshotResponse.json()).snapshotId, "snap_0001");
  console.log("HTTP integration contracts passed");
} finally {
  await new Promise((resolve) => server.close(resolve));
}
