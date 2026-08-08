/**
 * The typed client interface for the environments service (registry of
 * workspaces, their system grants, and their rehearsal bindings). The
 * service is not built yet, so the implementation reads and writes the
 * org-scoped workspaces table through withOrgContext; every organization
 * sees only the workspaces it created, and a fresh organization has none.
 *
 * TODO(environments-E0): replace the table-backed adapter with real calls
 * to the environments directory behind the single authenticated edge; the
 * table then becomes a cache of the registry or retires entirely.
 */
import "server-only";

import { withOrgContext } from "@/lib/db";
import { grantsFromChoices } from "@/lib/workspaces/system-catalog";

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

export interface CreateWorkspaceInput {
  name: string;
  purpose: string;
  systems: Array<{ systemId: string; scope: "read" | "write"; useStandIn: boolean }>;
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
    const systems = grantsFromChoices(input.systems);
    const now = new Date().toISOString();
    const versions: WorkspaceVersion[] = [{ version: 1, note: "Created", createdAt: now }];
    return withOrgContext({ orgId }, async (client) => {
      const { rows } = await client.query<WorkspaceRow>(
        // Created workspaces bind to the local stub registry so their runs
        // execute for real. TODO(environments-E0): real bindings.
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

/** The one environments client. TODO(environments-E0): real adapter. */
export function environmentsClient(): EnvironmentsClient {
  return new DbEnvironmentsClient();
}

/**
 * Which workspace a routine runs in when it carries no recorded binding:
 * the first workspace whose grants cover every system the routine needs.
 * Routines installed from the catalog record their workspace directly;
 * this fallback serves anything older or unbound.
 */
export function workspaceForRoutine(
  routine: { systems: string[] },
  workspaces: Workspace[],
): Workspace | null {
  return (
    workspaces.find((workspace) => {
      const connected = new Set(workspace.systems.map((grant) => grant.systemId));
      return routine.systems.every((systemId) => connected.has(systemId));
    }) ?? null
  );
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
