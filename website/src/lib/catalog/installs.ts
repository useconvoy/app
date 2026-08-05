/**
 * The install shell's landing spot. An install pins the catalog entry's
 * snapshot version into this in-process set and records nothing
 * domain-side; the routine "arrives" when the routines list starts
 * consuming this set alongside the run directory.
 *
 * TODO(environments-E0): installs become registry facts (routine bound
 * into a workspace with a pinned template version) and this store is
 * deleted; the routines list then reads the registry instead of this map.
 */
import "server-only";

export interface InstalledRoutine {
  entryId: string;
  routineId: string;
  workspaceId: string;
  /** Snapshot version pinned at install time; template updates never move it silently. */
  pinnedVersion: number;
  installedAt: string;
}

declare global {
  var __convoyInstalledRoutines: Map<string, InstalledRoutine[]> | undefined;
}

function store(): Map<string, InstalledRoutine[]> {
  if (!globalThis.__convoyInstalledRoutines) {
    globalThis.__convoyInstalledRoutines = new Map();
  }
  return globalThis.__convoyInstalledRoutines;
}

/** Installs recorded for an org, install order preserved. */
export function installedRoutines(orgId: string): InstalledRoutine[] {
  return [...(store().get(orgId) ?? [])];
}

/** Record an install; a reinstall of the same entry re-pins its version. */
export function recordInstall(orgId: string, install: InstalledRoutine): void {
  const existing = (store().get(orgId) ?? []).filter((row) => row.entryId !== install.entryId);
  store().set(orgId, [...existing, install]);
}

/**
 * "Update available" is a pure version comparison: the catalog entry moved
 * past the version pinned at install.
 */
export function updateAvailable(entryVersion: number, install: InstalledRoutine | undefined): boolean {
  return install !== undefined && entryVersion > install.pinnedVersion;
}
