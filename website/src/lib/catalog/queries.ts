/**
 * Server-only data access for the catalog (DESIGN §6). A catalog entry is
 * the four-part bundle: routine snapshot reference + scenario suite +
 * test-score thresholds + storefront metadata, published by the Convoy
 * workshop org and readable by every org through the 0001 catalog RLS
 * policy (own entries plus convoy-visible ones). Every query runs through
 * withOrgContext; org ids arrive from the verified session.
 */
import "server-only";

import type { PoolClient } from "pg";

import { withOrgContext, type DbContext } from "@/lib/db";
import {
  parseVersionRef,
  versionRef,
  type CapabilityRequirements,
} from "./compat";

export interface Storefront {
  name: string;
  tagline: string;
  description: string;
}

/** Stored as eval_thresholds jsonb; rendered only as a "test score floor". */
export interface ScoreThresholds {
  minScore: number;
}

export interface ChangelogItem {
  version: number;
  note: string;
  at: string;
}

export interface CatalogEntry {
  id: string;
  publisherOrgId: string;
  routineId: string;
  version: number;
  storefront: Storefront;
  requirements: CapabilityRequirements;
  thresholds: ScoreThresholds;
  scenarioSuiteRef: string | null;
  visibility: string;
  changelog: ChangelogItem[];
  createdAt: Date;
}

interface EntryRow {
  id: string;
  publisherOrgId: string;
  routineVersionRef: string;
  capabilityRequirements: unknown;
  evalThresholds: unknown;
  scenarioSuiteRef: string | null;
  visibility: string;
  storefront: unknown;
  changelog: unknown;
  createdAt: Date;
}

const ENTRY_COLUMNS = `id, publisher_org_id AS "publisherOrgId",
       routine_version_ref AS "routineVersionRef",
       capability_requirements AS "capabilityRequirements",
       eval_thresholds AS "evalThresholds",
       scenario_suite_ref AS "scenarioSuiteRef",
       visibility, storefront, changelog, created_at AS "createdAt"`;

/** Hydrate stored jsonb into the typed shapes, tolerating older rows. */
function toEntry(row: EntryRow): CatalogEntry {
  const { routineId, version } = parseVersionRef(row.routineVersionRef);
  const storefront = (row.storefront ?? {}) as Partial<Storefront>;
  const requirements = (row.capabilityRequirements ?? {}) as Partial<CapabilityRequirements>;
  const thresholds = (row.evalThresholds ?? {}) as Partial<ScoreThresholds>;
  return {
    id: row.id,
    publisherOrgId: row.publisherOrgId,
    routineId,
    version,
    storefront: {
      name: storefront.name ?? routineId,
      tagline: storefront.tagline ?? "",
      description: storefront.description ?? "",
    },
    requirements: {
      systems: Array.isArray(requirements.systems) ? requirements.systems : [],
      vendorSpecificTools: Array.isArray(requirements.vendorSpecificTools)
        ? requirements.vendorSpecificTools
        : [],
    },
    thresholds: { minScore: typeof thresholds.minScore === "number" ? thresholds.minScore : 0 },
    scenarioSuiteRef: row.scenarioSuiteRef,
    visibility: row.visibility,
    changelog: Array.isArray(row.changelog) ? (row.changelog as ChangelogItem[]) : [],
    createdAt: row.createdAt,
  };
}

/** Storefront list: own entries plus convoy-visible ones (RLS decides). */
export async function listCatalog(ctx: DbContext): Promise<CatalogEntry[]> {
  return withOrgContext(ctx, async (client) => {
    const { rows } = await client.query<EntryRow>(
      `SELECT ${ENTRY_COLUMNS} FROM catalog_entries ORDER BY created_at DESC`,
    );
    return rows.map(toEntry);
  });
}

/** One entry, or null when RLS hides it or it does not exist. */
export async function getEntry(ctx: DbContext, entryId: string): Promise<CatalogEntry | null> {
  return withOrgContext(ctx, async (client) => {
    const { rows } = await client.query<EntryRow>(
      `SELECT ${ENTRY_COLUMNS} FROM catalog_entries WHERE id = $1`,
      [entryId],
    );
    return rows[0] ? toEntry(rows[0]) : null;
  });
}

export interface PublishInput {
  routineId: string;
  version: number;
  storefront: Storefront;
  capabilityRequirements: CapabilityRequirements;
  evalThresholds: ScoreThresholds;
  /** Changelog note for this publish; defaults to a plain first-publish line. */
  note?: string;
  scenarioSuiteRef?: string;
}

async function auditRow(
  client: PoolClient,
  orgId: string,
  actorId: string,
  action: string,
  subject: string,
): Promise<void> {
  await client.query(
    "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
    [orgId, actorId, action, subject],
  );
}

/**
 * Publish (or republish) a routine to the catalog as a snapshot version.
 * One row per routine per publisher: a republish bumps the snapshot ref in
 * place and appends to the changelog jsonb, so installers see the full
 * version history behind one storefront entry. Visibility is always
 * "convoy" in phase 1 (DESIGN §6: Convoy-published only). Writes the
 * admin_audit row in the same transaction as the entry.
 *
 * Called by the publish server action after its permission gate; also
 * exercised directly by the integration suite.
 */
export async function publishEntryCore(
  ctx: Required<DbContext>,
  input: PublishInput,
): Promise<{ id: string; version: number }> {
  const ref = versionRef(input.routineId, input.version);
  const changelogItem: ChangelogItem = {
    version: input.version,
    note: input.note?.trim() || "Published",
    at: new Date().toISOString(),
  };
  return withOrgContext(ctx, async (client) => {
    const { rows: existing } = await client.query<{ id: string; routineVersionRef: string }>(
      `SELECT id, routine_version_ref AS "routineVersionRef" FROM catalog_entries
        WHERE publisher_org_id = $1 AND split_part(routine_version_ref, '@', 1) = $2`,
      [ctx.orgId, input.routineId],
    );
    let id: string;
    if (existing[0]) {
      const current = parseVersionRef(existing[0].routineVersionRef).version;
      if (input.version <= current) {
        throw new Error(`Version must be above v${current}`);
      }
      id = existing[0].id;
      await client.query(
        `UPDATE catalog_entries
            SET routine_version_ref = $2,
                capability_requirements = $3,
                eval_thresholds = $4,
                storefront = $5,
                scenario_suite_ref = COALESCE($6, scenario_suite_ref),
                changelog = changelog || $7::jsonb
          WHERE id = $1`,
        [
          id,
          ref,
          JSON.stringify(input.capabilityRequirements),
          JSON.stringify(input.evalThresholds),
          JSON.stringify(input.storefront),
          input.scenarioSuiteRef ?? null,
          JSON.stringify([changelogItem]),
        ],
      );
    } else {
      const { rows } = await client.query<{ id: string }>(
        `INSERT INTO catalog_entries
           (publisher_org_id, routine_version_ref, capability_requirements,
            eval_thresholds, scenario_suite_ref, visibility, storefront, changelog)
         VALUES ($1, $2, $3, $4, $5, 'convoy', $6, $7)
         RETURNING id`,
        [
          ctx.orgId,
          ref,
          JSON.stringify(input.capabilityRequirements),
          JSON.stringify(input.evalThresholds),
          input.scenarioSuiteRef ?? null,
          JSON.stringify(input.storefront),
          JSON.stringify([changelogItem]),
        ],
      );
      id = rows[0]!.id;
    }
    await auditRow(client, ctx.orgId, ctx.userId, "catalog.entry_published", ref);
    return { id, version: input.version };
  });
}
