// @vitest-environment node
/**
 * Governance against a real Postgres: catalog publishes and org policy
 * updates land admin_audit rows with the acting human attributed, and the
 * catalog RLS policy shows convoy-visible entries cross-org while hiding
 * private ones. Seeding follows the db-rls suite: orgs/users/memberships
 * ride the owner connection; everything else goes through the same
 * withOrgContext helpers production code uses. Skips cleanly without a
 * database.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { withOrgContext } from "@/lib/db";
import { getEntry, listCatalog, publishEntryCore } from "@/lib/catalog/queries";
import { listAuditEntries } from "@/lib/orgs/queries";
import { newTenantId } from "@/lib/orgs/validation";
import { getOrgSettings, savePoliciesCore } from "@/lib/orgs/settings";

const ADMIN_DSN = process.env.WEBSITE_PG_ADMIN_DSN;
const APP_DSN =
  process.env.WEBSITE_PG_DSN ??
  "postgresql://convoy_website_app:convoy_website_app@localhost:5440/convoy_website";
const WEBSITE_ROOT = new URL("../..", import.meta.url).pathname;

const run = randomUUID().slice(0, 8);
const workshopOrg = randomUUID();
const customerOrg = randomUUID();
const publisher = randomUUID();
const customerAdmin = randomUUID();

const ROUTINE_ID = `routine-audit-${run}`;

const publishInput = {
  routineId: ROUTINE_ID,
  version: 1,
  storefront: {
    name: "Quarterly user access review",
    tagline: "Reconciles access and chases sign-offs.",
    description: "Looks up people and their access, reconciles differences, and chases sign-offs.",
  },
  capabilityRequirements: {
    systems: [
      { systemId: "identity_provider", scope: "read" as const },
      { systemId: "messaging", scope: "write" as const },
    ],
    vendorSpecificTools: [],
  },
  evalThresholds: { minScore: 85 },
};

describe.skipIf(!ADMIN_DSN)("catalog + admin close-out audit trail (real Postgres)", () => {
  let admin: pg.Client;
  let entryId: string;

  beforeAll(async () => {
    execFileSync(process.execPath, ["scripts/migrate.mjs"], {
      cwd: WEBSITE_ROOT,
      env: { ...process.env, WEBSITE_PG_ADMIN_DSN: ADMIN_DSN as string },
      stdio: "pipe",
    });
    process.env.WEBSITE_PG_DSN = APP_DSN;

    admin = new pg.Client({ connectionString: ADMIN_DSN });
    await admin.connect();
    await admin.query(
      `INSERT INTO organizations (id, tenant_id, name) VALUES
        ($1, $3, 'Workshop ${run}'), ($2, $4, 'Customer ${run}')`,
      [workshopOrg, customerOrg, newTenantId(), newTenantId()],
    );
    await admin.query(
      `INSERT INTO users (id, email, name) VALUES
        ($1, 'publisher-${run}@example.com', 'P. Ublisher'),
        ($2, 'customer-${run}@example.com', 'C. Ustomer')`,
      [publisher, customerAdmin],
    );
    await admin.query(
      `INSERT INTO memberships (org_id, user_id, role) VALUES
        ($1, $3, 'operator'), ($2, $4, 'admin')`,
      [workshopOrg, customerOrg, publisher, customerAdmin],
    );
  }, 30_000);

  afterAll(async () => {
    await globalThis.__convoyWebsitePool?.end();
    globalThis.__convoyWebsitePool = undefined;
    if (!admin) return;
    await admin.query("DELETE FROM admin_audit WHERE org_id = ANY($1)", [[workshopOrg, customerOrg]]);
    await admin.query("DELETE FROM catalog_entries WHERE publisher_org_id = ANY($1)", [
      [workshopOrg, customerOrg],
    ]);
    await admin.query("DELETE FROM memberships WHERE org_id = ANY($1)", [[workshopOrg, customerOrg]]);
    await admin.query("DELETE FROM users WHERE id = ANY($1)", [[publisher, customerAdmin]]);
    await admin.query("DELETE FROM organizations WHERE id = ANY($1)", [[workshopOrg, customerOrg]]);
    await admin.end();
  });

  it("publishing writes the entry and an attributed admin_audit row", async () => {
    const result = await publishEntryCore({ orgId: workshopOrg, userId: publisher }, publishInput);
    entryId = result.id;
    expect(result.version).toBe(1);

    const { rows } = await admin.query(
      "SELECT actor_id, subject FROM admin_audit WHERE org_id = $1 AND action = 'catalog.entry_published'",
      [workshopOrg],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]).toEqual({ actor_id: publisher, subject: `${ROUTINE_ID}@v1` });
  });

  it("a republish bumps the snapshot in place and appends to the changelog", async () => {
    await publishEntryCore(
      { orgId: workshopOrg, userId: publisher },
      { ...publishInput, version: 2, note: "Chases harder" },
    );
    const entry = await getEntry({ orgId: workshopOrg, userId: publisher }, entryId);
    expect(entry?.version).toBe(2);
    expect(entry?.changelog.map((item) => item.version)).toEqual([1, 2]);
    expect(entry?.changelog[1]?.note).toBe("Chases harder");

    // Same storefront row, not a second one.
    const { rowCount } = await admin.query(
      "SELECT 1 FROM catalog_entries WHERE publisher_org_id = $1",
      [workshopOrg],
    );
    expect(rowCount).toBe(1);
    // Stale versions are refused.
    await expect(
      publishEntryCore({ orgId: workshopOrg, userId: publisher }, { ...publishInput, version: 2 }),
    ).rejects.toThrow("Version must be above v2");
  });

  it("convoy-visible entries are readable cross-org; private ones are not", async () => {
    // A private entry in the workshop org, inserted under its own context.
    const privateId = await withOrgContext(
      { orgId: workshopOrg, userId: publisher },
      async (client) => {
        const { rows } = await client.query<{ id: string }>(
          `INSERT INTO catalog_entries
             (publisher_org_id, routine_version_ref, visibility, storefront)
           VALUES ($1, $2, 'private', '{"name":"Internal only"}')
           RETURNING id`,
          [workshopOrg, `routine-private-${run}@v1`],
        );
        return rows[0]!.id;
      },
    );

    const seenByCustomer = await listCatalog({ orgId: customerOrg, userId: customerAdmin });
    const ids = seenByCustomer.map((entry) => entry.id);
    expect(ids).toContain(entryId);
    expect(ids).not.toContain(privateId);
    expect(await getEntry({ orgId: customerOrg, userId: customerAdmin }, privateId)).toBeNull();

    // The publisher sees both of its own entries.
    const seenByPublisher = await listCatalog({ orgId: workshopOrg, userId: publisher });
    expect(seenByPublisher.map((entry) => entry.id).sort()).toEqual([entryId, privateId].sort());
  });

  it("a policy update persists settings and writes an attributed audit row", async () => {
    await savePoliciesCore(
      { orgId: customerOrg, userId: customerAdmin },
      { defaultRunBudgetCapUsd: 40, monthlySpendNoticeUsd: 400, viewerEvidenceExport: false },
    );
    const settings = await getOrgSettings(customerOrg);
    expect(settings.policies).toEqual({
      defaultRunBudgetCapUsd: 40,
      monthlySpendNoticeUsd: 400,
      viewerEvidenceExport: false,
    });

    const { rows } = await admin.query(
      "SELECT actor_id FROM admin_audit WHERE org_id = $1 AND action = 'org.policies_updated'",
      [customerOrg],
    );
    expect(rows).toEqual([{ actor_id: customerAdmin }]);
  });

  it("the audit surface resolves actor names and filters by action prefix", async () => {
    const page = await listAuditEntries(workshopOrg, { actionPrefix: "catalog." });
    expect(page.entries.length).toBe(2);
    expect(page.entries.every((entry) => entry.action === "catalog.entry_published")).toBe(true);
    expect(page.entries[0]?.actorName).toBe("P. Ublisher");
    expect(page.hasMore).toBe(false);

    // Audit rows never leak cross-org: the customer org sees only its own.
    const customerPage = await listAuditEntries(customerOrg, {});
    expect(customerPage.entries.map((entry) => entry.action)).toEqual(["org.policies_updated"]);
  });
});
