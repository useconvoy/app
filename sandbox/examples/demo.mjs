import { mkdtemp, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { SandboxRuntime } from "../packages/runtime/src/index.mjs";
import { WebhookDispatcher } from "../packages/webhook-sink/src/index.mjs";
import { evaluateScenario } from "../packages/scenarios/src/index.mjs";
import { slackProvider } from "../providers/slack/src/index.mjs";
import { googleWorkspaceProvider } from "../providers/google-workspace/src/index.mjs";
import { githubProvider } from "../providers/github/src/index.mjs";

const root = await mkdtemp(path.join(os.tmpdir(), "convoy-sandbox-demo-"));
const fixture = JSON.parse(await readFile(new URL("../fixtures/connector-development.json", import.meta.url), "utf8"));
const scenario = JSON.parse(await readFile(new URL("../scenarios/connector-smoke-test.json", import.meta.url), "utf8"));
const runtime = await new SandboxRuntime({
  root,
  providers: [slackProvider, googleWorkspaceProvider, githubProvider],
  webhookDispatcher: new WebhookDispatcher(),
}).initialize();

await runtime.provision({ sandboxId: "demo", scenario: scenario.id, fixture });
await runtime.invoke("demo", "slack", {
  method: "POST",
  path: "/api/chat.postMessage",
  query: {},
  headers: { authorization: "Bearer xoxb-convoy-sandbox" },
  body: { channel: "C_OPERATIONS", text: "Sample task is in progress." },
});
await runtime.invoke("demo", "google", {
  method: "POST",
  path: "/sheets/v4/spreadsheets/sheet-operations/values/Operations!A1:append",
  query: {},
  headers: { authorization: "Bearer google-convoy-sandbox" },
  body: { values: [["Sample task", "In progress", "Convoy"]] },
});
await runtime.invoke("demo", "github", {
  method: "POST",
  path: "/repos/sandbox/example-service/issues",
  query: {},
  headers: { authorization: "Bearer github-convoy-sandbox" },
  body: { title: "Sample task follow-up", body: "Created through the GitHub connector sandbox." },
});
await runtime.advanceClock("demo", 1_000);
console.log(JSON.stringify(evaluateScenario(await runtime.inspect("demo"), scenario), null, 2));
