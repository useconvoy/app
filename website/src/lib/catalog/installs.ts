/**
 * Installed-Agent lookups for the storefront. An install enriches an Agent
 * with catalog provenance (source_entry_id plus the snapshot version), so
 * "installed" and "update available" are read from the org's Agents. Every
 * query runs through withOrgContext; org ids arrive from the verified
 * session.
 */
import "server-only";

import { withOrgContext, type DbContext } from "@/lib/db";

export interface InstalledRoutine {
  entryId: string;
  /** Compatibility field containing the enriched Agent id. */
  routineId: string;
  workspaceId: string | null;
  /** Snapshot version pinned at install time; template updates never move it silently. */
  pinnedVersion: number;
  installedAt: string;
}

/** Installs recorded for an org, install order preserved. */
export async function installedRoutines(ctx: DbContext): Promise<InstalledRoutine[]> {
  return withOrgContext(ctx, async (client) => {
    const { rows } = await client.query<{
      entryId: string;
      routineId: string;
      workspaceId: string | null;
      pinnedVersion: number;
      installedAt: Date;
    }>(
      `SELECT source_entry_id AS "entryId", id AS "routineId",
              workspace_id AS "workspaceId", source_version AS "pinnedVersion",
              created_at AS "installedAt"
         FROM agents
        WHERE org_id = $1 AND source_entry_id IS NOT NULL
        ORDER BY created_at, id`,
      [ctx.orgId],
    );
    return rows.map((row) => ({ ...row, installedAt: row.installedAt.toISOString() }));
  });
}

/**
 * "Update available" is a pure version comparison: the catalog entry moved
 * past the version pinned at install.
 */
export function updateAvailable(entryVersion: number, install: InstalledRoutine | undefined): boolean {
  return install !== undefined && entryVersion > install.pinnedVersion;
}
