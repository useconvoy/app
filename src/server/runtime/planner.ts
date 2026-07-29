import type { AgentVersion, CrmDeal, Run, ToolCall } from "../types";
import { db } from "../db";

// Demo-mode planner: a deterministic "reason, act, flag" policy per agent
// template. It derives the next action purely from the run's persisted trace,
// so a paused run resumes correctly after approval with no in-memory state
// (spec §6.4 — reconstruct-and-continue). The live-model engine (claude.ts)
// replaces this when ANTHROPIC_API_KEY + CONVOY_LIVE_MODEL are set.

export type PlannedAction =
  | { type: "tool"; tool: string; args: Record<string, unknown> }
  | { type: "complete"; summary: string }
  | { type: "fail"; summary: string };

export function planNextAction(run: Run, version: AgentVersion): PlannedAction {
  const d = db();
  const agent = d.agents.find((a) => a.id === run.agentId)!;
  const calls = d.toolCalls
    .filter((t) => t.runId === run.id && (t.status === "executed" || t.status === "approved_executed"))
    .sort((a, b) => a.seq - b.seq);

  const called = (tool: string, match?: (tc: ToolCall) => boolean) =>
    calls.find((c) => c.tool === tool && (!match || match(c)));
  const flagHandled = (key: string) =>
    calls.some((c) => c.tool === "flag_for_review" && c.args.reason_key === key);

  switch (agent.templateId) {
    case "closed_won_paperwork":
      return planClosedWon(run, version, calls, called, flagHandled);
    case "lead_research":
      return planLeadResearch(run, called);
    case "docs_sync":
      return planDocsSync(run, called, flagHandled);
    case "crm_hygiene":
      return planCrmHygiene(run, called);
    default:
      return genericPlan(run, version, calls);
  }
}

function planClosedWon(
  run: Run,
  version: AgentVersion,
  calls: ToolCall[],
  called: (tool: string, match?: (tc: ToolCall) => boolean) => ToolCall | undefined,
  flagHandled: (key: string) => boolean
): PlannedAction {
  const params = version.params;
  const dealId = String(run.triggerPayload.dealId ?? "");

  // 1. Read the deal.
  const readCall = called("crm.read_deal");
  if (!readCall) return { type: "tool", tool: "crm.read_deal", args: { dealId } };
  const deal = readCall.result as CrmDeal & { error?: string };
  if (!deal || deal.error) return { type: "fail", summary: `Deal ${dealId} not found in CRM.` };

  const fmtAmount = `${deal.currency} ${Number(deal.amount).toLocaleString("en-US")}`;

  // 2. Read the pricing & discount policy.
  if (!called("docs.get")) {
    return { type: "tool", tool: "docs.get", args: { title: params.pricing_doc_title ?? "Pricing & Discount Policy" } };
  }

  // 3. Duplicate check.
  const searchCall = called("crm.search_deals");
  if (!searchCall) return { type: "tool", tool: "crm.search_deals", args: { company: deal.company } };
  const search = searchCall.result as { results?: { id: string; stage: string; name: string }[] };
  const duplicates = (search.results ?? []).filter((r) => r.id !== deal.id && r.stage === "closedwon");
  if (duplicates.length > 0 && !flagHandled("duplicate_deal")) {
    return {
      type: "tool",
      tool: "flag_for_review",
      args: {
        reason_key: "duplicate_deal",
        reason: `Duplicate deal suspected: '${duplicates[0].name}' for ${deal.company} is already Closed-Won. Papering this twice would double-invoice the customer.`,
        proposed_action: "Confirm this is a distinct deal before generating paperwork; if it is a duplicate, close this record without invoicing.",
      },
    };
  }

  // 4. Validations — consequential gaps get flagged, not guessed around.
  if (!deal.contactEmail && !flagHandled("missing_contact_email")) {
    return {
      type: "tool",
      tool: "flag_for_review",
      args: {
        reason_key: "missing_contact_email",
        reason: `Deal '${deal.name}' has no contact email on file — the invoice cannot be sent.`,
        proposed_action: "Complete the paperwork without the invoice email; RevOps to obtain a billing contact and send manually.",
      },
    };
  }
  if (!deal.poNumber && !flagHandled("missing_po")) {
    return {
      type: "tool",
      tool: "flag_for_review",
      args: {
        reason_key: "missing_po",
        reason: `Deal '${deal.name}' has no PO number. Invoicing without a PO reference risks rejection by the customer's AP process.`,
        proposed_action: "Proceed and issue the invoice without a PO reference, noting 'PO to follow' on the order form.",
      },
    };
  }
  const threshold = Number(params.discount_threshold_pct ?? 15);
  if (deal.discountPct > threshold && !flagHandled("discount_over_threshold")) {
    return {
      type: "tool",
      tool: "flag_for_review",
      args: {
        reason_key: "discount_over_threshold",
        reason: `Discount is ${deal.discountPct}% against a ${threshold}% standard and no exception doc found — flagging for review.`,
        proposed_action: `Approve the ${deal.discountPct}% discount for this deal (requires VP Sales exception per pricing policy) so paperwork can proceed.`,
      },
    };
  }

  // 5. Order form.
  const orderTitle = `Order Form — ${deal.name}`;
  const orderCall = called("docs.create", (c) => String(c.args.title ?? "").startsWith("Order Form"));
  if (!orderCall) {
    const lines = [
      `ORDER FORM (${params.order_form_template ?? "Meridian Standard Order Form v3"})`,
      ``,
      `Customer: ${deal.company}`,
      `Deal: ${deal.name}`,
      `Contact: ${deal.contactName}${deal.contactEmail ? ` <${deal.contactEmail}>` : ""}`,
      `Total: ${fmtAmount}`,
      `Discount applied: ${deal.discountPct}%${deal.discountPct > threshold ? " (exception approved via review)" : ""}`,
      `Term: ${deal.termMonths} months`,
      `PO number: ${deal.poNumber ?? "PO to follow"}`,
      `Payment terms: ${params.invoice_terms ?? "Net 30"}`,
      deal.currency !== "USD" ? `Note: all amounts denominated in ${deal.currency} per the executed agreement.` : ``,
    ].filter(Boolean);
    return { type: "tool", tool: "docs.create", args: { title: orderTitle, content: lines.join("\n") } };
  }
  const orderDocId = (orderCall.result as { docId?: string }).docId;

  // 6. Update the CRM record.
  if (!called("crm.update_deal")) {
    return {
      type: "tool",
      tool: "crm.update_deal",
      args: { dealId: deal.id, fields: { paperwork_status: "complete", order_form: orderTitle, invoice_terms: params.invoice_terms ?? "Net 30" } },
    };
  }

  // 7. Kickoff checklist.
  if (!called("docs.create", (c) => String(c.args.title ?? "").startsWith("Kickoff"))) {
    return {
      type: "tool",
      tool: "docs.create",
      args: {
        title: `Kickoff Checklist — ${deal.company}`,
        content: `KICKOFF CHECKLIST — ${deal.company}\n\n[ ] Provision workspace (${deal.name})\n[ ] Schedule kickoff call with ${deal.contactName}\n[ ] Confirm billing contact${deal.contactEmail ? ` (${deal.contactEmail})` : " (missing — see review flag)"}\n[ ] Hand off to delivery team (owner: ${deal.ownerName})\n[ ] Term: ${deal.termMonths} months — set renewal reminder`,
      },
    };
  }

  // 8. Notify RevOps.
  if (!called("chat.post")) {
    return {
      type: "tool",
      tool: "chat.post",
      args: {
        channel: params.notification_channel ?? "#revops",
        text: `Closed-Won paperwork complete for ${deal.name} (${fmtAmount}). Order form + kickoff checklist ready${deal.contactEmail ? "; invoice email queued." : "; no invoice sent (missing billing contact)."}`,
      },
    };
  }

  // 9. Invoice email (skipped if the missing-contact flag was approved).
  if (deal.contactEmail && !called("email.send")) {
    return {
      type: "tool",
      tool: "email.send",
      args: {
        to: deal.contactEmail,
        subject: `Invoice & Order Form — ${deal.name}`,
        body: `Hi ${deal.contactName.split(" ")[0]},\n\nThank you for your business. Attached is the order form for ${deal.name}.\n\nTotal due: ${fmtAmount}\nTerms: ${params.invoice_terms ?? "Net 30"}\nPO reference: ${deal.poNumber ?? "PO to follow"}\nTerm: ${deal.termMonths} months\n\nBest,\nMeridian Labs Billing`,
        attachmentDocId: orderDocId,
      },
    };
  }

  const flagged = calls.filter((c) => c.tool === "flag_for_review").length;
  return {
    type: "complete",
    summary: `Papered ${deal.name}: order form + kickoff checklist created, CRM updated, RevOps notified${deal.contactEmail ? `, invoice sent to ${deal.contactEmail}` : ", invoice held (no billing contact)"}${flagged ? ` — ${flagged} decision(s) escalated for human review` : ""}.`,
  };
}

function planLeadResearch(run: Run, called: (t: string) => ToolCall | undefined): PlannedAction {
  const leadRef = { leadId: run.triggerPayload.leadId, company: run.triggerPayload.company };
  const readCall = called("crm.read_lead");
  if (!readCall) return { type: "tool", tool: "crm.read_lead", args: leadRef };
  const lead = readCall.result as { company?: string; name?: string; error?: string };
  if (!lead || lead.error) return { type: "fail", summary: "Lead not found in CRM." };
  if (!called("crm.update_lead")) {
    return { type: "tool", tool: "crm.update_lead", args: { ...leadRef, fields: { industry: "B2B logistics SaaS", headcount_band: "100-250", fit: "meets ICP — revenue ops function confirmed" } } };
  }
  if (!called("docs.create")) {
    return { type: "tool", tool: "docs.create", args: { title: `Research Brief — ${lead.company}`, content: `RESEARCH BRIEF — ${lead.company}\n\nContact: ${lead.name}\nIndustry: B2B logistics SaaS\nHeadcount: ~100-250\nICP fit: strong — dedicated revenue ops function, growth-stage.\nRecommended next step: research-qualify and route to AE.` } };
  }
  if (!called("crm.mark_lead_qualified")) {
    return { type: "tool", tool: "crm.mark_lead_qualified", args: leadRef };
  }
  return { type: "complete", summary: `Researched ${lead.company}: enrichment written to CRM, brief created, lead research-qualified.` };
}

function planDocsSync(run: Run, called: (t: string) => ToolCall | undefined, flagHandled: (k: string) => boolean): PlannedAction {
  const topic = String(run.triggerPayload.docTitle ?? "Webhooks API");
  if (!called("docs.get")) return { type: "tool", tool: "docs.get", args: { title: topic } };
  if (!called("docs.create")) {
    return { type: "tool", tool: "docs.create", args: { title: `${topic} (draft update)`, content: `Draft update to '${topic}' reflecting the flagged product change:\n${String(run.triggerPayload.change ?? "see change log")}` } };
  }
  if (!called("docs.publish")) return { type: "tool", tool: "docs.publish", args: { title: `${topic} (draft update)` } };
  return { type: "complete", summary: `Drafted and published update to '${topic}'.` };
}

function planCrmHygiene(run: Run, called: (t: string) => ToolCall | undefined): PlannedAction {
  if (!called("crm.search_deals")) return { type: "tool", tool: "crm.search_deals", args: { filter: "malformed-fields" } };
  if (!called("crm.update_deal")) {
    const search = called("crm.search_deals")!.result as { results?: { id: string }[] };
    const target = search.results?.[0];
    if (!target) return { type: "complete", summary: "Nightly hygiene sweep: no malformed records found." };
    return { type: "tool", tool: "crm.update_deal", args: { dealId: target.id, fields: { hygiene_normalized: "true" } } };
  }
  return { type: "complete", summary: "Nightly hygiene sweep complete: fields normalized, no merge candidates found." };
}

function genericPlan(run: Run, version: AgentVersion, calls: ToolCall[]): PlannedAction {
  // From-scratch agents in demo mode: exercise each granted read tool once,
  // then complete. (Live-model mode drives custom agents with the real model.)
  const next = version.toolGrants.find((t) => !calls.some((c) => c.tool === t) && !isWrite(t));
  if (next) return { type: "tool", tool: next, args: defaultArgsFor(next, run) };
  return { type: "complete", summary: `Run complete: exercised ${calls.length} granted tool(s) per instructions.` };
}

function isWrite(tool: string): boolean {
  return ["email.send", "crm.merge_records", "docs.publish"].includes(tool);
}

function defaultArgsFor(tool: string, run: Run): Record<string, unknown> {
  if (tool.startsWith("crm.read_deal")) return { dealId: run.triggerPayload.dealId ?? "" };
  if (tool === "crm.search_deals") return { company: "" };
  if (tool === "docs.get") return { title: "Pricing & Discount Policy" };
  return {};
}
