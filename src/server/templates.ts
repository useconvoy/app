import type { AgentTemplate } from "./types";

// The four prebuilt Meridian convoy templates (spec §3). Each declares the
// tools it needs from its environment, its parameters, and its trigger.

export const TEMPLATES: AgentTemplate[] = [
  {
    id: "closed_won_paperwork",
    name: "Closed-Won Paperwork Agent",
    description:
      "When a deal moves to Closed-Won, assemble its paperwork: order form, CRM fields, kickoff checklist, team notification, and the invoice email to the customer.",
    instructions: `You are Meridian Labs' Closed-Won Paperwork agent. When a deal reaches Closed-Won:
1. Read the full deal record and the pricing & discount policy doc.
2. Check for duplicate deals for the same company — if you suspect a duplicate, flag for review instead of papering it twice.
3. Validate the deal: a missing PO number, a missing contact email, or a discount above the standard threshold are consequential — flag them for human review with a clear reason and your proposed action.
4. Generate the order form from the order form template, with exact amounts in the deal's currency.
5. Update the deal record: paperwork status, order form link.
6. Create the kickoff checklist for the delivery team.
7. Notify the RevOps channel.
8. Send the invoice email to the deal's contact with the order form attached.
Never invent numbers. If anything about the deal looks out-of-distribution, use flag_for_review rather than guessing.`,
    toolGrants: [
      "crm.read_deal",
      "crm.search_deals",
      "crm.update_deal",
      "docs.get",
      "docs.create",
      "email.send",
      "chat.post",
    ],
    params: {
      order_form_template: "Meridian Standard Order Form v3",
      invoice_terms: "Net 30",
      discount_threshold_pct: "15",
      pricing_doc_title: "Pricing & Discount Policy",
      notification_channel: "#revops",
    },
    trigger: { type: "crm_webhook", config: { event: "deal.stage_changed", to_stage: "closedwon" } },
  },
  {
    id: "lead_research",
    name: "Lead Research Agent",
    description:
      "When a new lead is created, research the company, write enrichment fields back to the CRM, and produce a research brief. Marking a lead research-qualified is gated in Production.",
    instructions: `You are Meridian Labs' Lead Research agent. For each new lead: read the lead record, research the company, write enrichment fields (industry, headcount band, fit notes) back to the CRM, and create a one-page research brief doc. If the lead clearly meets the ICP, mark it research-qualified — that action is consequential, so expect it to be gated in Production.`,
    toolGrants: ["crm.read_lead", "crm.update_lead", "crm.mark_lead_qualified", "docs.create"],
    params: { icp_notes: "B2B SaaS, 50-2000 employees, revenue ops function exists" },
    trigger: { type: "crm_webhook", config: { event: "lead.created" } },
  },
  {
    id: "docs_sync",
    name: "Docs Sync Agent",
    description:
      "When a product change is flagged, draft updates to the affected docs. Publishing is gated in Production.",
    instructions: `You are Meridian Labs' Docs Sync agent. When a product change is flagged, read the change log entry, find the affected docs, and draft updated versions. Never publish directly — publishing is a gated action; create drafts and request publication.`,
    toolGrants: ["docs.get", "docs.create", "docs.publish"],
    params: { style_guide: "Meridian docs style v2" },
    trigger: { type: "manual", config: { event: "product.change_flagged" } },
  },
  {
    id: "crm_hygiene",
    name: "CRM Hygiene Agent",
    description:
      "Nightly: dedupe and normalize CRM fields, flag stale records. Merging duplicate records is gated in Production.",
    instructions: `You are Meridian Labs' CRM Hygiene agent. Each night: scan for malformed or inconsistent fields and normalize them, flag records untouched for 90+ days, and detect duplicate records. Merging two records is destructive and consequential — always route merges through review.`,
    toolGrants: ["crm.search_deals", "crm.update_deal", "crm.merge_records"],
    params: { stale_days: "90" },
    trigger: { type: "schedule", config: { cron: "0 2 * * *" } },
  },
];

export function templateById(id: string): AgentTemplate | undefined {
  return TEMPLATES.find((t) => t.id === id);
}
