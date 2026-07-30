import type { ConnectorId, EnvironmentKind, PolicyEffect, PolicyRule } from "./types";

// Permission policies and the pure evaluator that decides every tool call.
//
// This module holds no state. `gateway.ts` calls `evaluateRules` with the
// workspace's live rules; the public landing page calls the same function with
// `SEED_POLICIES` to show real verdicts without reading or seeding the demo
// store. One evaluator, one set of semantics, two callers.

export const SANDBOX_ID = "env_sandbox";
export const PRODUCTION_ID = "env_production";

export interface PolicyDecision {
  effect: PolicyEffect;
  rule?: PolicyRule;
  note?: string;
}

/**
 * Resolve (environment, connector, tool, args) to an effect.
 *
 * Precedence: an exact tool rule beats a `*` wildcard rule. With no rule at
 * all, Sandbox allows and Production requires approval — unstated actions fail
 * toward review, never toward execution.
 */
export function evaluateRules(
  rules: PolicyRule[],
  environmentKind: EnvironmentKind,
  connectorId: ConnectorId,
  tool: string,
  args: Record<string, unknown>,
): PolicyDecision {
  const scoped = rules.filter((p) => p.connectorId === connectorId);
  const rule = scoped.find((p) => p.tool === tool) ?? scoped.find((p) => p.tool === "*");

  if (!rule) {
    return environmentKind === "sandbox"
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

/** The permission policies Meridian Labs' two environments ship with. */
export const SEED_POLICIES: PolicyRule[] = [
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
