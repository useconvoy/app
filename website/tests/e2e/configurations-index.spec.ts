import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";
import type { PortalSnapshot } from "../../src/lib/portal/types";

// Contract fixtures only: the generic sample workspace and a contract device, no real account data.
function snapshot(): PortalSnapshot {
  const now = Date.now();
  const at = (offset = 0) => new Date(now + offset).toISOString();
  const telemetry = Array.from({ length: 6 }, (_, i) => ({ ts: at((i - 5) * 15000), cpu_pct: 20 + i, gpu_pct: 8, mem_total_mb: 7620, mem_available_mb: 3500 + i, power_w: 7.5 + i * 0.1, temp_max_c: 45 + i * 0.3, disk_free_mb: 20000, runtime_state: "running", clock_confidence: "unknown" }));
  return {
    fetched_at: at(), telemetry_stale_after_s: 90, heartbeat_interval_s: 15,
    device: { id: "dev_contract", name: "Contract Jetson", status: "online", live_at: at(-2000), observed_at: at(-2000), observed_health: "ok", observed_stage: "ready", agent_version: "contract-version", observed_active_release_id: "rel_contract", gateway_mode: "production", runtime_state: "running" },
    release: null, telemetry, latest_telemetry: telemetry[5], usage: { from: "2026-09-01", to: "2026-09-30", metrics: null }, recent_inference: [],
    chat: { eligible: true, online: true, reason: null, release_id: "rel_contract", max_tokens: 128, context_window: 2048 },
  };
}

async function mock(page: Page, document: ConvoyWorkspace | null = null) {
  const account = { user: { email: "fixture@example.test", role: "operator" }, installation: { simulator: true, dispatch_paused_at: null, quarantined_at: null } };
  await page.route("**/api/platform/auth/me", route => route.fulfill({ json: account }));
  await page.route("**/api/platform/devices/*", route => route.fulfill({ json: { id: "dev_contract", name: "Contract Jetson", status: "online", hardware: { jetson_model: "Contract Jetson board", l4t_release: "36.4" }, last_telemetry: {} } }));
  await page.route("**/api/portal/snapshot", route => route.fulfill({ json: snapshot() }));
  await page.route("**/api/platform/workspace-documents/configurations", route => document === null
    ? route.fulfill({ status: 404, json: { error: "This resource is unavailable in your project." } })
    : route.fulfill({ json: { name: "configurations", schema_version: 1, body: document, size_bytes: 1, updated_at: new Date().toISOString() } }));
}
const cards = (page: Page) => page.locator(".cfg-card:not(.cfg-card--add)");
const card = (page: Page, name: string) => cards(page).filter({ has: page.getByRole("heading", { name, exact: true }) });
const chip = (page: Page, label: string) => page.getByRole("group", { name: "Filter by status" }).getByRole("button", { name: new RegExp(`^${label} \\d+$`) });
const search = (page: Page) => page.getByLabel("Search configurations");
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true); }
async function axe(page: Page, label: string) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  expect(result.violations, `${label}: ${JSON.stringify(result.violations, null, 2)}`).toEqual([]);
}

test("cards show each configuration's stack, revisions, robots, attention, latest gate run and verdict", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Configurations");
  await expect(page.locator(".portal-updated")).toContainText("3 configurations · 7 robots attached");
  await expect(cards(page)).toHaveCount(3);
  await expect(cards(page).locator("h2")).toHaveText(["Bimanual station · Hybrid", "Bimanual station · Cloud only", "Bimanual station · Edge only"]);

  const hybrid = card(page, "Bimanual station · Hybrid");
  await expect(hybrid).toHaveAttribute("href", "/app/configurations/hybrid");
  for (const text of ["In production r3", "Testing r4", "Recommended.", "Jetson Orin Nano Super 8 GB · 25 W mode", "Cloud VLA policy v3.1 · Cloud VLM verifier", "r4 · 78.1 % (n = 360)", "Passed gate", "Test 2 · Production 4", "2 need attention", "1 warning", "Updated 3 min ago"]) {
    await expect(hybrid).toContainText(text);
  }
  await expect(hybrid.locator(".ci-eval .cfg-prov--sample")).toHaveText("Sample");
  const cloud = card(page, "Bimanual station · Cloud only");
  for (const text of ["Testing r2", "Below gate.", "r2 · 72.8 % (n = 360)", "Test 1 (Sim 01) · Production 0"]) await expect(cloud).toContainText(text);
  const edge = card(page, "Bimanual station · Edge only");
  for (const text of ["Draft r1", "Compatibility warning.", "None (edge only)", "No gated run yet", "Test 0 · Production 0"]) await expect(edge).toContainText(text);
  await expect(edge.locator(".ci-note .cfg-prov--recorded")).toHaveText("Recorded · Sep 29");
  await expect(page.getByRole("link", { name: "Add configuration" })).toHaveCount(2);
  for (const link of await page.getByRole("link", { name: "Add configuration" }).all()) await expect(link).toHaveAttribute("href", "/app/configurations/new");

  const feed = page.getByRole("list", { name: "Recent activity, newest first" });
  await expect(feed.getByRole("listitem")).toHaveCount(7);
  await expect(feed.getByRole("listitem").first()).toContainText("Flagged");
  await expect(feed.getByRole("link", { name: "Unit 08" })).toHaveAttribute("href", "/app/configurations/hybrid/robots/unit-08");
  await expect(feed.getByRole("link", { name: "Run 21" })).toHaveAttribute("href", "/app/configurations/hybrid/robots/lab-bench/evals/run-21");
  await expect(feed.getByRole("listitem").filter({ hasText: "Unit 18 registered" }).getByRole("link")).toHaveCount(0);
});

test("search, status chips and sort filter the cards and stay in the URL", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations");
  await search(page).fill("unit 16");
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).first()).toContainText("Bimanual station · Hybrid");
  await expect(page).toHaveURL(/[?&]q=unit\+16/);
  await expect(chip(page, "All")).toContainText("1");
  await expect(chip(page, "Draft")).toContainText("0");
  await expect(page.getByRole("status").filter({ hasText: "configuration of 3 shown" })).toHaveText("1 configuration of 3 shown");
  await page.reload();
  await expect(search(page)).toHaveValue("unit 16");
  await expect(cards(page)).toHaveCount(1);
  await search(page).fill("");
  await expect(cards(page)).toHaveCount(3);
  await expect(page).not.toHaveURL(/q=/);

  await chip(page, "Draft").click();
  await expect(chip(page, "Draft")).toHaveAttribute("aria-pressed", "true");
  await expect(chip(page, "All")).toHaveAttribute("aria-pressed", "false");
  await expect(page).toHaveURL(/[?&]status=draft/);
  await expect(cards(page).locator("h2")).toHaveText(["Bimanual station · Edge only"]);
  await chip(page, "In production").click();
  await expect(cards(page).locator("h2")).toHaveText(["Bimanual station · Hybrid"]);
  await chip(page, "Testing").click();
  await expect(cards(page).locator("h2")).toHaveText(["Bimanual station · Hybrid", "Bimanual station · Cloud only"]);
  await chip(page, "All").click();
  await expect(page).not.toHaveURL(/status=/);

  await page.getByLabel("Sort").selectOption("name");
  await expect(cards(page).locator("h2")).toHaveText(["Bimanual station · Cloud only", "Bimanual station · Edge only", "Bimanual station · Hybrid"]);
  await expect(page).toHaveURL(/[?&]sort=name/);
  await page.reload();
  await expect(page.getByLabel("Sort")).toHaveValue("name");
  await expect(cards(page).first()).toContainText("Bimanual station · Cloud only");
  await page.getByLabel("Sort").selectOption("eval");
  await expect(cards(page).locator("h2")).toHaveText(["Bimanual station · Hybrid", "Bimanual station · Cloud only", "Bimanual station · Edge only"]);

  // The sidebar link opens the index afresh: no search, filter or sort.
  await search(page).fill("cloud");
  await expect(cards(page)).toHaveCount(2);
  await page.getByRole("navigation", { name: "Workspace" }).getByRole("link", { name: /Configurations/ }).click();
  await expect(page).toHaveURL(/\/app\/configurations\/?$/);
  await expect(search(page)).toHaveValue("");
  await expect(cards(page)).toHaveCount(3);
});

test("no results and an empty workspace say what to do next", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations?q=nothing+like+this");
  await expect(page.getByText("No configurations match “nothing like this”.")).toBeVisible();
  await expect(cards(page)).toHaveCount(0);
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(cards(page)).toHaveCount(3);
  await expect(search(page)).toHaveValue("");
  await page.goto("/app/configurations?status=draft&q=hybrid");
  await expect(page.getByText("No draft configurations match “hybrid”.")).toBeVisible();
  await axe(page, "no results");

  const sample = createSampleWorkspace();
  const empty: ConvoyWorkspace = {
    ...sample, meta: { ...sample.meta, label: "Empty workspace", sample: false }, configurations: [], runs: [], rollouts: [],
    robots: sample.robots.map(robot => ({ ...robot, configId: null, rev: null })), logs: sample.logs.map(line => ({ ...line, configId: null })), activity: [],
  };
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mock(page, empty);
  await page.goto("/app/configurations");
  await expect(page.locator(".portal-workspace-label")).toHaveText("Empty workspace");
  await expect(page.getByText("No configurations in this workspace yet.")).toBeVisible();
  await expect(page.getByLabel("Import workspace")).toBeVisible();
  await expect(page.getByRole("link", { name: "Add configuration" })).toHaveCount(0);
  await expect(page.getByLabel("Search configurations")).toHaveCount(0);
  await expect(page.getByText("No activity has been recorded in this workspace yet.")).toBeVisible();
  await page.goto("/app/configurations/new");
  await expect(page.getByText("There is nothing to start a configuration from yet.")).toBeVisible();
});

test("cards and the add tile open their pages", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations");
  await card(page, "Bimanual station · Cloud only").click();
  await expect(page).toHaveURL(/\/app\/configurations\/cloud-only\/?$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Bimanual station · Cloud only");
  await page.goBack();
  await page.locator(".cfg-card--add").click();
  await expect(page).toHaveURL(/\/app\/configurations\/new\/?$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("New configuration");
});

test("search, chips and cards work from the keyboard", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations");
  await search(page).focus();
  await page.keyboard.type("cloud");
  await expect(cards(page)).toHaveCount(2);
  await page.keyboard.press("Tab");
  await expect(chip(page, "All")).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(chip(page, "Draft")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(chip(page, "Draft")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("No draft configurations match “cloud”.")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).focus();
  await page.keyboard.press("Space");
  await expect(cards(page)).toHaveCount(3);
  await card(page, "Bimanual station · Hybrid").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Bimanual station · Hybrid");
});

for (const width of [1440, 390]) test(`the index is accessible and fits at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mock(page);
  await page.goto("/app/configurations");
  await expect(cards(page)).toHaveCount(3);
  await noOverflow(page);
  await axe(page, `index ${width}`);
  await chip(page, "In production").click();
  await expect(cards(page)).toHaveCount(1);
  await noOverflow(page);
  await axe(page, `filtered ${width}`);
});
