import { mkdtemp, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { SandboxRuntime } from "../packages/runtime/src/index.mjs";
import { WebhookDispatcher } from "../packages/webhook-sink/src/index.mjs";
import { slackProvider } from "../providers/slack/src/index.mjs";
import { googleWorkspaceProvider } from "../providers/google-workspace/src/index.mjs";
import { githubProvider } from "../providers/github/src/index.mjs";

export async function loadFixture() {
  return JSON.parse(await readFile(new URL("../fixtures/connector-development.json", import.meta.url), "utf8"));
}

export async function createTestRuntime(options = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), "convoy-sandbox-test-"));
  const deliveries = [];
  const webhookDispatcher = new WebhookDispatcher({
    deliver: options.deliver ?? (async (delivery) => {
      deliveries.push(delivery);
      return { status: 202 };
    }),
  });
  const runtime = await new SandboxRuntime({
    root,
    providers: [slackProvider, googleWorkspaceProvider, githubProvider],
    webhookDispatcher,
  }).initialize();
  return { root, runtime, deliveries };
}

export function providerRequest({ method = "GET", path, token, query = {}, body = {} }) {
  return { method, path, query, body, headers: token ? { authorization: `Bearer ${token}` } : {} };
}
