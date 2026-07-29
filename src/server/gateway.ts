import { db, persist } from "./db";
import { emit } from "./events";
import { id, now } from "./ids";
import { connectorForTool } from "./connectors";
import type {
  Approval,
  ApprovalKind,
  ConnectorInstance,
  PolicyRule,
  Run,
  ToolCall,
} from "./types";

// The Policy Gateway (spec §6.2) — the choke point. The agent runtime never
// touches credentials or external systems: it emits abstract tool calls; this
// module resolves run → deployment → environment, evaluates policy, injects
// environment-scoped credentials, executes, and writes an immutable trace row
// on every branch before returning.

export type GatewayResult =
  | { status: "ok"; result: unknown }
  | { status: "denied"; reason: string }
  | { status: "pending_approval"; approvalId: string }
  | { status: "killed"; reason: string };

export function callTool(runId: string, tool: string, args: Record<string, unknown>): GatewayResult {
  const d = db();
  const run = d.runs.find((r) => r.id === runId);
  if (!run) throw new Error(`Unknown run ${runId}`);
  const agent = d.agents.find((a) => a.id === run.agentId)!;
  const version = d.agentVersions.find((v) => v.id === run.agentVersionId)!;
  const seq = d.toolCalls.filter((t) => t.runId === runId).length + 1;

  // Fleet kill switch — checked on every call, so a paused agent stops mid-run.
  if (agent.paused) {
    trace(run, seq, tool, args, undefined, "deny", "denied", { error: "kill_switch" }, "Agent is paused fleet-wide (kill switch)");
    return { status: "killed", reason: "Agent is paused fleet-wide (kill switch)" };
  }

  // Built-in escalation path: available to every agent, in every environment.
  if (tool === "flag_for_review") {
    return createApprovalGate(run, seq, tool, args, "agent_flagged", String(args.reason ?? "Agent requested review"));
  }

  // Tool grant check: the version must have declared this tool.
  if (!version.toolGrants.includes(tool)) {
    trace(run, seq, tool, args, undefined, "deny", "denied", { error: "not_granted" }, "Tool not in this agent version's grants");
    return { status: "denied", reason: `Tool ${tool} is not granted to this agent version` };
  }

  const connector = connectorForTool(tool);
  if (!connector) {
    trace(run, seq, tool, args, undefined, "deny", "denied", { error: "unknown_tool" }, "Unknown tool");
    return { status: "denied", reason: `Unknown tool ${tool}` };
  }

  const instance = d.connectorInstances.find(
    (ci) => ci.environmentId === run.environmentId && ci.connectorId === connector.id
  );
  if (!instance || instance.health !== "green") {
    trace(run, seq, tool, args, connector.id, "deny", "denied", { error: "no_connector" }, "Connector not attached or unhealthy in this environment");
    return { status: "denied", reason: `Connector ${connector.name} is not available in this environment` };
  }

  // Policy evaluation for (connector, tool, environment).
  const { effect, rule, note } = evaluatePolicy(run.environmentId, connector.id, tool, args);

  if (effect === "deny") {
    trace(run, seq, tool, args, connector.id, "deny", "denied", { error: "policy_denied" }, note);
    return { status: "denied", reason: note ?? "Denied by policy" };
  }

  if (effect === "require_approval") {
    return createApprovalGate(run, seq, tool, args, "policy_gate", note ?? "Policy requires human approval", rule);
  }

  // allow → inject env-scoped credentials (vault) and execute.
  const result = executeTool(instance, run, tool, args);
  trace(run, seq, tool, args, connector.id, "allow", "executed", result, note);
  return { status: "ok", result };
}

export function evaluatePolicy(
  environmentId: string,
  connectorId: string,
  tool: string,
  args: Record<string, unknown>
): { effect: "allow" | "deny" | "require_approval"; rule?: PolicyRule; note?: string } {
  const d = db();
  const rules = d.policies.filter((p) => p.environmentId === environmentId && p.connectorId === connectorId);
  const rule = rules.find((p) => p.tool === tool) ?? rules.find((p) => p.tool === "*");
  const env = d.environments.find((e) => e.id === environmentId)!;
  if (!rule) {
    // Safe defaults: sandbox is permissive, production gates anything unstated.
    return env.kind === "sandbox"
      ? { effect: "allow", note: "No rule — sandbox default allow" }
      : { effect: "require_approval", note: "No rule — production default requires approval" };
  }
  if (rule.conditions?.recipient_domain_not_in) {
    const to = String(args.to ?? "");
    const domain = to.split("@")[1]?.toLowerCase() ?? "";
    const allowlist = rule.conditions.recipient_domain_not_in.map((x) => x.toLowerCase());
    if (domain && allowlist.includes(domain)) {
      return { effect: "allow", rule, note: `Recipient domain ${domain} is allowlisted` };
    }
    return {
      effect: rule.effect,
      rule,
      note: rule.note ?? `Recipient domain ${domain || "(none)"} is outside the allowlist`,
    };
  }
  return { effect: rule.effect, rule, note: rule.note };
}

function createApprovalGate(
  run: Run,
  seq: number,
  tool: string,
  args: Record<string, unknown>,
  kind: ApprovalKind,
  note: string,
  rule?: PolicyRule
): GatewayResult {
  const d = db();
  const tc: ToolCall = {
    id: id("tc"),
    runId: run.id,
    seq,
    tool,
    connectorId: connectorForTool(tool)?.id,
    args,
    policyEffect: kind === "agent_flagged" ? "agent_flagged" : "require_approval",
    policyNote: note,
    status: "pending_approval",
    latencyMs: 0,
    ts: now(),
  };
  d.toolCalls.push(tc);

  const agent = d.agents.find((a) => a.id === run.agentId)!;
  const title =
    kind === "agent_flagged"
      ? `${agent.name} flagged: ${String(args.reason ?? "review requested")}`
      : approvalTitle(tool, args);
  const approval: Approval = {
    id: id("apr"),
    runId: run.id,
    toolCallId: tc.id,
    agentId: run.agentId,
    environmentId: run.environmentId,
    kind,
    tool,
    title,
    detail: args,
    reason: kind === "agent_flagged" ? String(args.reason ?? "") : note,
    approvers: rule?.approvers ?? ["revops_lead"],
    status: "pending",
    requestedAt: now(),
  };
  d.approvals.push(approval);
  tc.approvalId = approval.id;
  run.state = "paused_pending_approval";

  // Notify the environment's chat channel — the gateway does this, not the agent.
  const slack = d.connectorInstances.find((ci) => ci.environmentId === run.environmentId && ci.connectorId === "slack");
  if (slack && run.triggerType !== "test") {
    d.chatMessages.push({
      id: id("msg"),
      environmentId: run.environmentId,
      channel: slack.config.channel ?? "#revops",
      text: `🔔 Approval needed — ${title}. Review in Convoy → Approvals.`,
      ts: now(),
    });
  }

  d.auditEvents.push({
    id: id("aud"),
    ts: now(),
    actor: kind === "agent_flagged" ? `agent:${run.agentId}` : "policy-gateway",
    type: "approval.requested",
    message: kind === "agent_flagged" ? `Agent flagged for review: ${approval.reason}` : `Policy gate: ${title}`,
    refs: { runId: run.id, toolCallId: tc.id, approvalId: approval.id, agentId: run.agentId, environmentId: run.environmentId },
  });
  persist();
  emit({ type: "tool_call", runId: run.id, payload: tc });
  emit({ type: "run_updated", runId: run.id, payload: run });
  emit({ type: "approval_created", runId: run.id, payload: approval });
  return { status: "pending_approval", approvalId: approval.id };
}

function approvalTitle(tool: string, args: Record<string, unknown>): string {
  if (tool === "email.send") return `Send external email to ${String(args.to ?? "?")}`;
  if (tool === "docs.publish") return `Publish doc '${String(args.title ?? "?")}'`;
  if (tool === "crm.merge_records") return `Merge CRM records ${String(args.primary ?? "?")} + ${String(args.duplicate ?? "?")}`;
  if (tool === "crm.mark_lead_qualified") return `Mark lead research-qualified`;
  return `Approve ${tool}`;
}

/** Execute a previously-gated call after human approval. */
export function executeApprovedCall(toolCallId: string, approver: string): void {
  const d = db();
  const tc = d.toolCalls.find((t) => t.id === toolCallId)!;
  const run = d.runs.find((r) => r.id === tc.runId)!;
  if (tc.policyEffect === "agent_flagged") {
    tc.result = { reviewed: true, decision: "proceed", by: approver };
  } else {
    const connector = connectorForTool(tc.tool)!;
    const instance = d.connectorInstances.find(
      (ci) => ci.environmentId === run.environmentId && ci.connectorId === connector.id
    )!;
    tc.result = executeTool(instance, run, tc.tool, tc.args);
  }
  tc.status = "approved_executed";
  tc.latencyMs = Math.round(80 + Math.random() * 400);
  persist();
  emit({ type: "tool_call", runId: run.id, payload: tc });
}

function trace(
  run: Run,
  seq: number,
  tool: string,
  args: Record<string, unknown>,
  connectorId: ToolCall["connectorId"],
  policyEffect: "allow" | "deny",
  status: ToolCall["status"],
  result: unknown,
  note?: string
): void {
  const d = db();
  const tc: ToolCall = {
    id: id("tc"),
    runId: run.id,
    seq,
    tool,
    connectorId,
    args,
    result,
    policyEffect,
    policyNote: note,
    status,
    latencyMs: Math.round(60 + Math.random() * 420),
    ts: now(),
  };
  d.toolCalls.push(tc);
  persist();
  emit({ type: "tool_call", runId: run.id, payload: tc });
}

// ---- Simulated external systems -------------------------------------------
// In the full product these are MCP servers called with vault-injected
// credentials. The demo executes against per-environment simulated systems
// stored server-side — same manifests, same governance, zero external deps.

function executeTool(instance: ConnectorInstance, run: Run, tool: string, args: Record<string, unknown>): unknown {
  const d = db();
  const envId = run.environmentId;
  const ns = run.scenarioId ? `ns:${run.id}` : undefined;

  switch (tool) {
    case "crm.read_deal": {
      const deal = d.crmDeals.find((x) => x.environmentId === envId && x.id === args.dealId);
      return deal ?? { error: "deal_not_found" };
    }
    case "crm.search_deals": {
      const q = String(args.company ?? args.filter ?? "").toLowerCase();
      const results = d.crmDeals
        .filter((x) => x.environmentId === envId && (q === "" || x.company.toLowerCase().includes(q) || x.name.toLowerCase().includes(q)))
        .map((x) => ({ id: x.id, name: x.name, company: x.company, stage: x.stage, amount: x.amount, currency: x.currency }));
      return { count: results.length, results };
    }
    case "crm.update_deal": {
      const deal = d.crmDeals.find((x) => x.environmentId === envId && x.id === args.dealId);
      if (!deal) return { error: "deal_not_found" };
      Object.assign(deal.fields, args.fields as Record<string, string>);
      deal.updatedAt = now();
      persist();
      emit({ type: "system_updated", payload: { system: "crm", environmentId: envId } });
      return { ok: true, updated: Object.keys((args.fields as object) ?? {}) };
    }
    case "crm.read_lead": {
      const lead = d.crmLeads.find((x) => x.environmentId === envId && (x.id === args.leadId || x.company === args.company));
      return lead ?? { error: "lead_not_found" };
    }
    case "crm.update_lead": {
      const lead = d.crmLeads.find((x) => x.environmentId === envId && (x.id === args.leadId || x.company === args.company));
      if (!lead) return { error: "lead_not_found" };
      Object.assign(lead.fields, args.fields as Record<string, string>);
      lead.updatedAt = now();
      persist();
      return { ok: true };
    }
    case "crm.mark_lead_qualified": {
      const lead = d.crmLeads.find((x) => x.environmentId === envId && (x.id === args.leadId || x.company === args.company));
      if (lead) {
        lead.fields.research_qualified = "true";
        persist();
      }
      return { ok: true };
    }
    case "crm.merge_records":
      return { ok: true, merged: [args.primary, args.duplicate] };
    case "docs.get": {
      const q = String(args.title ?? "").toLowerCase();
      const doc = d.docs.find((x) => x.environmentId === envId && x.title.toLowerCase().includes(q));
      return doc ? { title: doc.title, status: doc.status, content: doc.content } : { error: "doc_not_found" };
    }
    case "docs.create": {
      const doc = {
        id: id("doc"),
        environmentId: envId,
        title: String(args.title ?? "Untitled"),
        content: String(args.content ?? ""),
        status: "draft" as const,
        createdByRunId: run.id,
        namespace: ns,
        createdAt: now(),
      };
      d.docs.push(doc);
      persist();
      emit({ type: "system_updated", payload: { system: "docs", environmentId: envId } });
      return { ok: true, docId: doc.id, title: doc.title };
    }
    case "docs.publish": {
      const q = String(args.title ?? "").toLowerCase();
      const doc = d.docs.find((x) => x.environmentId === envId && (x.id === args.docId || x.title.toLowerCase().includes(q)));
      if (!doc) return { error: "doc_not_found" };
      doc.status = "published";
      persist();
      return { ok: true, docId: doc.id };
    }
    case "email.send": {
      const email = {
        id: id("em"),
        environmentId: envId,
        from: instance.config.sender ?? "noreply@meridianlabs.dev",
        to: String(args.to ?? ""),
        subject: String(args.subject ?? ""),
        body: String(args.body ?? ""),
        attachmentDocId: args.attachmentDocId ? String(args.attachmentDocId) : undefined,
        status: "sent" as const,
        sentByRunId: run.id,
        namespace: ns,
        ts: now(),
      };
      d.emails.push(email);
      persist();
      emit({ type: "system_updated", payload: { system: "email", environmentId: envId } });
      return { ok: true, messageId: email.id, to: email.to };
    }
    case "email.search": {
      const q = String(args.query ?? "").toLowerCase();
      return d.emails
        .filter((x) => x.environmentId === envId && (x.subject.toLowerCase().includes(q) || x.to.toLowerCase().includes(q)))
        .map((x) => ({ to: x.to, subject: x.subject, ts: x.ts }));
    }
    case "chat.post": {
      const msg = {
        id: id("msg"),
        environmentId: envId,
        channel: String(args.channel ?? instance.config.channel ?? "#revops"),
        text: String(args.text ?? ""),
        byRunId: run.id,
        namespace: ns,
        ts: now(),
      };
      d.chatMessages.push(msg);
      persist();
      emit({ type: "system_updated", payload: { system: "chat", environmentId: envId } });
      return { ok: true, channel: msg.channel };
    }
    default:
      return { error: `unimplemented tool ${tool}` };
  }
}
