/**
 * E2E global setup: apply website migrations to the local Postgres, then
 * sign in through the local dev provider and create an org through the
 * real onboarding page, saving storageState for the specs. Playwright
 * starts the webServer before global setup runs, so the app on :3100 is
 * already reachable here.
 */
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium, type FullConfig } from "@playwright/test";
import pg from "pg";

const ROOT = join(__dirname, "..", "..");

/**
 * Give the fresh org one shared workspace and one configured Agent so the specs have
 * something real to run: the same rows the catalog install flow writes,
 * bound to the local stub registry so on-demand runs actually execute.
 */
async function seedAgent(adminDsn: string, orgName: string): Promise<void> {
  const client = new pg.Client({ connectionString: adminDsn });
  await client.connect();
  try {
    const { rows } = await client.query<{ id: string }>(
      "SELECT id FROM organizations WHERE name = $1",
      [orgName],
    );
    const orgId = rows[0]?.id;
    if (!orgId) throw new Error(`org not found for seeding: ${orgName}`);
    const systems = JSON.stringify([
      {
        systemId: "identity_provider",
        displayName: "Identity provider",
        scope: "read",
        sideEffecting: false,
      },
      { systemId: "hris", displayName: "HR system", scope: "read", sideEffecting: false },
      {
        systemId: "document_store",
        displayName: "Google Drive",
        scope: "write",
        sideEffecting: false,
      },
      {
        systemId: "messaging",
        displayName: "Slack",
        scope: "write",
        sideEffecting: true,
        standIn: {
          note: "Stand-in for Slack: messages are held in the outbox instead of being sent.",
        },
      },
    ]);
    const versions = JSON.stringify([
      { version: 1, note: "Created", createdAt: new Date().toISOString() },
    ]);
    const workspace = await client.query<{ id: string }>(
      `INSERT INTO workspaces
         (org_id, name, purpose, environment_id, rehearsal_environment_id, systems, clock_mode, versions)
       VALUES ($1, 'Compliance workspace', 'Access reviews run here.',
               'prod-local', 'stub-local', $2, 'wall', $3)
       RETURNING id`,
      [orgId, systems, versions],
    );
    await client.query(
      `INSERT INTO agents
         (org_id, workspace_id, name, purpose, production_binding_id,
          rehearsal_binding_id, sandbox_template, goal, systems,
          budget_cap_usd, plan_steps, automation_configured)
       VALUES ($1, $2, 'Quarterly user access review',
               'Looks up people and their access, reconciles differences, and chases sign-offs.',
               'prod-local', 'stub-local', 'convoy-devbox-python',
               'Reconcile access differences and collect sign-offs.', $3, 75, $4, true)`,
      [
        orgId,
        workspace.rows[0]!.id,
        ["identity_provider", "hris", "document_store", "messaging"],
        JSON.stringify([
          "Pull the current list of people and their access",
          "Compare against the HR system and note differences",
          "Write an exception memo for each difference",
          "Chase anyone who has not responded",
          "Assemble the final review packet",
        ]),
      ],
    );
  } finally {
    await client.end();
  }
}

export default async function globalSetup(config: FullConfig): Promise<void> {
  const adminDsn =
    process.env.WEBSITE_PG_ADMIN_DSN ??
    "postgresql://convoy_admin:convoy_admin@localhost:5433/convoy_website";
  execFileSync(process.execPath, [join(ROOT, "scripts", "migrate.mjs")], {
    cwd: ROOT,
    env: { ...process.env, WEBSITE_PG_ADMIN_DSN: adminDsn },
    stdio: "inherit",
  });

  const baseURL = config.projects[0]?.use?.baseURL ?? "http://localhost:3100";
  const stamp = Date.now();
  const email = `w1-e2e-${stamp}@example.com`;
  const orgName = `E2E Org ${stamp}`;

  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ baseURL });
    await page.goto("/sign-in");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Name").fill("J. Doe");
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL("**/onboarding", { timeout: 30_000 });
    await page.getByLabel("Organization name").fill(orgName);
    await page.getByRole("button", { name: "Create organization" }).click();
    await page.waitForURL("**/app", { timeout: 30_000 });

    mkdirSync(join(ROOT, "test-results"), { recursive: true });
    await page.context().storageState({ path: join(ROOT, "test-results", "auth.json") });
    // Specs that talk to the control plane directly need this org's tenant;
    // the name is the lookup key into the website database.
    writeFileSync(
      join(ROOT, "test-results", "e2e-org.json"),
      JSON.stringify({ email, orgName }),
    );
  } finally {
    await browser.close();
  }

  await seedAgent(adminDsn, orgName);
}
