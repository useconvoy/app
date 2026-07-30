// Core object model — see docs/product-spec.md §2 and §6.3.

export type EnvironmentKind = "sandbox" | "production";

export interface Environment {
  id: string;
  name: string;
  kind: EnvironmentKind;
  createdAt: string;
}

export type ConnectorId = "hubspot" | "google_email" | "google_docs" | "slack";

export interface ToolDef {
  name: string; // e.g. "crm.read_deal"
  description: string;
  kind: "read" | "write";
}

export interface ConnectorDef {
  id: ConnectorId;
  name: string;
  category: "CRM" | "Email" | "Docs" | "Chat";
  tools: ToolDef[];
}

export interface ConnectorInstance {
  id: string;
  environmentId: string;
  connectorId: ConnectorId;
  label: string; // e.g. "HubSpot test portal #1"
  credentialRef: string; // opaque handle into the vault — never exposed past the gateway
  health: "green" | "red";
  config: Record<string, string>;
}

export type PolicyEffect = "allow" | "deny" | "require_approval";

export interface PolicyRule {
  id: string;
  environmentId: string;
  connectorId: ConnectorId;
  tool: string;
  effect: PolicyEffect;
  /** When present, `effect` applies only if the condition triggers; otherwise the call is allowed. */
  conditions?: { recipient_domain_not_in?: string[] };
  approvers?: string[];
  timeoutHours?: number;
  onTimeout?: "reject" | "allow";
  note?: string;
}

export interface AgentTemplate {
  id: string;
  name: string;
  description: string;
  instructions: string;
  toolGrants: string[];
  params: Record<string, string>;
  trigger: TriggerSpec;
}

export interface Agent {
  id: string;
  name: string;
  description: string;
  templateId?: string;
  paused: boolean; // fleet-wide kill switch
  createdAt: string;
}

export interface TriggerSpec {
  type: "crm_webhook" | "manual" | "schedule";
  config: Record<string, string>;
}

export interface AgentVersion {
  id: string;
  agentId: string;
  version: number;
  instructions: string;
  toolGrants: string[];
  params: Record<string, string>;
  trigger: TriggerSpec;
  createdAt: string;
}

export interface Deployment {
  id: string;
  agentVersionId: string;
  agentId: string;
  environmentId: string;
  status: "active" | "superseded";
  createdAt: string;
}

export type RunState =
  | "queued"
  | "running"
  | "paused_pending_approval"
  | "succeeded"
  | "failed"
  | "rejected"
  | "killed";

export type TriggerType = "manual" | "webhook" | "schedule" | "test";

export interface Run {
  id: string;
  deploymentId: string;
  agentId: string;
  agentVersionId: string;
  environmentId: string;
  triggerType: TriggerType;
  triggerPayload: Record<string, unknown>;
  scenarioId?: string;
  state: RunState;
  summary?: string;
  startedAt: string;
  endedAt?: string;
  /** Live-model mode only: persisted message history so a paused run survives a restart (spec §6.4). */
  modelMessages?: unknown[];
  /** Live-model mode only: the tool_use id awaiting an approval decision. */
  pendingModelToolUseId?: string;
  /** Runtime provider selected when the run was created. */
  provider?: "local" | "temporal-fargate";
}

export interface CloudMissionAgent {
  agentId: string;
  depth: number;
  status: "RUNNING" | "COMPLETED";
  computeProvider?: string;
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
  thesis?: string;
  artifactKey?: string;
  artifact?: string;
  completedAt?: string;
}

export interface CloudMission {
  missionId: string;
  status: "QUEUED" | "RUNNING" | "COMPLETED";
  mission: {
    totalAgents?: number;
    completedAt?: string;
  } | null;
  agents: CloudMissionAgent[];
}

export type ToolCallStatus =
  | "executed"
  | "denied"
  | "pending_approval"
  | "approved_executed"
  | "rejected";

export interface ToolCall {
  id: string;
  runId: string;
  seq: number;
  tool: string;
  connectorId?: ConnectorId; // undefined for built-ins like flag_for_review
  args: Record<string, unknown>;
  result?: unknown;
  policyEffect: PolicyEffect | "agent_flagged" | "builtin";
  policyNote?: string;
  status: ToolCallStatus;
  approvalId?: string;
  latencyMs: number;
  ts: string;
}

export type ApprovalKind = "policy_gate" | "agent_flagged";

export interface Approval {
  id: string;
  runId: string;
  toolCallId: string;
  agentId: string;
  environmentId: string;
  kind: ApprovalKind;
  tool: string;
  /** Human-readable card: what exactly is about to happen. */
  title: string;
  detail: Record<string, unknown>;
  reason?: string; // for agent_flagged
  approvers: string[];
  status: "pending" | "approved" | "rejected" | "timed_out";
  approver?: string;
  requestedAt: string;
  decidedAt?: string;
}

export type AssertionSpec =
  | { type: "run_state"; expect: RunState }
  | { type: "flag_raised"; kind: ApprovalKind; reasonContains: string }
  | { type: "no_flag_raised" }
  | { type: "crm_deal_field"; dealRef: string; field: string; expectContains: string }
  | { type: "doc_exists"; titleContains: string; contentContains?: string }
  | { type: "no_doc"; titleContains: string }
  | { type: "email_sent"; toContains: string; bodyContains?: string }
  | { type: "no_email_sent" }
  | { type: "chat_posted"; textContains: string };

export interface TestScenario {
  id: string;
  agentTemplateId: string;
  key: string;
  name: string;
  description: string;
  fixture: { deal: Partial<CrmDeal>; extraDeals?: Partial<CrmDeal>[] };
  assertions: AssertionSpec[];
}

export interface AssertionResult {
  spec: AssertionSpec;
  pass: boolean;
  detail: string;
}

export interface TestRun {
  id: string;
  agentVersionId: string;
  scenarioId: string;
  runId: string;
  status: "running" | "pass" | "fail";
  results: AssertionResult[];
  at: string;
}

export interface Promotion {
  id: string;
  agentId: string;
  agentVersionId: string;
  fromEnvironmentId: string;
  toEnvironmentId: string;
  deploymentId: string;
  diff: PromotionDiff;
  approvedBy: string;
  at: string;
}

export interface PromotionDiff {
  credentialChanges: { connector: string; from: string; to: string }[];
  policyDeltas: { tool: string; from: string; to: string }[];
  testStatus: { total: number; passed: number; version: number };
}

export interface AuditEvent {
  id: string;
  ts: string;
  actor: string; // "system" | "agent:<id>" | user name
  type: string;
  message: string;
  refs: Partial<{
    runId: string;
    toolCallId: string;
    approvalId: string;
    agentId: string;
    environmentId: string;
    deploymentId: string;
    promotionId: string;
  }>;
}

// ---- Simulated external systems (per environment / connector instance) ----

export interface CrmDeal {
  id: string;
  environmentId: string;
  name: string;
  company: string;
  amount: number;
  currency: string;
  stage: string; // "qualified" | "proposal" | "closedwon" ...
  contactName: string;
  contactEmail: string;
  poNumber: string | null;
  discountPct: number;
  termMonths: number;
  ownerName: string;
  fields: Record<string, string>; // agent-written fields, e.g. paperwork_status
  namespace?: string; // test-fixture namespace for idempotent seeding/teardown
  updatedAt: string;
}

export interface CrmLead {
  id: string;
  environmentId: string;
  name: string;
  company: string;
  email: string;
  fields: Record<string, string>;
  updatedAt: string;
}

export interface Doc {
  id: string;
  environmentId: string;
  title: string;
  content: string;
  status: "draft" | "published";
  createdByRunId?: string;
  namespace?: string;
  createdAt: string;
}

export interface Email {
  id: string;
  environmentId: string;
  from: string;
  to: string;
  subject: string;
  body: string;
  attachmentDocId?: string;
  status: "sent";
  sentByRunId?: string;
  namespace?: string;
  ts: string;
}

export interface ChatMessage {
  id: string;
  environmentId: string;
  channel: string;
  text: string;
  byRunId?: string;
  namespace?: string;
  ts: string;
}

export interface Database {
  environments: Environment[];
  connectorInstances: ConnectorInstance[];
  policies: PolicyRule[];
  agents: Agent[];
  agentVersions: AgentVersion[];
  deployments: Deployment[];
  runs: Run[];
  toolCalls: ToolCall[];
  approvals: Approval[];
  testScenarios: TestScenario[];
  testRuns: TestRun[];
  promotions: Promotion[];
  auditEvents: AuditEvent[];
  // simulated external systems
  crmDeals: CrmDeal[];
  crmLeads: CrmLead[];
  docs: Doc[];
  emails: Email[];
  chatMessages: ChatMessage[];
}
