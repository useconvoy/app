import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type Request } from "@playwright/test";
import type { PortalSnapshot } from "../../src/lib/portal/types";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";

// Contract fixtures only: the generic sample workspace (served as "no document yet") and a contract device.
type Device = "online" | "offline" | "unavailable";
function snapshot(device: Device): PortalSnapshot {
  const now = Date.now();
  const at = (offset = 0) => new Date(now + offset).toISOString();
  const online = device === "online";
  const telemetry = Array.from({ length: 6 }, (_, i) => ({ ts: at((i - 5) * 15000), cpu_pct: 20 + i, gpu_pct: 8, mem_total_mb: 7620, mem_available_mb: 3500 + i, power_w: 7.5 + i * 0.1, temp_max_c: 45 + i * 0.3, disk_free_mb: 20000, runtime_state: "running", clock_confidence: "unknown" }));
  return {
    fetched_at: at(), telemetry_stale_after_s: 90, heartbeat_interval_s: 15,
    device: { id: "dev_contract", name: "Contract Jetson", status: online ? "online" : "offline", live_at: at(online ? -2000 : -600_000), observed_at: at(-2000), observed_health: "ok", observed_stage: "ready", agent_version: "contract-version", observed_active_release_id: "rel_contract", gateway_mode: "production", runtime_state: "running" },
    release: { id: "rel_contract", name: "Contract model", version: "1", digest: "contract-digest", model_repo: "contract/model", model_file: "contract.gguf", runtime_name: "llama.cpp", runtime_backend: "cuda", context_window: 2048, output_limit: 128 },
    telemetry, latest_telemetry: telemetry[5],
    usage: { from: "2026-09-01", to: "2026-09-30", metrics: null },
    recent_inference: [180.5, 210.25, 650].map((latency, i) => ({ trace_id: `tr_contract_${i}`, start_ts: at(-i * 20000), status: "ok", latency_ms: latency, ttft_ms: 60, queue_ms: 0, tokens_in: 110, tokens_out: 8, tok_s: 57.5 })),
    chat: { eligible: online, online, reason: online ? null : "Device is offline.", release_id: "rel_contract", max_tokens: 128, context_window: 2048 },
  };
}

interface Mocks { device?: Device; puts?: Request[]; failPut?: boolean }
async function mock(page: Page, options: Mocks = {}) {
  let stored: unknown = null;
  const account = { user: { email: "fixture@example.test", role: "operator" }, installation: { simulator: true, dispatch_paused_at: null, quarantined_at: null } };
  await page.route("**/api/platform/auth/me", route => route.fulfill({ json: account }));
  await page.route("**/api/platform/projects", route => route.fulfill({ json: [] }));
  await page.route("**/api/platform/devices/*", route => route.fulfill({ json: { id: "dev_contract", name: "Contract Jetson", status: "online", hardware: { jetson_model: "Contract Jetson board", l4t_release: "36.4", cuda_version: "12.6" }, last_telemetry: {} } }));
  await page.route("**/api/portal/snapshot", route => options.device === "unavailable"
    ? route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "The device service is not configured." } } })
    : route.fulfill({ json: snapshot(options.device ?? "online") }));
  await page.route("**/api/platform/workspace-documents/configurations", async route => {
    const request = route.request();
    if (request.method() === "PUT") {
      options.puts?.push(request);
      if (options.failPut) return route.fulfill({ status: 500, json: { error: { message: "The workspace service is unavailable. Try again." } } });
      const payload = request.postDataJSON() as { schema_version: number; body: unknown };
      stored = payload.body;
      return route.fulfill({ json: { name: "configurations", schema_version: payload.schema_version, body: payload.body, size_bytes: request.postData()?.length ?? 0, updated_at: new Date().toISOString() } });
    }
    return stored === null
      ? route.fulfill({ status: 404, json: { error: "This resource is unavailable in your project." } })
      : route.fulfill({ json: { name: "configurations", schema_version: 1, body: stored, size_bytes: 1, updated_at: new Date().toISOString() } });
  });
}
const body = (request: Request) => (request.postDataJSON() as { body: ConvoyWorkspace }).body;
const h1 = (page: Page) => page.getByRole("heading", { level: 1 });
const tab = (page: Page, name: string | RegExp) => page.getByRole("tab", { name });
const robotsTable = (page: Page) => page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: /Planner p50/ }) });
const rowNames = (table: Locator) => table.locator("tbody th[scope=row] .cfg-row-link");
const headerButton = (page: Page, name: string) => page.locator(".cfg-actions").getByRole("button", { name, exact: true });
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true); }
async function axe(page: Page, label: string) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  expect(result.violations, `${label}: ${JSON.stringify(result.violations, null, 2)}`).toEqual([]);
}

test("overview: production KPIs, the measured device, telemetry, latency, robots, specification and logs", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations/hybrid");
  await expect(h1(page)).toHaveText("Bimanual station · Hybrid");
  await expect(page.locator(".portal-updated")).toContainText("6 robots · Test 2 · Production 4");

  const kpis = page.getByRole("region", { name: "Production KPIs" });
  await expect(kpis).toContainText("Sample · last 24 h · 4 production robots");
  await expect(kpis.locator(".cfg-kpi").filter({ hasText: "Robots reporting" })).toContainText("4of 4");
  await expect(kpis.locator(".cfg-kpi").filter({ hasText: "Edge planner p50" })).toContainText("p95 388 ms · 4 robots · 24 h");
  await expect(kpis.locator(".cfg-kpi").filter({ hasText: "Latest eval" })).toContainText("281 / 360 episodes");
  await expect(kpis.getByRole("link", { name: "Open Run 23" })).toHaveAttribute("href", "/app/configurations/hybrid/robots/lab-bench/evals/run-23");
  await expect(kpis.locator(".cfg-prov--measured")).toHaveCount(0);

  const device = page.getByRole("region", { name: "Connected device · Lab bench" });
  await expect(device.getByRole("heading", { name: "Contract model" })).toBeVisible();
  await expect(device.locator(".cfg-kpi").filter({ hasText: "Jetson SoC temperature" })).toContainText("46.5°C");
  await expect(device.locator(".cfg-kpi").filter({ hasText: "Board input power" })).toContainText("8.0W");
  await expect(device.locator(".cfg-kpi").filter({ hasText: "Memory available" })).toContainText("3,505MiB");
  await expect(device.locator(".cfg-kpi").filter({ hasText: "Planner p50" })).toContainText("p95 650 ms · newest 3 requests");
  await expect(device.locator(".cfg-kpi .cfg-prov--measured")).toHaveCount(4);
  await expect(device.locator("time").first()).toHaveAttribute("datetime", /T/);
  await expect(device.getByRole("link", { name: /Open chat/ })).toHaveAttribute("href", "/app/applications?section=device&view=chat");
  await expect(device.getByRole("link", { name: "Open Lab bench" })).toHaveAttribute("href", "/app/configurations/hybrid/robots/lab-bench");
  await expect(device.locator(".cd-evidence")).toContainText("planner p50 122 ms, p95 635 ms");

  const telemetry = page.getByRole("region", { name: "Robot telemetry" });
  await expect(telemetry.locator(".cfg-mini")).toHaveCount(12);
  await expect(telemetry.locator(".cfg-mini--flag")).toHaveCount(2);
  await expect(telemetry.locator(".cfg-mini--flag").first()).toContainText("Unit 08");
  await expect(telemetry.getByRole("img", { name: /^Lab bench Jetson SoC temperature, 6 measured samples, 45\.0–46\.5 °C; reference lines at 90 °C and 99 °C$/ })).toBeVisible();
  await expect(telemetry.getByRole("img", { name: /^Unit 13 Board input power, hourly peak over 24 h, .*; reference lines at 25 W$/ })).toBeVisible();
  await expect(telemetry).toContainText("30–105 °C on every card");

  const latency = page.locator(".portal-panel").filter({ has: page.getByRole("heading", { name: "Edge vs cloud latency" }) });
  await expect(latency.getByRole("img", { name: /^Edge planner latency, production robots, hourly over the last 24 h/ })).toBeVisible();
  await expect(latency.getByText("Fallback trigger 350 ms", { exact: true })).toBeVisible();
  await expect(latency).toContainText("Lab bench is not included");

  await expect(rowNames(robotsTable(page))).toHaveText(["Lab bench", "Unit 08", "Unit 16", "Unit 13", "Unit 02", "Unit 07"]);
  await expect(page.getByRole("heading", { name: "Model metadata" })).toBeVisible();
  await expect(page.locator(".portal-fact").filter({ hasText: "JetPack / L4T" })).toContainText("L4T 36.4 · CUDA 12.6");
  await expect(page.getByRole("log", { name: "Model logs, newest first" }).locator(".cfg-log__row")).toHaveCount(8);
});

test("tabs follow the URL, the keyboard and the browser history", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations/hybrid");
  await expect(tab(page, "Overview")).toHaveAttribute("aria-selected", "true");
  await tab(page, /^Robots/).click();
  await expect(page).toHaveURL(/\?tab=robots$/);
  await expect(page.getByRole("columnheader", { name: "Flags" })).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await expect(tab(page, "Logs")).toBeFocused();
  await expect(page).toHaveURL(/\?tab=logs$/);
  await expect(page.getByRole("log", { name: "Model logs, filtered, newest first" })).toBeVisible();
  await page.keyboard.press("End");
  await expect(tab(page, "Specification")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "Safety checks" })).toBeVisible();
  await page.keyboard.press("Home");
  await expect(tab(page, "Overview")).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/\/hybrid\/?$/);
  await page.goBack();
  await expect(tab(page, "Specification")).toHaveAttribute("aria-selected", "true");
  await page.goto("/app/configurations/hybrid?tab=logs");
  await expect(tab(page, "Logs")).toHaveAttribute("aria-selected", "true");
  // Logs: level, source and robot filters.
  const log = page.getByRole("log", { name: "Model logs, filtered, newest first" }).locator(".cfg-log__row");
  await expect(log).toHaveCount(13);
  await page.getByRole("group", { name: "Log level" }).getByRole("button", { name: /^Warn/ }).click();
  await expect(log).toHaveCount(6);
  await page.getByRole("combobox", { name: /^Source/ }).selectOption("router");
  await expect(log).toHaveCount(2);
  await page.getByRole("combobox", { name: /^Robot/ }).selectOption("unit-08");
  await expect(page.getByText("No log lines match these filters.")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(log).toHaveCount(13);
});

test("robots table filters, sorts and opens a robot", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations/hybrid");
  const table = robotsTable(page);
  const chips = page.getByRole("group", { name: "Filter robots" });
  await chips.getByRole("button", { name: /Needs attention/ }).click();
  await expect(chips.getByRole("button", { name: /Needs attention/ })).toHaveAttribute("aria-pressed", "true");
  await expect(rowNames(table)).toHaveText(["Unit 08", "Unit 16"]);
  await expect(page).toHaveURL(/robots=attention/);
  await chips.getByRole("button", { name: /^Production/ }).click();
  await expect(rowNames(table)).toHaveText(["Unit 08", "Unit 16", "Unit 13", "Unit 07"]);
  await chips.getByRole("button", { name: /^All/ }).click();
  await expect(page).not.toHaveURL(/robots=/);
  const temp = table.getByRole("columnheader", { name: /SoC temp/ });
  await temp.getByRole("button").click();
  await expect(temp).toHaveAttribute("aria-sort", "descending");
  await expect(rowNames(table)).toHaveText(["Lab bench", "Unit 08", "Unit 16", "Unit 02", "Unit 07", "Unit 13"]);
  await temp.getByRole("button").click();
  await expect(temp).toHaveAttribute("aria-sort", "ascending");
  await expect(rowNames(table)).toHaveText(["Lab bench", "Unit 13", "Unit 07", "Unit 02", "Unit 16", "Unit 08"]);
  await table.getByRole("row").filter({ hasText: "Unit 07" }).getByText("2,610 MiB").click({ force: true }); // the row link overlays the whole row
  await expect(page).toHaveURL(/\/app\/configurations\/hybrid\/robots\/unit-07\/?$/);
  await expect(h1(page)).toHaveText("Unit 07");
});

test("the attention banner links each flagged robot and the robots table", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations/hybrid");
  const banner = page.locator(".cd-banner");
  await expect(banner).toContainText("2 robots need attention:");
  await expect(banner).toContainText("1 warning:");
  await expect(banner.getByRole("link", { name: "Unit 08" })).toHaveAttribute("href", "/app/configurations/hybrid/robots/unit-08");
  await expect(banner.getByRole("link", { name: "Unit 16" })).toHaveAttribute("href", "/app/configurations/hybrid/robots/unit-16");
  await banner.getByRole("link", { name: "Unit 13" }).click();
  await expect(h1(page)).toHaveText("Unit 13");
  await page.goBack();
  await expect(h1(page)).toHaveText("Bimanual station · Hybrid");
  await banner.getByRole("link", { name: "Show in robots table" }).click();
  await expect(tab(page, /^Robots/)).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/tab=robots&robots=attention/);
  await expect(rowNames(robotsTable(page))).toHaveText(["Unit 08", "Unit 16"]);
});

test("add robot: keyboard, focus return, the production gate and the pairing steps", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts });
  await page.goto("/app/configurations/hybrid");
  const open = headerButton(page, "Add robot");
  await open.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Add robot to Bimanual station · Hybrid" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("radio", { name: /^Unit 18/ })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Add robot" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(open).toBeFocused();

  await open.click();
  await expect(dialog.getByRole("radio", { name: /^Test/ })).toBeChecked();
  await expect(dialog.getByText("Test robots need no gate.")).toBeVisible();
  await dialog.getByRole("radio", { name: /^Production/ }).check();
  await expect(dialog.getByRole("status").filter({ hasText: "r4 passed gate on Run 23." })).toContainText("Overall success: 78.1 % (≥ 70 %)");
  await dialog.getByRole("combobox", { name: /^Revision/ }).selectOption("r3");
  await expect(dialog).toContainText("r3 passed gate on Run 22.");
  await dialog.getByRole("radio", { name: /Pair a new device/ }).check();
  await expect(dialog.locator("pre")).toContainText("convoy-agent enroll");
  await expect(dialog.locator("pre")).toContainText("--server https://<convoy-host> --token <token> --name <device-name>");
  const submit = dialog.getByRole("button", { name: "Add robot" });
  await expect(submit).toHaveAttribute("aria-disabled", "true");
  await expect(dialog).toContainText("Available once the device is enrolled and registered in this workspace.");
  await submit.click({ force: true }); // aria-disabled: stays reachable and announced, but does nothing
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  expect(puts).toHaveLength(0);

  // A revision whose latest gate run did not pass cannot take production robots.
  await page.goto("/app/configurations/cloud-only");
  await headerButton(page, "Add robot").click();
  const blocked = page.getByRole("dialog", { name: "Add robot to Bimanual station · Cloud only" });
  await expect(blocked.getByRole("radio", { name: /^Production/ })).toBeDisabled();
  await expect(blocked).toContainText("Not available: r2 is below gate on Run 21 (Each slice family: Network 48.3 %, needs ≥ 55 %).");
  await blocked.getByRole("combobox", { name: /^Revision/ }).selectOption("r1");
  await expect(blocked).toContainText("Not available: r1 has no gated suite run yet.");
});

test("adding a robot writes the document with PUT, closes on success and lists the robot", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts });
  await page.goto("/app/configurations/hybrid");
  await headerButton(page, "Add robot").click();
  const dialog = page.getByRole("dialog", { name: "Add robot to Bimanual station · Hybrid" });
  await dialog.getByLabel("Site").fill("Lab · Bench 2");
  await dialog.getByRole("button", { name: "Add robot" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator(".cd-flash")).toContainText("Unit 18 added to Bimanual station · Hybrid r4 as a test robot.");
  expect(puts).toHaveLength(1);
  expect(puts[0].headers()["idempotency-key"]).toMatch(/^[\x21-\x7e]{1,128}$/);
  expect(puts[0].headers()["x-convoy-client"]).toBe("web");
  expect(body(puts[0]).robots.find(robot => robot.id === "unit-18")).toMatchObject({ configId: "hybrid", role: "test", rev: "r4", site: "Lab · Bench 2" });
  await expect(tab(page, /^Robots/)).toContainText("7");
  await expect(robotsTable(page).getByRole("row").filter({ hasText: "Unit 18" })).toContainText("Not reported");
  await expect(headerButton(page, "Add robot")).toBeFocused();
});

test("a failed save keeps the dialog open with the error and no success notice", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts, failPut: true });
  await page.goto("/app/configurations/hybrid");
  await headerButton(page, "Add robot").click();
  const dialog = page.getByRole("dialog", { name: "Add robot to Bimanual station · Hybrid" });
  await dialog.getByRole("button", { name: "Add robot" }).click();
  await expect(dialog.getByRole("alert")).toContainText("The workspace service is unavailable. Try again.");
  await expect(dialog).toBeVisible();
  await expect(page.locator(".cd-flash")).toHaveCount(0);
  expect(puts).toHaveLength(1);
  await expect(robotsTable(page).getByRole("row").filter({ hasText: "Unit 18" })).toHaveCount(0);
});

test("flags: a manual flag with a note, and clearing a stored flag", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts });
  await page.goto("/app/configurations/hybrid?tab=robots");
  await page.getByRole("button", { name: "Flag Unit 07" }).click();
  const dialog = page.getByRole("dialog", { name: "Flags · Unit 07" });
  await expect(dialog).toContainText("No flags on Unit 07.");
  await expect(dialog.getByLabel("Reason")).toBeFocused();
  await dialog.getByRole("button", { name: "Flag robot" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Give the flag a reason and a note.");
  expect(puts).toHaveLength(0);
  await dialog.getByLabel("Reason").fill("Gripper slipping");
  await dialog.getByLabel("Note").fill("Slipped twice on bin 4; check the pads.");
  await dialog.getByRole("radio", { name: /^Needs attention/ }).check();
  await dialog.getByRole("button", { name: "Flag robot" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator(".cd-flash")).toContainText("Unit 07 flagged: Gripper slipping.");
  expect(body(puts[0]).robots.find(robot => robot.id === "unit-07")?.flags[0]).toMatchObject({ rule: "manual", severity: "attention", label: "Gripper slipping", by: "fixture@example.test" });
  await expect(page.locator(".cd-banner")).toContainText("3 robots need attention:");
  await expect(page.locator(".cd-banner").getByRole("link", { name: "Unit 07" })).toBeVisible();

  await page.getByRole("button", { name: "Review 1 flag on Unit 13" }).click();
  const unit13 = page.getByRole("dialog", { name: "Flags · Unit 13" });
  await expect(unit13).toContainText("Power peaks");
  await unit13.getByRole("button", { name: "Clear flag “Power peaks”" }).click();
  await expect(unit13.getByRole("status").filter({ hasText: "Cleared" })).toHaveText("Cleared “Power peaks” on Unit 13.");
  await expect(unit13).toContainText("No flags on Unit 13.");
  expect(puts).toHaveLength(2);
  expect(body(puts[1]).robots.find(robot => robot.id === "unit-13")?.flags).toEqual([]);
  await unit13.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator(".cd-banner")).not.toContainText("warning");
});

test("promoting a revision that passed its gate is confirmed, saved and reported", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts });
  await page.goto("/app/configurations/hybrid");
  await page.getByRole("button", { name: "Promote r4 to production" }).click();
  const dialog = page.getByRole("dialog", { name: "Promote r4 to production" });
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await expect(dialog).toContainText("r3 → r4");
  await expect(dialog).toContainText("Unit 07, Unit 08, Unit 13, Unit 16");
  await expect(dialog).toContainText("It does not deploy anything to the robots.");
  await dialog.getByRole("button", { name: "Promote r4" }).click();
  await expect(dialog).toBeHidden();
  const flash = page.locator(".cd-flash");
  await expect(flash).toContainText("r4 is now the production revision of Bimanual station · Hybrid (was r3); 4 production robots are recorded on r4.");
  await expect(flash).toBeFocused();
  const saved = body(puts[0]);
  expect(saved.configurations.find(config => config.id === "hybrid")).toMatchObject({ productionRev: "r4", candidateRev: null });
  expect(saved.robots.filter(robot => robot.configId === "hybrid" && robot.role === "production").every(robot => robot.rev === "r4")).toBe(true);
  await expect(page.locator(".cfg-title").getByText("In production r4", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Promote/ })).toHaveCount(0);
});

test("run eval suite shows runs in progress and queues a run without claiming it ran", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts });
  await page.goto("/app/configurations/hybrid");
  await headerButton(page, "Run eval suite").click();
  const dialog = page.getByRole("dialog", { name: "Run eval suite" });
  await expect(dialog.getByRole("link", { name: "Run 24" })).toHaveAttribute("href", "/app/configurations/hybrid/robots/lab-bench/evals/run-24");
  await expect(dialog).toContainText("Running 212/360");
  await expect(dialog.getByRole("link", { name: "Run 23" })).toBeVisible();
  await expect(dialog).toContainText("No evaluation runner is connected to this workspace");
  await dialog.getByLabel("Purpose (optional)").fill("Candidate · seeds 11–15");
  await dialog.getByRole("button", { name: "Queue run" }).click();
  await expect(dialog).toBeHidden();
  const flash = page.locator(".cd-flash");
  await expect(flash).toContainText("Run 26 queued for r4. No evaluation runner is connected to this workspace, so it stays queued until a runner reports results.");
  await expect(flash.getByRole("link", { name: "Open Run 26" })).toHaveAttribute("href", "/app/configurations/hybrid/robots/lab-bench/evals/run-26");
  expect(body(puts[0]).runs.find(run => run.id === "run-26")).toMatchObject({ status: "queued", rev: "r4", robotId: "lab-bench", purpose: "Candidate · seeds 11–15", counts: { episodes: 0, successes: null } });
});

test("the connected device reads Not reported when the device is offline or cannot be read", async ({ page }) => {
  await mock(page, { device: "offline" });
  await page.goto("/app/configurations/hybrid");
  const device = page.getByRole("region", { name: "Connected device · Lab bench" });
  await expect(device.locator(".portal-device-board")).toContainText("Offline");
  await expect(device.locator(".cfg-kpi .portal-metric-value")).toHaveText(["Not reported", "Not reported", "Not reported", "Not reported"]);
  await expect(device.getByRole("status").filter({ hasText: "is offline" })).toContainText("Lab bench is offline (No recent live contact). Last contact");
  await expect(robotsTable(page).getByRole("row").filter({ hasText: "Lab bench" })).toContainText("Not reported");

  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mock(page, { device: "unavailable" });
  await page.reload();
  await expect(device.locator(".cfg-kpi .portal-metric-value")).toHaveText(["Not reported", "Not reported", "Not reported", "Not reported"]);
  await expect(device).toContainText("Lab bench could not be read: The device service is not configured. Its values read Not reported until the device reports.");
  await expect(device.getByRole("button", { name: "Check again" })).toBeVisible();
  await expect(page.getByRole("note").filter({ hasText: "Values marked Sample" })).toContainText("no device report is available right now");
  await expect(page.getByRole("region", { name: "Production KPIs" })).toContainText("Sample · last 24 h · 4 production robots");
});

for (const width of [1440, 390]) test(`dashboard tabs and dialogs are accessible and fit at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mock(page);
  for (const view of ["", "?tab=robots", "?tab=logs", "?tab=spec"]) {
    await page.goto(`/app/configurations/hybrid${view}`);
    await expect(h1(page)).toHaveText("Bimanual station · Hybrid");
    await page.waitForLoadState("networkidle");
    await noOverflow(page);
    await axe(page, `hybrid${view}`);
  }
  await page.goto("/app/configurations/hybrid");
  await headerButton(page, "Add robot").click();
  await page.getByRole("dialog").getByRole("radio", { name: /^Production/ }).check();
  await axe(page, "add robot dialog");
  await page.getByRole("dialog").getByRole("radio", { name: /Pair a new device/ }).check();
  await axe(page, "add robot dialog, pairing");
  await page.keyboard.press("Escape");
  await headerButton(page, "Run eval suite").click();
  await axe(page, "run eval suite dialog");
  await page.keyboard.press("Escape");
  await page.goto("/app/configurations/cloud-only");
  await expect(h1(page)).toHaveText("Bimanual station · Cloud only");
  await noOverflow(page);
  await axe(page, "cloud-only");
  await page.goto("/app/configurations/edge-only?tab=robots");
  await expect(page.getByText("No robots are attached to this configuration yet.")).toBeVisible();
  await axe(page, "edge-only robots");
});
