import type { ConnectorDef, ConnectorId } from "./types";

// Connector definitions and their tool manifests. In the full product these
// are MCP servers mounted behind the gateway (spec §6.2); the demo ships
// simulated implementations with identical manifests — the governance layer
// is real either way.

export const CONNECTORS: ConnectorDef[] = [
  {
    id: "hubspot",
    name: "HubSpot CRM",
    category: "CRM",
    tools: [
      { name: "crm.read_deal", description: "Read a deal record by id", kind: "read" },
      { name: "crm.search_deals", description: "Search deals by company or name", kind: "read" },
      { name: "crm.update_deal", description: "Write fields on a deal record", kind: "write" },
      { name: "crm.read_lead", description: "Read a lead record by id", kind: "read" },
      { name: "crm.update_lead", description: "Write enrichment fields on a lead", kind: "write" },
      { name: "crm.mark_lead_qualified", description: "Mark a lead research-qualified", kind: "write" },
      { name: "crm.merge_records", description: "Merge two duplicate records", kind: "write" },
    ],
  },
  {
    id: "google_email",
    name: "Gmail",
    category: "Email",
    tools: [
      { name: "email.send", description: "Send an email, optionally attaching a doc", kind: "write" },
      { name: "email.search", description: "Search sent mail", kind: "read" },
    ],
  },
  {
    id: "google_docs",
    name: "Google Docs",
    category: "Docs",
    tools: [
      { name: "docs.get", description: "Read a document by title", kind: "read" },
      { name: "docs.create", description: "Create a draft document", kind: "write" },
      { name: "docs.publish", description: "Publish a document", kind: "write" },
    ],
  },
  {
    id: "slack",
    name: "Slack",
    category: "Chat",
    tools: [{ name: "chat.post", description: "Post a message to a channel", kind: "write" }],
  },
];

export const BUILTIN_TOOLS = [
  {
    name: "flag_for_review",
    description:
      "Built-in escalation: pause the run and ask a human to review a consequential or ambiguous decision. Available to every agent in every environment.",
    kind: "write" as const,
  },
];

export function connectorForTool(tool: string): ConnectorDef | undefined {
  return CONNECTORS.find((c) => c.tools.some((t) => t.name === tool));
}

export function connectorById(id: ConnectorId): ConnectorDef {
  const c = CONNECTORS.find((c) => c.id === id);
  if (!c) throw new Error(`Unknown connector ${id}`);
  return c;
}
