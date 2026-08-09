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
  systems: Array<{ systemId: string; scope: "read" | "write"; useStandIn: boolean }>;
  /** Definitions for any "custom:" ids in `systems`; supplied server-side. */
  customSystems?: CustomWorkspaceSystem[];
}

export interface EnvironmentsClient {
  listWorkspaces(orgId: string): Promise<Workspace[]>;
  getWorkspace(orgId: string, workspaceId: string): Promise<Workspace | null>;
  createWorkspace(orgId: string, input: CreateWorkspaceInput): Promise<Workspace>;
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
}

// ---------------------------------------------------------------------------
// The real registry (environments-E0), behind the two-env-var flag.

interface RegistryConfig {
  baseUrl: string;
  token: string;
}

// Every workspace gets a rehearsal copy that can actually run code: a
// non-empty sandbox_template is what makes the registry grant `sandbox_exec`
// and compile a runnable rehearsal binding (an empty template yields a hollow
// rehearsal that can read connectors but never execute the routine's own
// steps). This default is a placeholder image ref until the
// environments-as-content pipeline lets an org pick per-workspace images.
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
  options: { method?: "GET" | "POST"; body?: unknown; actsFor?: string } = {},
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

    // Granted systems with a registry provider become connections; ensure
    // one connection per provider exists in the organization first. New
    // connections are created secretless with the catalog's declared
    // manifest: the tool surface is pinned now, and REAL CREDENTIALS ARE
    // ATTACHED LATER through the connections surface (which re-verifies
    // the manifest against the live system).
    const existing = await registryFetch<RegistryConnectionRow[]>(
      this.config,
      `/organizations/${ctx.registryOrgId}/connections`,
      { actsFor: ctx.actsFor },
    );
    const connections: Array<{ connectionId: string; toolAllowlist: string[] }> = [];
    for (const plan of connectionPlanForGrants(systems)) {
      let connectionId = existing.find((row) => row.provider === plan.provider)?.connectionId;
      if (!connectionId) {
        const created = await registryFetch<{ connectionId: string }>(
          this.config,
          `/organizations/${ctx.registryOrgId}/connections`,
          {
            method: "POST",
            body: {
              kind: "mcp_managed",
              provider: plan.provider,
              displayName: plan.displayName,
              manifest: plan.manifest,
            },
            actsFor: ctx.actsFor,
          },
        );
        connectionId = created.connectionId;
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
      const tools = choice.scope === "write"
        ? definition.tools
        : definition.tools.filter((tool) => !tool.sideEffecting);
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
  const registryOrgId = await getEnvironmentsOrgId(orgId);
  if (!registryOrgId) return null;
  const session = await requireSession();
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
  input: { displayName: string; url: string; bearerToken?: string },
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
        config: { url: input.url },
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
