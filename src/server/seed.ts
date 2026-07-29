import type {
  Agent,
  AgentVersion,
  Approval,
  AuditEvent,
  ChatMessage,
  CrmDeal,
  CrmLead,
  Database,
  Deployment,
  Doc,
  Email,
  Environment,
  PolicyRule,
  Run,
  TestScenario,
  ToolCall,
} from "./types";
import { TEMPLATES } from "./templates";
import { CLOSED_WON_SCENARIOS } from "./scenarios";
import { id } from "./ids";

// Seeds Meridian Labs' workspace: two environments, four connectors each,
// policies, the four-agent convoy, demo CRM data, and the overnight fleet
// history from the cold open (47 leads researched, 6 deals papered, 12 doc
// drafts, 31 records cleaned). One-command reseed: `npm run reset`.

export const SANDBOX_ID = "env_sandbox";
export const PRODUCTION_ID = "env_production";
export const APPROVER = "Maya Torres (RevOps Lead)";
export const DEMO_SANDBOX_DEAL_ID = "deal_sbx_latch";
export const DEMO_PROD_DEAL_ID = "deal_prod_northwind";

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString();
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

export function seedDatabase(): Database {
  const environments: Environment[] = [
    { id: SANDBOX_ID, name: "Sandbox", kind: "sandbox", createdAt: hoursAgo(24 * 14) },
    { id: PRODUCTION_ID, name: "Production", kind: "production", createdAt: hoursAgo(24 * 13) },
  ];

  const connectorInstances: Database["connectorInstances"] = [
    // Sandbox — test-scoped credentials (spec §6.5)
    { id: "ci_sbx_hubspot", environmentId: SANDBOX_ID, connectorId: "hubspot", label: "HubSpot developer test portal #1", credentialRef: "vault://sandbox/hubspot", health: "green", config: { portal: "meridian-sbx-48211" } },
    { id: "ci_sbx_email", environmentId: SANDBOX_ID, connectorId: "google_email", label: "Gmail — revops.sandbox@meridianlabs.dev", credentialRef: "vault://sandbox/google", health: "green", config: { account: "revops.sandbox@meridianlabs.dev", sender: "revops.sandbox@meridianlabs.dev" } },
    { id: "ci_sbx_docs", environmentId: SANDBOX_ID, connectorId: "google_docs", label: "Google Docs — Sandbox workspace", credentialRef: "vault://sandbox/google", health: "green", config: { drive: "Meridian Sandbox" } },
    { id: "ci_sbx_slack", environmentId: SANDBOX_ID, connectorId: "slack", label: "Slack — #revops-sandbox", credentialRef: "vault://sandbox/slack", health: "green", config: { channel: "#revops-sandbox" } },
    // Production — real credentials
    { id: "ci_prod_hubspot", environmentId: PRODUCTION_ID, connectorId: "hubspot", label: "HubSpot production portal", credentialRef: "vault://production/hubspot", health: "green", config: { portal: "meridian-prod-11073" } },
    { id: "ci_prod_email", environmentId: PRODUCTION_ID, connectorId: "google_email", label: "Gmail — billing@meridianlabs.dev", credentialRef: "vault://production/google", health: "green", config: { account: "billing@meridianlabs.dev", sender: "billing@meridianlabs.dev" } },
    { id: "ci_prod_docs", environmentId: PRODUCTION_ID, connectorId: "google_docs", label: "Google Docs — Company workspace", credentialRef: "vault://production/google", health: "green", config: { drive: "Meridian Labs" } },
    { id: "ci_prod_slack", environmentId: PRODUCTION_ID, connectorId: "slack", label: "Slack — #revops", credentialRef: "vault://production/slack", health: "green", config: { channel: "#revops" } },
  ];

  const policies: PolicyRule[] = [
    // ---- Sandbox: everything auto-allowed, email restricted to the sandbox domain ----
    { id: "pol_sbx_default", environmentId: SANDBOX_ID, connectorId: "hubspot", tool: "*", effect: "allow", note: "Sandbox default: auto-allow, everything logged" },
    { id: "pol_sbx_docs", environmentId: SANDBOX_ID, connectorId: "google_docs", tool: "*", effect: "allow", note: "Sandbox default: auto-allow" },
    { id: "pol_sbx_slack", environmentId: SANDBOX_ID, connectorId: "slack", tool: "*", effect: "allow", note: "Sandbox default: auto-allow" },
    {
      id: "pol_sbx_email",
      environmentId: SANDBOX_ID,
      connectorId: "google_email",
      tool: "email.send",
      effect: "deny",
      conditions: { recipient_domain_not_in: ["sandbox.meridianlabs.dev", "meridianlabs.dev"] },
      note: "Email sends restricted to the sandbox domain — anything external is refused",
    },
    { id: "pol_sbx_email_read", environmentId: SANDBOX_ID, connectorId: "google_email", tool: "email.search", effect: "allow" },
    // ---- Production: strict ----
    { id: "pol_prod_crm_read", environmentId: PRODUCTION_ID, connectorId: "hubspot", tool: "crm.read_deal", effect: "allow" },
    { id: "pol_prod_crm_search", environmentId: PRODUCTION_ID, connectorId: "hubspot", tool: "crm.search_deals", effect: "allow" },
    { id: "pol_prod_crm_write", environmentId: PRODUCTION_ID, connectorId: "hubspot", tool: "crm.update_deal", effect: "allow", note: "Allowed, logged" },
    { id: "pol_prod_lead_read", environmentId: PRODUCTION_ID, connectorId: "hubspot", tool: "crm.read_lead", effect: "allow" },
    { id: "pol_prod_lead_write", environmentId: PRODUCTION_ID, connectorId: "hubspot", tool: "crm.update_lead", effect: "allow", note: "Allowed, logged" },
    {
      id: "pol_prod_lead_qualify",
      environmentId: PRODUCTION_ID,
      connectorId: "hubspot",
      tool: "crm.mark_lead_qualified",
      effect: "require_approval",
      approvers: ["revops_lead"],
      timeoutHours: 4,
      onTimeout: "reject",
      note: "Qualification gates pipeline spend — human sign-off",
    },
    {
      id: "pol_prod_merge",
      environmentId: PRODUCTION_ID,
      connectorId: "hubspot",
      tool: "crm.merge_records",
      effect: "require_approval",
      approvers: ["revops_lead"],
      timeoutHours: 8,
      onTimeout: "reject",
      note: "Merges are destructive",
    },
    {
      id: "pol_prod_email",
      environmentId: PRODUCTION_ID,
      connectorId: "google_email",
      tool: "email.send",
      effect: "require_approval",
      conditions: { recipient_domain_not_in: ["meridianlabs.dev"] },
      approvers: ["revops_lead"],
      timeoutHours: 4,
      onTimeout: "reject",
      note: "External email always gates on a human, no matter how confident the model is",
    },
    { id: "pol_prod_email_read", environmentId: PRODUCTION_ID, connectorId: "google_email", tool: "email.search", effect: "allow" },
    { id: "pol_prod_docs_read", environmentId: PRODUCTION_ID, connectorId: "google_docs", tool: "docs.get", effect: "allow" },
    { id: "pol_prod_docs_create", environmentId: PRODUCTION_ID, connectorId: "google_docs", tool: "docs.create", effect: "allow", note: "Drafts allowed, logged" },
    {
      id: "pol_prod_docs_publish",
      environmentId: PRODUCTION_ID,
      connectorId: "google_docs",
      tool: "docs.publish",
      effect: "require_approval",
      approvers: ["revops_lead"],
      timeoutHours: 24,
      onTimeout: "reject",
      note: "Published docs are customer-visible",
    },
    { id: "pol_prod_slack", environmentId: PRODUCTION_ID, connectorId: "slack", tool: "chat.post", effect: "allow", note: "Internal notifications allowed, logged" },
  ];

  // ---- Agents: the Meridian convoy, v1 of each, deployed to both environments ----
  const agents: Agent[] = [];
  const agentVersions: AgentVersion[] = [];
  const deployments: Deployment[] = [];
  const agentIdByTemplate: Record<string, string> = {};

  for (const t of TEMPLATES) {
    const agentId = `agent_${t.id}`;
    agentIdByTemplate[t.id] = agentId;
    agents.push({
      id: agentId,
      name: t.name,
      description: t.description,
      templateId: t.id,
      paused: false,
      createdAt: hoursAgo(24 * 10),
    });
    const versionId = `ver_${t.id}_1`;
    agentVersions.push({
      id: versionId,
      agentId,
      version: 1,
      instructions: t.instructions,
      toolGrants: t.toolGrants,
      params: t.params,
      trigger: t.trigger,
      createdAt: hoursAgo(24 * 10),
    });
    deployments.push(
      { id: `dep_${t.id}_sbx`, agentVersionId: versionId, agentId, environmentId: SANDBOX_ID, status: "active", createdAt: hoursAgo(24 * 10) },
      { id: `dep_${t.id}_prod`, agentVersionId: versionId, agentId, environmentId: PRODUCTION_ID, status: "active", createdAt: hoursAgo(24 * 6) }
    );
  }

  const testScenarios: TestScenario[] = CLOSED_WON_SCENARIOS.map((s) => ({ ...s, id: `scn_${s.key}` }));

  // ---- Simulated external systems ----
  const crmDeals: CrmDeal[] = [
    // Sandbox pipeline — the Beat 4 demo deal is in "proposal", ready to be marked Closed-Won.
    mkDeal({ id: DEMO_SANDBOX_DEAL_ID, environmentId: SANDBOX_ID, name: "Latch Robotics — Platform (12 seats)", company: "Latch Robotics", amount: 14400, currency: "USD", stage: "proposal", contactName: "Riley Chen", contactEmail: "riley@sandbox.meridianlabs.dev", poNumber: "PO-4417", discountPct: 10, termMonths: 12, ownerName: "J. Park" }),
    mkDeal({ id: "deal_sbx_orbis", environmentId: SANDBOX_ID, name: "Orbis Media — Platform (20 seats)", company: "Orbis Media", amount: 24000, currency: "USD", stage: "proposal", contactName: "Theo Marsh", contactEmail: "theo@sandbox.meridianlabs.dev", poNumber: null, discountPct: 18, termMonths: 12, ownerName: "J. Park" }),
    mkDeal({ id: "deal_sbx_quill", environmentId: SANDBOX_ID, name: "Quill & Co — Platform (6 seats)", company: "Quill & Co", amount: 7200, currency: "USD", stage: "qualified", contactName: "Ana Ruiz", contactEmail: "ana@sandbox.meridianlabs.dev", poNumber: "PO-9902", discountPct: 0, termMonths: 12, ownerName: "S. Idris" }),
    // Production pipeline — the Beat 6 demo deal: 22% discount, external contact.
    mkDeal({ id: DEMO_PROD_DEAL_ID, environmentId: PRODUCTION_ID, name: "Northwind Systems — Platform (85 seats)", company: "Northwind Systems", amount: 87700, currency: "USD", stage: "proposal", contactName: "Elena Vasquez", contactEmail: "ap@northwindsystems.com", poNumber: "PO-NW-1207", discountPct: 22, termMonths: 24, ownerName: "M. Torres" }),
    mkDeal({ id: "deal_prod_arden", environmentId: PRODUCTION_ID, name: "Arden Biotech — Platform (15 seats)", company: "Arden Biotech", amount: 18000, currency: "USD", stage: "proposal", contactName: "Wes Adler", contactEmail: "wes.adler@ardenbio.com", poNumber: "PO-AB-311", discountPct: 8, termMonths: 12, ownerName: "S. Idris" }),
  ];

  const docs: Doc[] = [
    { id: "doc_pricing_sbx", environmentId: SANDBOX_ID, title: "Pricing & Discount Policy", status: "published", createdAt: hoursAgo(24 * 12), content: "Meridian Labs — Pricing & Discount Policy (v4)\n\nList price: $100/seat/month, billed annually.\nStandard discount authority: up to 15% (AE discretion).\nDiscounts above 15% require a written exception approved by the VP Sales, attached to the deal record.\nMulti-year terms: price locked for the term; invoice annually in advance.\nPayment terms: Net 30 from invoice date." },
    { id: "doc_pricing_prod", environmentId: PRODUCTION_ID, title: "Pricing & Discount Policy", status: "published", createdAt: hoursAgo(24 * 12), content: "Meridian Labs — Pricing & Discount Policy (v4)\n\nList price: $100/seat/month, billed annually.\nStandard discount authority: up to 15% (AE discretion).\nDiscounts above 15% require a written exception approved by the VP Sales, attached to the deal record.\nMulti-year terms: price locked for the term; invoice annually in advance.\nPayment terms: Net 30 from invoice date." },
  ];

  const crmLeads: CrmLead[] = [
    { id: "lead_sbx_1", environmentId: SANDBOX_ID, name: "Kim Osborne", company: "Tandem Freight", email: "kim@tandemfreight.example", fields: {}, updatedAt: hoursAgo(4) },
    { id: "lead_prod_1", environmentId: PRODUCTION_ID, name: "Dev Batra", company: "Signalpoint", email: "dev@signalpoint.example", fields: { industry: "B2B SaaS", headcount_band: "200-500", fit: "strong — has RevOps team" }, updatedAt: hoursAgo(6) },
  ];

  const emails: Email[] = [];
  const chatMessages: ChatMessage[] = [
    { id: id("msg"), environmentId: PRODUCTION_ID, channel: "#revops", text: "Reminder: pipeline review moved to 2pm Thursday.", ts: hoursAgo(30) },
  ];

  // ---- Overnight fleet history (the cold-open numbers) ----
  const runs: Run[] = [];
  const toolCalls: ToolCall[] = [];
  const approvals: Approval[] = [];
  const auditEvents: AuditEvent[] = [];

  const audit = (ts: string, actor: string, type: string, message: string, refs: AuditEvent["refs"] = {}) =>
    auditEvents.push({ id: id("aud"), ts, actor, type, message, refs });

  audit(hoursAgo(24 * 14), "Maya Torres", "environment.created", "Environment 'Sandbox' created with 4 connectors attached", { environmentId: SANDBOX_ID });
  audit(hoursAgo(24 * 13), "Maya Torres", "environment.created", "Environment 'Production' created with 4 connectors attached and strict policy set", { environmentId: PRODUCTION_ID });
  for (const t of TEMPLATES) {
    audit(hoursAgo(24 * 10), "Maya Torres", "agent.created", `Agent '${t.name}' created from template (v1)`, { agentId: agentIdByTemplate[t.id] });
    audit(hoursAgo(24 * 6), "Maya Torres", "deployment.promoted", `'${t.name}' v1 promoted Sandbox → Production (suite green)`, { agentId: agentIdByTemplate[t.id], environmentId: PRODUCTION_ID });
  }

  let histSeq = 0;
  const mkHistoricalRun = (opts: {
    templateId: string;
    environmentId: string;
    hoursBack: number;
    calls: { tool: string; args: Record<string, unknown>; result?: unknown; gated?: "policy_gate" | "agent_flagged"; approvalTitle?: string; approvalLatencyMin?: number; rejected?: boolean }[];
    summary: string;
    triggerType?: Run["triggerType"];
    state?: Run["state"];
  }) => {
    histSeq += 1;
    const agentId = agentIdByTemplate[opts.templateId];
    const dep = deployments.find((d) => d.agentId === agentId && d.environmentId === opts.environmentId)!;
    const started = hoursAgo(opts.hoursBack);
    const runId = `run_hist_${histSeq}`;
    const run: Run = {
      id: runId,
      deploymentId: dep.id,
      agentId,
      agentVersionId: dep.agentVersionId,
      environmentId: opts.environmentId,
      triggerType: opts.triggerType ?? "webhook",
      triggerPayload: {},
      state: opts.state ?? "succeeded",
      summary: opts.summary,
      startedAt: started,
      endedAt: new Date(new Date(started).getTime() + (60 + Math.random() * 200) * 1000).toISOString(),
    };
    runs.push(run);
    opts.calls.forEach((c, i) => {
      const tc: ToolCall = {
        id: id("tc"),
        runId,
        seq: i + 1,
        tool: c.tool,
        args: c.args,
        result: c.result ?? { ok: true },
        policyEffect: c.gated === "agent_flagged" ? "agent_flagged" : c.gated ? "require_approval" : "allow",
        status: c.gated ? (c.rejected ? "rejected" : "approved_executed") : "executed",
        latencyMs: Math.round(60 + Math.random() * 700),
        ts: new Date(new Date(started).getTime() + (i + 1) * 9000).toISOString(),
      };
      toolCalls.push(tc);
      if (c.gated) {
        const latency = c.approvalLatencyMin ?? 6 + Math.round(Math.random() * 18);
        const requestedAt = tc.ts;
        const decidedAt = new Date(new Date(tc.ts).getTime() + latency * 60_000).toISOString();
        const ap: Approval = {
          id: id("apr"),
          runId,
          toolCallId: tc.id,
          agentId,
          environmentId: opts.environmentId,
          kind: c.gated,
          tool: c.tool,
          title: c.approvalTitle ?? `Approve ${c.tool}`,
          detail: c.args,
          reason: c.gated === "agent_flagged" ? String(c.args.reason ?? "") : undefined,
          approvers: ["revops_lead"],
          status: c.rejected ? "rejected" : "approved",
          approver: APPROVER,
          requestedAt,
          decidedAt,
        };
        approvals.push(ap);
        tc.approvalId = ap.id;
        audit(decidedAt, APPROVER, c.rejected ? "approval.rejected" : "approval.approved", `${c.rejected ? "Rejected" : "Approved"}: ${ap.title}`, { runId, approvalId: ap.id, agentId, toolCallId: tc.id });
      }
    });
    audit(run.endedAt!, `agent:${agentId}`, "run.completed", `${opts.summary}`, { runId, agentId, environmentId: opts.environmentId });
    return runId;
  };

  // 6 deals papered overnight by Closed-Won Paperwork (production)
  const overnightDeals = [
    { company: "Helix Manufacturing", amount: 32400, contact: "ap@helixmfg.com", latency: 11 },
    { company: "Bright & Weber", amount: 21600, contact: "accounts@brightweber.com", latency: 8 },
    { company: "Cobalt Shipping", amount: 45600, contact: "finance@cobaltshipping.com", latency: 14 },
    { company: "Fenwick Data", amount: 15600, contact: "billing@fenwickdata.com", latency: 5 },
    { company: "Aurora Legal", amount: 27600, contact: "ops@auroralegal.com", latency: 19 },
    { company: "Trellis Foods", amount: 38400, contact: "ap@trellisfoods.com", latency: 9 },
  ];
  overnightDeals.forEach((d, i) => {
    const dealId = `deal_prod_hist_${i + 1}`;
    crmDeals.push(
      mkDeal({ id: dealId, environmentId: PRODUCTION_ID, name: `${d.company} — Platform`, company: d.company, amount: d.amount, currency: "USD", stage: "closedwon", contactName: "AP Team", contactEmail: d.contact, poNumber: `PO-${1000 + i}`, discountPct: 10, termMonths: 12, ownerName: "M. Torres", fields: { paperwork_status: "complete", order_form: `Order Form — ${d.company} — Platform` } })
    );
    const orderDocId = id("doc");
    docs.push({ id: orderDocId, environmentId: PRODUCTION_ID, title: `Order Form — ${d.company} — Platform`, content: `ORDER FORM (Meridian Standard Order Form v3)\n\nCustomer: ${d.company}\nTotal: USD ${d.amount.toLocaleString("en-US")}\nTerms: Net 30`, status: "published", createdAt: hoursAgo(9 - i * 0.5), createdByRunId: `run_hist_${histSeq + 1}` });
    emails.push({ id: id("em"), environmentId: PRODUCTION_ID, from: "billing@meridianlabs.dev", to: d.contact, subject: `Invoice & Order Form — ${d.company} — Platform`, body: `Please find attached the order form for ${d.company} — Platform. Total: USD ${d.amount.toLocaleString("en-US")}. Terms: Net 30.`, attachmentDocId: orderDocId, status: "sent", sentByRunId: `run_hist_${histSeq + 1}`, ts: hoursAgo(9 - i * 0.5) });
    chatMessages.push({ id: id("msg"), environmentId: PRODUCTION_ID, channel: "#revops", text: `Closed-Won paperwork complete for ${d.company} — Platform (USD ${d.amount.toLocaleString("en-US")}). Order form + kickoff checklist ready; invoice sent.`, byRunId: `run_hist_${histSeq + 1}`, ts: hoursAgo(9 - i * 0.5) });
    mkHistoricalRun({
      templateId: "closed_won_paperwork",
      environmentId: PRODUCTION_ID,
      hoursBack: 10 - i * 0.5,
      summary: `Papered ${d.company} — Platform: order form, CRM updated, kickoff checklist, invoice sent (approved).`,
      calls: [
        { tool: "crm.read_deal", args: { dealId }, result: { company: d.company, amount: d.amount } },
        { tool: "docs.get", args: { title: "Pricing & Discount Policy" } },
        { tool: "docs.create", args: { title: `Order Form — ${d.company} — Platform` } },
        { tool: "crm.update_deal", args: { dealId, fields: { paperwork_status: "complete" } } },
        { tool: "chat.post", args: { channel: "#revops", text: `Paperwork complete for ${d.company}` } },
        { tool: "email.send", args: { to: d.contact, subject: `Invoice & Order Form — ${d.company} — Platform` }, gated: "policy_gate", approvalTitle: `Send external email to ${d.contact}`, approvalLatencyMin: d.latency },
      ],
    });
  });

  // 47 leads researched overnight
  const leadCompanies = ["Vantage Robotics", "Clearwater BI", "Mosaic Health", "Pinch Logistics", "Kestrel Security", "Waypoint CRM", "Baseline Audio", "Fernwood Capital", "Atlas Terminal", "Juniper Retail"];
  for (let i = 0; i < 47; i++) {
    const co = `${leadCompanies[i % leadCompanies.length]}${i >= 10 ? " " + String.fromCharCode(65 + (i % 26)) : ""}`;
    const gated = i % 6 === 0;
    mkHistoricalRun({
      templateId: "lead_research",
      environmentId: PRODUCTION_ID,
      hoursBack: 2 + (i * 14) / 47,
      summary: gated ? `Researched ${co}; qualification approved.` : `Researched ${co}; enrichment written, brief created.`,
      calls: [
        { tool: "crm.read_lead", args: { company: co } },
        { tool: "crm.update_lead", args: { company: co, fields: { industry: "B2B", fit: "reviewed" } } },
        { tool: "docs.create", args: { title: `Research Brief — ${co}` } },
        ...(gated ? [{ tool: "crm.mark_lead_qualified", args: { company: co }, gated: "policy_gate" as const, approvalTitle: `Mark lead '${co}' research-qualified` }] : []),
      ],
    });
  }

  // 12 doc drafts overnight
  for (let i = 0; i < 12; i++) {
    const topic = ["Webhooks API", "SSO setup", "Billing exports", "Audit log", "Permissions", "Data retention", "Sandbox mode", "CSV import", "Alerts", "Report builder", "API keys", "Rate limits"][i];
    mkHistoricalRun({
      templateId: "docs_sync",
      environmentId: PRODUCTION_ID,
      hoursBack: 3 + i,
      triggerType: "manual",
      summary: `Drafted update to '${topic}' doc from change log; publication ${i % 4 === 0 ? "approved" : "pending drafts batch"}.`,
      calls: [
        { tool: "docs.get", args: { title: topic } },
        { tool: "docs.create", args: { title: `${topic} (draft update)` } },
        ...(i % 4 === 0 ? [{ tool: "docs.publish", args: { title: `${topic} (draft update)` }, gated: "policy_gate" as const, approvalTitle: `Publish doc '${topic} (draft update)'` }] : []),
      ],
    });
  }

  // 31 CRM records cleaned overnight (one nightly run touches many; model as runs for tile volume)
  for (let i = 0; i < 31; i++) {
    const rec = `record #${4200 + i * 7}`;
    const merge = i % 10 === 3;
    mkHistoricalRun({
      templateId: "crm_hygiene",
      environmentId: PRODUCTION_ID,
      hoursBack: 5 + (i * 3) / 31,
      triggerType: "schedule",
      summary: merge ? `Duplicate pair detected at ${rec}; merge ${i % 20 === 3 ? "approved" : "rejected — kept separate"}.` : `Normalized fields on ${rec}.`,
      calls: [
        { tool: "crm.search_deals", args: { filter: "malformed-fields" } },
        { tool: "crm.update_deal", args: { record: rec, fields: { normalized: "true" } } },
        ...(merge ? [{ tool: "crm.merge_records", args: { primary: rec, duplicate: rec + "-b" }, gated: "policy_gate" as const, approvalTitle: `Merge duplicate records ${rec}`, rejected: i % 20 !== 3 }] : []),
      ],
    });
  }

  // A couple of recent sandbox test runs for texture
  mkHistoricalRun({
    templateId: "closed_won_paperwork",
    environmentId: SANDBOX_ID,
    hoursBack: 26,
    triggerType: "manual",
    summary: "Sandbox validation run: full paperwork on test deal, invoice sent to sandbox inbox.",
    calls: [
      { tool: "crm.read_deal", args: { dealId: "deal_sbx_quill" } },
      { tool: "docs.create", args: { title: "Order Form — Quill & Co" } },
      { tool: "email.send", args: { to: "ana@sandbox.meridianlabs.dev" } },
    ],
  });

  return {
    environments,
    connectorInstances,
    policies,
    agents,
    agentVersions,
    deployments,
    runs,
    toolCalls,
    approvals,
    testScenarios,
    testRuns: [],
    promotions: [],
    auditEvents,
    crmDeals,
    crmLeads,
    docs,
    emails,
    chatMessages,
  };
}

function mkDeal(d: Omit<CrmDeal, "fields" | "updatedAt"> & { fields?: Record<string, string> }): CrmDeal {
  return { fields: {}, updatedAt: hoursAgo(2), ...d } as CrmDeal;
}

export { minutesAgo };
