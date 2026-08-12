/**
 * The typed client interface for the environments service (registry of
 * workspaces, their system grants, and their rehearsal bindings), with two
 * implementations behind one flag:
 *
 * - Real registry (environments-E0), used when BOTH env vars are set:
 *   CONVOY_ENVIRONMENTS_URL and CONVOY_ENVIRONMENTS_INTERNAL_TOKEN. Calls
 *   are server-side fetches carrying X-Convoy-Internal (the shared
 *   service token) and X-Convoy-Acts-For (the signed-in user's email from
 *   the verified session) on every org-scoped call. The org-scoped
 *   workspaces table stays on as a cache: the website's own uuid remains
 *   the id that routes and routine bindings reference, system grants and
 *   version notes render from the cache, and the environment bindings and
 *   clock mode read straight from the registry rows.
 *
 * - Table-backed stand-in, exactly as before, whenever either env var is
 *   unset (the deployed console today) or the active org carries no
 *   environments_org_id (created before the flag, or its provisioning
 *   call failed). Nothing already deployed changes behavior.
 */
import "server-only";

import { requireSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getEnvironmentsOrgId, setEnvironmentsOrgId } from "@/lib/orgs/queries";
import type { Role } from "@/lib/permissions";
import { firstCoveringWorkspace } from "@/lib/workspaces/fit";
import { grantsFromChoices } from "@/lib/workspaces/system-catalog";
import {
  connectionPlanForGrants,
  environmentsRole,
  normalizeClockMode,
  workspaceFromRegistryRow,
  type DeclaredManifest,
  type RegistryConnectionRow,
  type RegistryWorkspaceRow,
} from "./environments-mapping";

export interface StandIn {
  /** Plain-language note shown wherever the stand-in appears. */
  note: string;
}

export interface SystemGrant {
  systemId: string;
  displayName: string;
  scope: "read" | "write";
  /** True when a write through this system touches the outside world. */
  sideEffecting: boolean;
  /** Required whenever a side-effecting system is granted write. */
  standIn?: StandIn;
  /**
   * Tool surface pinned when the workspace grant is created. Optional for
   * workspaces created before connector grants were carried into runs.
   */
  tools?: Array<{ name: string; sideEffecting: boolean }>;
}

export interface WorkspaceVersion {
  version: number;
  note: string;
  createdAt: string;
}

export interface Workspace {
  id: string;
  name: string;
  purpose: string;
  /** Runnable production binding in the environments registry. */
  environmentId: string;
  /** Runnable rehearsal-copy binding; every workspace carries one. */
  rehearsalEnvironmentId: string;
  systems: SystemGrant[];
  clockMode: "wall" | "virtual";
  versions: WorkspaceVersion[];
  createdAt: string;
}

export interface BrowserPolicy {
  allowedDomains: string[];
  persistProfile: boolean;
}

/** An agent plus the runtime configuration used for each of its runs. */
export interface Agent {
  id: string;
  workspaceId: string;
  name: string;
  purpose: string;
  /** Internal registry id; null on the table-backed stand-in path. */
  registryEnvironmentId: string | null;
  productionBindingId: string;
  rehearsalBindingId: string;
  sandboxTemplate: string;
  browserPolicy: BrowserPolicy | null;
  /** Agent files and memory are checkpointed automatically across compute leases. */
  persistence: "automatic";
  /** The outcome this Agent owns. Empty only for a migrated runtime-only draft. */
  goal: string;
  /** Workspace systems this Agent is allowed to use for its goal. */
  systems: string[];
  /** Numeric string as Postgres returns it, e.g. "75.00". */
  budgetCapUsd: string;
  /** Plain-language working instructions; the runtime may revise its live plan. */
  planSteps: string[];
  /** Plain schedule text; null means on demand only. */
  scheduleDescription: string | null;
  /** Catalog provenance when this Agent was installed from a template. */
  sourceEntryId: string | null;
  sourceVersion: number | null;
  /** False only for an older runtime profile that still needs a goal. */
  automationConfigured: boolean;
  version: number;
  isDefault: boolean;
  createdAt: string;
  updatedAt: string;
}

/**
 * Shape of the compatibility report rendered during catalog installs:
 * green checks, mapping choices, and connect prompts.
 * TODO(environments-E0): the environments service will own this
 * computation; lib/catalog/compat computes it website-side until then.
 */
export interface CompatibilityReport {
  workspaceId: string;
  greenChecks: string[];
  mappingChoices: Array<{ systemId: string; options: string[] }>;
  connectPrompts: string[];
  vendorSpecificToolCount: number;
}

/** A custom system granted at workspace create: the org's own connection. */
export interface CustomWorkspaceSystem {
  /** Grant id, "custom:<connectionId>". */
  id: string;
  connectionId: string;
  displayName: string;
  sideEffecting: boolean;
  standInNote: string | null;
  /** The connection's declared tools, for scope → allowlist mapping. */
  tools: Array<{ name: string; sideEffecting: boolean }>;
}

export interface CreateWorkspaceInput {
  name: string;
  purpose: string;
  systems: Array<{
    systemId: string;
    scope: "read" | "write";
    useStandIn: boolean;
    /** Explicitly enabled actions; absent grants the scope-derived surface. */
    tools?: string[];
  }>;
  /** Definitions for any "custom:" ids in `systems`; supplied server-side. */
  customSystems?: CustomWorkspaceSystem[];
}

export interface CreateAgentInput {
  workspaceId: string;
  name: string;
  purpose: string;
  goal: string;
  planSteps: string[];
  scheduleDescription: string | null;
  budgetCapUsd: number;
  sandboxTemplate: string;
  browserPolicy: BrowserPolicy | null;
  makeDefault?: boolean;
}

export interface EnvironmentsClient {
  listWorkspaces(orgId: string): Promise<Workspace[]>;
  getWorkspace(orgId: string, workspaceId: string): Promise<Workspace | null>;
  createWorkspace(orgId: string, input: CreateWorkspaceInput): Promise<Workspace>;
  listAgents(orgId: string, workspaceId?: string): Promise<Agent[]>;
  getAgent(orgId: string, agentId: string): Promise<Agent | null>;
  createAgent(orgId: string, input: CreateAgentInput): Promise<Agent>;
}

interface WorkspaceRow {
  id: string;
  name: string;
  purpose: string;
  environmentId: string;
  rehearsalEnvironmentId: string;
  systems: unknown;
  clockMode: string;
  versions: unknown;
  createdAt: Date;
}

interface AgentRow {
  id: string;
  workspaceId: string;
  registryEnvironmentId: string | null;
  name: string;
  purpose: string;
  productionBindingId: string;
  rehearsalBindingId: string;
  sandboxTemplate: string;
  browserPolicy: unknown;
  goal: string;
  systems: string[];
  budgetCapUsd: string;
  planSteps: unknown;
  scheduleDescription: string | null;
  sourceEntryId: string | null;
  sourceVersion: number | null;
  automationConfigured: boolean;
  version: number;
  isDefault: boolean;
  createdAt: Date;
  updatedAt: Date;
}

const AGENT_COLUMNS = `id, workspace_id AS "workspaceId",
       registry_environment_id AS "registryEnvironmentId", name, purpose,
       production_binding_id AS "productionBindingId",
       rehearsal_binding_id AS "rehearsalBindingId",
       sandbox_template AS "sandboxTemplate", browser_policy AS "browserPolicy",
       goal, systems, budget_cap_usd AS "budgetCapUsd",
       plan_steps AS "planSteps",
       schedule_description AS "scheduleDescription",
       source_entry_id AS "sourceEntryId",
       source_version AS "sourceVersion",
       automation_configured AS "automationConfigured",
       version, is_default AS "isDefault", created_at AS "createdAt",
       updated_at AS "updatedAt"`;

function toBrowserPolicy(value: unknown): BrowserPolicy | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as { allowedDomains?: unknown; persistProfile?: unknown };
  return {
    allowedDomains: Array.isArray(raw.allowedDomains)
      ? raw.allowedDomains.filter((domain): domain is string => typeof domain === "string")
      : [],
    persistProfile: raw.persistProfile !== false,
  };
}

function toAgent(row: AgentRow): Agent {
  return {
    id: row.id,
    workspaceId: row.workspaceId,
    name: row.name,
    purpose: row.purpose,
    registryEnvironmentId: row.registryEnvironmentId,
    productionBindingId: row.productionBindingId,
    rehearsalBindingId: row.rehearsalBindingId,
    sandboxTemplate: row.sandboxTemplate,
    browserPolicy: toBrowserPolicy(row.browserPolicy),
    persistence: "automatic",
    goal: row.goal,
    systems: Array.isArray(row.systems) ? row.systems : [],
    budgetCapUsd: row.budgetCapUsd,
    planSteps: Array.isArray(row.planSteps)
      ? row.planSteps.filter((step): step is string => typeof step === "string")
      : [],
    scheduleDescription: row.scheduleDescription,
    sourceEntryId: row.sourceEntryId,
    sourceVersion: row.sourceVersion,
    automationConfigured: row.automationConfigured,
    version: row.version,
    isDefault: row.isDefault,
    createdAt: row.createdAt.toISOString(),
    updatedAt: row.updatedAt.toISOString(),
  };
}

const WORKSPACE_COLUMNS = `id, name, purpose,
       environment_id AS "environmentId",
       rehearsal_environment_id AS "rehearsalEnvironmentId",
       systems, clock_mode AS "clockMode", versions,
       created_at AS "createdAt"`;

/**
 * Registry-linked row shape, selected only on the remote paths so a
 * database that has not applied migration 0007 never sees the column
 * while the flag is off.
 */
interface LinkedWorkspaceRow extends WorkspaceRow {
  environmentsWorkspaceId: string | null;
}

const LINKED_WORKSPACE_COLUMNS = `${WORKSPACE_COLUMNS},
       environments_workspace_id AS "environmentsWorkspaceId"`;

/** Hydrate a stored row into the typed shape, tolerating older rows. */
function toWorkspace(row: WorkspaceRow): Workspace {
  return {
    id: row.id,
    name: row.name,
    purpose: row.purpose,
    environmentId: row.environmentId,
    rehearsalEnvironmentId: row.rehearsalEnvironmentId,
    systems: Array.isArray(row.systems) ? (row.systems as SystemGrant[]) : [],
    clockMode: row.clockMode === "virtual" ? "virtual" : "wall",
    versions: Array.isArray(row.versions) ? (row.versions as WorkspaceVersion[]) : [],
    createdAt: row.createdAt.toISOString(),
  };
}

/** Route params are arbitrary text; only a uuid can be a row id. */
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

class DbEnvironmentsClient implements EnvironmentsClient {
  async listWorkspaces(orgId: string): Promise<Workspace[]> {
    return withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<WorkspaceRow>(
        `SELECT ${WORKSPACE_COLUMNS} FROM workspaces WHERE org_id = $1 ORDER BY created_at`,
        [orgId],
      );
      return rows.map(toWorkspace);
    });
  }

  async getWorkspace(orgId: string, workspaceId: string): Promise<Workspace | null> {
    if (!UUID_PATTERN.test(workspaceId)) return null;
    return withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<WorkspaceRow>(
        `SELECT ${WORKSPACE_COLUMNS} FROM workspaces WHERE org_id = $1 AND id = $2`,
        [orgId, workspaceId],
      );
      return rows[0] ? toWorkspace(rows[0]) : null;
    });
  }

  async createWorkspace(orgId: string, input: CreateWorkspaceInput): Promise<Workspace> {
    const systems = grantsFromChoices(input.systems, input.customSystems ?? []);
    const now = new Date().toISOString();
    const versions: WorkspaceVersion[] = [{ version: 1, note: "Created", createdAt: now }];
    return withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<WorkspaceRow>(
        // Flag-off path: created workspaces bind to the local stub
        // registry so their runs execute for real. The registry client
        // below carries the real bindings when the flag is set.
        `INSERT INTO workspaces
           (org_id, name, purpose, environment_id, rehearsal_environment_id,
            systems, clock_mode, versions)
         VALUES ($1, $2, $3, 'prod-local', 'stub-local', $4, 'wall', $5)
         RETURNING ${WORKSPACE_COLUMNS}`,
        [orgId, input.name, input.purpose, JSON.stringify(systems), JSON.stringify(versions)],
      );
      return toWorkspace(rows[0]!);
    });
  }

  async listAgents(
    orgId: string,
    workspaceId?: string,
  ): Promise<Agent[]> {
    if (workspaceId && !UUID_PATTERN.test(workspaceId)) return [];
    return withOrgContext({ orgId }, async (client) => {
      const values: string[] = [orgId];
      const workspaceFilter = workspaceId ? " AND workspace_id = $2" : "";
      if (workspaceId) values.push(workspaceId);
      const { rows } = await client.query<AgentRow>(
        `SELECT ${AGENT_COLUMNS}
           FROM agents
          WHERE org_id = $1${workspaceFilter}
          ORDER BY is_default DESC, created_at`,
        values,
      );
      return rows.map(toAgent);
    });
  }

  async getAgent(
    orgId: string,
    agentId: string,
  ): Promise<Agent | null> {
    if (!UUID_PATTERN.test(agentId)) return null;
    return withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<AgentRow>(
        `SELECT ${AGENT_COLUMNS}
           FROM agents WHERE org_id = $1 AND id = $2`,
        [orgId, agentId],
      );
      return rows[0] ? toAgent(rows[0]) : null;
    });
  }

  async createAgent(
    orgId: string,
    input: CreateAgentInput,
  ): Promise<Agent> {
    const workspace = await this.getWorkspace(orgId, input.workspaceId);
    if (!workspace) throw new Error("That workspace does not exist");
    return withOrgContext({ orgId }, async (client) => {
      const existing = await client.query<{ present: boolean }>(
        "SELECT EXISTS (SELECT 1 FROM agents WHERE org_id = $1 AND workspace_id = $2) AS present",
        [orgId, input.workspaceId],
      );
      const makeDefault = input.makeDefault === true || existing.rows[0]?.present !== true;
      if (makeDefault) {
        await client.query(
          "UPDATE agents SET is_default = false, updated_at = now() WHERE org_id = $1 AND workspace_id = $2",
          [orgId, input.workspaceId],
        );
      }
      const { rows } = await client.query<AgentRow>(
        `INSERT INTO agents
           (org_id, workspace_id, name, purpose, production_binding_id,
            rehearsal_binding_id, sandbox_template, browser_policy, is_default,
            goal, systems, budget_cap_usd, plan_steps, schedule_description,
            automation_configured)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                 $10, $11, $12, $13, $14, true)
         RETURNING ${AGENT_COLUMNS}`,
        [
          orgId,
          input.workspaceId,
          input.name,
          input.purpose,
          workspace.environmentId,
          workspace.rehearsalEnvironmentId,
          input.sandboxTemplate,
          input.browserPolicy ? JSON.stringify(input.browserPolicy) : null,
          makeDefault,
          input.goal,
          workspace.systems.map((system) => system.systemId),
          input.budgetCapUsd,
          JSON.stringify(input.planSteps),
          input.scheduleDescription,
        ],
      );
      return toAgent(rows[0]!);
    });
  }
}

// ---------------------------------------------------------------------------
// The real registry (environments-E0), behind the two-env-var flag.

interface RegistryConfig {
  baseUrl: string;
  token: string;
}

// The registry still requires a workspace compatibility binding. Agents add
// their own runtime definition beneath it; this template only keeps that
// internal bridge runnable while new Agents choose their own template.
const DEFAULT_SANDBOX_TEMPLATE = "convoy-devbox-python";

/** Both env vars or nothing: a half-configured flag stays off. */
function registryConfig(): RegistryConfig | null {
  const baseUrl = process.env.CONVOY_ENVIRONMENTS_URL;
  const token = process.env.CONVOY_ENVIRONMENTS_INTERNAL_TOKEN;
  if (!baseUrl || !token) return null;
  return { baseUrl: baseUrl.replace(/\/+$/, ""), token };
}

/**
 * One registry call. X-Convoy-Internal rides every request; org-scoped
 * calls also carry X-Convoy-Acts-For with the signed-in user's email (the
 * registry resolves it to an already-invited user there). Provisioning is
 * the one call without acts-for: the creator does not exist registry-side
 * until that call returns.
 */
async function registryFetch<T>(
  config: RegistryConfig,
  path: string,
  options: { method?: "GET" | "POST" | "DELETE"; body?: unknown; actsFor?: string } = {},
): Promise<T> {
  const headers: Record<string, string> = { "X-Convoy-Internal": config.token };
  if (options.actsFor) headers["X-Convoy-Acts-For"] = options.actsFor;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const method = options.method ?? "GET";
  const response = await fetch(config.baseUrl + path, {
    method,
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = (await response.text().catch(() => "")).slice(0, 300);
    throw new Error(`environments registry ${method} ${path} returned ${response.status}: ${detail}`);
  }
  return (await response.json()) as T;
}

/** What one registry workspace create returns. */
interface RegistryWorkspaceCreated {
  workspaceId: string;
  version: number;
  policyHash: string;
  environmentId: string;
}

interface RegistryAgentRuntimeRow {
  environmentId: string;
  workspaceId: string;
  version: number;
  name: string;
  purpose: string;
  sandboxTemplate: string;
  browserPolicy: BrowserPolicy | null;
  productionBindingId: string;
  rehearsalBindingId: string;
  createdAt: string;
}

class RegistryEnvironmentsClient implements EnvironmentsClient {
  constructor(
    private readonly config: RegistryConfig,
    private readonly fallback: DbEnvironmentsClient,
  ) {}

  /**
   * Per-call context: the acting user's email from the verified session
   * and the registry organization behind this org. Null context means the
   * org was never provisioned registry-side; every method then falls back
   * to the table-backed client so those orgs keep working unchanged.
   */
  private async context(orgId: string): Promise<{ registryOrgId: string; actsFor: string } | null> {
    const session = await requireSession();
    const registryOrgId = await getEnvironmentsOrgId(orgId);
    if (!registryOrgId) return null;
    return { registryOrgId, actsFor: session.email };
  }

  private listRegistryRows(ctx: { registryOrgId: string; actsFor: string }) {
    return registryFetch<RegistryWorkspaceRow[]>(
      this.config,
      `/organizations/${ctx.registryOrgId}/workspaces`,
      { actsFor: ctx.actsFor },
    );
  }

  async listWorkspaces(orgId: string): Promise<Workspace[]> {
    const ctx = await this.context(orgId);
    if (!ctx) return this.fallback.listWorkspaces(orgId);
    const registryRows = await this.listRegistryRows(ctx);
    return withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<LinkedWorkspaceRow>(
        `SELECT ${LINKED_WORKSPACE_COLUMNS} FROM workspaces WHERE org_id = $1 ORDER BY created_at`,
        [orgId],
      );
      const byRegistryId = new Map(
        rows
          .filter((row) => row.environmentsWorkspaceId)
          .map((row) => [row.environmentsWorkspaceId as string, row]),
      );
      const merged: Workspace[] = [];
      const seen = new Set<string>();
      for (const registryRow of registryRows) {
        let cached = byRegistryId.get(registryRow.workspaceId);
        if (!cached) {
          // A registry workspace this console has never stored (created
          // through another surface): give it a website uuid so routes
          // and routine bindings can reference it. Its grants render
          // empty until something website-side records them.
          const { rows: inserted } = await client.query<LinkedWorkspaceRow>(
            `INSERT INTO workspaces
               (org_id, name, purpose, environment_id, rehearsal_environment_id,
                systems, clock_mode, versions, environments_workspace_id)
             VALUES ($1, $2, $3, $4, $5, '[]', $6, $7, $8)
             RETURNING ${LINKED_WORKSPACE_COLUMNS}`,
            [
              orgId,
              registryRow.name,
              registryRow.purpose,
              registryRow.environmentId,
              registryRow.rehearsalEnvironmentId,
              normalizeClockMode(registryRow.clockMode),
              JSON.stringify([
                { version: registryRow.version, note: "Linked", createdAt: new Date().toISOString() },
              ]),
              registryRow.workspaceId,
            ],
          );
          cached = inserted[0]!;
        }
        seen.add(cached.id);
        merged.push(workspaceFromRegistryRow(registryRow, toWorkspace(cached)));
      }
      // Rows the registry does not know: workspaces created while the
      // flag was off (or whose registry row is momentarily unlisted).
      // They keep their table-backed behavior rather than disappearing.
      for (const row of rows) {
        if (!seen.has(row.id)) merged.push(toWorkspace(row));
      }
      merged.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
      return merged;
    });
  }

  async getWorkspace(orgId: string, workspaceId: string): Promise<Workspace | null> {
    const ctx = await this.context(orgId);
    if (!ctx) return this.fallback.getWorkspace(orgId, workspaceId);
    if (!UUID_PATTERN.test(workspaceId)) return null;
    const cached = await withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<LinkedWorkspaceRow>(
        `SELECT ${LINKED_WORKSPACE_COLUMNS} FROM workspaces WHERE org_id = $1 AND id = $2`,
        [orgId, workspaceId],
      );
      return rows[0] ?? null;
    });
    if (!cached) return null;
    if (!cached.environmentsWorkspaceId) return toWorkspace(cached);
    const registryRows = await this.listRegistryRows(ctx);
    const registryRow = registryRows.find(
      (row) => row.workspaceId === cached.environmentsWorkspaceId,
    );
    // A linked row the registry no longer lists renders from the cache
    // rather than vanishing mid-session.
    return registryRow ? workspaceFromRegistryRow(registryRow, toWorkspace(cached)) : toWorkspace(cached);
  }

  async createWorkspace(orgId: string, input: CreateWorkspaceInput): Promise<Workspace> {
    const ctx = await this.context(orgId);
    if (!ctx) return this.fallback.createWorkspace(orgId, input);
    const custom = input.customSystems ?? [];
    const systems = grantsFromChoices(input.systems, custom);

    // A workspace bundles the organization's existing connectors: every
    // provider-backed grant must resolve to a connection made on the
    // Connectors page. Creating secretless placeholder connections here
    // was the pre-Connectors behavior; now a missing connection is the
    // caller's error, stated in the connector's own name. (Credential
    // health is enforced by the create action; this guard keeps the
    // client honest for any other caller.)
    const existing = await registryFetch<RegistryConnectionRow[]>(
      this.config,
      `/organizations/${ctx.registryOrgId}/connections`,
      { actsFor: ctx.actsFor },
    );
    const connections: Array<{ connectionId: string; toolAllowlist: string[] }> = [];
    for (const plan of connectionPlanForGrants(systems)) {
      const connectionId = existing.find((row) => row.provider === plan.provider)?.connectionId;
      if (!connectionId) {
        throw new Error(
          `${plan.displayName} is not connected: connect it on the Connectors page first`,
        );
      }
      connections.push({ connectionId, toolAllowlist: plan.toolAllowlist });
    }

    // Custom systems already ARE connections; the grant maps straight to
    // an allowlist over the tools the server declared (read stays to the
    // non-side-effecting ones, write takes them all).
    const customById = new Map(custom.map((definition) => [definition.id, definition]));
    for (const choice of input.systems) {
      const definition = customById.get(choice.systemId);
      if (!definition) continue;
      const selected = choice.tools ? new Set(choice.tools) : null;
      const tools = definition.tools
        .filter((tool) => (selected ? selected.has(tool.name) : true))
        .filter((tool) => choice.scope === "write" || !tool.sideEffecting);
      connections.push({
        connectionId: definition.connectionId,
        toolAllowlist: tools.map((tool) => tool.name),
      });
    }

    const created = await registryFetch<RegistryWorkspaceCreated>(
      this.config,
      `/organizations/${ctx.registryOrgId}/workspaces`,
      {
        method: "POST",
        body: {
          name: input.name,
          purpose: input.purpose,
          connections,
          sandboxTemplate: DEFAULT_SANDBOX_TEMPLATE,
        },
        actsFor: ctx.actsFor,
      },
    );
    // The create response carries the production binding only; the list
    // row adds the rehearsal binding and clock modes.
    const registryRow = (await this.listRegistryRows(ctx)).find(
      (row) => row.workspaceId === created.workspaceId,
    );

    const now = new Date().toISOString();
    const versions: WorkspaceVersion[] = [{ version: created.version, note: "Created", createdAt: now }];
    const cached = await withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<LinkedWorkspaceRow>(
        `INSERT INTO workspaces
           (org_id, name, purpose, environment_id, rehearsal_environment_id,
            systems, clock_mode, versions, environments_workspace_id)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
         RETURNING ${LINKED_WORKSPACE_COLUMNS}`,
        [
          orgId,
          input.name,
          input.purpose,
          registryRow?.environmentId ?? created.environmentId,
          registryRow?.rehearsalEnvironmentId ?? "",
          JSON.stringify(systems),
          normalizeClockMode(registryRow?.clockMode),
          JSON.stringify(versions),
          created.workspaceId,
        ],
      );
      return rows[0]!;
    });
    return registryRow
      ? workspaceFromRegistryRow(registryRow, toWorkspace(cached))
      : toWorkspace(cached);
  }

  async listAgents(
    orgId: string,
    workspaceId?: string,
  ): Promise<Agent[]> {
    if (!workspaceId) {
      const workspaces = await this.listWorkspaces(orgId);
      const nested = await Promise.all(
        workspaces.map((workspace) => this.listAgents(orgId, workspace.id)),
      );
      return nested.flat().sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    }
    const ctx = await this.context(orgId);
    if (!ctx || !UUID_PATTERN.test(workspaceId)) {
      return this.fallback.listAgents(orgId, workspaceId);
    }
    const cachedWorkspace = await withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<LinkedWorkspaceRow>(
        `SELECT ${LINKED_WORKSPACE_COLUMNS} FROM workspaces WHERE org_id = $1 AND id = $2`,
        [orgId, workspaceId],
      );
      return rows[0] ?? null;
    });
    if (!cachedWorkspace?.environmentsWorkspaceId) {
      return this.fallback.listAgents(orgId, workspaceId);
    }
    const remote = await registryFetch<RegistryAgentRuntimeRow[]>(
      this.config,
      `/organizations/${ctx.registryOrgId}/workspaces/${cachedWorkspace.environmentsWorkspaceId}/environments`,
      { actsFor: ctx.actsFor },
    );
    return withOrgContext({ orgId }, async (client) => {
      const existing = await client.query<AgentRow>(
        `SELECT ${AGENT_COLUMNS}
           FROM agents WHERE org_id = $1 AND workspace_id = $2`,
        [orgId, workspaceId],
      );
      const byRegistryId = new Map(
        existing.rows
          .filter((row) => row.registryEnvironmentId)
          .map((row) => [row.registryEnvironmentId as string, row]),
      );
      const hasDefault = existing.rows.some((row) => row.isDefault);
      const merged: Agent[] = [];
      for (const [index, runtime] of remote.entries()) {
        let row = byRegistryId.get(runtime.environmentId);
        if (!row) {
          const inserted = await client.query<AgentRow>(
            `INSERT INTO agents
               (org_id, workspace_id, registry_environment_id, name, purpose,
                production_binding_id, rehearsal_binding_id, sandbox_template,
                browser_policy, version, is_default)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
             RETURNING ${AGENT_COLUMNS}`,
            [
              orgId,
              workspaceId,
              runtime.environmentId,
              runtime.name,
              runtime.purpose,
              runtime.productionBindingId,
              runtime.rehearsalBindingId,
              runtime.sandboxTemplate,
              runtime.browserPolicy ? JSON.stringify(runtime.browserPolicy) : null,
              runtime.version,
              !hasDefault && index === 0,
            ],
          );
          row = inserted.rows[0]!;
        }
        merged.push(toAgent(row));
      }
      const remoteIds = new Set(remote.map((runtime) => runtime.environmentId));
      for (const row of existing.rows) {
        if (!row.registryEnvironmentId || !remoteIds.has(row.registryEnvironmentId)) {
          merged.push(toAgent(row));
        }
      }
      return merged.sort((a, b) => Number(b.isDefault) - Number(a.isDefault));
    });
  }

  async getAgent(
    orgId: string,
    agentId: string,
  ): Promise<Agent | null> {
    const cached = await this.fallback.getAgent(orgId, agentId);
    if (!cached) return null;
    const agents = await this.listAgents(orgId, cached.workspaceId);
    return agents.find((agent) => agent.id === agentId) ?? cached;
  }

  async createAgent(
    orgId: string,
    input: CreateAgentInput,
  ): Promise<Agent> {
    const ctx = await this.context(orgId);
    if (!ctx) return this.fallback.createAgent(orgId, input);
    const cachedWorkspace = await withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<LinkedWorkspaceRow>(
        `SELECT ${LINKED_WORKSPACE_COLUMNS} FROM workspaces WHERE org_id = $1 AND id = $2`,
        [orgId, input.workspaceId],
      );
      return rows[0] ?? null;
    });
    if (!cachedWorkspace?.environmentsWorkspaceId) {
      return this.fallback.createAgent(orgId, input);
    }
    const created = await registryFetch<RegistryAgentRuntimeRow>(
      this.config,
      `/organizations/${ctx.registryOrgId}/workspaces/${cachedWorkspace.environmentsWorkspaceId}/environments`,
      {
        method: "POST",
        body: {
          name: input.name,
          purpose: input.purpose,
          sandboxTemplate: input.sandboxTemplate,
          browserPolicy: input.browserPolicy,
        },
        actsFor: ctx.actsFor,
      },
    );
    return withOrgContext({ orgId }, async (client) => {
      const existing = await client.query<{ present: boolean }>(
        "SELECT EXISTS (SELECT 1 FROM agents WHERE org_id = $1 AND workspace_id = $2) AS present",
        [orgId, input.workspaceId],
      );
      const makeDefault = input.makeDefault === true || existing.rows[0]?.present !== true;
      if (makeDefault) {
        await client.query(
          "UPDATE agents SET is_default = false, updated_at = now() WHERE org_id = $1 AND workspace_id = $2",
          [orgId, input.workspaceId],
        );
      }
      const { rows } = await client.query<AgentRow>(
        `INSERT INTO agents
           (org_id, workspace_id, registry_environment_id, name, purpose,
            production_binding_id, rehearsal_binding_id, sandbox_template,
            browser_policy, version, is_default, goal, systems,
            budget_cap_usd, plan_steps, schedule_description,
            automation_configured)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                 $12, $13, $14, $15, $16, true)
         RETURNING ${AGENT_COLUMNS}`,
        [
          orgId,
          input.workspaceId,
          created.environmentId,
          created.name,
          created.purpose,
          created.productionBindingId,
          created.rehearsalBindingId,
          created.sandboxTemplate,
          created.browserPolicy ? JSON.stringify(created.browserPolicy) : null,
          created.version,
          makeDefault,
          input.goal,
          toWorkspace(cachedWorkspace).systems.map((system) => system.systemId),
          input.budgetCapUsd,
          JSON.stringify(input.planSteps),
          input.scheduleDescription,
        ],
      );
      return toAgent(rows[0]!);
    });
  }
}

/**
 * The one environments client: the real registry when the flag is fully
 * set, the table-backed stand-in otherwise.
 */
export function environmentsClient(): EnvironmentsClient {
  const config = registryConfig();
  const fallback = new DbEnvironmentsClient();
  return config ? new RegistryEnvironmentsClient(config, fallback) : fallback;
}

// ---------------------------------------------------------------------------
// Org lifecycle and invite hooks (called from the org server actions).

/**
 * Mirror a newly created website org into the registry and persist the
 * returned organization id on the org row. Best-effort by design: a
 * failure logs, leaves environments_org_id null, and the org simply stays
 * table-backed; org creation itself never blocks on the registry.
 */
export async function provisionEnvironmentsOrg(input: {
  orgId: string;
  name: string;
  creatorEmail: string;
}): Promise<void> {
  const config = registryConfig();
  if (!config) return;
  try {
    const created = await registryFetch<{ organizationId: string; userId: string }>(
      config,
      "/organizations",
      // Provisioning call: internal token only. The creator gets the
      // registry's first admin membership from this call, which is what
      // later lets their email act for the org.
      { method: "POST", body: { name: input.name, creatorEmail: input.creatorEmail } },
    );
    await setEnvironmentsOrgId(input.orgId, created.organizationId);
  } catch (error) {
    console.error(`environments registry provisioning failed for org ${input.orgId}`, error);
  }
}

/**
 * Mirror a website invite into the registry as a membership. Called when
 * the invite is CREATED, not accepted, for three reasons: the email and
 * role are definitive on the invite row at creation; the acting admin's
 * email resolves to a registry admin, which the membership upsert
 * requires (at acceptance the actor would be the invitee, who has no
 * registry standing yet); and the registry's own membership model is
 * invite-by-email that activates on the invitee's first sign-in, so
 * creating it early is its native semantics. A website-side revocation
 * before acceptance leaves the registry membership in place; the
 * registry contract has no removal call yet, and reconciliation is a
 * later phase. Best-effort like provisioning: a failure logs and the
 * invite stands.
 */
export async function syncInviteToEnvironments(input: {
  orgId: string;
  actorEmail: string;
  email: string;
  role: Role;
}): Promise<void> {
  const config = registryConfig();
  if (!config) return;
  try {
    const registryOrgId = await getEnvironmentsOrgId(input.orgId);
    if (!registryOrgId) return;
    await registryFetch(config, `/organizations/${registryOrgId}/memberships`, {
      method: "POST",
      body: { email: input.email, role: environmentsRole(input.role) },
      actsFor: input.actorEmail,
    });
  } catch (error) {
    console.error(`environments registry membership sync failed for org ${input.orgId}`, error);
  }
}

// ---------------------------------------------------------------------------
// The systems surface: the org's registry connections, for the Systems page
// and the workspace modal's status chips. Everything here returns null (or
// no-ops) when the registry flag is off or the org was never provisioned —
// callers render the honest not-linked state instead of failing.

export interface SystemConnection {
  connectionId: string;
  kind: string;
  provider: string;
  displayName: string;
  status: "active" | "needs_reauth" | "revoked";
  hasCredential: boolean;
  toolCount: number;
  /** The connection's declared tool surface, with honesty flags. */
  tools: Array<{ name: string; sideEffecting: boolean }>;
}

interface RegistryConnectionDetail {
  connectionId: string;
  kind: string;
  provider: string;
  displayName: string;
  status: SystemConnection["status"];
  hasCredential: boolean;
  tools: Array<{ name: string; execution: string; sideEffecting: boolean }>;
}

async function systemsContext(orgId: string) {
  const config = registryConfig();
  if (!config) return null;
  let registryOrgId = await getEnvironmentsOrgId(orgId);
  const session = await requireSession();
  if (!registryOrgId) {
    // Lazy backfill: an organization created before this deployment was
    // linked to the registry provisions its registry org on first use,
    // so connectors are connectable the moment the link exists instead
    // of only for organizations created after it.
    const org = await withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<{ name: string }>(
        "SELECT name FROM organizations WHERE id = $1",
        [orgId],
      );
      return rows[0] ?? null;
    });
    if (!org) return null;
    await provisionEnvironmentsOrg({ orgId, name: org.name, creatorEmail: session.email });
    registryOrgId = await getEnvironmentsOrgId(orgId);
    if (!registryOrgId) return null;
  }
  return { config, registryOrgId, actsFor: session.email };
}

/** Every registry connection of the org, with credential + tool facts. */
export async function listSystemConnections(orgId: string): Promise<SystemConnection[] | null> {
  const ctx = await systemsContext(orgId);
  if (!ctx) return null;
  const rows = await registryFetch<RegistryConnectionRow[]>(
    ctx.config,
    `/organizations/${ctx.registryOrgId}/connections`,
    { actsFor: ctx.actsFor },
  );
  const details = await Promise.all(
    rows.map((row) =>
      registryFetch<RegistryConnectionDetail>(
        ctx.config,
        `/organizations/${ctx.registryOrgId}/connections/${row.connectionId}`,
        { actsFor: ctx.actsFor },
      ),
    ),
  );
  return details.map((detail) => ({
    connectionId: detail.connectionId,
    kind: detail.kind,
    provider: detail.provider,
    displayName: detail.displayName,
    status: detail.status,
    hasCredential: detail.hasCredential,
    toolCount: detail.tools.length,
    tools: detail.tools.map((tool) => ({ name: tool.name, sideEffecting: tool.sideEffecting })),
  }));
}

/** One provider directory entry, served by the registry. */
export interface ProviderDirectoryEntry {
  provider: string;
  displayName: string;
  description: string;
  kind: "code" | "declarative";
  scope: "platform";
  mcpUrl?: string;
  credential: { label: string; placeholder: string; multiline: boolean; steps: string[] };
  tools: Array<{ name: string; execution?: string; sideEffecting: boolean; description?: string }>;
}

/**
 * The registry-served provider directory: every provider the platform can
 * reach, before any connection exists. Null when the registry is not
 * linked (the caller falls back to the static catalog).
 */
export async function listProviderDirectory(
  orgId: string,
): Promise<ProviderDirectoryEntry[] | null> {
  const ctx = await systemsContext(orgId);
  if (!ctx) return null;
  return registryFetch<ProviderDirectoryEntry[]>(ctx.config, "/providers", {
    actsFor: ctx.actsFor,
  });
}

/**
 * Re-run the registry's provider probe on a connection's stored
 * credential. The registry reveals the value only to probe the provider
 * and moves the connection between active and needs_reauth; nothing
 * secret crosses back over this call.
 */
export async function verifySystemConnection(
  orgId: string,
  connectionId: string,
): Promise<{ connectionId: string; status: string; verified: boolean | null; reason?: string }> {
  const ctx = await systemsContext(orgId);
  if (!ctx) throw new Error("This deployment is not linked to the connections registry");
  return registryFetch(
    ctx.config,
    `/organizations/${ctx.registryOrgId}/connections/${connectionId}/verify`,
    { method: "POST", body: {}, actsFor: ctx.actsFor },
  );
}

/**
 * Ensure a managed connection exists for a catalog system's provider,
 * attaching the pasted credential. Declared manifests keep registration
 * offline; the separate credential attach then runs the provider's
 * verification probe. Returns the surfaced verification state.
 */
export async function connectManagedSystem(
  orgId: string,
  input: { provider: string; displayName: string; manifest: DeclaredManifest; secretValue: string },
): Promise<{ connectionId: string; status: string }> {
  const ctx = await systemsContext(orgId);
  if (!ctx) throw new Error("This deployment is not linked to the systems registry");
  const existing = (await listSystemConnections(orgId)) ?? [];
  let connectionId = existing.find((connection) => connection.provider === input.provider)
    ?.connectionId;
  if (!connectionId) {
    const created = await registryFetch<{ connectionId: string }>(
      ctx.config,
      `/organizations/${ctx.registryOrgId}/connections`,
      {
        method: "POST",
        body: {
          kind: "mcp_managed",
          provider: input.provider,
          displayName: input.displayName,
          manifest: input.manifest,
        },
        actsFor: ctx.actsFor,
      },
    );
    connectionId = created.connectionId;
  }
  const attached = await registryFetch<{ status: string }>(
    ctx.config,
    `/organizations/${ctx.registryOrgId}/connections/${connectionId}/secret`,
    { method: "POST", body: { secretValue: input.secretValue }, actsFor: ctx.actsFor },
  );
  return { connectionId, status: attached.status };
}

/**
 * Register a custom system: a remote MCP server the enterprise runs.
 * Registration without a declared manifest makes the registry fetch the
 * tool surface live from the server, so a bad URL or rejected token fails
 * here, not at run time. Returns the tools the server declared.
 */
export async function connectCustomSystem(
  orgId: string,
  input: {
    displayName: string;
    url: string;
    bearerToken?: string;
  },
): Promise<{ connectionId: string; tools: string[] }> {
  const ctx = await systemsContext(orgId);
  if (!ctx) throw new Error("This deployment is not linked to the systems registry");
  const created = await registryFetch<{ connectionId: string; tools: string[] }>(
    ctx.config,
    `/organizations/${ctx.registryOrgId}/connections`,
    {
      method: "POST",
      body: {
        kind: "mcp_custom",
        provider: "mcp_custom",
        displayName: input.displayName,
        config: {
          url: input.url,
        },
        ...(input.bearerToken ? { secretValue: input.bearerToken } : {}),
      },
      actsFor: ctx.actsFor,
    },
  );
  return created;
}

/** Re-attach a credential on an existing connection (any provider). */
export async function attachSystemCredential(
  orgId: string,
  connectionId: string,
  secretValue: string,
): Promise<{ status: string }> {
  const ctx = await systemsContext(orgId);
  if (!ctx) throw new Error("This deployment is not linked to the systems registry");
  return registryFetch<{ status: string }>(
    ctx.config,
    `/organizations/${ctx.registryOrgId}/connections/${connectionId}/secret`,
    { method: "POST", body: { secretValue }, actsFor: ctx.actsFor },
  );
}

/**
 * Which workspace a routine runs in when it carries no recorded binding:
 * the first workspace whose grants cover every system the routine needs
 * (coverage itself is defined once, in lib/workspaces/fit). Routines
 * installed from the catalog record their workspace directly; this
 * fallback serves anything older or unbound, and full precedence over the
 * recorded binding lives in lib/routines/data resolveWorkspace.
 */
export function workspaceForRoutine(
  routine: { systems: string[] },
  workspaces: Workspace[],
): Workspace | null {
  return firstCoveringWorkspace(routine.systems, workspaces);
}

/** The inverse mapping, for "used by M routines" workspace cards. */
export function routinesUsingWorkspace<R extends { systems: string[]; workspaceId?: string | null }>(
  workspace: Workspace,
  workspaces: Workspace[],
  routines: ReadonlyArray<R>,
): R[] {
  return routines.filter((routine) =>
    routine.workspaceId
      ? routine.workspaceId === workspace.id
      : workspaceForRoutine(routine, workspaces)?.id === workspace.id,
  );
}

// ---------------------------------------------------------------------------
// Event triggers (registry-only: rules live in the environments service and
// fire through its gateway webhook doors; the table-backed fallback has no
// event machinery, so these return null when the registry flag is off).

export interface EventRule {
  ruleId: string;
  connectionId: string;
  eventType: string;
  agentId: string;
  enabled: boolean;
  goal: string;
  environmentId: string;
  budgetUsd: string;
  createdAt: string;
}

export interface CreateEventRuleInput {
  connectionId: string;
  eventType: string;
  agentId: string;
  goal: string;
  environmentId: string;
  budgetUsd: string;
  tools: string[];
  instructions: string[];
}

export async function listEventRules(
  orgId: string,
  agentId?: string,
): Promise<EventRule[] | null> {
  const ctx = await systemsContext(orgId);
  if (!ctx) return null;
  const suffix = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return registryFetch<EventRule[]>(
    ctx.config,
    `/organizations/${ctx.registryOrgId}/event-rules${suffix}`,
    { actsFor: ctx.actsFor },
  );
}

export async function createEventRule(
  orgId: string,
  input: CreateEventRuleInput,
): Promise<EventRule> {
  const ctx = await systemsContext(orgId);
  if (!ctx) throw new Error("Event triggers need the environments registry");
  return registryFetch<EventRule>(
    ctx.config,
    `/organizations/${ctx.registryOrgId}/event-rules`,
    { method: "POST", actsFor: ctx.actsFor, body: input },
  );
}

export async function deleteEventRule(orgId: string, ruleId: string): Promise<void> {
  const ctx = await systemsContext(orgId);
  if (!ctx) throw new Error("Event triggers need the environments registry");
  await registryFetch<{ ruleId: string; deleted: boolean }>(
    ctx.config,
    `/organizations/${ctx.registryOrgId}/event-rules/${ruleId}`,
    { method: "DELETE", actsFor: ctx.actsFor },
  );
}

/** Store the provider's webhook signing secret on a connection and return
 * the gateway hook path deliveries should target. */
export async function setConnectionWebhookSecret(
  orgId: string,
  connectionId: string,
  secret: string,
): Promise<{ connectionId: string; hookPath: string }> {
  const ctx = await systemsContext(orgId);
  if (!ctx) throw new Error("Event triggers need the environments registry");
  return registryFetch<{ connectionId: string; hookPath: string }>(
    ctx.config,
    `/organizations/${ctx.registryOrgId}/connections/${connectionId}/webhook-secret`,
    { method: "POST", actsFor: ctx.actsFor, body: { secret } },
  );
}
