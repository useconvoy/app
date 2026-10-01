import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page, type Request } from "@playwright/test";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";
import type { PortalSnapshot } from "../../src/lib/portal/types";

// Contract fixtures only: the generic sample workspace (served as "no document yet") and a contract device.
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

interface Mocks { puts?: Request[]; read?: "missing" | "unavailable"; write?: (request: Request) => Promise<{ status: number; json: unknown } | null> }
async function mock(page: Page, options: Mocks = {}) {
  let stored: unknown = null;
  const account = { user: { email: "fixture@example.test", role: "operator" }, installation: { simulator: true, dispatch_paused_at: null, quarantined_at: null } };
  await page.route("**/api/platform/auth/me", route => route.fulfill({ json: account }));
  await page.route("**/api/platform/devices/*", route => route.fulfill({ json: { id: "dev_contract", name: "Contract Jetson", status: "online", hardware: { jetson_model: "Contract Jetson board", l4t_release: "36.4" }, last_telemetry: {} } }));
  await page.route("**/api/portal/snapshot", route => route.fulfill({ json: snapshot() }));
  await page.route("**/api/platform/workspace-documents/configurations", async route => {
    const request = route.request();
    if (request.method() === "PUT") {
      options.puts?.push(request);
      const override = await options.write?.(request);
      if (override) return route.fulfill(override);
      const payload = request.postDataJSON() as { schema_version: number; body: unknown };
      stored = payload.body;
      return route.fulfill({ json: { name: "configurations", schema_version: payload.schema_version, body: payload.body, size_bytes: request.postData()?.length ?? 0, updated_at: new Date().toISOString() } });
    }
    if (stored !== null) return route.fulfill({ json: { name: "configurations", schema_version: 1, body: stored, size_bytes: 1, updated_at: new Date().toISOString() } });
    return options.read === "unavailable"
      ? route.fulfill({ status: 503, json: { error: "The workspace service is unavailable." } })
      : route.fulfill({ status: 404, json: { error: "This resource is unavailable in your project." } });
  });
}
const h1 = (page: Page) => page.getByRole("heading", { level: 1 });
const stepHeading = (page: Page, name: string) => page.getByRole("heading", { level: 2, name, exact: true });
const stepper = (page: Page) => page.getByRole("list", { name: "New configuration steps" });
const stepButton = (page: Page, label: string) => stepper(page).locator(".cfg-step").filter({ has: page.locator(".cfg-step__label").getByText(label, { exact: true }) }).getByRole("button");
const compat = (page: Page) => page.getByRole("region", { name: "Compatibility check" });
const check = (page: Page, title: string) => compat(page).getByRole("listitem").filter({ hasText: title });
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true); }
async function axe(page: Page, label: string) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  expect(result.violations, `${label}: ${JSON.stringify(result.violations, null, 2)}`).toEqual([]);
}

test("a new revision steps through all six steps with Continue, Back and the stepper", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations/new?from=hybrid");
  await expect(h1(page)).toHaveText("Bimanual station · Hybrid r5");
  await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Bimanual station · Hybrid" })).toHaveAttribute("href", "/app/configurations/hybrid");
  await expect(page.locator(".portal-updated")).toHaveText("Started from r4 · Not saved yet · Creating stores r5 in the workspace; nothing is deployed");
  await expect(stepButton(page, "Robot")).toHaveAttribute("aria-current", "step");
  await expect(stepHeading(page, "Robot")).toBeVisible();
  await expect(page.getByRole("radio", { name: /^New revision r5 of Bimanual station · Hybrid/ })).toBeChecked();
  await expect(page.getByLabel("Configuration name")).toHaveValue("Bimanual station · Hybrid");
  await expect(page.getByRole("button", { name: "Back", exact: true })).toBeDisabled();

  const steps: Array<[string, string]> = [["Continue to edge hardware", "Edge hardware"], ["Continue to edge model", "Edge models"], ["Continue to cloud model", "Cloud models"], ["Continue to routing & safety", "Routing & safety"], ["Continue to review", "Review"]];
  for (const [button, heading] of steps) {
    await page.getByRole("button", { name: button }).click();
    await expect(stepHeading(page, heading)).toBeFocused();
  }
  await expect(page).toHaveURL(/[?&]step=review/);
  await expect(stepButton(page, "Review")).toHaveAttribute("aria-current", "step");
  await expect(stepButton(page, "Robot")).toContainText("Bimanual mobile manipulator · Bimanual sim twin");
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(stepHeading(page, "Routing & safety")).toBeFocused();
  await stepButton(page, "Edge hardware").click();
  await expect(stepHeading(page, "Edge hardware")).toBeFocused();
  await expect(page.getByText("Connected device: Lab bench.")).toBeVisible();
  await expect(page.locator(".ci-device .cfg-prov--measured")).toContainText("Measured");
  await page.reload();
  await expect(stepHeading(page, "Edge hardware")).toBeVisible();
});

test("edits change the stepper and the review's compatibility check; Edit returns to a step", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations/new?from=hybrid&step=review");
  await expect(page.getByRole("note").filter({ hasText: "Ready to create." })).toContainText("Compatibility check: 3 pass · 1 warning · 1 blocked · 1 not measured.");
  await expect(check(page, "Board power within the 25 W mode")).toContainText("Recorded · Sep 13–14");
  await expect(check(page, "Cloud round trip against the 350 ms fallback trigger")).toContainText("Not measured");

  await page.getByRole("button", { name: "Edit edge hardware" }).click();
  await expect(stepHeading(page, "Edge hardware")).toBeFocused();
  await page.getByRole("radio", { name: /^15 W/ }).check();
  await expect(stepButton(page, "Edge hardware")).toContainText("Jetson Orin Nano Super 8 GB · 15 W");
  await stepButton(page, "Edge model").click();
  await page.getByRole("checkbox", { name: /^Skill pack · Short-horizon skill pack/ }).uncheck();
  await expect(page.locator(".ci-group__note").filter({ hasText: "of 7.4 GiB" })).toContainText("3.0 of 7.4 GiB · 4.4 GiB left for the system, agent and cameras");
  await stepButton(page, "Routing & safety").click();
  await page.getByLabel("Cloud RTT p95 above, ms").fill("300");
  await stepButton(page, "Review").click();

  await expect(check(page, "Board power within the 15 W mode")).toContainText("Board input has not been recorded on the Jetson Orin Nano Super 8 GB against the 15 W cap.");
  await expect(check(page, "Board power within the 15 W mode")).toContainText("Not measured");
  await expect(check(page, "Memory with every edge model resident")).toContainText("3.0 of 7.4 GiB: planner 1.2, fallback policy 1.8 GiB.");
  await expect(check(page, "Memory with every edge model resident").locator(".cfg-prov--sample")).toHaveText("Sample");
  await expect(check(page, "Cloud round trip against the 300 ms fallback trigger")).toBeVisible();
  await expect(page.getByRole("region", { name: "Edge models" })).not.toContainText("Short-horizon skill pack");
  await expect(page.getByRole("region", { name: "Routing & safety" })).toContainText("RTT p95 > 300 ms over 3 s");
  await page.getByRole("button", { name: "Edit edge models" }).click();
  await expect(stepHeading(page, "Edge models")).toBeFocused();
});

test("creating a revision saves the workspace with PUT and opens the configuration", async ({ page }) => {
  const puts: Request[] = [];
  let release: () => void = () => undefined;
  const held = new Promise<void>(resolve => { release = resolve; });
  await mock(page, { puts, write: async () => { await held; return null; } });
  await page.goto("/app/configurations/new?from=hybrid&step=review");
  await expect(page.getByRole("note").filter({ hasText: "No workspace document is stored for this account yet. Creating saves" })).toContainText("this account’s workspace document");
  await page.getByRole("button", { name: "Create revision r5" }).click();
  const creating = page.getByRole("button", { name: "Creating…" });
  await expect(creating).toHaveAttribute("aria-disabled", "true");
  await expect(h1(page)).toHaveText("Bimanual station · Hybrid r5", { timeout: 1000 });
  release();
  await expect(page).toHaveURL(/\/app\/configurations\/hybrid\/?$/);
  await expect(h1(page)).toHaveText("Bimanual station · Hybrid");
  await expect(page.locator(".cfg-title").getByText("Testing r5", { exact: true })).toBeVisible();
  expect(puts).toHaveLength(1);
  expect(puts[0].headers()["idempotency-key"]).toMatch(/^[\x21-\x7e]{1,128}$/);
  expect(puts[0].headers()["x-convoy-client"]).toBe("web");
  const body = (puts[0].postDataJSON() as { schema_version: number; body: ConvoyWorkspace }).body;
  const hybrid = body.configurations.find(config => config.id === "hybrid")!;
  expect(hybrid.revisions.map(revision => revision.rev)).toEqual(["r3", "r4", "r5"]);
  expect([hybrid.candidateRev, hybrid.productionRev]).toEqual(["r5", "r3"]);
  expect(hybrid.revisions[2].compatibility.map(item => item.id)).toEqual(["planner-fit", "planner-4b", "policy-rate", "cloud-rtt", "power", "memory"]);
  expect(body.activity[0]).toMatchObject({ kind: "configuration-created", message: "Bimanual station · Hybrid r5 created from r4", provenance: { kind: "recorded" } });

  await page.goto("/app/configurations");
  await expect(page.getByText("No workspace document is stored", { exact: false })).toHaveCount(0);
  await expect(page.locator(".cfg-card").filter({ hasText: "Bimanual station · Hybrid" })).toContainText("Testing r5");
  await expect(page.getByRole("list", { name: "Recent activity, newest first" }).getByRole("listitem").first()).toContainText("Bimanual station · Hybrid r5 created from r4");
});

test("a new configuration needs a name; route problems point to the step that fixes them", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts });
  await page.goto("/app/configurations/new");
  await expect(h1(page)).toHaveText("New configuration");
  await expect(page.getByRole("radio", { name: /^New configuration/ })).toBeChecked();
  await expect(page.getByLabel("Configuration name")).toHaveValue("");
  await expect(page.getByText("Name the configuration.")).toHaveCount(0);
  await stepButton(page, "Review").click();
  await expect(page.getByText("Not ready to create.")).toBeVisible();
  const create = page.getByRole("button", { name: "Create configuration" });
  await expect(create).toHaveAttribute("aria-disabled", "true");
  // aria-disabled keeps the button focusable and clickable; a click only reveals what to fix.
  await create.click({ force: true });
  expect(puts).toHaveLength(0);
  await page.getByRole("button", { name: "Step 01 · Robot" }).click();
  await expect(page.getByLabel("Configuration name")).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByText("Name the configuration.")).toBeVisible();
  await page.getByLabel("Configuration name").fill("Bimanual station · Cloud only");
  await expect(page.getByText("Another configuration is already called “Bimanual station · Cloud only”.")).toBeVisible();
  await page.getByLabel("Configuration name").fill("Bimanual station · Lab edge");
  await expect(h1(page)).toHaveText("Bimanual station · Lab edge");
  await expect(page.getByLabel("Configuration name")).not.toHaveAttribute("aria-invalid", "true");

  await stepButton(page, "Routing & safety").click();
  await page.getByRole("radio", { name: /^Edge only/ }).check();
  await expect(page.getByText("Edge only needs an on-device motor policy: choose one in step 03.")).toBeVisible();
  await expect(page.getByText("Edge only uses no cloud models: set them to None in step 04.")).toBeVisible();
  await page.getByRole("button", { name: "Go to step 04 · Cloud models" }).click();
  await expect(stepHeading(page, "Cloud models")).toBeFocused();
  await expect(page.getByRole("note").filter({ hasText: "The route in step 05 is Edge only" })).toBeVisible();
  await page.getByRole("radio", { name: /^No cloud policy/ }).check();
  await page.getByRole("radio", { name: /^No verifier/ }).check();
  await stepButton(page, "Routing & safety").click();
  await page.getByRole("button", { name: "Go to step 03 · Edge models" }).click();
  await page.getByRole("checkbox", { name: /^Policy · SmolVLA/ }).check();
  await page.getByRole("checkbox", { name: /^Fallback policy · SmolVLA/ }).uncheck();
  await page.getByRole("checkbox", { name: /^Skill pack/ }).uncheck();
  await stepButton(page, "Review").click();
  await expect(page.getByRole("note").filter({ hasText: "Ready to create." })).toBeVisible();
  await expect(check(page, "On-device policy at the control rate")).toContainText("Recorded · Sep 29");
  await expect(compat(page).getByText("Cloud round trip", { exact: false })).toHaveCount(0);
  await create.click();
  await expect(page).toHaveURL(/\/app\/configurations\/bimanual-station-lab-edge\/?$/);
  await expect(h1(page)).toHaveText("Bimanual station · Lab edge");
  await expect(page.locator(".cfg-title").getByText("Draft r1", { exact: true })).toBeVisible();
  const body = (puts[0].postDataJSON() as { body: ConvoyWorkspace }).body;
  const created = body.configurations.find(config => config.id === "bimanual-station-lab-edge")!;
  expect(created.revisions[0].routing.mode).toBe("edge-only");
  expect(created.revisions[0].cloudModels).toEqual([]);
  expect(created.revisions[0].edgeModels.map(model => `${model.role}:${model.shortName}`)).toEqual(["planner:Qwen2.5-1.5B planner", "policy:SmolVLA policy"]);
});

test("a failed save keeps the draft and says why; trying again works", async ({ page }) => {
  let fail = true;
  await mock(page, { write: async () => fail ? { status: 500, json: { error: { message: "The workspace service is unavailable. Try again." } } } : null });
  await page.goto("/app/configurations/new?from=hybrid&step=review");
  await page.getByRole("button", { name: "Create revision r5" }).click();
  await expect(page.locator("#ci-save-error")).toContainText("Not saved. The workspace service is unavailable. Try again.");
  await expect(page).toHaveURL(/step=review/);
  const create = page.getByRole("button", { name: "Create revision r5" });
  await expect(create).not.toHaveAttribute("aria-disabled", "true");
  await expect(create).toHaveAttribute("aria-describedby", "ci-save-error");
  fail = false;
  await create.click();
  await expect(page).toHaveURL(/\/app\/configurations\/hybrid\/?$/);
});

test("an unreadable stored workspace cannot be overwritten from the form", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts, read: "unavailable" });
  await page.goto("/app/configurations/new?from=hybrid&step=review");
  await expect(page.getByText("The stored workspace could not be read, so changes are not saved.")).toBeVisible();
  const create = page.getByRole("button", { name: "Create revision r5" });
  await expect(create).toHaveAttribute("aria-disabled", "true");
  await expect(page.locator("#ci-create-hint")).toHaveText("This workspace cannot be saved right now; see the notice at the top of the page.");
  await create.click({ force: true });
  await expect(create).toHaveText("Create revision r5");
  expect(puts).toHaveLength(0);
});

test("the form works from the keyboard", async ({ page }) => {
  await mock(page);
  await page.goto("/app/configurations/new?from=hybrid");
  await page.getByRole("radio", { name: /^New revision r5/ }).focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("radio", { name: /^New configuration/ })).toBeChecked();
  await expect(h1(page)).toHaveText("New configuration");
  await page.getByLabel("Configuration name").focus();
  await page.keyboard.type("Keyboard station");
  await expect(h1(page)).toHaveText("Keyboard station");
  await page.getByRole("button", { name: "Continue to edge hardware" }).focus();
  await page.keyboard.press("Enter");
  await expect(stepHeading(page, "Edge hardware")).toBeFocused();
  await page.getByRole("radio", { name: /^25 W/ }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("radio", { name: /^MAXN SUPER/ })).toBeChecked();
  await stepButton(page, "Review").focus();
  await page.keyboard.press("Enter");
  await expect(stepHeading(page, "Review")).toBeFocused();
  await expect(check(page, "Board power within the MAXN SUPER mode")).toContainText("Warning");
});

for (const width of [1440, 390]) test(`every step is accessible and fits at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mock(page);
  for (const step of ["robot", "hardware", "edge", "cloud", "routing", "review"]) {
    await page.goto(`/app/configurations/new?from=hybrid&step=${step}`);
    await expect(h1(page)).toHaveText("Bimanual station · Hybrid r5");
    await expect(page.locator(".ci-main h2").first()).toBeVisible();
    await noOverflow(page);
    await axe(page, `${step} ${width}`);
  }
  await page.goto("/app/configurations/new?step=review");
  await expect(page.getByText("Not ready to create.")).toBeVisible();
  await noOverflow(page);
  await axe(page, `blank review ${width}`);
});
