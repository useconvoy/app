import Anthropic from "@anthropic-ai/sdk";
import { db, persist } from "../db";
import { callTool } from "../gateway";
import { finishRun } from "./loop";
import { CONNECTORS, BUILTIN_TOOLS } from "../connectors";
import type { AgentVersion, Run } from "../types";

// Live-model engine (spec §6.4): the model reasons over the agent's
// instructions, acts only through gateway-exposed tools, and flags via
// flag_for_review. Message history is persisted per run after each turn, so a
// paused run survives a restart (reconstruct-and-continue).
//
// Off by default so the demo runs with zero credentials; enable with
// ANTHROPIC_API_KEY set and CONVOY_LIVE_MODEL=1.

const MODEL = process.env.CONVOY_MODEL ?? "claude-sonnet-5";

export function isLiveModelEnabled(): boolean {
  return Boolean(process.env.ANTHROPIC_API_KEY) && process.env.CONVOY_LIVE_MODEL === "1";
}

const g = globalThis as unknown as { __convoyAnthropic?: Anthropic };
function client(): Anthropic {
  if (!g.__convoyAnthropic) g.__convoyAnthropic = new Anthropic();
  return g.__convoyAnthropic;
}

const TOOL_SCHEMAS: Record<string, Anthropic.Tool.InputSchema> = {
  "crm.read_deal": { type: "object", properties: { dealId: { type: "string" } }, required: ["dealId"] },
  "crm.search_deals": { type: "object", properties: { company: { type: "string" } } },
  "crm.update_deal": { type: "object", properties: { dealId: { type: "string" }, fields: { type: "object" } }, required: ["dealId", "fields"] },
  "crm.read_lead": { type: "object", properties: { leadId: { type: "string" }, company: { type: "string" } } },
  "crm.update_lead": { type: "object", properties: { leadId: { type: "string" }, fields: { type: "object" } } },
  "crm.mark_lead_qualified": { type: "object", properties: { leadId: { type: "string" } } },
  "crm.merge_records": { type: "object", properties: { primary: { type: "string" }, duplicate: { type: "string" } } },
  "docs.get": { type: "object", properties: { title: { type: "string" } }, required: ["title"] },
  "docs.create": { type: "object", properties: { title: { type: "string" }, content: { type: "string" } }, required: ["title", "content"] },
  "docs.publish": { type: "object", properties: { title: { type: "string" } } },
  "email.send": { type: "object", properties: { to: { type: "string" }, subject: { type: "string" }, body: { type: "string" }, attachmentDocId: { type: "string" } }, required: ["to", "subject", "body"] },
  "email.search": { type: "object", properties: { query: { type: "string" } } },
  "chat.post": { type: "object", properties: { channel: { type: "string" }, text: { type: "string" } }, required: ["text"] },
  flag_for_review: {
    type: "object",
    properties: {
      reason_key: { type: "string", description: "Stable key for this concern, e.g. discount_over_threshold" },
      reason: { type: "string", description: "Why a human should review this" },
      proposed_action: { type: "string", description: "What you propose to do if approved" },
    },
    required: ["reason", "proposed_action"],
  },
};

function toolsForVersion(version: AgentVersion): Anthropic.Tool[] {
  const defs: Anthropic.Tool[] = [];
  for (const connector of CONNECTORS) {
    for (const t of connector.tools) {
      if (!version.toolGrants.includes(t.name)) continue;
      defs.push({
        // API tool names cannot contain dots.
        name: t.name.replace(/\./g, "__"),
        description: t.description,
        input_schema: TOOL_SCHEMAS[t.name] ?? { type: "object", properties: {} },
      });
    }
  }
  defs.push({
    name: "flag_for_review",
    description: BUILTIN_TOOLS[0].description,
    input_schema: TOOL_SCHEMAS.flag_for_review,
  });
  return defs;
}

function systemPrompt(version: AgentVersion): string {
  return [
    version.instructions,
    "",
    "You act only through the tools provided; every call is policy-checked by a gateway. A tool may pause for human approval — you will receive the result once decided. When the task is done, reply with a one-paragraph completion report and stop calling tools.",
    `Parameters: ${JSON.stringify(version.params)}`,
  ].join("\n");
}

export async function advanceClaudeRun(runId: string): Promise<void> {
  const d = db();
  const run = d.runs.find((r) => r.id === runId)!;
  const version = d.agentVersions.find((v) => v.id === run.agentVersionId)!;
  const messages: Anthropic.MessageParam[] =
    (run.modelMessages as Anthropic.MessageParam[] | undefined) ?? [
      { role: "user", content: `Trigger event: ${JSON.stringify({ type: run.triggerType, payload: run.triggerPayload })}. Carry out your task.` },
    ];
  await driveLoop(run, version, messages);
}

export async function resumeClaudeRun(runId: string): Promise<void> {
  const d = db();
  const run = d.runs.find((r) => r.id === runId)!;
  const version = d.agentVersions.find((v) => v.id === run.agentVersionId)!;
  const tc = d.toolCalls.find((t) => t.runId === runId && t.status === "approved_executed" && t.approvalId);
  const messages = (run.modelMessages as Anthropic.MessageParam[]) ?? [];
  if (run.pendingModelToolUseId) {
    messages.push({
      role: "user",
      content: [
        {
          type: "tool_result",
          tool_use_id: run.pendingModelToolUseId,
          content: JSON.stringify(tc?.result ?? { approved: true }),
        },
      ],
    });
    run.pendingModelToolUseId = undefined;
    run.modelMessages = messages;
    persist();
  }
  await driveLoop(run, version, messages);
}

async function driveLoop(run: Run, version: AgentVersion, messages: Anthropic.MessageParam[]): Promise<void> {
  const tools = toolsForVersion(version);
  for (let turn = 0; turn < 25; turn++) {
    let response: Anthropic.Message;
    try {
      response = await client().messages.create({
        model: MODEL,
        max_tokens: 16000,
        system: systemPrompt(version),
        tools,
        tool_choice: { type: "auto", disable_parallel_tool_use: true },
        messages,
      });
    } catch (err) {
      finishRun(run, "failed", `Model call failed: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }

    if (response.stop_reason === "refusal") {
      finishRun(run, "failed", "Model declined the request (safety refusal).");
      return;
    }

    messages.push({ role: "assistant", content: response.content });
    run.modelMessages = messages;
    persist();

    const toolUse = response.content.find((b): b is Anthropic.ToolUseBlock => b.type === "tool_use");
    if (!toolUse) {
      const text = response.content.filter((b): b is Anthropic.TextBlock => b.type === "text").map((b) => b.text).join("\n");
      finishRun(run, "succeeded", text || "Run complete.");
      return;
    }

    const toolName = toolUse.name === "flag_for_review" ? toolUse.name : toolUse.name.replace(/__/g, ".");
    const result = callTool(run.id, toolName, toolUse.input as Record<string, unknown>);

    if (result.status === "pending_approval") {
      run.pendingModelToolUseId = toolUse.id;
      run.modelMessages = messages;
      persist();
      return; // paused; decideApproval resumes or terminates
    }
    if (result.status === "killed") {
      finishRun(run, "killed", "Run stopped mid-flight: agent paused fleet-wide (kill switch).");
      return;
    }
    const payload =
      result.status === "ok" ? JSON.stringify(result.result) : JSON.stringify({ denied: true, reason: result.reason });
    messages.push({
      role: "user",
      content: [{ type: "tool_result", tool_use_id: toolUse.id, content: payload, is_error: result.status === "denied" }],
    });
    run.modelMessages = messages;
    persist();
  }
  finishRun(run, "failed", "Run exceeded maximum model turns.");
}
