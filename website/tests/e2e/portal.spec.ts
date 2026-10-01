import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import type { PortalChatRequest, PortalSnapshot } from "../../src/lib/portal/types";

// Contract fixtures only. Production code never imports these measurements or credentials.
function snapshot(): PortalSnapshot {
  const now = Date.now();
  const at = (offset = 0) => new Date(now + offset).toISOString();
  const telemetry = Array.from({ length: 8 }, (_, i) => ({ ts: at((i - 7) * 10000), cpu_pct: i === 5 ? null : 10 + i, gpu_pct: null, mem_total_mb: 7620, mem_available_mb: 3400 + i, power_w: 6.4 + i * .1, temp_max_c: 42 + i * .5, disk_free_mb: 20000, runtime_state: "ready", clock_confidence: "ntp" }));
  return {
    fetched_at: at(), telemetry_stale_after_s: 60, heartbeat_interval_s: 10,
    device: { id: "dev_contract", name: "Contract Jetson", status: "online", live_at: at(), observed_at: at(), observed_health: "healthy", observed_stage: "ready", agent_version: "contract-version", observed_active_release_id: "rel_contract", gateway_mode: "production", runtime_state: "ready" },
    release: { id: "rel_contract", name: "Contract model", version: "1", digest: "contract-digest", model_repo: "contract/model", model_file: "contract.gguf", runtime_name: "llama.cpp", runtime_backend: "cuda", context_window: 2048, output_limit: 128 },
    telemetry, latest_telemetry: telemetry[7],
    usage: { from: "2026-09-01", to: "2026-09-14", metrics: { inference_requests: 17, tokens_in: 403, tokens_out: 51, inference_minutes: .8, runtime_up_minutes: 30, agent_up_minutes: 31, unknown_coverage_s: null, contacts: 7, reconnects: 0 } },
    recent_inference: [{ trace_id: "tr_contract", start_ts: at(-1000), status: "ok", latency_ms: 245.2, ttft_ms: 180, queue_ms: 0, tokens_in: 40, tokens_out: 3, tok_s: null }],
    chat: { eligible: true, online: true, reason: null, release_id: "rel_contract", max_tokens: 128, context_window: 2048 },
  };
}
async function mockSession(page: Page, data = snapshot(), authenticated = true) {
  const account = { user: { email: "fixture@example.test", role: "operator" }, installation: { simulator: true, dispatch_paused_at: null, quarantined_at: null } };
  await page.route("**/api/platform/auth/me", route => route.fulfill({ status: authenticated ? 200 : 401, json: authenticated ? account : { error: "Sign in" } }));
  await page.route("**/api/platform/auth/login", async route => { authenticated = true; await route.fulfill({ json: { user: account.user } }); });
  await page.route("**/api/platform/auth/logout", async route => { authenticated = false; await route.fulfill({ json: { ok: true } }); });
  await page.route("**/api/platform/projects", route => route.fulfill({ json: [] }));
  await page.route("**/api/portal/snapshot", (route) => route.fulfill({ json: data }));
  await page.route("**/api/platform/workspace-documents/configurations", route => route.fulfill({ status: 404, json: { error: "This resource is unavailable in your project." } }));
  await page.route("**/api/platform/devices/*", route => route.fulfill({ json: { id: data.device.id, name: data.device.name, status: data.device.status, hardware: {}, last_telemetry: {} } }));
}
async function navigate(page: Page, name: string) { await page.getByRole("navigation", { name: "Device tools", exact: true }).getByRole("link", { name: new RegExp(name === "Device" ? "Connection" : name) }).click(); }
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true); }

test("portal metadata stays out of search indexing", async ({ request }) => {
  const html = await (await request.get("/portal")).text();
  expect(html).toContain('name="robots" content="noindex, nofollow"');
  expect(html).toContain('name="googlebot" content="noindex, nofollow"');
  expect(html).toContain('<title>Convoy | Workspace</title>');
});

test("landing opens one workspace; one login covers configurations, applications and device tools", async ({ page }) => {
  await mockSession(page, snapshot(), false);
  await page.goto("/");
  await page.locator(".hero-actions").getByRole("link", { name: "Open demo" }).click();
  await expect(page).toHaveURL(/\/app\/configurations\/?$/);
  await expect(page.getByRole("heading", { name: "One workspace for your robots." })).toBeVisible();
  await page.getByLabel("Email", { exact: true }).fill("fixture@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fixture-only");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Configurations" })).toBeVisible();
  // Applications stays reachable by its URL on the same session; Configurations never links to it.
  await expect(page.locator('a[href*="/app/applications"], a[href*="/portal"]')).toHaveCount(0);
  await page.goto("/app/applications");
  await expect(page.getByRole("heading", { name: "Build. Deploy. Observe." })).toBeVisible();
  await page.getByRole("navigation", { name: "Robot applications", exact: true }).getByRole("link", { name: "Device connection" }).click();
  await expect(page.getByRole("heading", { name: "Your device, observed." })).toBeVisible();
  await expect(page.getByText("Contract Jetson", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "One workspace for your robots." })).toBeVisible();
});

test("device, usage and traces retain measurement provenance and exact data", async ({ page }) => {
  const data = snapshot(); data.latest_telemetry!.power_w = null;
  data.recent_inference.push({ ...data.recent_inference[0], trace_id: "tr_contract_second", latency_ms: 354.8 });
  await mockSession(page, data);
  await page.goto("/portal");
  await expect(page.locator(".portal-metric-card").filter({ has: page.getByRole("heading", { name: "Power", exact: true }) })).toContainText("Not reported");
  await page.getByText("Inspect telemetry samples").click();
  await expect(page.getByRole("table", { name: "Exact telemetry samples, newest first" })).toBeVisible();
  await navigate(page, "Usage");
  await expect(page.getByRole("heading", { name: "Measured on the device." })).toBeVisible();
  await expect(page.locator(".portal-usage-counts")).toContainText("403");
  await expect(page.getByText("Contract Jetson only", { exact: true })).toBeVisible();
  await navigate(page, "Traces");
  await expect(page.locator(".portal-trace-summary").getByText("300 ms", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "tr_contract", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Trace detail" })).toBeVisible();
  await expect(page.locator(".portal-trace-facts")).toContainText("245.2 ms");
  await expect(page.locator(".portal-trace-facts")).toContainText("Not reported");
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Measured on the device." })).toBeVisible();
});

test("chat uses real request states, reconciles uncertain submit, preserves context and separates clocks", async ({ page }) => {
  await mockSession(page);
  let posts = 0; let polls = 0; let id = "";
  const submitted: unknown[] = [];
  await page.route("**/api/portal/chat", async (route) => {
    posts++;
    const body = route.request().postDataJSON(); submitted.push(body); id = body.request_id;
    if (posts === 1) await route.fulfill({ status: 502, json: { error: { code: "relay", message: "Connection lost" } } });
    else await route.fulfill({ status: 202, json: result("queued") });
  });
  const result = (status: PortalChatRequest["status"]): PortalChatRequest => ({ id, device_id: "dev_contract", release_id: "rel_contract", status, created_at: new Date().toISOString(), expires_at: null, content: status === "succeeded" ? "Cedar" : null, finish_reason: status === "succeeded" ? "stop" : null, usage: status === "succeeded" ? { prompt_tokens: 41, completion_tokens: 3, total_tokens: 44 } : null, metrics: status === "succeeded" ? { latency_ms: 245.2, ttft_ms: null, queue_ms: 0 } : null, trace_id: status === "succeeded" ? "tr_contract" : null, error: null });
  await page.route("**/api/portal/chat/*", async (route) => { polls++; await route.fulfill({ json: result(polls === 1 ? "running" : "succeeded") }); });
  await page.goto("/portal?view=chat");
  await page.getByLabel("Message", { exact: true }).fill("My rover is named Cedar. Reply only with its name.");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Running on device", { exact: true })).toBeVisible();
  await expect(page.locator(".portal-answer")).toHaveText("Cedar");
  expect(posts).toBe(1);
  await expect(page.locator(".portal-result-metrics")).toContainText("Browser round trip");
  await expect(page.locator(".portal-result-metrics")).toContainText("On-device latency");
  await expect(page.locator(".portal-result-metrics")).toContainText("245.2 ms");
  await navigate(page, "Device"); await navigate(page, "Chat");
  await expect(page.locator(".portal-answer")).toHaveText("Cedar");
  await page.getByLabel("Message", { exact: true }).fill("What is my rover named?");
  await page.getByLabel("Message", { exact: true }).press("Control+Enter");
  await expect(page.locator(".portal-answer")).toHaveCount(2);
  expect(submitted[1]).toMatchObject({ expected_release_id: "rel_contract", max_tokens: 128, messages: [{ role: "user", content: "My rover is named Cedar. Reply only with its name." }, { role: "assistant", content: "Cedar" }, { role: "user", content: "What is my rover named?" }] });
  await page.getByRole("button", { name: "Inspect trace" }).first().click();
  await expect(page.getByRole("heading", { name: "Trace detail" })).toBeVisible();
});

test("missing request recovery and offline states never fabricate an answer", async ({ page }) => {
  await mockSession(page);
  let posts = 0;
  await page.route("**/api/portal/chat", async (route) => { posts++; await route.fulfill({ status: 502, json: { error: { code: "lost", message: "Lost" } } }); });
  await page.route("**/api/portal/chat/*", (route) => route.fulfill({ status: 404, json: { error: { code: "missing", message: "Request not found" } } }));
  await page.goto("/portal?view=chat");
  await page.getByLabel("Message", { exact: true }).fill("Hello");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByRole("button", { name: "Resume checking" })).toBeVisible();
  await page.getByRole("button", { name: "Resume checking" }).click();
  await expect(page.getByRole("button", { name: "Resume checking" })).toBeVisible();
  expect(posts).toBe(1);
  await expect(page.locator(".portal-answer")).toHaveCount(0);
  await expect(page.getByLabel("Message", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(page.getByLabel("Message", { exact: true })).toBeEnabled();
  const offline = snapshot(); offline.chat.eligible = false; offline.chat.online = false; offline.chat.reason = "Device is offline.";
  await page.route("**/api/portal/snapshot", (route) => route.fulfill({ json: offline }));
  await page.getByRole("button", { name: "Test connection", exact: true }).click();
  await page.getByLabel("Message", { exact: true }).fill("Hello again");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
  await expect(page.getByText("Device is offline.", { exact: true }).last()).toBeVisible();
});

test("a skewed browser wall clock does not stale a freshly received snapshot or block chat", async ({ page }) => {
  await page.clock.setFixedTime(new Date(Date.now() + 5 * 60 * 1000));
  await mockSession(page);
  await page.goto("/portal?view=chat");
  await page.getByLabel("Message", { exact: true }).fill("Hello");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeEnabled();
  await expect(page.locator(".portal-topbar .portal-badge")).toHaveText("Online");
  await page.waitForTimeout(1200);
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeEnabled();
  await expect(page.locator(".portal-updated")).not.toContainText("stale");
});

test("recent history ingestion cannot turn stale live contact into a green online badge", async ({ page }) => {
  const data = snapshot();
  data.device.status = "online";
  data.device.live_at = new Date(Date.now() - 5 * 60 * 1000).toISOString();
  data.chat.online = false; data.chat.eligible = false; data.chat.reason = "No recent live report.";
  await mockSession(page, data);
  await page.goto("/portal");
  await expect(page.locator(".portal-topbar .portal-badge")).toHaveText("No recent live contact");
  await expect(page.locator(".portal-topbar .portal-badge")).not.toHaveClass(/portal-badge-success/);
  await navigate(page, "Chat");
  await page.getByLabel("Message", { exact: true }).fill("Hello");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
});

for (const state of ["retired", "credential_revoked"]) test(`device identity state ${state} overrides a previously live report`, async ({ page }) => {
  const data = snapshot(); data.device.status = state;
  await mockSession(page, data);
  await page.goto("/portal");
  await expect(page.locator(".portal-topbar .portal-badge")).toHaveText(state.replaceAll("_", " "));
  await expect(page.locator(".portal-topbar .portal-badge")).not.toHaveClass(/portal-badge-success/);
});

test("expired request stays a failure and conversation limits prevent submission", async ({ page }) => {
  await mockSession(page);
  let posts = 0;
  await page.route("**/api/portal/chat", async (route) => {
    posts++;
    const body = route.request().postDataJSON();
    await route.fulfill({ status: 202, json: { id: body.request_id, device_id: "dev_contract", release_id: "rel_contract", status: "expired", created_at: null, expires_at: null, content: null, finish_reason: null, usage: null, metrics: null, trace_id: null, error: { code: "expired", message: "The request deadline passed." } } });
  });
  await page.goto("/portal?view=chat");
  await page.getByLabel("Message", { exact: true }).fill("Hello");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Request expired.", { exact: false })).toBeVisible();
  await expect(page.locator(".portal-answer")).toHaveCount(0);
  await page.getByRole("button", { name: "Edit message", exact: true }).click();
  await expect(page.getByLabel("Message", { exact: true })).toHaveValue("Hello");
  await page.getByLabel("Message", { exact: true }).fill("x".repeat(8193));
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
  await expect(page.getByText("This conversation exceeds 8 KiB.", { exact: false })).toBeVisible();
  expect(posts).toBe(1);
});

test("portal text enlargement preserves access to navigation and controls", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockSession(page);
  await page.goto("/portal");
  await expect(page.getByRole("heading", { name: "Your device, observed." })).toBeVisible();
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  for (const view of ["Device", "Chat", "Usage", "Traces"]) { await navigate(page, view); await noOverflow(page); }
});

for (const width of [1440, 768, 390, 320]) test(`portal is accessible and fits at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mockSession(page);
  await page.goto("/portal");
  await expect(page.getByRole("heading", { name: "Your device, observed." })).toBeVisible();
  for (const view of ["Device", "Chat", "Usage", "Traces"]) {
    await navigate(page, view);
    await noOverflow(page);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
    expect(result.violations, JSON.stringify(result.violations, null, 2)).toEqual([]);
    if (width === 1440 || width === 390) await page.screenshot({ path: `tests/screenshots/portal-${view.toLowerCase()}-${width}.png`, fullPage: true });
  }
});

test("legacy links share the workspace session, preserve view and sign out everywhere", async ({ page }) => {
  await mockSession(page);
  await page.goto("/app/device?view=chat");
  await expect(page).toHaveURL(/\/app\/applications\?section=device&view=chat$/);
  await expect(page.getByRole("heading", { name: "Talk to your model." })).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "Robot applications", exact: true });
  await navigation.getByRole("link", { name: "Applications", exact: true }).click();
  await expect(page.getByLabel("New project name")).toBeVisible();
  await page.getByLabel("New project name").fill("Keep my draft");
  await navigation.getByRole("link", { name: "Device connection" }).click();
  await expect(page.getByRole("heading", { name: "Talk to your model." })).toBeVisible();
  await page.goBack();
  await expect(page.getByLabel("New project name")).toHaveValue("Keep my draft");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "One workspace for your robots." })).toBeVisible();
  await page.goto("/portal?view=usage");
  await expect(page.getByRole("heading", { name: "One workspace for your robots." })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Measured on the device." })).toHaveCount(0);
});

test("session expiry closes both sections and viewers cannot send a test message", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/platform/auth/me", route => route.fulfill({ json: { user: { email: "viewer@example.test", role: "viewer" }, installation: { simulator: true } } }));
  await page.goto("/portal?view=chat");
  await page.getByLabel("Message", { exact: true }).fill("Hello");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
  await expect(page.getByText("An operator account is required to send a test message.")).toBeVisible();
  await page.route("**/api/portal/snapshot", route => route.fulfill({ status: 401, json: { error: { code: "authentication_required", message: "Sign in" } } }));
  await page.getByRole("button", { name: "Test connection", exact: true }).click();
  await expect(page.getByRole("heading", { name: "One workspace for your robots." })).toBeVisible();
  await expect(page.getByLabel("Message", { exact: true })).toHaveCount(0);
});
