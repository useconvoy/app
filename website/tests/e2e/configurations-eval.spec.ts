import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page, type Request } from "@playwright/test";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import type { ConvoyWorkspace } from "../../src/lib/configurations/types";

// Contract fixtures only: the generic sample workspace and contract episode/evaluation ids, no real account data.
const RUN = "/app/configurations/hybrid/robots/lab-bench/evals";
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
const EPISODE = "epi_contract01", RUN_EPISODE = "epi_contract02", EVALUATION = "eva_contract01";

interface Mocks { document?: ConvoyWorkspace | null; puts?: Request[]; frames?: string[]; missingEpisode?: string }
async function mock(page: Page, options: Mocks = {}) {
  let stored: unknown = options.document ?? null;
  const account = { user: { email: "fixture@example.test", role: "operator" }, installation: { simulator: true, dispatch_paused_at: null, quarantined_at: null } };
  await page.route("**/api/platform/auth/me", route => route.fulfill({ json: account }));
  await page.route("**/api/platform/projects", route => route.fulfill({ json: [] }));
  await page.route("**/api/platform/devices/*", route => route.fulfill({ status: 503, json: { error: "The management service is unavailable." } }));
  await page.route("**/api/portal/snapshot", route => route.fulfill({ status: 503, json: { error: "unavailable" } }));
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
  await page.route(/\/api\/platform\/episodes\/epi_contract\d+\/replay$/, route => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2)!;
    if (id === options.missingEpisode) return route.fulfill({ status: 404, json: { error: "This resource is unavailable in your project." } });
    return route.fulfill({ json: { episode_id: id, mission_id: "mis_contract01", release_digest: "contract-release-digest-0001", steps: 54, skill: "pick_place_puck", planner_ms: 917.78, wall_seconds: 41.21, sim_seconds: 0.675, source: "Recorded coordinator camera observations and applied actions" } });
  });
  await page.route(/\/api\/platform\/episodes\/epi_contract\d+\/replay\/frames\/\d+$/, route => {
    const index = Number(route.request().url().split("/").pop());
    options.frames?.push(String(index));
    return route.fulfill({ json: { index, image_png_base64: PNG, action: index ? [0.12, -0.4, 0.05, 1] : null, reward: index ? 0.25 : null, success: index ? false : null, policy_ms: index ? 512.3 : null } });
  });
  await page.route(`**/api/platform/evaluations/${EVALUATION}`, route => route.fulfill({ json: {
    id: EVALUATION, project_id: "prj_contract", suite_id: "esu_contract", release_id: "apr_contract", robot_id: "rob_contract", state: "completed", detail: "all allocated cases have terminal outcomes", updated_at: "2026-10-01T04:00:39Z",
    cases: [{ seed: 0, mission_id: "mis_contract01", episode_id: EPISODE }],
    report: { passed: true, successes: 1, min_successes: 1, case_count: 1, median_wall_s: 41.21, suite_digest: "suite-digest", release_digest: "release-digest", scope: "lockstep_simulation; no hardware or real-time qualification",
      cases: [{ seed: 0, mission_id: "mis_contract01", episode_id: EPISODE, state: "completed", passed: true, evidence_valid: true, steps: 54, wall_duration_s: 41.21 }] },
  } }));
}

/** The sample with links to recorded evidence: a rollout with an episode, and a run with an episode and an evaluation. */
function documentWithRecordings(): ConvoyWorkspace {
  const ws = createSampleWorkspace();
  ws.meta = { ...ws.meta, label: "Lab workspace", sample: false };
  const rollout = ws.rollouts.find(item => item.id === "ep-23-044")!;
  rollout.episodeId = EPISODE;
  rollout.provenance = { kind: "recorded", at: "2026-10-01T04:00:39Z", n: 1, source: `Control-plane episode ${EPISODE}` };
  const run = ws.runs.find(item => item.id === "run-18")!;
  run.recordedEpisodeId = RUN_EPISODE;
  run.recordedEvaluationId = EVALUATION;
  return ws;
}

const h1 = (page: Page) => page.locator("h1:visible");
const tile = (page: Page, label: string) => page.locator(".cfg-kpi").filter({ has: page.getByRole("heading", { name: label }) });
const rolloutRows = (page: Page) => page.getByRole("table", { name: /rollouts stored for Run/ }).locator("tbody tr");
const position = (page: Page) => page.locator(".cfg-transport__pos");
/** A toolbar select by its visible label (the label wraps the select, so its text includes the options). */
const field = (page: Page, label: string) => page.locator("label.cfg-field").filter({ hasText: new RegExp(`^${label}`) }).locator("select");
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true); }

test("a finished run shows success with n and CI, safety by check, episode time on its clock, the gate and per-task results", async ({ page }) => {
  await mock(page);
  await page.goto(`${RUN}/run-23`);
  await expect(h1(page)).toHaveText("Run 23 · Bimanual station suite v1");
  await expect(page.locator(".cfg-topbar").getByText("Test", { exact: true })).toBeVisible();
  const success = tile(page, "Success rate");
  await expect(success.locator(".portal-metric-value")).toHaveText("78.1%");
  await expect(success).toContainText("281 of 360 episodes · gate 70 %");
  await expect(success).toContainText("95 % CI 73.5–82.0 %");
  await expect(success).toContainText("2.8 pts vs r3 (75.3 %)");
  await expect(success.locator(".cfg-prov--sample")).toHaveText("Sample");
  const safety = tile(page, "Safety violations");
  await expect(safety.locator(".portal-metric-value")).toHaveText("0.8per 100 episodes");
  await expect(safety).toContainText("3 episodes with a violation · 0 critical · 1 major · 2 minor");
  await expect(safety.getByRole("img")).toHaveAccessibleName("Safety violations by check over 360 episodes: S1 0, S2 1, S3 0, S4 2, S5 0, S6 0");
  const time = tile(page, "Median episode time");
  await expect(time.locator(".portal-metric-value")).toHaveText("15.6s simulated");
  await expect(time).toContainText("p90 26.2 s · n = 360 episodes");
  const edge = tile(page, "Edge planner latency · Lab bench");
  await expect(edge.locator(".portal-metric-value")).toHaveText("122ms p50");
  await expect(edge.locator(".cfg-prov--recorded")).toHaveText("Recorded · Sep 14");
  await expect(edge).toContainText("Recorded on bench hardware in Run 19, not during this run");
  await expect(edge.getByRole("link", { name: "Run 19 · Edge latency soak" })).toHaveAttribute("href", `${RUN}/run-19`);

  const gate = page.getByRole("region", { name: "Promotion gate passed" });
  await expect(gate).toContainText("r4 candidate vs production r3 (Run 22) · 6 criteria");
  await expect(gate.getByText("6 of 6 pass")).toBeVisible();
  await expect(gate.getByRole("list", { name: "Gate criteria" }).getByRole("listitem")).toHaveCount(6);
  await expect(gate.getByRole("listitem").filter({ hasText: "Critical safety events: none" })).toContainText("Pass");

  const tasks = page.getByRole("table", { name: /Success per task/ });
  const shaft = tasks.getByRole("row", { name: /Shaft insertion/ });
  await expect(shaft).toContainText("68.3 %");
  await expect(shaft).toContainText("41 / 60");
  await expect(shaft).toContainText("−1.7");
  await tasks.getByRole("button", { name: /Rate/ }).click();
  await expect(tasks.locator("tbody tr").first()).toContainText("Shaft insertion");
  await expect(page.getByRole("list", { name: "Failure modes, 79 of 360 episodes failed" }).getByRole("listitem").first()).toContainText("Grasp lost27 · 34 %");
  await expect(page.getByRole("list", { name: /Safety events by check/ }).getByRole("listitem").filter({ hasText: "S4 Dropped object" })).toContainText("2 · minor");

  const compare = page.getByRole("button", { name: "Compare with Run 22 (r3)" });
  await expect(compare).toHaveAttribute("aria-pressed", "false");
  await compare.click();
  await expect(page).toHaveURL(/compare=run-22/);
  await expect(compare).toHaveAttribute("aria-pressed", "true");
  const heading = page.getByRole("heading", { name: "Run 23 (r4) compared with Run 22 (r3, production)" });
  await expect(heading).toBeFocused();
  await expect(page.getByRole("table", { name: /change = this run minus Run 22/ }).getByRole("row", { name: /Success rate/ })).toContainText("+2.8 points");
  await page.goBack();
  await expect(heading).toHaveCount(0);
  await expect(compare).toHaveAttribute("aria-pressed", "false");
});

test("the weakest slices and the slice callout filter the rollouts; outcome, task, sort and pages narrow them further", async ({ page }) => {
  await mock(page);
  await page.goto(`${RUN}/run-23`);
  await expect(page.getByRole("table", { name: /All 12 rollouts stored for Run 23/ })).toBeVisible();
  await expect(rolloutRows(page)).toHaveCount(10);
  await expect(page.getByText("Showing 1–10 of 12 · page 1 of 2")).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText("Showing 11–12 of 12 · page 2 of 2")).toBeVisible();
  await expect(rolloutRows(page)).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Next", exact: true })).toHaveAttribute("aria-disabled", "true");
  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await expect(page.getByText("Showing 1–10 of 12 · page 1 of 2")).toBeVisible();

  const slices = page.getByRole("table", { name: /success per slice/ });
  const weak = slices.getByRole("row", { name: /One wrist camera off/ });
  await expect(weak).toContainText("(weakest slice)");
  await weak.getByRole("link", { name: "Filter rollouts" }).click();
  await expect(page).toHaveURL(/slice=sensor-wrist-off/);
  await expect(field(page, "Slice")).toHaveValue("sensor-wrist-off");
  await expect(rolloutRows(page)).toHaveCount(1);
  await expect(rolloutRows(page).first()).toContainText("ep-23-012");
  await expect(page.getByRole("table", { name: /1 of 12 rollouts stored for Run 23 in One wrist camera off/ })).toBeVisible();

  await page.getByRole("group", { name: "Filter rollouts by outcome" }).getByRole("button", { name: /Safety events/ }).click();
  await expect(page.getByRole("group", { name: "Filter rollouts by outcome" }).getByRole("button", { name: /Safety events/ })).toHaveAttribute("aria-pressed", "true");
  await expect(rolloutRows(page)).toHaveCount(1);
  await field(page, "Slice").selectOption("");
  await expect(rolloutRows(page)).toHaveCount(3);
  await page.getByRole("group", { name: "Filter rollouts by outcome" }).getByRole("button", { name: /All/ }).click();
  await field(page, "Task").selectOption("towel-fold");
  await expect(page).toHaveURL(/task=towel-fold/);
  await expect(rolloutRows(page)).toHaveCount(2);
  await field(page, "Sort").selectOption("seed");
  await expect(rolloutRows(page).first()).toContainText("ep-23-123");
  await field(page, "Slice").selectOption("link-drop");
  await expect(page.getByText("No stored rollouts match these filters.")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(rolloutRows(page)).toHaveCount(10);

  const callout = page.getByRole("img", { name: /Link drop mid-task success: Run 23 70.0 %/ });
  await expect(callout).toContainText("16.7 %");
  const filter = page.locator(".ev-callout").getByRole("button", { name: "Filter rollouts" });
  await filter.click();
  await expect(filter).toHaveAttribute("aria-pressed", "true");
  await expect(rolloutRows(page)).toHaveCount(2);
  await expect(rolloutRows(page).filter({ hasText: "ep-23-007" })).toHaveCount(1);
  await filter.click();
  await expect(filter).toHaveAttribute("aria-pressed", "false");
  await expect(rolloutRows(page)).toHaveCount(10);
});

test("the replay opens from the URL or a row, closes with Escape or Back to the run, and returns focus", async ({ page }) => {
  await mock(page);
  await page.goto(`${RUN}/run-23`);
  const replayLink = page.getByRole("link", { name: "Replay ep-23-007", exact: true });
  await replayLink.click();
  await expect(page).toHaveURL(/rollout=ep-23-007/);
  await expect(h1(page)).toHaveText("ep-23-007 · Bin to tray transfer");
  await expect(h1(page)).toBeFocused();
  await expect(page.getByRole("navigation", { name: "Breadcrumb" }).locator("[aria-current=page]")).toHaveText("ep-23-007");
  await expect(page.getByRole("heading", { name: "Run 23 · Bimanual station suite v1" })).toBeHidden();
  await page.keyboard.press("Escape");
  await expect(page).not.toHaveURL(/rollout=/);
  await expect(h1(page)).toHaveText("Run 23 · Bimanual station suite v1");
  await expect(replayLink).toBeFocused();

  await replayLink.press("Enter");
  await expect(h1(page)).toHaveText("ep-23-007 · Bin to tray transfer");
  await page.getByRole("button", { name: "Close replay" }).click();
  await expect(h1(page)).toHaveText("Run 23 · Bimanual station suite v1");
  await expect(replayLink).toBeFocused();

  await page.goto(`${RUN}/run-23?rollout=ep-23-095`);
  await expect(h1(page)).toHaveText("ep-23-095 · Arm-to-arm handover");
  await expect(page.locator(".ev-replay .cfg-title").getByText("Safety stop", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Joint at its soft limit at 16.1 s; Protective stop at 16.3 s" })).toBeVisible();
  await page.getByRole("button", { name: "Back to Run 23" }).click();
  await expect(h1(page)).toHaveText("Run 23 · Bimanual station suite v1");
  await page.goBack();
  await expect(h1(page)).toHaveText("ep-23-095 · Arm-to-arm handover");

  await page.goto(`${RUN}/run-23?rollout=ep-99-001`);
  await expect(page.getByText("This rollout is not part of Run 23.")).toBeVisible();
  await page.getByRole("button", { name: "Back to Run 23" }).click();
  await expect(h1(page)).toHaveText("Run 23 · Bimanual station suite v1");
});

test("the sample replay scrubs by marker, slider, keys, events and steps, and plays", async ({ page }) => {
  await mock(page);
  await page.goto(`${RUN}/run-23?rollout=ep-23-007`);
  await expect(position(page)).toHaveText("0.0 s simulated · step 0 / 960");
  const facts = page.getByLabel("Episode facts");
  await expect(facts).toContainText("Cloud 30.3 s");
  await expect(facts).toContainText("Fallback 7.9 s");
  await expect(facts).toContainText("3 min 17 s");
  await page.getByRole("button", { name: "Link lost, edge takes over at 9.0 s" }).click();
  await expect(position(page)).toHaveText("9.0 s simulated · step 225 / 960");
  const atStep = page.getByRole("region", { name: "At this step" });
  await expect(atStep).toContainText("Link down · chunk 10 expired");
  await expect(atStep).toContainText("transfer(part: A-12, tray: 5)");
  await expect(page.getByRole("list", { name: "Episode events" }).getByRole("button", { name: /Link lost, edge takes over/ })).toHaveAttribute("aria-current", "true");
  await expect(page.locator(".cfg-tr--current")).toContainText("225");

  const slider = page.getByRole("slider", { name: "Replay position" });
  await slider.fill("260");
  await expect(position(page)).toHaveText("10.4 s simulated · step 260 / 960");
  await expect(atStep.locator(".cfg-path--fallback")).toBeVisible();
  await expect(atStep).toContainText("588 ms");
  await slider.press("End");
  await expect(position(page)).toHaveText("38.4 s simulated · step 960 / 960");
  await expect(page.getByRole("button", { name: "Next step" })).toHaveAttribute("aria-disabled", "true");
  await slider.press("Home");
  await expect(position(page)).toHaveText("0.0 s simulated · step 0 / 960");
  await page.getByRole("button", { name: "Next step" }).click();
  await expect(position(page)).toHaveText("0.0 s simulated · step 1 / 960");

  await page.getByRole("list", { name: "Episode events" }).getByRole("button", { name: /Cloud link restored/ }).click();
  await expect(position(page)).toHaveText("17.2 s simulated · step 430 / 960");
  await page.getByRole("button", { name: "Go to step 340" }).click();
  await expect(position(page)).toHaveText("13.6 s simulated · step 340 / 960");

  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  await expect(position(page)).not.toHaveText("13.6 s simulated · step 340 / 960");
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: /End-effector speed over 38.0 simulated seconds/ })).toBeVisible();
});

test("with reduced motion, playback moves between key steps", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mock(page);
  await page.goto(`${RUN}/run-23?rollout=ep-23-007`);
  await expect(page.getByText("Reduced motion is on, so playback moves between key steps.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(position(page)).toHaveText("0.2 s simulated · step 5 / 960", { timeout: 4000 });
  await page.getByRole("button", { name: "Pause", exact: true }).click();
});

test("a rollout with a recorded episode replays real frames through the platform proxy", async ({ page }) => {
  const frames: string[] = [];
  await mock(page, { document: documentWithRecordings(), frames });
  await page.goto(`${RUN}/run-23`);
  await expect(rolloutRows(page).filter({ hasText: "ep-23-044" }).locator(".cfg-prov--recorded")).toHaveText("Recorded · Oct 1");
  await page.goto(`${RUN}/run-23?rollout=ep-23-044`);
  await expect(h1(page)).toHaveText(`${EPISODE} · Bin to tray transfer`);
  await expect(page.locator(".cfg-title .cfg-prov--recorded")).toHaveText("Recorded · Oct 1");
  const facts = page.getByLabel("Episode facts");
  for (const text of ["54", "0.675 s", "41.2 s", "pick_place_puck", "Planner 917.8 ms"]) await expect(facts).toContainText(text);
  await expect(page.getByRole("img", { name: "Recorded robot camera at action 0 of 54" })).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByRole("img", { name: "Recorded robot camera at action 1 of 54" })).toBeVisible();
  await expect(page.locator(".replay-readout")).toContainText("Policy time: 512.30 ms");
  expect(frames).toEqual(expect.arrayContaining(["0", "1"]));

  await page.goto(`${RUN}/run-18`);
  await expect(h1(page)).toHaveText("Run 18 · MetaWorld pick-place-v3 · seed 0");
  const evaluation = page.getByRole("region", { name: "Recorded evaluation" });
  await expect(evaluation).toContainText("Passed · 1 / 1 successful cases; 1 required · median case wall time 41.2 s");
  await expect(evaluation).toContainText("Scope: lockstep simulation; no hardware or real-time qualification.");
  await evaluation.getByRole("link", { name: `Replay ${EPISODE}` }).click();
  await expect(h1(page)).toHaveText(`${EPISODE} · Seed 0`);
  await expect(page.getByRole("img", { name: "Recorded robot camera at action 0 of 54" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(evaluation.getByRole("link", { name: `Replay ${EPISODE}` })).toBeFocused();
  await page.getByRole("region", { name: "Recorded episode" }).getByRole("link", { name: `Replay ${RUN_EPISODE} ▸` }).click();
  await expect(h1(page)).toHaveText(`${RUN_EPISODE} · MetaWorld pick-place-v3 · seed 0`);
});

test("a recorded episode without a published recording says so", async ({ page }) => {
  await mock(page, { document: documentWithRecordings(), missingEpisode: EPISODE });
  await page.goto(`${RUN}/run-23?rollout=ep-23-044`);
  await expect(page.getByText("No camera recording is available for this episode.", { exact: false })).toBeVisible();
  await expect(page.getByLabel("Episode facts")).toContainText("Not reported");
});

test("Re-run queues a run through save(), says no runner is connected, and the queued run has no results", async ({ page }) => {
  const puts: Request[] = [];
  await mock(page, { puts });
  await page.goto(`${RUN}/run-23`);
  const rerun = page.getByRole("button", { name: "Re-run" });
  await rerun.click();
  const dialog = page.getByRole("dialog", { name: "Queue this evaluation again?" });
  await expect(dialog).toContainText("No evaluation runner is connected to this workspace.");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(rerun).toBeFocused();
  expect(puts).toHaveLength(0);

  await rerun.click();
  await dialog.getByRole("button", { name: "Queue run" }).click();
  await expect(dialog).toBeHidden();
  expect(puts).toHaveLength(1);
  expect(puts[0].headers()["idempotency-key"]).toMatch(/^[\x21-\x7e]{1,128}$/);
  const body = (puts[0].postDataJSON() as { body: ConvoyWorkspace }).body;
  expect(body.runs.find(item => item.id === "run-26")).toMatchObject({
    number: 26, status: "queued", progress: { done: 0, total: 360 }, configId: "hybrid", rev: "r4", robotId: "lab-bench", suiteId: "station-suite-v1",
    counts: { episodes: 0, successes: null }, provenance: { kind: "not-reported" }, purpose: "Re-run of Run 23 · Candidate · seeds 1–5",
  });
  const notice = page.getByRole("status").filter({ hasText: "Run 26 is queued" });
  await expect(notice).toContainText("No evaluation runner is connected, so it stays queued with no results until a runner reports them.");
  await notice.getByRole("link", { name: "Open Run 26" }).click();
  await expect(h1(page)).toHaveText("Run 26 · Bimanual station suite v1");
  await expect(page.locator(".cfg-title").getByText("Queued", { exact: true })).toBeVisible();
  await expect(page.getByText("It was queued from this workspace. No evaluation runner is connected", { exact: false })).toBeVisible();
  await expect(page.getByText("This run has not started.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Re-run" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Compare with/ })).toHaveCount(0);
});

test("running and queued runs show progress with partial or pending results", async ({ page }) => {
  await mock(page);
  await page.goto(`${RUN}/run-24`);
  await expect(page.getByRole("progressbar", { name: "Run 24 episodes complete" }).first()).toHaveAttribute("aria-valuenow", "212");
  await expect(page.locator(".ev-progress")).toContainText("Run 24 is in progress: 212 of 360 episodes (58.9 %)");
  await expect(tile(page, "Success rate")).toContainText("167 of 212 episodes so far");
  await expect(tile(page, "Success rate")).toContainText("Partial · 212/360");
  const gate = page.getByRole("region", { name: "Gate decided when the run finishes" });
  await expect(gate.getByText("Pending", { exact: true })).toHaveCount(6);
  await expect(page.getByRole("table", { name: /All 3 rollouts stored for Run 24/ })).toBeVisible();
  await page.goto(`${RUN}/run-25`);
  await expect(page.locator(".ev-progress")).toContainText("Run 25 is queued · 0 of 360 episodes. Starts after Run 24.");
  await expect(page.getByText("This run has not started.")).toBeVisible();
  await expect(page.locator(".cfg-kpi")).toHaveCount(0);
  await page.goto(`${RUN}/run-19`);
  await expect(tile(page, "Scored correct")).toContainText("1,617 of 1,666 requests");
  await expect(page.locator(".cfg-kpi")).toHaveCount(2);
  await expect(page.getByRole("region", { name: "About this run" })).toContainText("Gateway completion latency on the device");
});

for (const width of [1440, 390]) test(`evaluation run views are accessible and fit at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mock(page, { document: documentWithRecordings() });
  for (const [path, title] of [
    [`${RUN}/run-23`, "Run 23 · Bimanual station suite v1"],
    [`${RUN}/run-23?compare=run-22`, "Run 23 · Bimanual station suite v1"],
    [`${RUN}/run-21`, "Run 21 · Bimanual station suite v1"],
    [`${RUN}/run-24`, "Run 24 · Bimanual station suite v1"],
    [`${RUN}/run-25`, "Run 25 · Bimanual station suite v1"],
    [`${RUN}/run-18`, "Run 18 · MetaWorld pick-place-v3 · seed 0"],
    [`${RUN}/run-23?rollout=ep-23-007`, "ep-23-007 · Bin to tray transfer"],
    [`${RUN}/run-23?rollout=ep-23-044`, `${EPISODE} · Bin to tray transfer`],
  ] as const) {
    await page.goto(path);
    await expect(h1(page)).toHaveText(title);
    if (path.includes("rollout=ep-23-044")) await expect(page.getByRole("img", { name: /Recorded robot camera/ })).toBeVisible();
    if (path.endsWith("run-18")) await expect(page.getByRole("region", { name: "Recorded evaluation" })).toContainText("Passed");
    await noOverflow(page);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
    expect(result.violations, `${path}: ${JSON.stringify(result.violations, null, 2)}`).toEqual([]);
  }
});
