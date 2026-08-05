/**
 * Every human action against the real local stack. No mocked
 * domain: runs are created at the control plane under this org's tenant
 * (or through the console's own start seam) and every verb is exercised
 * through the UI, with truth arriving via events.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";
import pg from "pg";

const ROOT = join(__dirname, "..", "..");
const CONTROL_PLANE = process.env.CONVOY_CONTROL_PLANE_URL ?? "http://localhost:8700";
const TOKEN = process.env.CONVOY_CONTROL_PLANE_TOKEN ?? "e2e-dev-token";
const ADMIN_DSN =
  process.env.WEBSITE_PG_ADMIN_DSN ??
  "postgresql://convoy_admin:convoy_admin@localhost:5433/convoy_website";

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

test.use({ storageState: join(ROOT, "test-results", "auth.json") });

function orgFacts(): { email: string; orgName: string } {
  return JSON.parse(readFileSync(join(ROOT, "test-results", "e2e-org.json"), "utf8"));
}

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

/** Grant the signed-in admin the promoter capability, straight in the DB. */
async function grantPromoter(): Promise<void> {
  const client = new pg.Client({ connectionString: ADMIN_DSN });
  await client.connect();
  await client.query(
    `UPDATE memberships m SET capabilities = array['promoter']
       FROM organizations o, users u
      WHERE o.id = m.org_id AND u.id = m.user_id
        AND o.name = $1 AND u.email = $2`,
    [orgFacts().orgName, orgFacts().email],
  );
  await client.end();
}

interface CreateRunOptions {
  goal: string;
  environmentId?: string;
  requireApproval?: boolean;
  gateStep?: { stepId: string; prompt: string; timeout?: string };
}

async function createRun(tenant: string, options: CreateRunOptions): Promise<string> {
  const body: Record<string, unknown> = {
    goal: options.goal,
    environment_id: options.environmentId ?? "stub-local",
    budget_usd: "5",
  };
  if (options.requireApproval) body["policy"] = { require_plan_approval: true };
  if (options.gateStep) {
    body["fixture_gates"] = {
      [options.gateStep.stepId]: {
        kind: "approval",
        prompt: options.gateStep.prompt,
        on_timeout: "pause",
        ...(options.gateStep.timeout ? { timeout: options.gateStep.timeout } : {}),
      },
    };
  }
  const created = await fetch(`${CONTROL_PLANE}/runs`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      "X-Actor-Id": orgFacts().email,
      "X-Tenant-Id": tenant,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  expect(created.status).toBeLessThan(300);
  const { run_id: runId } = (await created.json()) as { run_id: string };
  return runId;
}

async function waitForStatus(tenant: string, runId: string, status: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const view = await fetch(`${CONTROL_PLANE}/runs/${runId}`, {
          headers: {
            Authorization: `Bearer ${TOKEN}`,
            "X-Actor-Id": orgFacts().email,
            "X-Tenant-Id": tenant,
          },
        });
        return view.ok ? ((await view.json()) as { status: string }).status : "unreachable";
      },
      { timeout: 30_000 },
    )
    .toBe(status);
}

test("plan approval happy path: approve the rendered version, watch it confirm", async ({
  page,
}) => {
  const tenant = await tenantId();
  const runId = await createRun(tenant, {
    goal: "Approve before touching production records",
    environmentId: "prod-local",
    requireApproval: true,
  });
  await waitForStatus(tenant, runId, "awaiting_approval");

  await page.goto(`/app/runs/${runId}`);
  await expect(page.getByText("Waiting for approval").first()).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Approve plan" }).click();

  // Approval renders only from the confirmed revision_approved event.
  await expect(page.getByText("Plan approved")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Landed").first()).toBeVisible({ timeout: 30_000 });
});

test("stale approval shows the newer-version state and never auto-retries", async ({
  page,
  context,
}) => {
  const tenant = await tenantId();
  const runId = await createRun(tenant, {
    goal: "Two reviewers race on one plan",
    requireApproval: true,
  });
  await waitForStatus(tenant, runId, "awaiting_approval");

  // The stale tab: its event stream is blocked so it keeps rendering the
  // version it fetched, like a tab left open over lunch.
  const staleTab = await context.newPage();
  await staleTab.route("**/api/runs/*/events*", (route) => route.abort());
  await staleTab.goto(`/app/runs/${runId}`);
  await expect(staleTab.getByRole("button", { name: "Approve plan" })).toBeVisible({
    timeout: 20_000,
  });

  // The fresh tab approves and the run moves on.
  await page.goto(`/app/runs/${runId}`);
  await page.getByRole("button", { name: "Approve plan" }).click();
  await expect(page.getByText("Landed").first()).toBeVisible({ timeout: 30_000 });

  // The stale tab's approve now hits a 409: the conflict UI must show,
  // with no retry underneath.
  await staleTab.getByRole("button", { name: "Approve plan" }).click();
  await expect(staleTab.getByText("This plan has a newer version")).toBeVisible({
    timeout: 10_000,
  });
  await staleTab.close();
});

test("gate flow: scripted answer at a rehearsal moment, clock advance, land report, promotion", async ({
  page,
}) => {
  const tenant = await tenantId();
  const prompt = "Look over the exception memos before they go out";
  const runId = await createRun(tenant, {
    goal: "Chase sign-offs across rehearsal days",
    environmentId: "stub-local-virtual",
    gateStep: { stepId: "step-2", prompt, timeout: "PT48H" },
  });
  await waitForStatus(tenant, runId, "blocked_on_human");

  await page.goto(`/app/runs/${runId}`);
  await expect(
    page.getByText("You are looking at a rehearsal. Nothing here touches live systems."),
  ).toBeVisible({ timeout: 20_000 });
  const form = page.locator("[id='respond-step-2']");
  await expect(form).toBeVisible({ timeout: 20_000 });
  await expect(form.getByText(prompt)).toBeVisible();
  await expect(page.getByText("If no one answers in time, the run pauses.")).toBeVisible();

  // Script the answer three rehearsal days out.
  await form.getByLabel("Your answer").fill("Everyone has confirmed, send the memos");
  await form.getByRole("checkbox", { name: "Answer at a moment in rehearsal time" }).check();
  const moment = form.getByLabel(/Rehearsal moment/);
  const seeded = await moment.inputValue();
  expect(seeded).not.toBe("");
  const scheduled = new Date(new Date(seeded).getTime() + 3 * 86_400_000);
  const pad = (part: number) => String(part).padStart(2, "0");
  await moment.fill(
    `${scheduled.getFullYear()}-${pad(scheduled.getMonth() + 1)}-${pad(scheduled.getDate())}T${pad(
      scheduled.getHours(),
    )}:${pad(scheduled.getMinutes())}`,
  );
  // Wait for the respond action's round trip before touching the clock,
  // so the scheduled answer is registered before time moves.
  const respondDone = page.waitForResponse(
    (response) => response.request().method() === "POST" && response.url().includes(runId),
  );
  await form.getByRole("button", { name: "Respond" }).click();
  await respondDone;

  // Fast-forward a week: the scheduled answer lands at its virtual instant.
  await page.getByRole("button", { name: "+1 week" }).click();
  const answeredRow = page.locator('li:has(span:text-is("Answered"))');
  await expect(answeredRow).toBeVisible({ timeout: 30_000 });
  await expect(answeredRow).toContainText(
    `${MONTHS[scheduled.getMonth()]} ${scheduled.getDate()}`,
  );

  // The run lands; the report and the stand-in outbox render.
  await expect(page.getByText("Landed").first()).toBeVisible({ timeout: 30_000 });
  const report = page.getByRole("region", { name: "Land report" });
  await expect(report).toBeVisible({ timeout: 20_000 });
  await expect(report.getByText("What this routine would have done")).toBeVisible();
  await expect(report.locator("tbody tr").first()).toBeVisible();

  // Promotion rides a run started through the console's own seam, which
  // knows the routine. Submit, review, promote.
  await grantPromoter();
  await page.goto("/app/runs");
  await page.getByRole("button", { name: "Start a rehearsal run" }).first().click();
  const runLink = page.getByRole("link", { name: "Quarterly user access review" }).first();
  await expect(runLink).toBeVisible({ timeout: 20_000 });
  await runLink.click();
  await expect(page.getByText("Landed").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("region", { name: "Land report" })).toBeVisible({
    timeout: 20_000,
  });

  await page.getByRole("button", { name: "Submit for promotion" }).click();
  await expect(
    page.getByText("Promotion requested. A promoter will look it over."),
  ).toBeVisible({ timeout: 10_000 });
  await page.getByRole("link", { name: "Promotion review" }).click();

  await expect(page.getByRole("heading", { name: "Promotion review" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText("What this routine would have done")).toBeVisible();
  await page.getByRole("button", { name: "Promote", exact: true }).click();
  await expect(page.getByText("Promoted", { exact: true }).first()).toBeVisible({
    timeout: 10_000,
  });
});

async function registerThroughDetailPage(page: Page, runId: string): Promise<void> {
  await page.goto(`/app/runs/${runId}`);
  await expect(page.getByText("Held for a person").first()).toBeVisible({ timeout: 30_000 });
}

test("keyboard triage: j/k roving focus with visible focus, A drives the verb", async ({
  page,
}) => {
  const tenant = await tenantId();
  const first = await createRun(tenant, {
    goal: "First question for the keyboard pass",
    gateStep: { stepId: "step-2", prompt: "Keyboard pass question one" },
  });
  const second = await createRun(tenant, {
    goal: "Second question for the keyboard pass",
    gateStep: { stepId: "step-2", prompt: "Keyboard pass question two" },
  });
  await waitForStatus(tenant, first, "blocked_on_human");
  await waitForStatus(tenant, second, "blocked_on_human");
  await registerThroughDetailPage(page, first);
  await registerThroughDetailPage(page, second);

  await page.goto("/app/checkpoints");
  await expect(
    page.getByText("Keys: j next · k previous · A approve · E edit · R reject"),
  ).toBeVisible({ timeout: 20_000 });

  const cards = page.locator("ul[aria-describedby='checkpoints-keys'] > li");
  expect(await cards.count()).toBeGreaterThanOrEqual(2);

  // Roving tabindex: exactly one card is in the tab order; j moves focus
  // and the focus ring is visible (:focus-visible), k moves it back.
  await cards.first().focus();
  await expect(cards.first()).toBeFocused();
  await page.keyboard.press("j");
  await expect(cards.nth(1)).toBeFocused();
  expect(
    await cards.nth(1).evaluate((element) => element.matches(":focus-visible")),
  ).toBe(true);
  expect(await cards.nth(1).getAttribute("tabindex")).toBe("0");
  expect(await cards.first().getAttribute("tabindex")).toBe("-1");
  await page.keyboard.press("k");
  await expect(cards.first()).toBeFocused();
  expect(
    await cards.first().evaluate((element) => element.matches(":focus-visible")),
  ).toBe(true);

  // A on a respond card drives its verb: with nothing typed it moves focus
  // into the answer field, keyboard-only.
  await page.keyboard.press("a");
  const answer = cards.first().getByLabel("Your answer");
  await expect(answer).toBeFocused();
});

test("pause, then resume from the Checkpoints inbox", async ({ page }) => {
  const tenant = await tenantId();
  const goal = "Hold for the pause and resume pass";
  const runId = await createRun(tenant, {
    goal,
    gateStep: { stepId: "step-2", prompt: "Waiting while paused" },
  });
  await waitForStatus(tenant, runId, "blocked_on_human");
  await registerThroughDetailPage(page, runId);

  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByText("Paused").first()).toBeVisible({ timeout: 20_000 });

  await page.goto("/app/checkpoints");
  const card = page
    .locator("ul[aria-describedby='checkpoints-keys'] > li")
    .filter({ hasText: goal })
    .filter({ hasText: "This run is paused" });
  await expect(card).toBeVisible({ timeout: 20_000 });
  await card.getByRole("button", { name: "Resume" }).click();
  await expect(card.getByText("Sent. This clears once the run confirms.")).toBeVisible({
    timeout: 10_000,
  });

  // The resume confirms on the run's own timeline.
  await page.goto(`/app/runs/${runId}`);
  await expect(page.getByText("Resumed").first()).toBeVisible({ timeout: 20_000 });
});

test("evidence binder: download the zip and check the manifest entries", async ({ page }) => {
  const tenant = await tenantId();
  const runId = await createRun(tenant, { goal: "Land quickly for the evidence export" });
  await waitForStatus(tenant, runId, "completed");

  await page.goto(`/app/runs/${runId}`);
  await expect(page.getByRole("region", { name: "Land report" })).toBeVisible({
    timeout: 20_000,
  });

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export evidence binder" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(`evidence-binder-${runId}.zip`);
  const path = join(ROOT, "test-results", download.suggestedFilename());
  await download.saveAs(path);

  const { execFileSync } = await import("node:child_process");
  const names = execFileSync(
    "python3",
    [
      "-c",
      [
        "import sys, zipfile",
        "archive = zipfile.ZipFile(sys.argv[1])",
        "assert archive.testzip() is None",
        "assert len(archive.read('manifest.json')) > 0",
        "print('\\n'.join(archive.namelist()))",
      ].join("\n"),
      path,
    ],
    { encoding: "utf8" },
  )
    .trim()
    .split("\n");
  expect(names).toEqual(["manifest.json", "run-summary.json", "events.ndjson", "land-report.json"]);
});
