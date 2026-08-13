/**
 * Pure workspace-fit logic, shared by the catalog install picker, the
 * routine builder's workspace choices, and run-time launch resolution
 * (lib/routines/launch). One definition of coverage lives here: a workspace
 * covers a routine when every system id the routine needs is among the
 * workspace's granted system ids. That is the same exact-id rule
 * workspaceForRoutine (lib/api/environments) has always applied; scopes
 * and vendor families are the install report's concern (lib/catalog/compat),
 * not coverage's.
 *
 * This module stays pure and import-light so client components and unit
 * tests can use it directly; the structural types below are satisfied by
 * the typed Workspace shape from lib/api/environments.
 */

import { systemCatalog } from "./system-catalog";

/** The slice of a workspace that coverage reads: its granted system ids. */
export interface WorkspaceGrants {
  systems: ReadonlyArray<{ systemId: string; displayName: string }>;
}

/**
 * Plain names for every system id a fit surface may mention: the shared
 * system catalog plus whatever concrete systems the workspaces connect.
 * Unknown ids render as themselves at the call sites.
 */
export function systemDisplayNames(
  workspaces: ReadonlyArray<WorkspaceGrants>,
): Record<string, string> {
  const names: Record<string, string> = {};
  for (const system of systemCatalog) names[system.id] = system.displayName;
  for (const workspace of workspaces) {
    for (const grant of workspace.systems) names[grant.systemId] = grant.displayName;
  }
  return names;
}

/** Required system ids the workspace does not grant, in required order. */
export function missingSystems(
  requiredSystems: readonly string[],
  workspace: WorkspaceGrants,
): string[] {
  const connected = new Set(workspace.systems.map((grant) => grant.systemId));
  return requiredSystems.filter((systemId) => !connected.has(systemId));
}

/** Whether a workspace's grants cover every required system id. */
export function coversSystems(
  requiredSystems: readonly string[],
  workspace: WorkspaceGrants,
): boolean {
  return missingSystems(requiredSystems, workspace).length === 0;
}

/** The auto-match: the first listed workspace that covers every system. */
export function firstCoveringWorkspace<W extends WorkspaceGrants>(
  requiredSystems: readonly string[],
  workspaces: readonly W[],
): W | null {
  return workspaces.find((workspace) => coversSystems(requiredSystems, workspace)) ?? null;
}

export interface WorkspaceSelection<W> {
  /** The workspace runs use, or null when nothing covers the routine. */
  workspace: W | null;
  /**
   * Set when the routine's recorded workspace still exists but no longer
   * covers its systems (the workspace was edited, say): runs fall back to
   * the auto-match above, and surfaces show a plain warning built from
   * the missing system ids.
   */
  staleAssignment: { workspace: W; missingSystems: string[] } | null;
}

/**
 * Which workspace a routine runs in. Precedence: the explicitly assigned
 * workspace wins while it exists and still covers the routine's systems;
 * anything else (no assignment, an assignment the org no longer has, or
 * one that stopped covering) falls back to the first covering workspace.
 */
export function selectWorkspace<W extends WorkspaceGrants & { id: string }>(
  routine: { systems: string[]; workspaceId: string | null },
  workspaces: readonly W[],
): WorkspaceSelection<W> {
  const assigned = routine.workspaceId
    ? workspaces.find((workspace) => workspace.id === routine.workspaceId) ?? null
    : null;
  if (assigned) {
    const missing = missingSystems(routine.systems, assigned);
    if (missing.length === 0) return { workspace: assigned, staleAssignment: null };
    return {
      workspace: firstCoveringWorkspace(routine.systems, workspaces),
      staleAssignment: { workspace: assigned, missingSystems: missing },
    };
  }
  return {
    workspace: firstCoveringWorkspace(routine.systems, workspaces),
    staleAssignment: null,
  };
}
