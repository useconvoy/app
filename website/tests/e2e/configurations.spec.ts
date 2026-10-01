import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import type { PortalSnapshot } from "../../src/lib/portal/types";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { mockDocuments, type DocumentServer } from "./support/documents";

// Contract fixtures only: the generic sample workspace and a contract device, no real account data.
function snapshot(): PortalSnapshot {
  const now = Date.now();
  const at = (offset = 0) => new Date(now + offset).toISOString();
  const telemetry = Array.from({ length: 6 }, (_, i) => ({ ts: at((i - 5) * 15000), cpu_pct: 20 + i, gpu_pct: 8, mem_total_mb: 7620, mem_available_mb: 3500 + i, power_w: 7.5 + i * 0.1, temp_max_c: 45 + i * 0.3, disk_free_mb: 20000, runtime_state: "running", clock_confidence: "unknown" }));
  return {
    fetched_at: at(), telemetry_stale_after_s: 90, heartbeat_interval_s: 15,
    device: { id: "dev_contract", name: "Contract Jetson", status: "online", live_at: at(-2000), observed_at: at(-2000), observed_health: "ok", observed_stage: "ready", agent_version: "contract-version", observed_active_release_id: "rel_contract", gateway_mode: "production", runtime_state: "running" },
    release: { id: "rel_contract", name: "Contract model", version: "1", digest: "contract-digest", model_repo: "contract/model", model_file: "contract.gguf", runtime_name: "llama.cpp", runtime_backend: "cuda", context_window: 2048, output_limit: 128 },
    telemetry, latest_telemetry: telemetry[5],
    usage: { from: "2026-09-01", to: "2026-09-30", metrics: null },
    recent_inference: [180.5, 210.25, 650].map((latency, i) => ({ trace_id: `tr_contract_${i}`, start_ts: at(-i * 20000), status: "ok", latency_ms: latency, ttft_ms: 60, queue_ms: 0, tokens_in: 110, tokens_out: 8, tok_s: 57.5 })),
    chat: { eligible: true, online: true, reason: null, release_id: "rel_contract", max_tokens: 128, context_window: 2048 },
  };
}

interface Mocks { authenticated?: boolean; document?: unknown | null; revision?: number; snapshotCalls?: { count: number } }
/** Account, device and the workspace documents API (contract v2: revisions and write preconditions). */
async function mock(page: Page, options: Mocks = {}): Promise<DocumentServer> {
  let authenticated = options.authenticated ?? true;
  const account = { user: { email: "fixture@example.test", role: "operator" }, installation: { simulator: true, dispatch_paused_at: null, quarantined_at: null } };
  await page.route("**/api/platform/auth/me", route => route.fulfill({ status: authenticated ? 200 : 401, json: authenticated ? account : { error: "Sign in" } }));
  await page.route("**/api/platform/auth/login", async route => { authenticated = true; await route.fulfill({ json: { user: account.user } }); });
  await page.route("**/api/platform/auth/logout", async route => { authenticated = false; await route.fulfill({ json: { ok: true } }); });
  await page.route("**/api/platform/projects", route => route.fulfill({ json: [] }));
  await page.route("**/api/platform/devices/*", route => route.fulfill({ json: { id: "dev_contract", name: "Contract Jetson", status: "online", hardware: { jetson_model: "Contract Jetson board", l4t_release: "36.4", cuda_version: "12.6" }, last_telemetry: {} } }));
  await page.route("**/api/portal/snapshot", route => { if (options.snapshotCalls) options.snapshotCalls.count++; return route.fulfill({ json: snapshot() }); });
  return mockDocuments(page, { document: options.document ?? null, revision: options.revision });
}
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true); }
const h1 = (page: Page) => page.getByRole("heading", { level: 1 });

test("/app opens Configurations behind the shared sign-in", async ({ page }) => {
  await mock(page, { authenticated: false });
  await page.goto("/app");
  await expect(page).toHaveURL(/\/app\/configurations\/?$/);
  await expect(page.getByRole("heading", { name: "One workspace for your robots." })).toBeVisible();
  await page.getByLabel("Email", { exact: true }).fill("fixture@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fixture-only");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(h1(page)).toHaveText("Configurations");
  await expect(page.getByRole("navigation", { name: "Workspace" }).getByRole("link", { name: /Configurations/ })).toHaveAttribute("aria-current", "page");
  await expect(page.getByText("fixture@example.test")).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "One workspace for your robots." })).toBeVisible();
  expect(await page.title()).toBe("Convoy | Configurations");
});

// Page bodies are filled in by later work; these checks stay on the frame: heading, breadcrumbs, notices, header badges.
test("every route renders its frame from the sample workspace and breadcrumbs lead back up", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations");
  await expect(h1(page)).toHaveText("Configurations");
  await expect(page.getByText("No workspace document is stored for this account yet", { exact: false })).toBeVisible();
  await expect(page.locator(".portal-workspace-label")).toHaveText("Sample workspace");
  await expect(page.getByRole("note").filter({ hasText: "Values marked Sample are illustrative" })).toContainText("Lab bench reports from a connected Jetson Orin Nano Super 8 GB.");
  await page.goto("/app/configurations/hybrid");
  await expect(h1(page)).toHaveText("Bimanual station · Hybrid");
  const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(crumbs.getByRole("link", { name: "Configurations" })).toHaveAttribute("href", "/app/configurations");
  await expect(crumbs.locator("[aria-current=page]")).toHaveText("Bimanual station · Hybrid");
  await expect(page.locator(".cfg-title").getByText("In production r3", { exact: true })).toBeVisible();
  await page.goto("/app/configurations/hybrid/robots/lab-bench");
  await expect(h1(page)).toHaveText("Lab bench");
  await expect(page.locator(".cfg-title .cfg-prov--measured")).toContainText("Measured");
  await page.goto("/app/configurations/hybrid/robots/lab-bench/evals/run-23");
  await expect(h1(page)).toHaveText("Run 23 · Bimanual station suite v1");
  await expect(page.locator(".cfg-title").getByText("Passed gate", { exact: true })).toBeVisible();
  await crumbs.getByRole("link", { name: "Lab bench" }).click();
  await expect(h1(page)).toHaveText("Lab bench");
  await crumbs.getByRole("link", { name: "Bimanual station · Hybrid" }).click();
  await expect(h1(page)).toHaveText("Bimanual station · Hybrid");
  await crumbs.getByRole("link", { name: "Configurations" }).click();
  await expect(h1(page)).toHaveText("Configurations");
  await page.goto("/app/configurations/new?from=hybrid");
  await expect(h1(page)).toHaveText("Bimanual station · Hybrid r5");
});

test("unknown ids show not-found states with a way back", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations/not-a-config");
  await expect(page.getByText("This configuration is not in the workspace.")).toBeVisible();
  await page.goto("/app/configurations/cloud-only/robots/lab-bench");
  await expect(page.getByText("This robot is not in this configuration.")).toBeVisible();
  await page.getByRole("link", { name: "Back to the configuration" }).click();
  await expect(h1(page)).toHaveText("Bimanual station · Cloud only");
  await page.goto("/app/configurations/hybrid/robots/lab-bench/evals/run-404");
  await expect(page.getByText("This run is not in the workspace.")).toBeVisible();
});

test("a valid stored document is used and an invalid one falls back to the sample with its problems", async ({ page }) => {
  const document = createSampleWorkspace();
  document.meta = { ...document.meta, label: "Lab workspace", sample: false };
  await mock(page, { document });
  await page.goto("/app/configurations");
  await expect(page.locator(".portal-workspace-label")).toHaveText("Lab workspace");
  await expect(page.getByText("No workspace document is stored", { exact: false })).toHaveCount(0);
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mock(page, { document: { ...createSampleWorkspace(), schemaVersion: 2 } });
  await page.reload();
  await expect(page.getByText("The stored workspace could not be used", { exact: false })).toBeVisible();
  await expect(page.getByText("schemaVersion: expected 1", { exact: false })).toBeVisible();
  await expect(page.locator(".portal-workspace-label")).toHaveText("Sample workspace");
});

test("importing a workspace creates the document with PUT, If-None-Match and an Idempotency-Key", async ({ page }) => {
  const server = await mock(page);
  await page.goto("/app/configurations");
  // One notice while the sample is shown: where it comes from, what Sample means, the device, and Import.
  const notice = page.getByRole("note").filter({ hasText: "Values marked Sample are illustrative" });
  await expect(notice).toContainText("Sample workspace: no workspace document is stored for this account yet.");
  await expect(notice.getByLabel("Import workspace")).toBeAttached();
  await expect(page.locator(".cfg-notice")).toHaveCount(1);
  await page.getByLabel("Import workspace").setInputFiles({ name: "broken.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ schemaVersion: 1 })) });
  await expect(page.locator(".cfg-import__error")).toContainText("Not imported: meta: is required");
  expect(server.writes).toHaveLength(0);
  await page.getByLabel("Import workspace").setInputFiles({ name: "huge.json", mimeType: "application/json", buffer: Buffer.alloc(2 * 1024 * 1024 + 1, 32) });
  await expect(page.locator(".cfg-import__error")).toContainText("a workspace document holds at most 2 MiB");
  const document = createSampleWorkspace();
  document.meta = { ...document.meta, label: "Imported workspace", sample: false };
  await page.getByLabel("Import workspace").setInputFiles({ name: "workspace.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(document)) });
  await expect(page.locator(".portal-workspace-label")).toHaveText("Imported workspace");
  expect(server.writes).toHaveLength(1);
  expect(server.writes[0]).toMatchObject({ ifNoneMatch: "*", ifMatch: null, client: "web", status: 200, body: { schemaVersion: 1, meta: { label: "Imported workspace" } } });
  expect(server.writes[0].idempotencyKey).toMatch(/^[\x21-\x7e]{1,128}$/);
  await expect(page.getByText("No workspace document is stored", { exact: false })).toHaveCount(0);
});

test("importing over a stored (invalid) document asks first and replaces exactly the revision read", async ({ page }) => {
  const server = await mock(page, { document: { ...createSampleWorkspace(), schemaVersion: 2 }, revision: 7 });
  await page.goto("/app/configurations");
  await expect(page.getByText("The stored workspace could not be used", { exact: false })).toBeVisible();
  const document = createSampleWorkspace();
  document.meta = { ...document.meta, label: "Replacement workspace", sample: false };
  const file = { name: "replacement.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(document)) };
  await page.getByLabel("Import workspace").setInputFiles(file);
  const confirm = page.getByRole("dialog", { name: "Replace the stored workspace?" });
  await expect(confirm).toContainText("(revision 7");
  await expect(confirm).toContainText("replacement.json");
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toBeHidden();
  expect(server.writes).toHaveLength(0);
  await page.getByLabel("Import workspace").setInputFiles(file);
  await confirm.getByRole("button", { name: "Replace workspace" }).click();
  await expect(page.locator(".portal-workspace-label")).toHaveText("Replacement workspace");
  expect(server.writes).toMatchObject([{ ifMatch: "\"7\"", ifNoneMatch: null, status: 200 }]);
  expect(server.revision).toBe(8);
});

test("a save that meets a newer document re-applies the change once on top of it", async ({ page }) => {
  const document = createSampleWorkspace();
  document.meta = { ...document.meta, label: "Lab workspace", sample: false };
  const server = await mock(page, { document, revision: 3 });
  await page.goto("/app/configurations/hybrid?tab=robots");
  await expect(page.locator(".portal-workspace-label")).toHaveText("Lab workspace");
  // Another tab renames the workspace after this page read revision 3.
  server.changeElsewhere(current => ({ ...current, meta: { ...current.meta, label: "Lab workspace (renamed elsewhere)" } }));
  await page.getByRole("button", { name: "Flag Unit 07" }).click();
  const dialog = page.getByRole("dialog", { name: "Flags · Unit 07" });
  await dialog.getByLabel("Reason").fill("Gripper slipping");
  await dialog.getByLabel("Note").fill("Slipped twice on bin 4.");
  await dialog.getByRole("button", { name: "Flag robot" }).click();
  await expect(dialog).toBeHidden();
  expect(server.writes.map(write => [write.ifMatch, write.status])).toEqual([["\"3\"", 412], ["\"4\"", 200]]);
  expect(server.writes[1].body.meta.label).toBe("Lab workspace (renamed elsewhere)");
  expect(server.writes[1].body.robots.find(robot => robot.id === "unit-07")?.flags[0]).toMatchObject({ label: "Gripper slipping", severity: "warning" });
  await expect(page.locator(".portal-workspace-label")).toHaveText("Lab workspace (renamed elsewhere)");
  await expect(page.locator(".cd-flash")).toContainText("Unit 07 flagged: Gripper slipping.");
});

test("live device data polls once for the page and stops when it leaves", async ({ page }) => {
  const snapshotCalls = { count: 0 };
  await mock(page, { snapshotCalls });
  await page.goto("/app/configurations/hybrid/robots/lab-bench");
  await expect(page.locator(".cfg-title .cfg-prov--measured")).toBeVisible();
  const afterLoad = snapshotCalls.count;
  expect(afterLoad).toBeGreaterThanOrEqual(1);
  expect(afterLoad).toBeLessThanOrEqual(2);
  await page.getByRole("link", { name: /Applications/ }).first().click();
  await expect(page.getByRole("heading", { name: "Build. Deploy. Observe." })).toBeVisible();
  const atLeave = snapshotCalls.count;
  await page.waitForTimeout(1000);
  expect(snapshotCalls.count).toBe(atLeave);
});

for (const width of [1440, 390]) test(`configuration pages are accessible and fit at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mock(page);
  for (const [path, title] of [
    ["/app/configurations", "Configurations"],
    ["/app/configurations/new", "New configuration"],
    ["/app/configurations/hybrid", "Bimanual station · Hybrid"],
    ["/app/configurations/hybrid/robots/unit-08", "Unit 08"],
    ["/app/configurations/hybrid/robots/lab-bench/evals/run-24", "Run 24 · Bimanual station suite v1"],
  ] as const) {
    await page.goto(path);
    await expect(h1(page)).toHaveText(title);
    await noOverflow(page);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
    expect(result.violations, `${path}: ${JSON.stringify(result.violations, null, 2)}`).toEqual([]);
  }
});
