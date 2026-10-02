import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { h1, mockApi, noOverflow, OFFLINE_EVAL, offlineDocument } from "./support/configurations";
import { evidenceDocument, mockPlannerEval, PLANNER_CONFIG, PLANNER_EVAL, PLANNER_ROBOT, TARGETS } from "./support/evidence";

/*
 * The evidence panels: an eval's latency budget and autonomy (Overview), and their compact versions on
 * the configuration dashboard. Contract fixtures with the metrics of a real on-device planner eval;
 * every API is mocked. Screenshots of each panel at 1440 and 390 px are attached to the results.
 */

const EVAL_PAGE = `/app/configurations/${PLANNER_CONFIG}/robots/${PLANNER_ROBOT}/evals/${PLANNER_EVAL}`;
const region = (scope: Page | Locator, name: string) => scope.getByRole("region", { name, exact: true });

async function axe(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map(violation => `${violation.id} ${violation.nodes.map(node => node.target.join(" ")).join(", ")}`)).toEqual([]);
}
async function shot(target: Page | Locator, testInfo: TestInfo, name: string) {
  const page = "goto" in target ? target : target.page();
  // No hover or focus ring in the picture, and the sticky top bar does not cover a tall element.
  await page.addStyleTag({ content: ".cv-bar { position: static !important; }" });
  await page.mouse.move(0, 0);
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const path = testInfo.outputPath(`${name}.png`);
  if ("goto" in target) await target.screenshot({ path, fullPage: true, animations: "disabled" });
  else await target.screenshot({ path, animations: "disabled" });
  await testInfo.attach(name, { path, contentType: "image/png" });
}
/** Width of an element as a share of another's (bars against their track). */
async function share(part: Locator, whole: Locator) {
  const [a, b] = await Promise.all([part.boundingBox(), whole.boundingBox()]);
  return a!.width / b!.width;
}
async function heights(locators: Locator[]) {
  return Promise.all(locators.map(async locator => Math.round((await locator.boundingBox())!.height)));
}
/** Axis tick labels side by side with room between them (none overlaps the next). */
async function ticksApart(chart: Locator) {
  const boxes = await chart.locator(".cv-lb__ticks span").evaluateAll(nodes => nodes.map(node => { const box = node.getBoundingClientRect(); return [box.left, box.right]; }));
  for (let i = 1; i < boxes.length; i++) expect(boxes[i][0] - boxes[i - 1][1], `tick ${i} clears tick ${i - 1}`).toBeGreaterThanOrEqual(4);
}

test.describe("evidence panels", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });

  test("the eval's latency budget: one planner decision split to scale, beside the configuration's targets", async ({ page }, testInfo) => {
    await mockApi(page, { document: evidenceDocument() });
    await mockPlannerEval(page);
    await page.goto(EVAL_PAGE);
    await expect(h1(page)).toHaveText("Eval 1");
    const card = region(page, "Latency budget");
    await expect(card.locator(".cv-badge")).toHaveText("Measured");
    await expect(card.locator(".cv-ev__sub")).toHaveText("Per planner decision · median of 10 episodes · linear scale");

    // The values: prefill = first token, decode = device − first token, network / relay = end to end − device.
    const table = card.getByRole("table");
    await expect(table.locator("thead th")).toHaveText(["Part", "p50", "p95"]);
    await expect(table.locator("tbody tr")).toHaveText([
      "Prefill801 ms–Not reported", "Decode562 ms–Not reported", "On device1,362 ms1,430 ms", "Network / relay1,322 ms1,986 ms", "End to end2,684 ms3,416 ms",
    ]);

    // Drawn to scale on one linear axis from 0 (0–4 s): the bars, and the targets as labelled reference rows.
    const chart = card.getByRole("img");
    await expect(chart).toHaveAccessibleName(/^Latency per planner decision, linear scale from 0\. p50 2,684 ms end to end: prefill 801 ms, decode 562 ms, network \/ relay 1,322 ms\. p95 3,416 ms end to end: on device 1,430 ms, network \/ relay 1,986 ms\. References: Teleop · near 60 ms, Teleop · target 100 ms, Teleop · far 120 ms\.$/);
    await expect(chart.locator(".cv-lb__ticks span"), "one unit, on the last tick").toHaveText(["0", "1", "2", "3", "4 s"]);
    const names = chart.locator(".cv-lb__name"), values = chart.locator(".cv-lb__value"), tracks = chart.locator(".cv-lb__track");
    await expect(names).toHaveText(["p50", "p95", ...TARGETS.map(target => target.label)]);
    await expect(values).toHaveText(["2,684 ms", "3,416 ms", "60 ms", "100 ms", "120 ms"]);
    const track = tracks.first();
    expect(await share(tracks.nth(0).locator(".cv-lb__bar"), track)).toBeCloseTo(2683.8 / 4000, 2);
    expect(await share(tracks.nth(1).locator(".cv-lb__bar"), track)).toBeCloseTo(3416.05 / 4000, 2);
    expect(await share(tracks.nth(0).locator(".cv-lb__seg--prefill"), track)).toBeCloseTo(800.7 / 4000, 2);
    expect(await share(tracks.nth(2).locator(".cv-lb__ref"), track)).toBeCloseTo(60 / 4000, 2);
    expect(await share(tracks.nth(4).locator(".cv-lb__ref"), track)).toBeCloseTo(120 / 4000, 2);
    await expect(tracks.nth(1).locator(".cv-lb__seg"), "p95 has no first-token figure: on device is not split").toHaveClass(["cv-lb__seg cv-lb__seg--device", "cv-lb__seg cv-lb__seg--network"]);

    // Small facts, the scale note, and the definitions behind the info toggle.
    await expect(card.locator(".cv-ev__facts > div")).toHaveText(["Tokens (p50)896 in · 22 out", "Prefill share59 % of on-device time", "Slowest episode p9517.8 s end to end · 1.45 s on device"]);
    await expect(card.locator(".cv-ev__note")).toHaveText("Motor loop runs separately on the robot.");
    const info = card.getByRole("button", { name: "Latency budget: definitions" });
    await expect(card.locator(".cv-ev__definitions")).toBeHidden();
    await info.click();
    await expect(info).toHaveAttribute("aria-expanded", "true");
    await expect(card.locator(".cv-ev__definitions dt")).toHaveText(["Prefill", "Decode", "Network / relay", "p50, p95", "Targets"]);
    await axe(page);
    await info.click();
    await shot(page.locator(".cv-ev-row"), testInfo, "eval-evidence-1440");
    await shot(page, testInfo, "eval-page-1440");

    // Phone width: the cards stack, the chart keeps every label whole, nothing scrolls sideways.
    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow(page);
    await expect(values).toHaveText(["2,684 ms", "3,416 ms", "60 ms", "100 ms", "120 ms"]);
    expect(await names.evaluateAll(nodes => nodes.filter(node => node.scrollWidth > node.clientWidth).map(node => node.textContent)), "no label is cut").toEqual([]);
    expect(await share(tracks.nth(0).locator(".cv-lb__bar"), track)).toBeCloseTo(2683.8 / 4000, 2);
    await ticksApart(chart);
    await shot(page.locator(".cv-ev-row"), testInfo, "eval-evidence-390");
  });

  test("the eval's autonomy: interventions from the recorded counts and outcomes, by type, Not reported when missing", async ({ page }, testInfo) => {
    await mockApi(page, { document: evidenceDocument() });
    await mockPlannerEval(page);
    await page.goto(EVAL_PAGE);
    const card = region(page, "Autonomy");
    await expect(card.locator(".cv-badge")).toHaveText("Measured");
    await expect(card.locator(".cv-ev__sub")).toHaveText("Teleop handoffs · 10 episodes · 308 decisions");
    await expect(card.locator(".cv-au__kpi")).toHaveText([
      "Autonomous episodes0 / 100 %", "Interventions per episode2.929 in 10 episodes", "Per 100 decisions9.429 in 308",
      "Decisions between10.6interventions, mean", "Accepted on first call–Not reported", "Decisions308valid + failed",
    ]);
    const interventions = card.getByRole("table", { name: "Interventions by type" });
    await expect(interventions.locator("thead th")).toHaveText(["Intervention", "Events", "Episodes"]);
    await expect(interventions.locator("tbody tr")).toHaveText(["Failed decision157", "Protective stop81", "Unfinished episode66", "Arm–arm contactNot counted1401"]);
    await expect(interventions.locator("tbody tr").last(), "shown, not counted").toHaveClass("cv-au__uncounted");
    // This fixture records invalid choices only: the other refusal and failure counts are Not reported, never 0.
    await expect(card.getByRole("table", { name: "Planner calls by result" }).locator("tbody tr")).toHaveText([
      "Valid reply29310", "Refused by the executive–Not reported–Not reported", "Device or transport failure–Not reported–Not reported",
    ]);
    await expect(card.locator(".cv-ev__facts > div")).toHaveText(["Planner calls378 · 78 % valid", "Counts reported10 of 10 episodes"]);
    await card.getByRole("button", { name: "Autonomy: definitions" }).click();
    await expect(card.locator(".cv-ev__definitions dt")).toHaveText(["Intervention", "Autonomous episode", "Decisions", "Refused", "Device or transport failure", "Failed decision", "Protective stop", "Unfinished episode", "Arm–arm contact", "First call"]);
    await axe(page);
    await card.getByRole("button", { name: "Autonomy: definitions" }).click();

    // Side by side with the latency budget, equal in height.
    const [latency, autonomy] = [region(page, "Latency budget"), card];
    const [a, b] = await Promise.all([latency.boundingBox(), autonomy.boundingBox()]);
    expect(Math.abs(a!.y - b!.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(a!.height - b!.height), `latency ${a!.height}px, autonomy ${b!.height}px`).toBeLessThanOrEqual(1);
    const feet = await page.locator(".cv-ev__foot").evaluateAll(nodes => nodes.map(node => Math.round(node.getBoundingClientRect().top)));
    expect(new Set(feet).size, "the feet start on one line").toBe(1);
    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow(page);
    await shot(card, testInfo, "eval-autonomy-390");

    // An episode that does not report its failed decisions: the totals it feeds say Not reported, never 0.
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await mockApi(page, { document: evidenceDocument() });
    await mockPlannerEval(page, (metrics, i) => { if (i === 1) delete metrics.planner_failed_decisions; });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(EVAL_PAGE);
    await expect(card.locator(".cv-au__kpi")).toHaveText([
      "Autonomous episodes–Not reported", "Interventions per episode–Not reported", "Per 100 decisions–Not reported",
      "Decisions between–Not reported", "Accepted on first call–Not reported", "Decisions–Not reported",
    ]);
    await expect(card.locator("tbody tr").first()).toHaveText("Failed decision–Not reported–Not reported");
    await expect(card.locator("tbody tr").nth(1), "reported counts stay").toHaveText("Protective stop81");
    await expect(card.locator(".cv-ev__facts > div").last()).toHaveText("Counts reported9 of 10 episodes");

    // Episodes that export the decision-level counts (platform-chat-v1): the first-call share over its own
    // decisions, 273 of 308 here.
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await mockApi(page, { document: evidenceDocument() });
    await mockPlannerEval(page, metrics => {
      const valid = metrics.planner_valid_replies as number, failed = metrics.planner_failed_decisions as number;
      Object.assign(metrics, { planner_decisions: valid + failed, planner_first_call_accepted: valid - 2, planner_reasked_decisions: failed + 2 });
    });
    await page.goto(EVAL_PAGE);
    await expect(card.locator(".cv-au__kpi").nth(4)).toHaveText("Accepted on first call89 %of decisions");
    await expect(card.locator(".cv-au__kpi").nth(5)).toHaveText("Decisions308valid + failed");
  });

  test("the dashboard: compact latency budget and autonomy of the newest eval, equal in height; a live device's when there is none", async ({ page }, testInfo) => {
    await mockApi(page, { document: evidenceDocument() });
    await mockPlannerEval(page);
    await page.goto(`/app/configurations/${PLANNER_CONFIG}`);
    await expect(h1(page)).toHaveText("Arm · Edge planner");
    const row = region(page, "Latency and autonomy");
    const [latency, autonomy] = [region(row, "Latency budget"), region(row, "Autonomy")];
    await expect(latency.locator(".cv-ev__sub")).toHaveText("Per planner decision · p50 · linear scale");
    await expect(latency.locator(".cv-lb__value")).toHaveText(["2,684 ms", "60 ms", "100 ms", "120 ms"]);
    await expect(latency.locator(".cv-lb__legend li")).toHaveText(["Prefill801 ms", "Decode562 ms", "Network / relay1,322 ms"]);
    await expect(latency.locator(".cv-ev__facts > div")).toHaveText(["Tokens (p50)896 in · 22 out", "Prefill share59 % of on-device time"]);
    await expect(latency.locator(".cv-ev__source a")).toHaveText("Eval 1 · Planner sim");
    await expect(autonomy.locator(".cv-au__kpi")).toHaveText(["Autonomous episodes0 / 100 %", "Interventions per episode2.9mean", "Per 100 decisions9.4interventions"]);
    await expect(autonomy.locator(".cv-au__line li")).toHaveText(["Failed decision15", "Protective stop8", "Unfinished episode6", "Arm–arm contact140not counted"]);
    await expect(autonomy.locator(".cv-ev__facts > div")).toHaveText(["Planner calls378 · 78 % valid"]);
    await expect(autonomy.locator(".cv-ev__source a")).toHaveText("Eval 1 · Planner sim");
    const [a, b] = await Promise.all([latency.boundingBox(), autonomy.boundingBox()]);
    expect(Math.abs(a!.y - b!.y)).toBeLessThanOrEqual(1);
    expect(await heights([latency, autonomy]).then(([x, y]) => Math.abs(x - y)), "equal in height").toBeLessThanOrEqual(1);
    await axe(page);
    await shot(row, testInfo, "dashboard-evidence-1440");
    await shot(page, testInfo, "dashboard-page-1440");
    await autonomy.locator(".cv-ev__source a").click();
    await expect(page).toHaveURL(new RegExp(`${EVAL_PAGE}/?$`));
    await expect(region(page, "Autonomy")).toBeVisible();

    await page.goto(`/app/configurations/${PLANNER_CONFIG}`);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(region(row, "Autonomy")).toBeVisible();
    const [top, below] = await Promise.all([latency.boundingBox(), autonomy.boundingBox()]);
    expect(below!.y, "stacked on a phone").toBeGreaterThanOrEqual(top!.y + top!.height);
    await noOverflow(page);
    expect(await row.locator("h2, .cv-lb__name, .cv-au__kpi dt").evaluateAll(nodes => nodes.filter(node => node.scrollWidth > node.clientWidth).map(node => node.textContent)), "no title or label is cut").toEqual([]);
    await shot(row, testInfo, "dashboard-evidence-390");

    // The sample: no eval with planner metrics, so the live device's requests (on the device only), no autonomy.
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await mockApi(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/app/configurations/edge-planner");
    const live = region(region(page, "Latency and autonomy"), "Latency budget");
    await expect(live.locator(".cv-ev__sub")).toHaveText("Per request, on the device · p50 · linear scale");
    await expect(live.locator(".cv-lb__legend li")).toHaveText(["Prefill60 ms", "Decode150 ms", "Network / relay–Not reported"]);
    await expect(live.locator(".cv-lb__value")).toHaveText(["210 ms", "60 ms", "120 ms"]);
    await expect(live.locator(".cv-lb__ticks span")).toHaveText(["0", "100", "200", "300 ms"]);
    await expect(live.locator(".cv-ev__source a")).toHaveText("Bench 01 · 3 requests");
    await expect(region(page, "Autonomy")).toHaveCount(0);
    const [card, content] = await Promise.all([live.boundingBox(), page.locator(".cv-tiles").first().boundingBox()]);
    expect(Math.abs(card!.width - content!.width), "alone, it spans the row").toBeLessThanOrEqual(1);
    await axe(page);
    await shot(region(page, "Latency and autonomy"), testInfo, "dashboard-live-1440");
    await page.setViewportSize({ width: 390, height: 844 });
    await ticksApart(live.getByRole("img"));
    await noOverflow(page);
  });

  test("an eval without planner metrics, and a configuration without targets, show no invented values", async ({ page }) => {
    await mockApi(page, { document: evidenceDocument(null) });
    await mockPlannerEval(page);
    await page.goto(EVAL_PAGE);
    const card = region(page, "Latency budget");
    await expect(card.locator(".cv-lb__ref")).toHaveCount(0);
    await expect(card.locator(".cv-lb__name")).toHaveText(["p50", "p95"]);
    await card.getByRole("button", { name: "Latency budget: definitions" }).click();
    await expect(card.locator(".cv-ev__definitions")).toContainText("None declared on the configuration.");

    // The contract offline eval reports rewards only: no evidence panels, its page as before.
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await mockApi(page, { document: offlineDocument() });
    await page.goto(`/app/configurations/arm-edge-vla/robots/offline-runner/evals/${OFFLINE_EVAL}`);
    await expect(page.getByRole("table", { name: "Metrics" }).locator("tbody tr")).toHaveText(["reward_sum32"]);
    await expect(region(page, "Latency budget")).toHaveCount(0);
    await expect(region(page, "Autonomy")).toHaveCount(0);
    await page.goto("/app/configurations/arm-edge-vla");
    await expect(h1(page)).toHaveText("Arm · Edge VLA");
    await expect(region(page, "Latency and autonomy")).toHaveCount(0);
  });
});
