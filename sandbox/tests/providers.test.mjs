import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { evaluateScenario } from "../packages/scenarios/src/index.mjs";
import { translateAutomationBenchToolCall } from "../adapters/automationbench/src/index.mjs";
import { createTestRuntime, loadFixture, providerRequest } from "./helpers.mjs";

test("Slack simulator implements list, history, and post semantics", async () => {
  const { runtime } = await createTestRuntime();
  await runtime.provision({ sandboxId: "slack", fixture: await loadFixture() });
  const list = await runtime.invoke("slack", "slack", providerRequest({
    path: "/api/conversations.list",
    token: "xoxb-convoy-sandbox",
  }));
  assert.equal(list.body.ok, true);
  assert.equal(list.body.channels[1].name, "operations");
  const invalid = await runtime.invoke("slack", "slack", providerRequest({ path: "/api/conversations.list", token: "wrong" }));
  assert.deepEqual(invalid.body, { ok: false, error: "invalid_auth" });
});

test("Google simulator enforces sharing and supports read and append", async () => {
  const { runtime } = await createTestRuntime();
  await runtime.provision({ sandboxId: "google", fixture: await loadFixture() });
  const files = await runtime.invoke("google", "google", providerRequest({
    path: "/drive/v3/files",
    token: "google-convoy-sandbox",
    query: { q: "'folder-shared' in parents and trashed = false and mimeType = 'application/vnd.google-apps.spreadsheet'" },
  }));
  assert.deepEqual(files.body.files.map((file) => file.id), ["sheet-operations"]);
  const append = await runtime.invoke("google", "google", providerRequest({
    method: "POST",
    path: "/sheets/v4/spreadsheets/sheet-operations/values/Operations!A1:append",
    token: "google-convoy-sandbox",
    body: { values: [["Sample task", "In progress", "Convoy"]] },
  }));
  assert.equal(append.body.updates.updatedRows, 1);
  const values = await runtime.invoke("google", "google", providerRequest({
    path: "/sheets/v4/spreadsheets/sheet-operations/values/Operations!A1:C3",
    token: "google-convoy-sandbox",
  }));
  assert.deepEqual(values.body.values[2], ["Sample task", "In progress", "Convoy"]);

  const doc = await runtime.invoke("google", "google", providerRequest({
    path: "/docs/v1/documents/doc-runbook",
    token: "google-convoy-sandbox",
  }));
  assert.equal(doc.body.title, "Operations Runbook");
  const originalText = doc.body.body.content[0].paragraph.elements[0].textRun.content;
  assert.match(originalText, /nightly export/);
  const updated = await runtime.invoke("google", "google", providerRequest({
    method: "POST",
    path: "/docs/v1/documents/doc-runbook:batchUpdate",
    token: "google-convoy-sandbox",
    body: { requests: [
      { deleteContentRange: { range: { startIndex: 1, endIndex: originalText.length } } },
      { insertText: { location: { index: 1 }, text: "Exports: the hourly-sync job runs every hour.\n" } },
    ] },
  }));
  assert.equal(updated.status, 200);
  const reread = await runtime.invoke("google", "google", providerRequest({
    path: "/docs/v1/documents/doc-runbook",
    token: "google-convoy-sandbox",
  }));
  assert.match(reread.body.body.content[0].paragraph.elements[0].textRun.content, /hourly-sync/);
});

test("GitHub simulator supports content reads and issue creation", async () => {
  const { runtime } = await createTestRuntime();
  await runtime.provision({ sandboxId: "github", fixture: await loadFixture() });
  const content = await runtime.invoke("github", "github", providerRequest({
    path: "/repos/sandbox/example-service/contents/README.md",
    token: "github-convoy-sandbox",
    query: { ref: "main" },
  }));
  assert.match(Buffer.from(content.body.content, "base64").toString("utf8"), /Example Service/);
  const issue = await runtime.invoke("github", "github", providerRequest({
    method: "POST",
    path: "/repos/sandbox/example-service/issues",
    token: "github-convoy-sandbox",
    body: { title: "Sample task follow-up" },
  }));
  assert.equal(issue.status, 201);
  assert.equal(issue.body.user.login, "convoy-sandbox[bot]");
});

test("scenario evaluator scores cross-provider outcomes", async () => {
  const { runtime } = await createTestRuntime();
  await runtime.provision({ sandboxId: "scenario", fixture: await loadFixture() });
  const calls = [
    translateAutomationBenchToolCall({ app: "slack", action: "send_message", args: { channel: "C_OPERATIONS", text: "Sample task is in progress" } }),
    translateAutomationBenchToolCall({ app: "google_sheets", action: "add_row", args: { spreadsheet_id: "sheet-operations", range: "Operations!A1", values: ["Sample task", "In progress", "Convoy"] } }),
    translateAutomationBenchToolCall({ app: "github", action: "create_issue", args: { repo: "sandbox/example-service", title: "Sample task follow-up" } }),
  ];
  const tokens = { slack: "xoxb-convoy-sandbox", google: "google-convoy-sandbox", github: "github-convoy-sandbox" };
  for (const call of calls) {
    await runtime.invoke("scenario", call.provider, { ...call, headers: { authorization: `Bearer ${tokens[call.provider]}` } });
  }
  const scenario = JSON.parse(await readFile(new URL("../scenarios/connector-smoke-test.json", import.meta.url), "utf8"));
  const evaluation = evaluateScenario(await runtime.inspect("scenario"), scenario);
  assert.equal(evaluation.passed, true);
  assert.equal(evaluation.score, 1);
});
