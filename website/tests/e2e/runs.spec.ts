/**
 * The runs read path against the real local stack. No mocked
 * domain: the dev server proxies to the control plane on :8700 and these
 * specs watch real runs stream.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";
import pg from "pg";

const ROOT = join(__dirname, "..", "..");
const CONTROL_PLANE = process.env.CONVOY_CONTROL_PLANE_URL ?? "http://localhost:8700";
const TOKEN = process.env.CONVOY_CONTROL_PLANE_TOKEN ?? "e2e-dev-token";
const ADMIN_DSN =
  process.env.WEBSITE_PG_ADMIN_DSN ??
  "postgresql://convoy_admin:convoy_admin@localhost:5433/convoy_website";

test.use({ storageState: join(ROOT, "test-results", "auth.json") });

test("start a run from the picker, stream live, and land", async ({ page }) => {
  await page.goto("/app/runs");
  await page.getByRole("link", { name: "Start a run" }).first().click();
  await page.waitForURL("**/app/runs/new");

  // Pick the seeded routine; its plan and the start affordance appear.
  await page.getByRole("link", { name: "Quarterly user access review" }).first().click();
  await expect(page.getByText("Pull the current list of people and their access")).toBeVisible();
  await page.getByRole("button", { name: "Start a rehearsal run" }).click();

  // The action redirects straight to the run's detail page.
  await expect(
    page.getByRole("heading", { name: "Quarterly user access review" }),
  ).toBeVisible({ timeout: 20_000 });

  // Rehearsal is unmistakable on a sandbox-bound run.
  await expect(
    page.getByText("You are looking at a rehearsal. Nothing here touches live systems."),
  ).toBeVisible({ timeout: 20_000 });

  // The timeline hydrates from seq 0 and follows the live stream.
  await expect(page.getByText("Run started")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Step finished").first()).toBeVisible({ timeout: 30_000 });

  // The run lands and the status chip says so.
  await expect(page.getByText("Wrapping up")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Landed").first()).toBeVisible({ timeout: 30_000 });
});

test("disconnect banner appears while offline and clears on reconnect", async ({
  page,
  context,
}) => {
  // A run held at a checkpoint keeps its stream open indefinitely, which
  // makes the disconnect path deterministic. It is created straight at the
  // control plane under this org's tenant; the detail page reads it
  // through the same edge as any other run.
  const { orgName } = JSON.parse(
    readFileSync(join(ROOT, "test-results", "e2e-org.json"), "utf8"),
  ) as { orgName: string };
  const client = new pg.Client({ connectionString: ADMIN_DSN });
  await client.connect();
  const { rows } = await client.query<{ tenant_id: string }>(
    "SELECT tenant_id FROM organizations WHERE name = $1",
    [orgName],
  );
  await client.end();
  const tenantId = rows[0]?.tenant_id;
  expect(tenantId).toBeTruthy();

  const created = await fetch(`${CONTROL_PLANE}/runs`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      "X-Actor-Id": "j.doe@example.com",
      "X-Tenant-Id": tenantId!,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      goal: "Hold at a checkpoint for the disconnect test",
      environment_id: "stub-local",
      budget_usd: "5",
      fixture_gates: {
        "step-2": { kind: "input", prompt: "Waiting on a person", on_timeout: "pause" },
      },
    }),
  });
  expect(created.status).toBeLessThan(300);
  const { run_id: runId } = (await created.json()) as { run_id: string };

  // Next dev mounts its own empty role=alert (route announcer), so the
  // banner is addressed by its copy, not by role alone.
  const banner = page.getByText("Connection lost. Reconnecting");

  await page.goto(`/app/runs/${runId}`);
  await expect(page.getByText("Held for a person").first()).toBeVisible({ timeout: 30_000 });
  await expect(banner).toHaveCount(0);

  // Drop the network: the stream dies and the banner must say so.
  await context.setOffline(true);
  await expect(banner).toBeVisible({ timeout: 20_000 });

  // Restore it: EventSource reconnects with Last-Event-ID and the banner clears.
  await context.setOffline(false);
  await expect(banner).toHaveCount(0, { timeout: 20_000 });
  await expect(page.getByText("Held for you").first()).toBeVisible();
});
