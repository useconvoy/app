import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";
import type { PortalSnapshot } from "../../src/lib/portal/types";
import { mockDocuments, type DocumentServer } from "./support/documents";

/*
 * User journeys through Configurations, each recorded as a video and a trace
 * with a screenshot at each milestone (test-results/<journey>/). Contract fixtures
 * only: the generic sample workspace, a contract device and contract episode ids;
 * every API is mocked, including the workspace documents API (contract v2:
 * revisions and write preconditions), so the journeys are deterministic.
 */
test.use({ video: { mode: "on", size: { width: 1280, height: 800 } }, trace: "on", viewport: { width: 1280, height: 800 } });

const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
const EPISODE = "epi_contract01";

function snapshot(): PortalSnapshot {
  const now = Date.now();
  const at = (offset = 0) => new Date(now + offset).toISOString();
  const telemetry = Array.from({ length: 8 }, (_, i) => ({ ts: at((i - 7) * 15000), cpu_pct: 20 + i, gpu_pct: 8, mem_total_mb: 7620, mem_available_mb: 3500 + i, power_w: 7.5 + i * 0.1, temp_max_c: 45 + i * 0.3, disk_free_mb: 20000, runtime_state: "running", clock_confidence: "unknown" }));
  return {
    fetched_at: at(), telemetry_stale_after_s: 90, heartbeat_interval_s: 15,
    device: { id: "dev_contract", name: "Contract Jetson", status: "online", live_at: at(-2000), observed_at: at(-2000), observed_health: "ok", observed_stage: "ready", agent_version: "contract-version", observed_active_release_id: "rel_contract", gateway_mode: "production", runtime_state: "running" },
    release: { id: "rel_contract", name: "Contract model", version: "1", digest: "contract-digest", model_repo: "contract/model", model_file: "contract.gguf", runtime_name: "llama.cpp", runtime_backend: "cuda", context_window: 2048, output_limit: 128 },
    telemetry, latest_telemetry: telemetry[7],
    usage: { from: "2026-09-01", to: "2026-09-30", metrics: null },
    recent_inference: [180.5, 210.25, 650].map((latency, i) => ({ trace_id: `tr_contract_${i}`, start_ts: at(-i * 20000), status: "ok", latency_ms: latency, ttft_ms: 60, queue_ms: 0, tokens_in: 110, tokens_out: 8, tok_s: 57.5 })),
    chat: { eligible: true, online: true, reason: null, release_id: "rel_contract", max_tokens: 128, context_window: 2048 },
  };
}

/** Signed-out (J1 signs in) or signed-in account, a contract device that reports, recorded episode frames and the documents API. */
async function mockApi(page: Page, options: { signedIn?: boolean; document?: ConvoyWorkspace | null } = {}): Promise<DocumentServer> {
  let signedIn = options.signedIn ?? true;
  const account = { user: { email: "fixture@example.test", role: "operator" }, installation: { simulator: true, dispatch_paused_at: null, quarantined_at: null } };
  await page.route("**/api/platform/auth/me", route => route.fulfill(signedIn ? { json: account } : { status: 401, json: { error: "Sign in" } }));
  await page.route("**/api/platform/auth/login", async route => { signedIn = true; await route.fulfill({ json: { user: account.user } }); });
  await page.route("**/api/platform/projects", route => route.fulfill({ json: [] }));
  await page.route("**/api/platform/devices/*", route => route.fulfill({ json: { id: "dev_contract", name: "Contract Jetson", status: "online", hardware: { jetson_model: "Contract Jetson board", l4t_release: "36.4", cuda_version: "12.6" }, last_telemetry: {} } }));
  await page.route("**/api/portal/snapshot", route => route.fulfill({ json: snapshot() }));
  await page.route(/\/api\/platform\/episodes\/epi_contract\d+\/replay$/, route => route.fulfill({ json: {
    episode_id: EPISODE, mission_id: "mis_contract01", release_digest: "contract-release-digest-0001", steps: 54, skill: "pick_place_puck", planner_ms: 917.78, wall_seconds: 41.21, sim_seconds: 0.675,
    source: "Recorded coordinator camera observations and applied actions",
  } }));
  await page.route(/\/api\/platform\/episodes\/epi_contract\d+\/replay\/frames\/\d+$/, route => {
    const index = Number(route.request().url().split("/").pop());
    return route.fulfill({ json: { index, image_png_base64: PNG, action: index ? [0.12, -0.4, 0.05, 1] : null, reward: index ? 0.25 : null, success: index ? false : null, policy_ms: index ? 512.3 : null } });
  });
  return mockDocuments(page, { document: options.document ?? null, revision: 1 });
}

/** The sample stored as the account's document, with one rollout linked to a recorded control-plane episode. */
function documentWithRecording(): ConvoyWorkspace {
  const ws = createSampleWorkspace();
  ws.meta = { ...ws.meta, label: "Lab workspace", sample: false };
  const rollout = ws.rollouts.find(item => item.id === "ep-23-044")!;
  rollout.episodeId = EPISODE;
  rollout.provenance = { kind: "recorded", at: "2026-10-01T04:00:39Z", n: 1, source: `Control-plane episode ${EPISODE}` };
  return ws;
}

const h1 = (page: Page) => page.locator("h1:visible");
const cards = (page: Page) => page.locator(".cfg-card:not(.cfg-card--add)");
const card = (page: Page, name: string) => cards(page).filter({ has: page.getByRole("heading", { name, exact: true }) });
const statusChip = (page: Page, label: string) => page.getByRole("group", { name: "Filter by status" }).getByRole("button", { name: new RegExp(`^${label} \\d+$`) });
const robotsTable = (page: Page) => page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: /Planner p50/ }) });
const rowNames = (table: Locator) => table.locator("tbody th[scope=row] .cfg-row-link");
const headerButton = (page: Page, name: string) => page.locator(".cfg-actions").getByRole("button", { name, exact: true });
const rolloutRows = (page: Page) => page.getByRole("table", { name: /rollouts stored for Run/ }).locator("tbody tr");
const selectField = (page: Page, label: string) => page.locator("label.cfg-field").filter({ hasText: new RegExp(`^${label}`) }).locator("select");
const banner = (page: Page) => page.locator(".cd-banner");

/**
 * A screenshot of a milestone, kept with the journey's video: the full page, or the
 * viewport while a dialog or drawer is open (it covers the viewport). Each milestone
 * also checks that the page does not scroll sideways.
 */
async function milestone(page: Page, testInfo: TestInfo, name: string, options: { overlay?: boolean } = {}) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), `${name}: no sideways scroll`).toBe(true);
  const path = testInfo.outputPath(`${name}.png`);
  // The sidebar is sticky: capture the full page from the top so it is drawn where a reader expects it.
  if (!options.overlay) await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path, fullPage: !options.overlay, animations: "disabled" });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

test("J1 · explore: sign in, find the hybrid configuration, follow the bench to a gate run, its weak slice and replays", async ({ page }, testInfo) => {
  await mockApi(page, { signedIn: false, document: documentWithRecording() });

  await test.step("Sign in and land on Configurations", async () => {
    await page.goto("/app");
    await expect(page).toHaveURL(/\/app\/configurations\/?$/);
    await page.getByLabel("Email", { exact: true }).fill("fixture@example.test");
    await page.getByLabel("Password", { exact: true }).fill("fixture-only");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(h1(page)).toHaveText("Configurations");
    await expect(page.locator(".portal-workspace-label")).toHaveText("Lab workspace");
    await expect(cards(page)).toHaveCount(3);
    await milestone(page, testInfo, "J1-01-configurations");
  });

  await test.step("Search and filter by status", async () => {
    await page.getByLabel("Search configurations").fill("jetson cloud");
    await expect(page).toHaveURL(/[?&]q=jetson\+cloud/);
    await expect(cards(page)).toHaveCount(2);
    await statusChip(page, "In production").click();
    await expect(cards(page).locator("h2")).toHaveText(["Bimanual station · Hybrid"]);
    const hybrid = card(page, "Bimanual station · Hybrid");
    for (const text of ["In production r3", "Testing r4", "2 need attention", "1 warning", "r4 · 78.1 % (n = 360)"]) await expect(hybrid).toContainText(text);
    await milestone(page, testInfo, "J1-02-search-and-filter");
  });

  await test.step("Open the hybrid configuration: KPIs, the connected device (measured) and telemetry small multiples", async () => {
    await card(page, "Bimanual station · Hybrid").click();
    await expect(h1(page)).toHaveText("Bimanual station · Hybrid");
    const kpis = page.getByRole("region", { name: "Production KPIs" });
    await expect(kpis).toContainText("Sample · last 24 h · 4 production robots");
    await expect(kpis.locator(".cfg-kpi")).toHaveCount(6);
    await expect(kpis.locator(".cfg-prov--measured")).toHaveCount(0);
    const device = page.getByRole("region", { name: "Connected device · Lab bench" });
    await expect(device.locator(".cfg-kpi .cfg-prov--measured")).toHaveCount(4);
    await expect(device.locator(".cfg-kpi").filter({ hasText: "Jetson SoC temperature" })).toContainText("47.1°C");
    await expect(device.locator(".portal-device-board")).toContainText("Healthy");
    const telemetry = page.getByRole("region", { name: "Robot telemetry" });
    await expect(telemetry.locator(".cfg-mini")).toHaveCount(12);
    await expect(telemetry.getByRole("img", { name: /^Lab bench Jetson SoC temperature, 8 measured samples/ })).toBeVisible();
    await expect(telemetry.getByRole("img", { name: /^Unit 08 Jetson SoC temperature, hourly max over 24 h/ })).toBeVisible();
    await expect(page.getByRole("note").filter({ hasText: "Values marked Sample" })).toContainText("Lab bench reports from a connected Jetson Orin Nano Super 8 GB.");
    await milestone(page, testInfo, "J1-03-configuration-overview");
  });

  await test.step("Robots tab: open the device-bound bench robot", async () => {
    await page.getByRole("tab", { name: /^Robots/ }).click();
    await expect(page).toHaveURL(/[?&]tab=robots/);
    const table = robotsTable(page);
    await expect(rowNames(table).first()).toHaveText("Lab bench");
    await expect(table.getByRole("row").filter({ hasText: "Lab bench" }).locator(".cfg-prov--measured")).toBeVisible();
    await table.getByRole("link", { name: "Lab bench", exact: true }).click();
    await expect(h1(page)).toHaveText("Lab bench");
    const badges = page.locator(".cfg-title");
    await expect(badges.getByText("Healthy", { exact: true })).toBeVisible();
    await expect(badges.locator(".cfg-prov--measured")).toContainText("Measured");
    await milestone(page, testInfo, "J1-04-bench-robot");
  });

  await test.step("Evals & sims: open the run that passed the gate", async () => {
    await expect(page.getByRole("tab", { name: /Evals & sims/ })).toHaveAttribute("aria-selected", "true");
    const runs = page.getByRole("table", { name: "Evaluation runs on Lab bench" });
    await expect(runs.getByRole("row").filter({ has: page.getByRole("link", { name: "Run 23", exact: true }) })).toContainText("Passed gate");
    await runs.getByRole("link", { name: "Run 23", exact: true }).click();
    await expect(h1(page)).toHaveText("Run 23 · Bimanual station suite v1");
    await expect(page.locator(".cfg-title").getByText("Passed gate", { exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "Promotion gate passed" })).toContainText("6 of 6 pass");
    await milestone(page, testInfo, "J1-05-gate-run");
  });

  await test.step("Filter the rollouts from the weakest slice", async () => {
    const weak = page.getByRole("table", { name: /success per slice/ }).getByRole("row", { name: /One wrist camera off/ });
    await expect(weak).toContainText("(weakest slice)");
    await weak.getByRole("link", { name: "Filter rollouts" }).click();
    await expect(page).toHaveURL(/[?&]slice=sensor-wrist-off/);
    await expect(rolloutRows(page)).toHaveCount(1);
    await expect(rolloutRows(page).first()).toContainText("ep-23-012");
    await milestone(page, testInfo, "J1-06-weak-slice-rollouts");
  });

  await test.step("Open the sample replay and scrub to an event, then close it", async () => {
    const replayLink = page.getByRole("link", { name: "Replay ep-23-012", exact: true });
    await replayLink.click();
    await expect(page).toHaveURL(/[?&]rollout=ep-23-012/);
    await expect(h1(page)).toHaveText("ep-23-012 · Shaft insertion");
    await expect(page.locator(".ev-replay .cfg-prov--sample").first()).toHaveText("Sample");
    await page.getByRole("button", { name: /^Wrist camera off at 10\.4 s/ }).click();
    await expect(page.locator(".cfg-transport__pos")).toContainText("10.4 s simulated");
    await expect(page.getByRole("list", { name: "Episode events" }).getByRole("button", { name: /Wrist camera off/ })).toHaveAttribute("aria-current", "true");
    await milestone(page, testInfo, "J1-07-sample-replay");
    await page.keyboard.press("Escape");
    await expect(h1(page)).toHaveText("Run 23 · Bimanual station suite v1");
    await expect(page).toHaveURL(/[?&]slice=sensor-wrist-off/);
    await expect(replayLink).toBeFocused();
  });

  await test.step("Open a recorded replay: the stored episode's frames through the platform proxy", async () => {
    await selectField(page, "Slice").selectOption("light-low");
    await expect(page).toHaveURL(/[?&]slice=light-low/);
    const recorded = rolloutRows(page).filter({ hasText: "ep-23-044" });
    await expect(recorded.locator(".cfg-prov--recorded")).toHaveText("Recorded · Oct 1");
    await page.getByRole("link", { name: "Replay ep-23-044", exact: true }).click();
    await expect(h1(page)).toHaveText(`${EPISODE} · Bin to tray transfer`);
    await expect(page.locator(".cfg-title .cfg-prov--recorded")).toHaveText("Recorded · Oct 1");
    await expect(page.getByRole("img", { name: "Recorded robot camera at action 0 of 54" })).toBeVisible();
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page.getByRole("img", { name: "Recorded robot camera at action 1 of 54" })).toBeVisible();
    await expect(page.locator(".replay-readout")).toContainText("Policy time: 512.30 ms");
    await milestone(page, testInfo, "J1-08-recorded-replay");
  });

  await test.step("Back to the run", async () => {
    await page.getByRole("button", { name: "Back to Run 23" }).click();
    await expect(h1(page)).toHaveText("Run 23 · Bimanual station suite v1");
    await expect(page).not.toHaveURL(/rollout=/);
    await milestone(page, testInfo, "J1-09-back-to-run");
  });
});

test("J2 · production traces: a fallback action's span waterfall, closed with Escape", async ({ page }, testInfo) => {
  await mockApi(page);

  await test.step("From the dashboard to a production robot", async () => {
    await page.goto("/app/configurations/hybrid");
    await expect(h1(page)).toHaveText("Bimanual station · Hybrid");
    await robotsTable(page).getByRole("link", { name: "Unit 07", exact: true }).click();
    await expect(h1(page)).toHaveText("Unit 07");
    await expect(page.locator(".cfg-page-head .portal-eyebrow")).toHaveText("Production robot");
    await milestone(page, testInfo, "J2-01-production-robot");
  });

  const button = page.getByRole("button", { name: /View trace · Close the box lid/ });
  await test.step("Actions & traces: filter Fallback", async () => {
    await expect(page.getByRole("tab", { name: /Actions & traces/ })).toHaveAttribute("aria-selected", "true");
    const filters = page.getByRole("group", { name: "Filter actions" });
    await filters.getByRole("button", { name: /^Fallback/ }).click();
    await expect(filters.getByRole("button", { name: /^Fallback/ })).toHaveAttribute("aria-pressed", "true");
    const rows = page.getByRole("table", { name: /Actions on Unit 07/ }).locator("tbody tr");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("Close the box lid");
    await expect(rows.first().locator(".cfg-path--fallback")).toBeVisible();
    await milestone(page, testInfo, "J2-02-fallback-actions");
  });

  await test.step("Open the trace drawer: the span waterfall", async () => {
    await button.click();
    const drawer = page.getByRole("dialog", { name: "Close the box lid" });
    await expect(drawer).toBeVisible();
    await expect(page).toHaveURL(/[?&]trace=trc_/);
    await expect(drawer.locator(".cfg-wf").first()).toBeVisible();
    await expect(drawer.locator(".cfg-wf__name").first()).toBeVisible();
    await expect(drawer.getByRole("heading", { name: "Inputs and outputs", exact: true })).toBeVisible();
    await expect(drawer.locator(".cfg-badges .cfg-prov--sample")).toHaveText("Sample");
    await milestone(page, testInfo, "J2-03-trace-drawer", { overlay: true });
  });

  await test.step("Close with Escape; focus returns to the trace's button", async () => {
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Close the box lid" })).toHaveCount(0);
    await expect(page).not.toHaveURL(/trace=/);
    await expect(button).toBeFocused();
  });
});

test("J3 · add a robot: production waits for a passed gate; a test robot is saved and listed", async ({ page }, testInfo) => {
  const server = await mockApi(page);

  await test.step("A configuration below its gate: Production is unavailable and says why", async () => {
    await page.goto("/app/configurations/cloud-only");
    await expect(h1(page)).toHaveText("Bimanual station · Cloud only");
    await headerButton(page, "Add robot").click();
    const dialog = page.getByRole("dialog", { name: "Add robot to Bimanual station · Cloud only" });
    await expect(dialog.getByRole("radio", { name: /^Production/ })).toBeDisabled();
    await expect(dialog).toContainText("Not available: r2 is below gate on Run 21 (Each slice family: Network 48.3 %, needs ≥ 55 %).");
    await milestone(page, testInfo, "J3-01-production-blocked", { overlay: true });
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
  });

  const dialog = page.getByRole("dialog", { name: "Add robot to Bimanual station · Hybrid" });
  await test.step("A configuration that passed: Production is available on its gate run", async () => {
    await page.goto("/app/configurations/hybrid");
    await headerButton(page, "Add robot").click();
    await dialog.getByRole("radio", { name: /^Production/ }).check();
    await expect(dialog.getByRole("status").filter({ hasText: "r4 passed gate on Run 23." })).toContainText("Overall success: 78.1 % (≥ 70 %)");
    await milestone(page, testInfo, "J3-02-production-allowed", { overlay: true });
  });

  await test.step("Choose Test and a site, then save", async () => {
    await dialog.getByRole("radio", { name: /^Test/ }).check();
    await expect(dialog.getByText("Test robots need no gate.")).toBeVisible();
    await dialog.getByLabel("Site").fill("Lab · Bench 2");
    await dialog.getByRole("button", { name: "Add robot" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.locator(".cd-flash")).toContainText("Unit 18 added to Bimanual station · Hybrid r4 as a test robot.");
    expect(server.writes).toHaveLength(1);
    expect(server.writes[0]).toMatchObject({ ifNoneMatch: "*", ifMatch: null, client: "web", status: 200 });
    expect(server.writes[0].body.robots.find(robot => robot.id === "unit-18")).toMatchObject({ configId: "hybrid", role: "test", rev: "r4", site: "Lab · Bench 2" });
  });

  await test.step("The robot appears in the robots table", async () => {
    await page.getByRole("tab", { name: /^Robots/ }).click();
    await expect(page.getByRole("tab", { name: /^Robots/ })).toContainText("7");
    const row = robotsTable(page).getByRole("row").filter({ hasText: "Unit 18" });
    await expect(row).toContainText("Test");
    await expect(row).toContainText("Not reported");
    await milestone(page, testInfo, "J3-03-robot-listed");
  });
});

test("J4 · a new revision: all six steps, the compatibility review, create", async ({ page }, testInfo) => {
  const server = await mockApi(page);

  await test.step("Edit configuration starts the next revision", async () => {
    await page.goto("/app/configurations/hybrid");
    await page.getByRole("link", { name: "Edit configuration" }).click();
    await expect(page).toHaveURL(/\/app\/configurations\/new\?from=hybrid/);
    await expect(h1(page)).toHaveText("Bimanual station · Hybrid r5");
    await milestone(page, testInfo, "J4-01-robot-step");
  });

  await test.step("Step through robot, edge hardware, edge model, cloud model, routing & safety", async () => {
    const steps: Array<[string, string]> = [["Continue to edge hardware", "Edge hardware"], ["Continue to edge model", "Edge models"], ["Continue to cloud model", "Cloud models"], ["Continue to routing & safety", "Routing & safety"], ["Continue to review", "Review"]];
    for (const [button, heading] of steps) {
      await page.getByRole("button", { name: button }).click();
      await expect(page.getByRole("heading", { level: 2, name: heading, exact: true })).toBeFocused();
    }
    await expect(page).toHaveURL(/[?&]step=review/);
  });

  await test.step("Review the compatibility check: memory fit, recorded evidence, cloud not measured", async () => {
    const compat = page.getByRole("region", { name: "Compatibility check" });
    const check = (title: string) => compat.getByRole("listitem").filter({ hasText: title });
    await expect(page.getByRole("note").filter({ hasText: "Ready to create." })).toContainText("Compatibility check: 3 pass · 1 warning · 1 blocked · 1 not measured.");
    await expect(check("Memory with every edge model resident")).toContainText("3.7 of 7.4 GiB");
    await expect(check("Board power within the 25 W mode")).toContainText("Recorded · Sep 13–14");
    await expect(check("Planner model loads and runs on this device").locator(".cfg-prov--recorded")).toBeVisible();
    await expect(check("Cloud round trip against the 350 ms fallback trigger")).toContainText("Not measured");
    await milestone(page, testInfo, "J4-02-review");
  });

  await test.step("Create the revision and land on the dashboard", async () => {
    await page.getByRole("button", { name: "Create revision r5" }).click();
    await expect(page).toHaveURL(/\/app\/configurations\/hybrid\/?$/);
    await expect(h1(page)).toHaveText("Bimanual station · Hybrid");
    await expect(page.locator(".cfg-title").getByText("Testing r5", { exact: true })).toBeVisible();
    expect(server.writes).toHaveLength(1);
    expect(server.writes[0]).toMatchObject({ ifNoneMatch: "*", status: 200 });
    const hybrid = server.writes[0].body.configurations.find(config => config.id === "hybrid")!;
    expect([hybrid.revisions.map(revision => revision.rev), hybrid.candidateRev, hybrid.productionRev]).toEqual([["r3", "r4", "r5"], "r5", "r3"]);
    await milestone(page, testInfo, "J4-03-new-revision");
  });
});

test("J5 · flag a robot with a note: the banner, the table and its page agree; then clear it", async ({ page }, testInfo) => {
  const server = await mockApi(page);

  await test.step("Flag Unit 07 with a note, as needing attention", async () => {
    await page.goto("/app/configurations/hybrid?tab=robots");
    await expect(banner(page)).toContainText("2 robots need attention:");
    await page.getByRole("button", { name: "Flag Unit 07" }).click();
    const dialog = page.getByRole("dialog", { name: "Flags · Unit 07" });
    await dialog.getByLabel("Reason").fill("Gripper slipping");
    await dialog.getByLabel("Note").fill("Slipped twice on bin 4; check the pads.");
    await dialog.getByRole("radio", { name: /^Needs attention/ }).check();
    await milestone(page, testInfo, "J5-01-flag-dialog", { overlay: true });
    await dialog.getByRole("button", { name: "Flag robot" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.locator(".cd-flash")).toContainText("Unit 07 flagged: Gripper slipping.");
  });

  await test.step("The attention banner, the robots table and the robot page say the same", async () => {
    await expect(banner(page)).toContainText("3 robots need attention:");
    await expect(banner(page).getByRole("link", { name: "Unit 07" })).toBeVisible();
    const row = robotsTable(page).getByRole("row").filter({ hasText: "Unit 07" });
    await expect(row).toContainText("Needs attention");
    await expect(row).toContainText("Gripper slipping");
    await milestone(page, testInfo, "J5-02-banner-and-table");
    await banner(page).getByRole("link", { name: "Unit 07" }).click();
    await expect(h1(page)).toHaveText("Unit 07");
    await expect(page.locator(".cfg-title").getByText("Needs attention", { exact: true })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "1 flag in effect." })).toContainText("Gripper slipping");
    await milestone(page, testInfo, "J5-03-robot-page");
  });

  await test.step("Clear the flag", async () => {
    await page.getByRole("button", { name: "Clear flag" }).click();
    const dialog = page.getByRole("dialog", { name: "Clear flag" });
    await dialog.getByRole("button", { name: "Clear flag" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("note").filter({ hasText: "Cleared" })).toContainText("Cleared “Gripper slipping” on Unit 07.");
    await expect(page.locator(".cfg-title").getByText("Healthy", { exact: true })).toBeVisible();
    await page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Bimanual station · Hybrid" }).click();
    await expect(banner(page)).toContainText("2 robots need attention:");
    await expect(banner(page).getByRole("link", { name: "Unit 07" })).toHaveCount(0);
    expect(server.writes.map(write => [write.ifNoneMatch, write.ifMatch, write.status])).toEqual([["*", null, 200], [null, "\"1\"", 200]]);
    await milestone(page, testInfo, "J5-04-flag-cleared");
  });
});

test("J6 · queue an evaluation on the bench: it is queued, and the page says no runner is connected", async ({ page }, testInfo) => {
  const server = await mockApi(page);

  await test.step("Bench robot: Run evaluation", async () => {
    await page.goto("/app/configurations/hybrid/robots/lab-bench");
    await expect(h1(page)).toHaveText("Lab bench");
    await page.getByRole("button", { name: "Run evaluation" }).click();
    const dialog = page.getByRole("dialog", { name: "Run evaluation on Lab bench" });
    await expect(dialog).toContainText("No evaluation runner is connected");
    await milestone(page, testInfo, "J6-01-run-evaluation", { overlay: true });
    await dialog.getByRole("button", { name: "Queue run" }).click();
    await expect(dialog).toBeHidden();
  });

  await test.step("Queued, with the runner not connected", async () => {
    await expect(page.getByRole("note").filter({ hasText: "is queued on Lab bench" })).toContainText("Run 26 is queued on Lab bench in the workspace document. No evaluation runner is connected, so it has not started.");
    const row = page.getByRole("table", { name: "Evaluation runs on Lab bench" }).getByRole("row").filter({ has: page.getByRole("link", { name: "Run 26", exact: true }) });
    await expect(row).toContainText("Queued");
    await expect(row).toContainText("Runner not connected");
    expect(server.writes).toHaveLength(1);
    expect(server.writes[0].body.runs.find(run => run.id === "run-26")).toMatchObject({ status: "queued", robotId: "lab-bench", counts: { episodes: 0, successes: null }, provenance: { kind: "not-reported" } });
    await milestone(page, testInfo, "J6-02-queued");
  });

  await test.step("The run page has no results to show", async () => {
    await page.getByRole("link", { name: "Open Run 26", exact: true }).click();
    await expect(h1(page)).toHaveText("Run 26 · Bimanual station suite v1");
    await expect(page.locator(".cfg-title").getByText("Queued", { exact: true })).toBeVisible();
    await expect(page.getByText("This run has not started.")).toBeVisible();
    await milestone(page, testInfo, "J6-03-queued-run");
  });
});
