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

const ROOT = join(__dirname, "..", "..");

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
}
