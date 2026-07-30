#!/usr/bin/env node

import readline from "node:readline";
import {
  applyAction,
  evaluateWorld,
  getManifest,
  getState,
  listWorlds,
  resetWorld,
} from "./engine.mjs";

const supportedProtocolVersions = ["2025-11-25", "2025-06-18", "2025-03-26"];

const actionSchemas = {
  "vendor.list": schema(
    {
      criticality: {
        type: "string",
        enum: ["critical", "high", "standard"],
      },
      review_status: {
        type: "string",
        enum: ["due", "in_review", "complete"],
      },
      limit: { type: "integer", minimum: 1, maximum: 250 },
    },
    [],
  ),
  "vendor.read": schema(
    {
      vendor_id: { type: "string" },
    },
    ["vendor_id"],
  ),
  "evidence.search": schema(
    {
      vendor_id: { type: "string" },
      requirement_id: { type: "string" },
      domain: {
        type: "string",
        enum: ["security", "privacy", "architecture", "legal"],
      },
      limit: { type: "integer", minimum: 1, maximum: 500 },
    },
    [],
  ),
  "evidence.record_finding": schema(
    {
      vendor_id: { type: "string" },
      requirement_id: { type: "string" },
      outcome: {
        type: "string",
        enum: ["valid", "missing", "stale", "contradictory"],
      },
      rationale: { type: "string" },
      evidence_ids: {
        type: "array",
        items: { type: "string" },
      },
      idempotency_key: { type: "string" },
    },
    [
      "vendor_id",
      "requirement_id",
      "outcome",
      "rationale",
      "idempotency_key",
    ],
  ),
  "exception.create": schema(
    {
      vendor_id: { type: "string" },
      requirement_id: { type: "string" },
      severity: {
        type: "string",
        enum: ["critical", "high", "medium", "low"],
      },
      reason: { type: "string" },
      idempotency_key: { type: "string" },
    },
    [
      "vendor_id",
      "requirement_id",
      "severity",
      "reason",
      "idempotency_key",
    ],
  ),
  "chat.send": schema(
    {
      channel_id: { type: "string" },
      text: { type: "string" },
    },
    ["channel_id", "text"],
  ),
  "crm.update_account": schema(
    {
      account_id: { type: "string" },
      changes: { type: "object" },
    },
    ["account_id", "changes"],
  ),
  "work.create_task": schema(
    {
      account_id: { type: "string" },
      vendor_id: { type: "string" },
      requirement_id: { type: "string" },
      exception_id: { type: "string" },
      title: { type: "string" },
      owner: { type: "string" },
      due_date: { type: "string" },
      idempotency_key: { type: "string" },
    },
    ["title", "owner", "due_date"],
  ),
  "drive.write_document": schema(
    {
      title: { type: "string" },
      body: { type: "string" },
      document_type: { type: "string" },
      summary_metrics: { type: "object" },
      idempotency_key: { type: "string" },
    },
    ["title", "body"],
  ),
  "evidence.attach": schema(
    {
      vendor_id: { type: "string" },
      category: {
        type: "string",
        enum: ["security", "privacy", "architecture", "legal", "financial"],
      },
      source: { type: "string" },
      summary: { type: "string" },
    },
    ["vendor_id", "category", "source", "summary"],
  ),
  "approval.request": schema(
    {
      type: { type: "string" },
      target_id: { type: "string" },
      reason: { type: "string" },
      idempotency_key: { type: "string" },
    },
    ["type", "target_id", "reason"],
  ),
  "approval.resolve": schema(
    {
      approval_id: { type: "string" },
      decision: { type: "string", enum: ["approved", "rejected"] },
    },
    ["approval_id", "decision"],
  ),
  "decision.record": schema(
    {
      vendor_id: { type: "string" },
      decision: { type: "string" },
      rationale: { type: "string" },
    },
    ["vendor_id", "decision", "rationale"],
  ),
};

const readOnlyActionTools = new Set([
  "vendor.list",
  "vendor.read",
  "evidence.search",
]);
const destructiveActionTools = new Set([
  "crm.update_account",
  "decision.record",
  "exception.create",
]);
const idempotentActionTools = new Set([
  "evidence.record_finding",
  "exception.create",
  "work.create_task",
  "drive.write_document",
  "approval.request",
]);

function schema(properties, required) {
  return {
    type: "object",
    properties: {
      world_id: { type: "string" },
      actor: { type: "string" },
      ...properties,
    },
    required: ["world_id", "actor", ...required],
    additionalProperties: false,
  };
}

const lifecycleTools = [
  {
    name: "world.list",
    title: "List Convoy Worlds",
    description: "List available resettable company environments.",
    inputSchema: { type: "object", additionalProperties: false },
    annotations: { readOnlyHint: true, destructiveHint: false },
  },
  {
    name: "world.inspect",
    title: "Inspect World",
    description: "Read a World's scenario, policy, tools, and current state.",
    inputSchema: {
      type: "object",
      properties: { world_id: { type: "string" } },
      required: ["world_id"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false },
  },
  {
    name: "world.reset",
    title: "Reset World",
    description: "Restore a World to its exact initial company snapshot.",
    inputSchema: {
      type: "object",
      properties: { world_id: { type: "string" } },
      required: ["world_id"],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: true,
      idempotentHint: true,
    },
  },
  {
    name: "world.evaluate",
    title: "Evaluate Mission",
    description: "Score current World state against outcome and safety checkpoints.",
    inputSchema: {
      type: "object",
      properties: { world_id: { type: "string" } },
      required: ["world_id"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false },
  },
];

async function toolDefinitions() {
  const descriptions = new Map();
  for (const world of await listWorlds()) {
    for (const tool of (await getManifest(world.id)).tools) {
      descriptions.set(tool.name, tool.description);
    }
  }
  const actionTools = Object.entries(actionSchemas).map(
    ([name, inputSchema]) => ({
      name,
      title: name,
      description:
        descriptions.get(name) ?? `Execute ${name} in a Convoy World.`,
      inputSchema,
      annotations: {
        readOnlyHint: readOnlyActionTools.has(name),
        destructiveHint: destructiveActionTools.has(name),
        idempotentHint: idempotentActionTools.has(name),
      },
    }),
  );
  return [...lifecycleTools, ...actionTools];
}

function toolResult(value, isError = false) {
  return {
    content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
    structuredContent: value,
    isError,
  };
}

async function callTool(name, args = {}) {
  switch (name) {
    case "world.list":
      return toolResult({ worlds: await listWorlds() });
    case "world.inspect":
      return toolResult({
        manifest: await getManifest(args.world_id),
        state: await getState(args.world_id),
      });
    case "world.reset":
      return toolResult(await resetWorld(args.world_id));
    case "world.evaluate":
      return toolResult(await evaluateWorld(args.world_id));
    default: {
      if (!actionSchemas[name]) throw new Error(`Unknown tool: ${name}`);
      const { world_id, actor, ...input } = args;
      const result = await applyAction(world_id, {
        actor,
        tool: name,
        input,
      });
      return toolResult(result, Boolean(result.denied));
    }
  }
}

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function protocolError(id, code, message) {
  send({ jsonrpc: "2.0", id, error: { code, message } });
}

async function handle(message) {
  if (message.jsonrpc !== "2.0" || !message.method) {
    protocolError(message.id ?? null, -32600, "Invalid JSON-RPC request.");
    return;
  }
  if (message.method === "notifications/initialized") return;
  if (message.method === "initialize") {
    const requested = message.params?.protocolVersion;
    send({
      jsonrpc: "2.0",
      id: message.id,
      result: {
        protocolVersion: supportedProtocolVersions.includes(requested)
          ? requested
          : supportedProtocolVersions[0],
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "convoy-worlds", version: "0.1.0" },
        instructions:
          "Inspect before acting, respect human gates, and evaluate the World when the mission is complete.",
      },
    });
    return;
  }
  if (message.method === "ping") {
    send({ jsonrpc: "2.0", id: message.id, result: {} });
    return;
  }
  if (message.method === "tools/list") {
    send({
      jsonrpc: "2.0",
      id: message.id,
      result: { tools: await toolDefinitions() },
    });
    return;
  }
  if (message.method === "tools/call") {
    try {
      send({
        jsonrpc: "2.0",
        id: message.id,
        result: await callTool(
          message.params?.name,
          message.params?.arguments ?? {},
        ),
      });
    } catch (error) {
      protocolError(message.id, -32603, error.message);
    }
    return;
  }
  protocolError(message.id ?? null, -32601, `Method not found: ${message.method}`);
}

const input = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});
input.on("line", async (line) => {
  if (!line.trim()) return;
  try {
    await handle(JSON.parse(line));
  } catch (error) {
    protocolError(null, -32700, `Parse error: ${error.message}`);
  }
});
