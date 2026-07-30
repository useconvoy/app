import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";

test("World MCP server negotiates protocol and exposes typed tools", async () => {
  const child = spawn(
    process.execPath,
    [new URL("../worlds/runtime/mcp-server.mjs", import.meta.url).pathname],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  let stdout = "";
  let stderr = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    stdout += chunk;
  });
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  child.stdin.write(
    `${JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: "2025-06-18" },
    })}\n`,
  );
  child.stdin.write(
    `${JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list" })}\n`,
  );
  child.stdin.end();

  const exitCode = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", resolve);
  });
  assert.equal(exitCode, 0, stderr);
  const responses = stdout
    .trim()
    .split(/\r?\n/)
    .map((line) => JSON.parse(line));
  assert.equal(responses[0].result.protocolVersion, "2025-06-18");
  const names = responses[1].result.tools.map((tool) => tool.name);
  assert.ok(names.includes("world.inspect"));
  assert.ok(names.includes("vendor.list"));
  assert.ok(names.includes("evidence.record_finding"));
  assert.ok(names.includes("exception.create"));
  assert.ok(names.includes("approval.request"));
  assert.ok(names.includes("decision.record"));
});
