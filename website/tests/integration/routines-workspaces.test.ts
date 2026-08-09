// @vitest-environment node
/**
 * Routines, workspaces, and score history against a real Postgres, through
 * the same production modules the server uses. Requires
 * WEBSITE_PG_ADMIN_DSN (the migration owner) and uses WEBSITE_PG_DSN for
 * the RLS-bound app role; the suite skips cleanly when no database is
 * provided. Seeding of orgs/users and cleanup use the owner connection;
 * everything under test runs under each org's own RLS context.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { environmentsClient } from "@/lib/api/environments";
import { recordScore, scoredRuns, scoreTrend } from "@/lib/api/evals";
import { installedRoutines } from "@/lib/catalog/installs";
import { withOrgContext } from "@/lib/db";
import { newTenantId } from "@/lib/orgs/validation";
import {
  getRoutine,
  listRoutines,
  setRoutineSchedule,
  upsertInstalledRoutine,
} from "@/lib/routines/queries";

const ADMIN_DSN = process.env.WEBSITE_PG_ADMIN_DSN;
const APP_DSN =
  process.env.WEBSITE_PG_DSN ??
  "postgresql://convoy_website_app:convoy_website_app@localhost:5440/convoy_website";
const WEBSITE_ROOT = new URL("../..", import.meta.url).pathname;

const run = randomUUID().slice(0, 8);
const orgA = randomUUID();
const orgB = randomUUID();
const userA = randomUUID();
const userB = randomUUID();

describe.skipIf(!ADMIN_DSN)("routines and workspaces (real Postgres)", () => {
  let admin: pg.Client;
  let entryId: string;
  let workspaceId: string;
  let executionEnvironmentId: string;
  let routineId: string;

  beforeAll(async () => {
    execFileSync(process.execPath, ["scripts/migrate.mjs"], {
      cwd: WEBSITE_ROOT,
      env: { ...process.env, WEBSITE_PG_ADMIN_DSN: ADMIN_DSN as string },
      stdio: "pipe",
    });
    // The app pool (src/lib/db) reads this lazily on first checkout.
    process.env.WEBSITE_PG_DSN = APP_DSN;

    admin = new pg.Client({ connectionString: ADMIN_DSN });
    await admin.connect();
    await admin.query(
      `INSERT INTO organizations (id, tenant_id, name) VALUES
        ($1, $3, 'Routines Org A ${run}'), ($2, $4, 'Routines Org B ${run}')`,
      [orgA, orgB, newTenantId(), newTenantId()],
    );
    await admin.query(
      `INSERT INTO users (id, email, name) VALUES
        ($1, 'ra-${run}@example.com', 'R. A'),
        ($2, 'rb-${run}@example.com', 'R. B')`,
      [userA, userB],
    );
    await admin.query(
      `INSERT INTO memberships (org_id, user_id, role) VALUES
        ($1, $3, 'admin'), ($2, $4, 'admin')`,
      [orgA, orgB, userA, userB],
    );
  }, 30_000);

  afterAll(async () => {
    await globalThis.__convoyWebsitePool?.end();
    globalThis.__convoyWebsitePool = undefined;
    if (!admin) return;
    await admin.query(
      "DELETE FROM routine_eval_scores WHERE org_id = ANY($1)",
      [[orgA, orgB]],
    );
    await admin.query("DELETE FROM routines WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM workspaces WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM catalog_entries WHERE publisher_org_id = ANY($1)", [
      [orgA, orgB],
    ]);
    await admin.query("DELETE FROM admin_audit WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM memberships WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM users WHERE id = ANY($1)", [[userA, userB]]);
    await admin.query("DELETE FROM organizations WHERE id = ANY($1)", [[orgA, orgB]]);
    await admin.end();
  });

  it("a fresh organization lists zero routines and zero workspaces", async () => {
    expect(await listRoutines(orgA)).toEqual([]);
    expect(await environmentsClient().listWorkspaces(orgA)).toEqual([]);
    expect(await installedRoutines({ orgId: orgA, userId: userA })).toEqual([]);
  });

  it("an install writes a real routine bound to its workspace, and reinstalling re-pins", async () => {
    const workspace = await environmentsClient().createWorkspace(orgA, {
      name: `Install workspace ${run}`,
      purpose: "Integration installs run here.",
      systems: [
        { systemId: "document_store", scope: "write", useStandIn: false },
        { systemId: "messaging", scope: "write", useStandIn: true },
      ],
    });
    workspaceId = workspace.id;
    const environment = await environmentsClient().createExecutionEnvironment(orgA, {
      workspaceId,
      name: `Production operations ${run}`,
      purpose: "Runs approved integration routines.",
      sandboxTemplate: "convoy-devbox-python",
      browserPolicy: { allowedDomains: ["app.example.com"], persistProfile: true },
      makeDefault: true,
    });
    executionEnvironmentId = environment.id;
    expect(environment.productionBindingId).toBe(workspace.environmentId);
    expect(environment.rehearsalBindingId).toBe(workspace.rehearsalEnvironmentId);
    expect(environment.isDefault).toBe(true);
    expect(await environmentsClient().listExecutionEnvironments(orgA, workspaceId)).toEqual([
      environment,
    ]);

    // A catalog entry for the routine to point back at, published under
    // org A's own context exactly as the publish action would.
    entryId = await withOrgContext({ orgId: orgA, userId: userA }, async (client) => {
      const { rows } = await client.query<{ id: string }>(
        `INSERT INTO catalog_entries (publisher_org_id, routine_version_ref, storefront)
         VALUES ($1, 'attestation-chase@v1', '{"name": "Policy attestation chase"}')
         RETURNING id`,
        [orgA],
      );
      return rows[0]!.id;
    });

    routineId = await withOrgContext({ orgId: orgA, userId: userA }, async (client) => {
      const { id } = await upsertInstalledRoutine(
        client,
        { orgId: orgA, userId: userA },
        {
          name: "Policy attestation chase",
          descriptor: "Reminds people to confirm they have read the policies.",
          systems: ["messaging", "document_store"],
          budgetCapUsd: 30,
          workspaceId: workspace.id,
          sourceEntryId: entryId,
          sourceVersion: 1,
        },
      );
      return id;
    });

    const listed = await listRoutines(orgA);
    expect(listed).toHaveLength(1);
    expect(listed[0]!.name).toBe("Policy attestation chase");
    expect(listed[0]!.workspaceId).toBe(workspace.id);
    expect(listed[0]!.sourceVersion).toBe(1);

    // Reinstalling the same entry re-pins the one row instead of stacking
    // a duplicate; the partial unique index makes the upsert well-defined.
    await withOrgContext({ orgId: orgA, userId: userA }, (client) =>
      upsertInstalledRoutine(
        client,
        { orgId: orgA, userId: userA },
        {
          name: "Policy attestation chase",
          descriptor: "Reminds people to confirm they have read the policies.",
          systems: ["messaging", "document_store"],
          budgetCapUsd: 30,
          workspaceId: workspace.id,
          sourceEntryId: entryId,
          sourceVersion: 2,
        },
      ),
    );
    const repinned = await listRoutines(orgA);
    expect(repinned).toHaveLength(1);
    expect(repinned[0]!.id).toBe(routineId);
    expect(repinned[0]!.sourceVersion).toBe(2);

    const installs = await installedRoutines({ orgId: orgA, userId: userA });
    expect(installs).toEqual([
      expect.objectContaining({ entryId, routineId, pinnedVersion: 2 }),
    ]);
  });

  it("routines and workspaces are org-isolated under RLS", async () => {
    expect(await listRoutines(orgB)).toEqual([]);
    expect(await environmentsClient().listWorkspaces(orgB)).toEqual([]);
    // Direct probes by id from the wrong org context see nothing.
    expect(await getRoutine(orgB, routineId)).toBeNull();
    expect(await environmentsClient().getWorkspace(orgB, workspaceId)).toBeNull();
    expect(
      await environmentsClient().getExecutionEnvironment(orgB, executionEnvironmentId),
    ).toBeNull();
    const probe = await withOrgContext({ orgId: orgB, userId: userB }, async (client) => {
      const routines = await client.query("SELECT id FROM routines WHERE org_id = $1", [orgA]);
      const workspaces = await client.query("SELECT id FROM workspaces WHERE org_id = $1", [orgA]);
      const environments = await client.query(
        "SELECT id FROM execution_environments WHERE org_id = $1",
        [orgA],
      );
      return {
        routines: routines.rowCount,
        workspaces: workspaces.rowCount,
        environments: environments.rowCount,
      };
    });
    expect(probe).toEqual({ routines: 0, workspaces: 0, environments: 0 });
  });

  it("schedule edits land on the routine row", async () => {
    await setRoutineSchedule(orgA, routineId, "Mondays at 9am");
    const routine = await getRoutine(orgA, routineId);
    expect(routine?.scheduleDescription).toBe("Mondays at 9am");
  });

  it("score inserts append and read back in order, invisible to other orgs", async () => {
    await recordScore(orgA, routineId, `run-${run}-1`, 72);
    await recordScore(orgA, routineId, `run-${run}-2`, 84);
    await recordScore(orgA, routineId, `run-${run}-3`, 91);

    const trend = await scoreTrend(orgA, routineId);
    expect(trend.map((point) => point.score)).toEqual([72, 84, 91]);
    // The trend is chronological; the stamps must never come back shuffled.
    const stamps = trend.map((point) => point.at);
    expect([...stamps].sort()).toEqual(stamps);

    const recent = await scoredRuns(orgA, routineId);
    expect(recent.map((row) => row.runRef)).toEqual([
      `run-${run}-3`,
      `run-${run}-2`,
      `run-${run}-1`,
    ]);

    expect(await scoreTrend(orgB, routineId)).toEqual([]);
    const crossOrg = await withOrgContext({ orgId: orgB, userId: userB }, (client) =>
      client.query("SELECT id FROM routine_eval_scores WHERE routine_id = $1", [routineId]),
    );
    expect(crossOrg.rowCount).toBe(0);
  });
});
