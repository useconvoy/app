/**
 * Screenshot pass over the console and marketing surfaces, driven against
 * the real control plane like the rest of the end-to-end suite. Each shot
 * captures a surface in a meaningful state rather than an empty shell: a
 * run holding for plan approval, a rehearsal holding at a question, the
 * inbox with work in it. Images land in test-results/screenshots.
 */
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";
import pg from "pg";

const ROOT = join(__dirname, "..", "..");
const SHOTS = join(ROOT, "test-results", "screenshots");
const CONTROL_PLANE = process.env.CONVOY_CONTROL_PLANE_URL ?? "http://localhost:8700";
const TOKEN = process.env.CONVOY_CONTROL_PLANE_TOKEN ?? "e2e-dev-token";
const ADMIN_DSN =
  process.env.WEBSITE_PG_ADMIN_DSN ??
  "postgresql://convoy_admin:convoy_admin@localhost:5433/convoy_website";

test.use({ storageState: join(ROOT, "test-results", "auth.json") });
test.describe.configure({ mode: "serial" });

function orgFacts(): { email: string; orgName: string } {
  return JSON.parse(readFileSync(join(ROOT, "test-results", "e2e-org.json"), "utf8"));
}

/** The tenant behind the signed-in org, so seeded runs land in its console. */
async function tenantId(): Promise<string> {
  const client = new pg.Client({ connectionString: ADMIN_DSN });
  await client.connect();
  const { rows } = await client.query<{ tenant_id: string }>(
    "SELECT tenant_id FROM organizations WHERE name = $1",
    [orgFacts().orgName],
  );
  await client.end();
  expect(rows[0]?.tenant_id).toBeTruthy();
  return rows[0]!.tenant_id;
}

function headers(tenant: string): Record<string, string> {
  return {
    Authorization: `Bearer ${TOKEN}`,
    "X-Actor-Id": orgFacts().email,
    "X-Tenant-Id": tenant,
    "Content-Type": "application/json",
  };
}

async function createRun(tenant: string, body: Record<string, unknown>): Promise<string> {
  const created = await fetch(`${CONTROL_PLANE}/runs`, {
    method: "POST",
    headers: headers(tenant),
    body: JSON.stringify(body),
  });
  expect(created.status).toBeLessThan(300);
  return ((await created.json()) as { run_id: string }).run_id;
}

async function waitForStatus(tenant: string, runId: string, status: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const view = await fetch(`${CONTROL_PLANE}/runs/${runId}`, { headers: headers(tenant) });
        return view.ok ? ((await view.json()) as { status: string }).status : "unreachable";
      },
      { timeout: 60_000 },
    )
    .toBe(status);
}

async function shoot(page: Page, name: string): Promise<void> {
  mkdirSync(SHOTS, { recursive: true });
  // Motion is token-driven; settle it so shots are deterministic.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true });
}

test("marketing home", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await shoot(page, "01-marketing-home");
});

test("sign-in frame", async ({ browser, baseURL }) => {
  const clean = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const anon = await clean.newPage();
  await anon.setViewportSize({ width: 1440, height: 900 });
  await anon.goto(`${baseURL}/sign-in`);
  await expect(anon.getByRole("heading", { level: 1 })).toBeVisible();
  await shoot(anon, "02-sign-in");
  await clean.close();
});

test("console surfaces with live work", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const tenant = await tenantId();

  const awaitingApproval = await createRun(tenant, {
    goal: "Refresh vendor documents before the quarterly review",
    environment_id: "stub-local",
    budget_usd: "40",
    policy: { require_plan_approval: true },
  });
  const heldForAnswer = await createRun(tenant, {
    goal: "Chase outstanding attestations across the team",
    environment_id: "stub-local-virtual",
    budget_usd: "30",
    fixture_gates: {
      "step-2": {
        kind: "approval",
        prompt: "Two people have not replied. Should the reminder go out again?",
        on_timeout: "pause",
      },
    },
  });
  const landing = await createRun(tenant, {
    goal: "Compare the access lists and note any differences",
    environment_id: "stub-local",
    budget_usd: "75",
  });

  await waitForStatus(tenant, heldForAnswer, "blocked_on_human");
  await page.goto(`/app/runs/${heldForAnswer}`);
  await expect(page.getByRole("button", { name: /respond/i }).first()).toBeVisible({
    timeout: 30_000,
  });
  // The rehearsal treatment branches on the sandbox flag carried by events,
  // so wait for the stream to hydrate before capturing the surface.
  await expect(page.getByText(/rehearsal/i).first()).toBeVisible({ timeout: 30_000 });
  await shoot(page, "05-run-detail-rehearsal-held");

  await waitForStatus(tenant, awaitingApproval, "awaiting_approval");
  await page.goto(`/app/runs/${awaitingApproval}`);
  await expect(page.getByRole("button", { name: /approve/i }).first()).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText(/plan drafted/i).first()).toBeVisible({ timeout: 30_000 });
  await shoot(page, "06-run-detail-plan-approval");

  // Runs seeded straight at the control plane join the console's directory
  // the first time they are opened, so visit this one before listing.
  await page.goto(`/app/runs/${landing}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });

  await page.goto("/app/checkpoints");
  await expect(
    page.getByRole("button", { name: /approve plan|respond|resume/i }).first(),
  ).toBeVisible({ timeout: 30_000 });
  await shoot(page, "04-checkpoints-inbox");

  await page.goto("/app/runs");
  await expect(page.getByText(/access lists/i).first()).toBeVisible({ timeout: 30_000 });
  await shoot(page, "03-runs-list");

  await page.goto("/app");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await shoot(page, "07-overview");

  await waitForStatus(tenant, landing, "completed");
  await page.goto(`/app/runs/${landing}`);
  await expect(page.getByText(/landed/i).first()).toBeVisible({ timeout: 30_000 });
  await shoot(page, "08-run-detail-landed");
});

test("build and improve surfaces", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });

  // The routine detail's address is per-org now, so reach it through the
  // list instead of a hardcoded id.
  await page.goto("/app/routines");
  await page.getByRole("link", { name: "Quarterly user access review" }).first().click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
  await shoot(page, "10-routine-detail");

  for (const [name, path] of [
    ["09-routines", "/app/routines"],
    ["11-workspaces", "/app/workspaces"],
    ["12-evaluation", "/app/evaluation"],
    ["13-learning", "/app/learning"],
    ["14-logs", "/app/logs"],
    ["15-admin-members", "/app/admin/members"],
    ["16-catalog", "/app/catalog"],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
    await shoot(page, name);
  }
});
