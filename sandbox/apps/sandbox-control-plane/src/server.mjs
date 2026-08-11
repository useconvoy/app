import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SandboxError, SandboxRuntime } from "../../../packages/runtime/src/index.mjs";
import { WebhookDispatcher } from "../../../packages/webhook-sink/src/index.mjs";
import { slackProvider } from "../../../providers/slack/src/index.mjs";
import { googleWorkspaceProvider } from "../../../providers/google-workspace/src/index.mjs";
import { githubProvider } from "../../../providers/github/src/index.mjs";

const moduleDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(moduleDirectory, "../../..");

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  if (chunks.length === 0) return {};
  const text = Buffer.concat(chunks).toString("utf8");
  const contentType = request.headers["content-type"] ?? "";
  if (contentType.includes("application/x-www-form-urlencoded")) return Object.fromEntries(new URLSearchParams(text));
  return JSON.parse(text);
}

function send(response, status, value, headers = {}) {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8", ...headers });
  response.end(`${JSON.stringify(value, null, 2)}\n`);
}

function adminAuthorized(request, adminToken) {
  if (!adminToken) return true;
  return request.headers.authorization === `Bearer ${adminToken}`;
}

export async function createControlPlane({
  runtimeRoot = process.env.SANDBOX_RUNTIME_ROOT ?? path.join(projectRoot, ".runtime"),
  adminToken = process.env.SANDBOX_ADMIN_TOKEN,
  webhookDeliver,
} = {}) {
  const webhookDispatcher = new WebhookDispatcher({ deliver: webhookDeliver });
  const runtime = await new SandboxRuntime({
    root: runtimeRoot,
    providers: [slackProvider, googleWorkspaceProvider, githubProvider],
    webhookDispatcher,
  }).initialize();

  const server = createServer(async (request, response) => {
    const url = new URL(request.url, `http://${request.headers.host ?? "localhost"}`);
    const parts = url.pathname.split("/").filter(Boolean);
    try {
      if (request.method === "GET" && url.pathname === "/health") {
        send(response, 200, {
          ok: true,
          service: "convoy-sandbox",
          providers: [...runtime.providers.keys()],
          sandboxes: ["sandbox_slack", "sandbox_drive", "sandbox_sheets", "sandbox_github"],
        });
        return;
      }

      if (parts[0] === "s" && parts.length >= 3) {
        const [, sandboxId, providerId, ...providerParts] = parts;
        const providerResponse = await runtime.invoke(sandboxId, providerId, {
          method: request.method,
          path: `/${providerParts.join("/")}`,
          query: Object.fromEntries(url.searchParams),
          headers: request.headers,
          body: await readBody(request),
        });
        send(response, providerResponse.status, providerResponse.body, providerResponse.headers);
        return;
      }

      if (!adminAuthorized(request, adminToken)) {
        send(response, 401, { error: "unauthorized" });
        return;
      }

      if (request.method === "GET" && url.pathname === "/v1/providers") {
        send(response, 200, { providers: [...runtime.providers.keys()] });
        return;
      }
      if (request.method === "GET" && url.pathname === "/v1/sandboxes") {
        send(response, 200, { sandboxes: await runtime.list() });
        return;
      }
      if (request.method === "POST" && url.pathname === "/v1/sandboxes") {
        const body = await readBody(request);
        let fixture = body.fixture;
        if (!fixture && body.fixtureName) {
          fixture = JSON.parse(await readFile(path.join(projectRoot, "fixtures", `${body.fixtureName}.json`), "utf8"));
        }
        send(response, 201, await runtime.provision({ ...body, fixture: fixture ?? {} }));
        return;
      }
      if (parts[0] === "v1" && parts[1] === "sandboxes" && parts[2]) {
        const sandboxId = parts[2];
        const operation = parts[3];
        if (request.method === "GET" && !operation) {
          send(response, 200, await runtime.inspect(sandboxId));
          return;
        }
        if (request.method === "POST" && operation === "seed") {
          send(response, 200, await runtime.seed(sandboxId, await readBody(request)));
          return;
        }
        if (request.method === "POST" && operation === "reset") {
          send(response, 200, await runtime.reset(sandboxId));
          return;
        }
        if (request.method === "POST" && operation === "snapshots") {
          send(response, 201, await runtime.snapshot(sandboxId, await readBody(request)));
          return;
        }
        if (request.method === "POST" && operation === "restore") {
          const body = await readBody(request);
          send(response, 200, await runtime.restore(sandboxId, body.snapshotId));
          return;
        }
        if (request.method === "POST" && operation === "fork") {
          const body = await readBody(request);
          send(response, 201, await runtime.fork(sandboxId, body.snapshotId, body.targetSandboxId));
          return;
        }
        if (request.method === "POST" && operation === "clock" && parts[4] === "advance") {
          const body = await readBody(request);
          send(response, 200, await runtime.advanceClock(sandboxId, body.milliseconds));
          return;
        }
        if (request.method === "DELETE" && !operation) {
          send(response, 200, await runtime.destroy(sandboxId, { purge: url.searchParams.get("purge") === "true" }));
          return;
        }
      }
      send(response, 404, { error: "not_found" });
    } catch (error) {
      const status = error instanceof SandboxError ? error.status : 400;
      send(response, status, { error: error.code ?? "bad_request", message: error.message });
    }
  });
  return { server, runtime };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT ?? 8790);
  const { server } = await createControlPlane();
  server.listen(port, "0.0.0.0", () => {
    console.log(`Convoy sandbox control plane listening on http://0.0.0.0:${port}`);
  });
}
