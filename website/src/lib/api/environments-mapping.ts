/**
 * Pure mapping between the website's workspace vocabulary and the
 * environments registry's console API (TODO(environments-E0) counterpart:
 * the registry serves organizations, connections, and workspaces; the
 * website's vocabulary wins on this side of the seam). No I/O here: the
 * adapter in lib/api/environments calls these, and the unit suite
 * exercises them directly.
 */

import type { SystemGrant, Workspace, WorkspaceVersion } from "@/lib/api/environments";
import type { Role } from "@/lib/permissions";
import { catalogSystem, type SystemConnectionSpec } from "@/lib/workspaces/system-catalog";

/** A workspace row as the registry lists it. */
export interface RegistryWorkspaceRow {
  workspaceId: string;
  version: number;
  name: string;
  purpose: string;
  policyHash: string;
  environmentId: string;
  rehearsalEnvironmentId: string;
  clockMode: string;
  rehearsalClockMode: string;
}

/** A connection row as the registry lists it. */
export interface RegistryConnectionRow {
  connectionId: string;
  kind: string;
  provider: string;
  displayName: string;
  status: string;
  manifestHash: string;
}

export type EnvironmentsRole = "admin" | "builder" | "member";

/**
 * Website role -> registry role. This is the one place the mapping lives:
 * admins own both sides (admin -> admin); operators are the editor tier
 * here (they manage workspaces and promote), which is the registry's
 * builder; members and viewers both land on member, the registry's floor,
 * because it has no read-only tier below it.
 */
export function environmentsRole(role: Role): EnvironmentsRole {
  if (role === "admin") return "admin";
  if (role === "operator") return "builder";
  return "member";
}

/** The declared manifest a secretless managed connection is created with. */
export interface DeclaredManifest {
  tools: Array<{ name: string; execution: "inline" | "promoted"; sideEffecting: boolean }>;
}

export function declaredManifest(spec: SystemConnectionSpec): DeclaredManifest {
  return {
    tools: spec.tools.map((tool) => ({
      name: tool.name,
      execution: tool.execution,
      sideEffecting: tool.sideEffecting,
    })),
  };
}

/**
 * Scope -> tool allowlist. "read" grants only the inline read tools;
 * "write" grants the system's whole declared surface.
 */
export function toolAllowlistFor(spec: SystemConnectionSpec, scope: "read" | "write"): string[] {
  const tools =
    scope === "read"
      ? spec.tools.filter((tool) => tool.execution === "inline" && !tool.sideEffecting)
      : spec.tools;
  return tools.map((tool) => tool.name);
}

/**
 * One granted system's registry materialization: the managed connection to
 * ensure (by provider) and the allowlist its grant scope translates to.
 */
export interface ConnectionPlan {
  systemId: string;
  provider: string;
  displayName: string;
  manifest: DeclaredManifest;
  toolAllowlist: string[];
}

/**
 * Which grants become registry connections. Only systems the registry has
 * a provider for map across; the rest (identity provider, HR, CRM today)
 * stay website-side grants until their providers exist, exactly as they
 * behave with the adapter off.
 */
export function connectionPlanForGrants(grants: SystemGrant[]): ConnectionPlan[] {
  const plans: ConnectionPlan[] = [];
  for (const grant of grants) {
    const spec = catalogSystem(grant.systemId)?.connection;
    if (!spec) continue;
    plans.push({
      systemId: grant.systemId,
      provider: spec.provider,
      displayName: grant.displayName,
      manifest: declaredManifest(spec),
      toolAllowlist: toolAllowlistFor(spec, grant.scope),
    });
  }
  return plans;
}

/** The registry speaks free text here; the website renders exactly two modes. */
export function normalizeClockMode(value: string | null | undefined): "wall" | "virtual" {
  return value === "virtual" ? "virtual" : "wall";
}

/**
 * Re-derive the catalog-owned facts on cached grants: displayName and
 * sideEffecting always flow from the system catalog, never from whatever
 * an older cached row froze in. Unknown systems pass through untouched.
 */
export function withCatalogFacts(grants: SystemGrant[]): SystemGrant[] {
  return grants.map((grant) => {
    const system = catalogSystem(grant.systemId);
    if (!system) return grant;
    return { ...grant, displayName: system.displayName, sideEffecting: system.sideEffecting };
  });
}

/** The website-cached half of a registry-linked workspace. */
export interface WorkspaceCacheFacts {
  id: string;
  systems: SystemGrant[];
  versions: WorkspaceVersion[];
  createdAt: string;
}

/**
 * Merge one registry row with its website cache row into the website's
 * Workspace shape. Name, purpose, both environment bindings, and the clock
 * mode come straight from the registry row; the id (the website's own
 * uuid, which routes and routine bindings reference), the system grants,
 * and the version notes come from the cache.
 */
export function workspaceFromRegistryRow(
  row: RegistryWorkspaceRow,
  cache: WorkspaceCacheFacts,
): Workspace {
  return {
    id: cache.id,
    name: row.name,
    purpose: row.purpose,
    environmentId: row.environmentId,
    rehearsalEnvironmentId: row.rehearsalEnvironmentId,
    systems: withCatalogFacts(cache.systems),
    clockMode: normalizeClockMode(row.clockMode),
    versions: cache.versions,
    createdAt: cache.createdAt,
  };
}
