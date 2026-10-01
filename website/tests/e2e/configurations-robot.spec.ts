import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page, type Request } from "@playwright/test";
import type { PortalSnapshot } from "../../src/lib/portal/types";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";

// Contract fixtures only: the generic sample workspace and a contract device, no real account data.
type DeviceState = "online" | "offline" | "unavailable";
function snapshot(state: Exclude<DeviceState, "unavailable">): PortalSnapshot {
  const now = Date.now();
  const lag = state === "offline" ? 10 * 60_000 : 2000;
  const at = (offset = 0) => new Date(now - lag + offset).toISOString();
  const telemetry = Array.from({ length: 8 }, (_, i) => ({ ts: at((i - 7) * 15000), cpu_pct: 20 + i, gpu_pct: 8, mem_total_mb: 7620, mem_available_mb: 3500 + i, power_w: 7.5 + i * 0.1, temp_max_c: 45 + i * 0.3, disk_free_mb: 20000, runtime_state: "running", clock_confidence: "unknown" }));
  const online = state === "online";
  return {
    fetched_at: new Date(now).toISOString(), telemetry_stale_after_s: 90, heartbeat_interval_s: 15,
    device: { id: "dev_contract", name: "Contract Jetson", status: online ? "online" : "offline", live_at: at(), observed_at: at(), observed_health: "ok", observed_stage: "ready", agent_version: "contract-version", observed_active_release_id: "rel_contract", gateway_mode: "production", runtime_state: "running" },
    release: { id: "rel_contract", name: "Contract model", version: "1", digest: "contract-digest", model_repo: "contract/model", model_file: "contract.gguf", runtime_name: "llama.cpp", runtime_backend: "cuda", context_window: 2048, output_limit: 128 },
    telemetry, latest_telemetry: telemetry[7],
    usage: { from: "2026-09-01", to: "2026-09-30", metrics: null },
    recent_inference: [180.5, 210.25, 650].map((latency, i) => ({ trace_id: `tr_contract_${i}`, start_ts: at(-i * 20000), status: "ok", latency_ms: latency, ttft_ms: 60 + i * 10, queue_ms: 0, tokens_in: 110, tokens_out: 8, tok_s: 57.5 })),
    chat: online ? { eligible: true, online: true, reason: null, release_id: "rel_contract", max_tokens: 128, context_window: 2048 } : { eligible: false, online: false, reason: "Device is offline.", release_id: "rel_contract", max_tokens: 128, context_window: 2048 },
  };
}

interface Mocks { device?: DeviceState; document?: ConvoyWorkspace | null; puts?: Request[]; snapshotCalls?: { count: number } }
async function mock(page: Page, options: Mocks = {}) {
  let stored: unknown = options.document ?? null;
  const account = { user: { email: "fixture@example.test", role: "operator" }, installation: { simulator: true, dispatch_paused_at: null, quarantined_at: null } };
  await page.route("**/api/platform/auth/me", route => route.fulfill({ json: account }));
  await page.route("**/api/platform/projects", route => route.fulfill({ json: [] }));
  await page.route("**/api/platform/devices/*", route => route.fulfill({ json: { id: "dev_contract", name: "Contract Jetson", status: "online", hardware: { jetson_model: "Contract Jetson board", l4t_release: "36.4", cuda_version: "12.6" }, last_telemetry: {} } }));
  await page.route("**/api/portal/snapshot", route => {
    if (options.snapshotCalls) options.snapshotCalls.count++;
    return options.device === "unavailable"
      ? route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "The device connection is not configured." } } })
      : route.fulfill({ json: snapshot(options.device ?? "online") });
  });
  await page.route("**/api/platform/workspace-documents/configurations", async route => {
    const request = route.request();
    if (request.method() === "PUT") {
      options.puts?.push(request);
      const payload = request.postDataJSON() as { schema_version: number; body: unknown };
      stored = payload.body;
      return route.fulfill({ json: { name: "configurations", schema_version: payload.schema_version, body: payload.body, size_bytes: request.postData()?.length ?? 0, updated_at: new Date().toISOString() } });
    }
    return stored === null
      ? route.fulfill({ status: 404, json: { error: "This resource is unavailable in your project." } })
      : route.fulfill({ json: { name: "configurations", schema_version: 1, body: stored, size_bytes: 1, updated_at: new Date().toISOString() } });
  });
}
const BENCH = "/app/configurations/hybrid/robots/lab-bench";
const UNIT_02 = "/app/configurations/hybrid/robots/unit-02";
const UNIT_07 = "/app/configurations/hybrid/robots/unit-07";
const ESCALATED = createSampleWorkspace().traces.find(trace => trace.escalated)!.id;
const h1 = (page: Page) => page.getByRole("heading", { level: 1 });
const tile = (page: Page, name: string) => page.locator(".portal-metric-card").filter({ has: page.getByRole("heading", { name, exact: true }) });
const runRow = (page: Page, run: string) => page.getByRole("table", { name: "Evaluation runs on Lab bench" }).getByRole("row").filter({ has: page.getByRole("link", { name: run, exact: true }) });
const lastBody = (puts: Request[]) => (puts.at(-1)!.postDataJSON() as { body: ConvoyWorkspace }).body;
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true); }
async function axe(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  expect(result.violations, `${page.url()}: ${JSON.stringify(result.violations, null, 2)}`).toEqual([]);
}

test("a device-bound test robot shows measured health, edge latency and inference traces from the device", async ({ page }) => {
  await mock(page);
  await page.goto(BENCH);
  await expect(h1(page)).toHaveText("Lab bench");
  await expect(page.locator(".cfg-page-head .portal-eyebrow")).toHaveText("Test robot");
  const badges = page.locator(".cfg-title");
  await expect(badges.getByText("Healthy", { exact: true })).toBeVisible();
  await expect(badges.locator(".cfg-prov--measured")).toContainText("Measured");

  const hero = page.getByRole("region", { name: "Contract model" });
  await expect(hero).toContainText("Running release · Contract Jetson board");
  await expect(hero.getByText("Online", { exact: true })).toBeVisible();
  await expect(hero.getByText("Ready for inference")).toBeVisible();
  await expect(hero.getByRole("link", { name: "Open chat" })).toHaveAttribute("href", "/app/applications?section=device&view=chat");

  for (const [name, value] of [["CPU utilization", "27"], ["Memory available", "3,507"], ["Jetson SoC temperature", "47.1"], ["Board input power", "8.2"]] as const) {
    await expect(tile(page, name).locator(".portal-metric-value")).toContainText(value);
    await expect(tile(page, name).locator(".cfg-kpi__foot .cfg-prov--measured")).toContainText("Measured");
    await expect(tile(page, name).getByRole("img")).toHaveAttribute("aria-label", /Lab bench .+: 8 measured values/);
  }
  await expect(tile(page, "Memory available")).toContainText("of 7,620 MiB");
  await expect(tile(page, "Device latency p50").locator(".portal-metric-value")).toContainText("210");
  await expect(tile(page, "Device latency p50")).toContainText("p95 650 ms · n = 3 requests");
  await expect(tile(page, "Time to first token p50")).toContainText("n = 3");
  await expect(tile(page, "Planner latency p50 · 15-minute soak").locator(".cfg-prov--recorded")).toHaveText("Recorded · Sep 14");
  await page.getByText("Inspect telemetry samples").click();
  await expect(page.getByRole("table", { name: "Exact telemetry samples, newest first" }).getByRole("row")).toHaveCount(9);

  // The bench is test hardware: promotion is unavailable and says why.
  const promote = page.getByRole("button", { name: "Promote to production" });
  await expect(promote).toHaveAttribute("aria-disabled", "true");
  await expect(promote).toHaveAccessibleDescription(/Bench hardware runs evaluations/);
  await promote.click({ force: true });
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // Tabs: arrow keys move between them; Traces lists the device's own gateway spans.
  const evalsTab = page.getByRole("tab", { name: /Evals & sims/ });
  await expect(evalsTab).toHaveAttribute("aria-selected", "true");
  await evalsTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /Traces/ })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/\?tab=traces$/);
  const traces = page.getByRole("tabpanel", { name: /Traces/ });
  await expect(traces.getByRole("heading", { name: "Edge inference traces" })).toBeVisible();
  await expect(traces.locator(".portal-panel-heading .cfg-prov--measured")).toContainText("Measured");
  await expect(traces.getByRole("img", { name: /Measured device latency for 3 requests/ })).toBeVisible();
  await expect(traces.getByRole("table", { name: "All 3 received traces, newest first" }).getByRole("row", { name: /tr_contract_2/ })).toContainText("650.0 ms");
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "Logs" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("No log lines are stored for Lab bench. The device connection does not expose device or runtime logs.")).toBeVisible();
});

test("an offline device keeps its last measured values with their age; an unreachable one reads Not reported", async ({ page }) => {
  await mock(page, { device: "offline" });
  await page.goto(BENCH);
  await expect(page.locator(".cfg-title").getByText("Offline", { exact: true })).toBeVisible();
  const hero = page.getByRole("region", { name: "Contract model" });
  await expect(hero.getByText("No recent live contact")).toBeVisible();
  await expect(page.getByText("Device is offline.")).toBeVisible();
  await expect(tile(page, "CPU utilization").locator(".portal-metric-value")).toContainText("27");
  await expect(tile(page, "CPU utilization").locator(".cfg-prov--measured")).toContainText(/Measured · 1\d min ago/);
  await expect(page.getByRole("status").filter({ hasText: "flag in effect" })).toContainText("No recent report");
  await expect(page.getByText("Last known measurements · stale")).toBeVisible();

  await page.unrouteAll({ behavior: "ignoreErrors" });
  const snapshotCalls = { count: 0 };
  await mock(page, { device: "unavailable", snapshotCalls });
  await page.reload();
  await expect(page.getByRole("heading", { name: "No device report is available right now" })).toBeVisible();
  await expect(page.locator(".cfg-title")).toContainText("Not reported");
  for (const name of ["CPU utilization", "Memory available", "Jetson SoC temperature", "Board input power"]) await expect(tile(page, name).locator(".portal-metric-value")).toHaveText("Not reported");
  await expect(page.getByRole("note").filter({ hasText: "Values marked Sample" })).toContainText("no device report is available right now");
  const before = snapshotCalls.count;
  await page.getByRole("button", { name: "Check again" }).click();
  await expect.poll(() => snapshotCalls.count).toBeGreaterThan(before);
});

test("the evaluation table shows status, gate and evidence per run and opens the run page", async ({ page }) => {
  const document = createSampleWorkspace();
  document.meta = { ...document.meta, label: "Lab workspace", sample: false };
  document.runs = document.runs.map(run => run.id === "run-18" ? { ...run, recordedEvaluationId: "eva_contract01" } : run);
  await mock(page, { document });
  await page.goto(BENCH);
  await expect(page.getByRole("table", { name: "Evaluation runs on Lab bench" }).locator("tbody tr")).toHaveCount(8);
  const running = runRow(page, "Run 24");
  await expect(running.getByRole("progressbar", { name: "Run 24 episodes complete" })).toHaveAttribute("aria-valuenow", "212");
  await expect(running).toContainText("Running 212/360");
  await expect(running).toContainText("167 / 212 · so far");
  await expect(runRow(page, "Run 25")).toContainText("Queued");
  await expect(runRow(page, "Run 25")).toContainText("Runner not connected");
  await expect(runRow(page, "Run 23")).toContainText("Passed gate");
  await expect(runRow(page, "Run 23")).toContainText("281 / 360 · CI 73.5–82.0 %");
  await expect(runRow(page, "Run 21")).toContainText("Below gate");
  await expect(runRow(page, "Run 21")).toContainText("Each slice family: Network 48.3 % (gate ≥ 55 %)");
  await expect(runRow(page, "Run 20").locator(".cfg-prov--recorded")).toHaveText("Recorded · Sep 29");
  await expect(runRow(page, "Run 20")).toContainText("Not reported");
  // A run recorded from a real control-plane evaluation names it and links to its run page.
  const recorded = runRow(page, "Run 18");
  await expect(recorded.locator(".cfg-prov--recorded")).toHaveText("Recorded · Sep 28");
  await expect(recorded).toContainText("Evaluation eva_contract01");
  await expect(recorded.getByRole("link", { name: "Open run 18" })).toHaveAttribute("href", `${BENCH}/evals/run-18`);
  await runRow(page, "Run 23").getByRole("link", { name: "Run 23", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${BENCH}/evals/run-23/?$`));
  await expect(h1(page)).toHaveText("Run 23 · Bimanual station suite v1");
});

test("queuing an evaluation saves it with PUT and says the runner is not connected", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts });
  await page.goto(BENCH);
  const open = page.getByRole("button", { name: "Run evaluation" });
  await open.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Run evaluation on Lab bench" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Evaluation suite")).toBeFocused();
  await expect(dialog.getByLabel("Evaluation suite")).toHaveValue("station-suite-v1");
  await expect(dialog.getByLabel("Configuration revision")).toHaveValue("r4");
  await expect(dialog).toContainText("Gate: Overall success ≥ 70 %");
  await expect(dialog).toContainText("No evaluation runner is connected");
  await expect(dialog).toContainText("Run 24 is running on Lab bench (212 of 360 episodes).");
  // Escape closes without saving and returns focus.
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(open).toBeFocused();
  expect(puts).toHaveLength(0);

  await open.click();
  await dialog.getByLabel("Purpose (optional)").fill("Contract check");
  await dialog.getByRole("button", { name: "Queue run" }).click();
  await expect(dialog).toHaveCount(0);
  expect(puts).toHaveLength(1);
  expect(puts[0].headers()["idempotency-key"]).toMatch(/^[\x21-\x7e]{1,128}$/);
  const queued = lastBody(puts).runs.find(run => run.number === 26);
  expect(queued).toMatchObject({ status: "queued", robotId: "lab-bench", configId: "hybrid", rev: "r4", suiteId: "station-suite-v1", variant: "Hybrid", purpose: "Contract check", provenance: { kind: "not-reported" }, counts: { episodes: 0, successes: null } });
  await expect(open).toBeFocused();
  await expect(page.getByRole("note").filter({ hasText: "is queued on Lab bench" })).toContainText("Run 26 is queued on Lab bench in the workspace document. No evaluation runner is connected, so it has not started.");
  await expect(page.getByRole("link", { name: "Open Run 26", exact: true })).toHaveAttribute("href", `${BENCH}/evals/run-26`);
  await expect(runRow(page, "Run 26")).toContainText("Queued");
  await expect(runRow(page, "Run 26")).toContainText("Runner not connected");
  await expect(runRow(page, "Run 26")).toContainText("Not started");
});

test("promotion needs a passing gate run; a production robot can move back to test", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts });
  await page.goto(UNIT_02);
  await expect(page.locator(".cfg-page-head .portal-eyebrow")).toHaveText("Test robot");
  await expect(page.getByRole("tabpanel", { name: /Evals & sims/ })).toContainText("No evaluation runs on Unit 02 yet.");
  await page.getByRole("button", { name: "Promote to production" }).click();
  const dialog = page.getByRole("dialog", { name: "Promote Unit 02 to production" });
  await expect(dialog).toContainText("r4 passed the gate on Run 23: 78.1 % (281 / 360) · 0 critical safety events.");
  await dialog.getByRole("button", { name: "Promote to production" }).click();
  await expect(dialog).toHaveCount(0);
  expect(lastBody(puts).robots.find(robot => robot.id === "unit-02")?.role).toBe("production");
  await expect(page.locator(".cfg-page-head .portal-eyebrow")).toHaveText("Production robot");
  await expect(page.getByRole("tab", { name: /Actions & traces/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".rb-change")).toBeFocused();
  await expect(page.locator(".rb-change")).toContainText("Unit 02 is now a production robot of Bimanual station · Hybrid in the workspace document. Nothing was deployed to the robot from this page.");

  await page.getByRole("button", { name: "Move to test" }).click();
  await page.getByRole("dialog", { name: "Move Unit 02 to test" }).getByRole("button", { name: "Move to test" }).click();
  await expect(page.locator(".cfg-page-head .portal-eyebrow")).toHaveText("Test robot");
  expect(puts).toHaveLength(2);
  expect(lastBody(puts).robots.find(robot => robot.id === "unit-02")?.role).toBe("test");
});

test("flagging and clearing a flag save to the workspace document", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts });
  await page.goto(UNIT_07);
  await expect(page.getByRole("button", { name: "Clear flag" })).toHaveCount(0);
  await page.getByRole("button", { name: "Flag", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Flag Unit 07" });
  await expect(dialog.getByLabel("Reason")).toBeFocused();
  await dialog.getByRole("button", { name: "Flag robot" }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Add a reason and a note before flagging.");
  expect(puts).toHaveLength(0);
  await dialog.getByLabel("Reason").fill("Gripper noise");
  await dialog.getByLabel("Note").fill("Clicking on close; check before the next shift");
  await dialog.getByText("Needs attention").click();
  await dialog.getByRole("button", { name: "Flag robot" }).click();
  await expect(dialog).toHaveCount(0);
  expect(lastBody(puts).robots.find(robot => robot.id === "unit-07")?.flags).toMatchObject([{ rule: "manual", severity: "attention", label: "Gripper noise", note: "Clicking on close; check before the next shift", by: "fixture@example.test", provenance: { kind: "recorded" } }]);
  await expect(page.getByRole("button", { name: "Flag", exact: true })).toBeFocused();
  await expect(page.locator(".cfg-title").getByText("Needs attention")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "1 flag in effect." })).toContainText("Gripper noise · Clicking on close; check before the next shift");

  await page.getByRole("button", { name: "Clear flag" }).click();
  const clear = page.getByRole("dialog", { name: "Clear flag" });
  await expect(clear).toContainText("Flagged by fixture@example.test");
  await clear.getByRole("button", { name: "Clear flag" }).click();
  await expect(clear).toHaveCount(0);
  expect(lastBody(puts).robots.find(robot => robot.id === "unit-07")?.flags).toEqual([]);
  await expect(page.getByRole("status").filter({ hasText: "flag in effect" })).toHaveCount(0);
  await expect(page.locator(".rb-change")).toBeFocused();
});

test("a production robot's actions filter by fallback, escalation and failure", async ({ page }) => {
  await mock(page);
  await page.goto(UNIT_07);
  await expect(page.locator(".cfg-page-head .portal-eyebrow")).toHaveText("Production robot");
  await expect(page.getByRole("tab", { name: /Actions & traces/ })).toHaveAttribute("aria-selected", "true");
  const filters = page.getByRole("group", { name: "Filter actions" });
  const rows = page.getByRole("table", { name: /Actions on Unit 07/ }).locator("tbody tr");
  await expect(rows).toHaveCount(12);
  await expect(filters.getByRole("button", { name: /^All/ })).toHaveAttribute("aria-pressed", "true");
  await expect(filters.getByRole("button", { name: /^All/ })).toContainText("12");
  for (const [chip, instruction] of [["Fallback", "Close the box lid"], ["Escalated", "Unstick the drawer at station 4"], ["Failed", "Move bracket B-7 to fixture 2"]] as const) {
    const button = filters.getByRole("button", { name: new RegExp(`^${chip}`) });
    await expect(button).toContainText("1");
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText(instruction);
  }
  await filters.getByRole("button", { name: /^All/ }).click();
  await expect(rows).toHaveCount(12);
  await expect(page.locator(".rb-intro").filter({ hasText: "Planner is the plan time on the edge" })).toBeVisible();

  await page.getByRole("tab", { name: "Logs" }).click();
  await expect(page.getByRole("log", { name: "Unit 07 logs" })).toContainText("navigate(target: staging area 2) planned in 147 ms");

  await page.goto("/app/configurations/hybrid/robots/unit-08");
  await expect(page.getByText("No actions are recorded for Unit 08.")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "1 flag in effect." })).toContainText("Near thermal throttle");
  await expect(page.getByRole("button", { name: "Clear flag" })).toBeVisible();
});

test("the trace drawer opens from the table and from the URL, closes with Escape and returns focus", async ({ page }) => {
  await mock(page);
  await page.goto(UNIT_07);
  const button = page.getByRole("button", { name: /View trace · Unstick the drawer at station 4/ });
  await button.focus();
  await page.keyboard.press("Enter");
  const drawer = page.getByRole("dialog", { name: "Unstick the drawer at station 4" });
  await expect(drawer).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`\\?trace=${ESCALATED}$`));
  await expect(drawer.getByRole("button", { name: "Close trace" })).toBeFocused();
  await expect(page.locator("tr.cfg-tr--current")).toContainText("Unstick the drawer at station 4");
  for (const heading of ["Autonomy · first 5 seconds", "Escalation and recovery · whole trace", "Inputs and outputs", "Recovery", "Model versions", "Safety"]) {
    await expect(drawer.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  }
  await expect(drawer).toContainText(ESCALATED);
  await expect(drawer.locator(".cfg-badges .cfg-prov--sample")).toHaveText("Sample");
  // Focus stays inside the drawer.
  for (let i = 0; i < 4; i++) await page.keyboard.press("Shift+Tab");
  expect(await drawer.evaluate(node => node.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(page).not.toHaveURL(/trace=/);
  await expect(button).toBeFocused();

  // Back closes a drawer that was opened from the table.
  await button.click();
  await expect(drawer).toBeVisible();
  await page.goBack();
  await expect(drawer).toHaveCount(0);
  await expect(page).not.toHaveURL(/trace=/);

  // Straight from the URL: the close button returns focus to the trace's row.
  await page.goto(`${UNIT_07}?trace=${ESCALATED}`);
  await expect(drawer).toBeVisible();
  await drawer.getByRole("button", { name: "Close", exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(page).not.toHaveURL(/trace=/);
  await expect(button).toBeFocused();

  await page.goto(`${UNIT_07}?trace=trc_unknown`);
  await expect(page.getByRole("dialog", { name: "Trace not found" })).toContainText("No action trace “trc_unknown” is recorded for Unit 07.");
});

for (const width of [1440, 390]) test(`robot pages are accessible and fit at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mock(page);
  for (const [path, title] of [[BENCH, "Lab bench"], [`${BENCH}?tab=traces`, "Lab bench"], [UNIT_02, "Unit 02"], [UNIT_07, "Unit 07"], ["/app/configurations/cloud-only/robots/sim-01", "Sim 01"]] as const) {
    await page.goto(path);
    await expect(h1(page)).toHaveText(title);
    await expect(page.getByRole("tablist", { name: "Robot views" })).toBeVisible();
    await noOverflow(page);
    await axe(page);
  }
  await page.goto(`${UNIT_07}?trace=${ESCALATED}`);
  await expect(page.getByRole("dialog", { name: "Unstick the drawer at station 4" })).toBeVisible();
  await noOverflow(page);
  await axe(page);
  await page.goto(BENCH);
  await page.getByRole("button", { name: "Run evaluation" }).click();
  await expect(page.getByRole("dialog", { name: "Run evaluation on Lab bench" })).toBeVisible();
  await axe(page);
});
