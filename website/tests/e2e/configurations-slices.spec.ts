import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { h1, mockApi, noOverflow, OFFLINE_EVAL, offlineDocument, tile } from "./support/configurations";
import { PLANNER_CONFIG, PLANNER_EVAL, PLANNER_ROBOT } from "./support/evidence";
import { mockTwoRuns, SECOND_EVAL, slicesDocument } from "./support/slices";

/*
 * Two offline evals of one simulator, the same task run from two machines, each with two slices:
 * results per slice (never one pooled rate), refused planner replies apart from device or transport
 * failures, the provenance declared on the robot, and both evals listed apart. Contract fixtures with
 * the values of two real on-device planner evals; every API is mocked. Screenshots at 1440 and 390 px
 * are attached to the results.
 */
const DASHBOARD = `/app/configurations/${PLANNER_CONFIG}`;
const ROBOT = `${DASHBOARD}/robots/${PLANNER_ROBOT}`;
const SETUP = ["MuJoCo planner run", "Simulator-state perception", "Scripted IK", "Qwen on Jetson (real calls)"];
const region = (scope: Page | Locator, name: string) => scope.getByRole("region", { name, exact: true });
const table = (scope: Page | Locator, name: string) => scope.getByRole("table", { name, exact: true });
const provenance = (scope: Page | Locator) => scope.getByRole("list", { name: "How this eval ran", exact: true }).locator("li");

async function axe(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map(violation => `${violation.id} ${violation.nodes.map(node => node.target.join(" ")).join(", ")}`)).toEqual([]);
}
async function shot(page: Page, testInfo: TestInfo, name: string) {
  // No hover or focus ring in the picture, and the sticky top bar does not cover the page.
  await page.addStyleTag({ content: ".cv-bar { position: static !important; }" });
  await page.mouse.move(0, 0);
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const path = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path, fullPage: true, animations: "disabled" });
  await testInfo.attach(name, { path, contentType: "image/png" });
}
/** Cells drawn past the viewport's right edge (a column pushed off a phone screen). */
const offscreen = (locator: Locator) => locator.evaluateAll(nodes => nodes.filter(node => node.getBoundingClientRect().right > document.documentElement.clientWidth + 0.5).map(node => node.textContent));

test.describe("slices, planner calls and provenance", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });

  test("two evals of one robot: newest first, each with its name, runner and date, success per slice and never pooled", async ({ page }, testInfo) => {
    await mockApi(page, { document: slicesDocument() });
    await mockTwoRuns(page);
    await page.goto(ROBOT);
    await expect(h1(page)).toHaveText("Planner sim");
    const evals = table(page, "Evals");
    await expect(evals.locator("thead th")).toHaveText(["Eval", "Name", "Runner", "Date", "Success", "Result"]);
    await expect(evals.locator("tbody th"), "the runner sits in its own column here").toHaveText(["Eval 2Offline sim", "Eval 1Offline sim"], { useInnerText: true });
    await expect(evals.locator("tbody td:nth-child(2)")).toHaveText(["Bimanual · planner on device · second runner", "Bimanual · planner on device · first runner"]);
    await expect(evals.locator("tbody td:nth-child(3)")).toHaveText(["Mac", "Linux cloud runner"]);
    await expect(evals.locator("tbody td:nth-child(5)")).toHaveText(["Nominal 3/5More items 0/5", "Nominal 4/5More items 0/5"]);
    await expect(evals.locator("tbody td").filter({ hasText: "%" }), "no pooled rate").toHaveCount(0);
    // The tiles describe the newest eval only.
    await expect(tile(page, "Success rate")).toHaveCount(0);
    await expect(tile(page, "Success by slice")).toHaveText(/Nominal 3\/5\s*More items 0\/5\s*Eval 2 · Mac$/);
    await expect(tile(page, "Episodes")).toContainText("10Eval 2");
    await expect(tile(page, "Median time")).toContainText("150.0sEval 2 · per episode");
    await axe(page);
    await shot(page, testInfo, "robot-1440");

    // A phone: the runner and date under each eval, the slices wrapped, nothing off screen.
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(evals.locator(".cv-cell-sub")).toHaveText([/^Mac · [A-Z][a-z]{2} \d{1,2}$/, /^Linux cloud runner · [A-Z][a-z]{2} \d{1,2}$/]);
    await noOverflow(page);
    expect(await offscreen(evals.locator("th, td").filter({ visible: true }))).toEqual([]);
    await shot(page, testInfo, "robot-390");

    await page.setViewportSize({ width: 1440, height: 1000 });
    await evals.locator("tbody tr").nth(1).click();
    await expect(h1(page)).toHaveText("Eval 1");
    await expect(provenance(page)).toHaveText([...SETUP, "Runner: Linux cloud runner", "Transport: Portal relay"]);
  });

  test("an eval of two slices: how it ran, success per slice, the slices side by side, the panels one slice at a time", async ({ page }, testInfo) => {
    await mockApi(page, { document: slicesDocument() });
    await mockTwoRuns(page);
    await page.goto(`${ROBOT}/evals/${SECOND_EVAL}`);
    await expect(h1(page)).toHaveText("Eval 2");
    await expect(provenance(page)).toHaveText([...SETUP, "Runner: Mac", "Transport: Portal relay"]);

    // Results per slice; the counts and time that span both slices say so.
    await expect(tile(page, "Success rate")).toHaveCount(0);
    await expect(tile(page, "Success · Nominal")).toHaveText(/3\/560 % · seeds 0–4$/);
    await expect(tile(page, "Success · More items")).toHaveText(/0\/50 % · seeds 200–204$/);
    await expect(tile(page, "Episodes")).toContainText("102 slices");
    await expect(tile(page, "Median time")).toContainText("Simulated · all slices");

    const slices = table(page, "Slices");
    await expect(slices.locator("thead th")).toHaveText(["Measure", "Nominal", "More items"]);
    await expect(slices.locator("tbody tr")).toHaveText([
      "Success3/5 · 60 %0/5 · 0 %", "Seeds0–4200–204", "Pills placed118/120124/150", "Failed decisions910",
      "Planner calls196219", "Refused by the executive5460", "Device or transport failures00",
      "On device p501,332 ms1,379 ms", "On device p951,397 ms1,458 ms", "End to end p502,411 ms2,428 ms", "End to end p952,859 ms2,895 ms",
      "Median steps296299", "Median time, simulated147.6 s150.0 s",
    ]);

    // The panels show one slice, the first by default, named in each scope line.
    const slice = page.getByRole("radiogroup", { name: "Slice" });
    await expect(slice.getByRole("radio")).toHaveCount(2);
    await expect(slice.getByRole("radio", { name: "Nominal" })).toBeChecked();
    const latency = region(page, "Latency budget"), autonomy = region(page, "Autonomy");
    await expect(latency.locator(".cv-ev__sub")).toHaveText("Nominal · per planner decision · median of 5 episodes · linear scale");
    await expect(latency.locator(".cv-lb__value")).toHaveText(["2,411 ms", "2,859 ms", "60 ms", "100 ms", "120 ms"]);
    await expect(autonomy.locator(".cv-ev__sub")).toHaveText("Nominal · teleop handoffs · 5 episodes · 151 decisions");
    await expect(autonomy.locator(".cv-au__kpi").first()).toHaveText("Autonomous episodes1 / 520 %");
    await expect(table(autonomy, "Interventions by type").locator("tbody tr")).toHaveText(["Failed decision94", "Protective stop00", "Unfinished episode22", "Arm–arm contactNot counted00"]);
    // Refused replies (the model's) apart from device or transport failures (the link's), a 0 shown as 0.
    const calls = table(autonomy, "Planner calls by result");
    await expect(calls.locator("thead th")).toHaveText(["Planner call", "Calls", "Episodes"]);
    await expect(calls.locator("tbody tr")).toHaveText(["Valid reply1425", "Refused by the executive545", "Device or transport failure00"]);
    await expect(calls.locator("tbody th").nth(1)).toHaveAttribute("title", /invalid choice 54 · invalid JSON 0 · invalid schema 0$/);
    await autonomy.getByRole("button", { name: "Autonomy: definitions" }).click();
    await expect(autonomy.locator(".cv-ev__definitions dt").nth(3)).toHaveText("Refused");
    await expect(autonomy.locator(".cv-ev__definitions dt").nth(4)).toHaveText("Device or transport failure");
    await axe(page);
    await autonomy.getByRole("button", { name: "Autonomy: definitions" }).click();
    await shot(page, testInfo, "eval-1440");

    await slice.getByText("More items", { exact: true }).click();
    await expect(slice.getByRole("radio", { name: "More items" })).toBeChecked();
    await expect(page).toHaveURL(/[?&]slice=more_items$/);
    await expect(latency.locator(".cv-ev__sub")).toHaveText("More items · per planner decision · median of 5 episodes · linear scale");
    await expect(latency.locator(".cv-lb__value")).toHaveText(["2,428 ms", "2,895 ms", "60 ms", "100 ms", "120 ms"]);
    await expect(autonomy.locator(".cv-au__kpi").first()).toHaveText("Autonomous episodes0 / 50 %");
    await expect(calls.locator("tbody tr")).toHaveText(["Valid reply1595", "Refused by the executive605", "Device or transport failure00"]);
    await slice.getByText("Nominal", { exact: true }).click();
    await expect(page).not.toHaveURL(/slice=/);
    await expect(calls.locator("tbody tr").first()).toHaveText("Valid reply1425");

    // Metrics: one mean per slice; the call results last, labelled as refused or device or transport failures.
    const metrics = table(page, "Metrics");
    await expect(metrics.locator("thead th")).toHaveText(["Metric", "Nominal", "More items"]);
    await expect(metrics.locator("tbody tr").filter({ hasText: /^Refused|^Device or transport/ })).toHaveText([
      "Refused · invalid choice10.812", "Refused · invalid JSON00", "Refused · invalid schema00",
      "Device or transport failure · device error00", "Device or transport failure · HTTP or transport error00", "Device or transport failure · timeout00",
    ]);
    await expect(metrics.locator("tbody tr").last()).toHaveText("Device or transport failure · timeout00");
    await expect(page.getByText("Reported simulator measurements, averaged per episode within each slice.", { exact: false })).toBeVisible();
    const rollouts = table(page, "Rollouts");
    await expect(rollouts.locator("thead th").nth(1)).toHaveText("Slice");
    await expect(rollouts.locator("tbody tr td:nth-child(2)")).toHaveText([...Array(5).fill("Nominal"), ...Array(5).fill("More items")]);

    await page.getByRole("tab", { name: "Details" }).click();
    for (const fact of ["RunMuJoCo planner run", "PerceptionSimulator-state perception", "RunnerMac", "TransportPortal relay", "SlicesNominal (seeds 0–4) · More items (seeds 200–204)"]) {
      await expect(page.locator(".cv-facts > div").filter({ hasText: fact })).toHaveCount(1);
    }

    // A phone: every slice keeps its column, nothing scrolls sideways.
    await page.getByRole("tab", { name: "Overview" }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow(page);
    expect(await offscreen(slices.locator("th, td")), "the slices table fits").toEqual([]);
    expect(await offscreen(metrics.locator("th, td")), "the metrics table fits").toEqual([]);
    await expect(provenance(page).last()).toBeVisible();
    await axe(page);
    await shot(page, testInfo, "eval-390");

    // A deep link opens on its slice.
    await page.goto(`${ROBOT}/evals/${SECOND_EVAL}?slice=more_items`);
    await expect(page.getByRole("radiogroup", { name: "Slice" }).getByRole("radio", { name: "More items" })).toBeChecked();
    await expect(region(page, "Autonomy").locator(".cv-ev__sub")).toHaveText("More items · teleop handoffs · 5 episodes · 169 decisions");
  });

  test("the first runner's eval reads on its own: its slices, its calls with none failed, its provenance", async ({ page }) => {
    await mockApi(page, { document: slicesDocument() });
    await mockTwoRuns(page);
    await page.goto(`${ROBOT}/evals/${PLANNER_EVAL}`);
    await expect(h1(page)).toHaveText("Eval 1");
    await expect(provenance(page)).toHaveText([...SETUP, "Runner: Linux cloud runner", "Transport: Portal relay"]);
    await expect(tile(page, "Success · Nominal")).toContainText("4/5");
    await expect(tile(page, "Success · More items")).toContainText("0/5");
    const slices = table(page, "Slices").locator("tbody tr");
    await expect(slices.filter({ hasText: "Pills placed" }), "not reported by this eval: no row").toHaveCount(0);
    await expect(slices.filter({ hasText: /^(Failed decisions|Planner calls|Refused|Device)/ })).toHaveText([
      "Failed decisions96", "Planner calls185193", "Refused by the executive4540", "Device or transport failures00",
    ]);
    const autonomy = region(page, "Autonomy");
    await expect(table(autonomy, "Interventions by type").locator("tbody tr").nth(1)).toHaveText("Protective stop81");
    await expect(table(autonomy, "Planner calls by result").locator("tbody tr")).toHaveText(["Valid reply1405", "Refused by the executive455", "Device or transport failure00"]);

    // An eval without declared provenance or slices shows neither.
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await mockApi(page, { document: offlineDocument() });
    await page.goto(`/app/configurations/arm-edge-vla/robots/offline-runner/evals/${OFFLINE_EVAL}`);
    await expect(h1(page)).toHaveText("Eval 1");
    await expect(provenance(page)).toHaveCount(0);
    await expect(page.getByRole("radiogroup", { name: "Slice" })).toHaveCount(0);
    await expect(tile(page, "Success rate")).toContainText("1 of 2");
  });

  test("the dashboard: success per slice of the newest eval, how it ran, and its panels one slice at a time", async ({ page }, testInfo) => {
    await mockApi(page, { document: slicesDocument() });
    await mockTwoRuns(page);
    await page.goto(DASHBOARD);
    await expect(h1(page)).toHaveText("Arm · Edge planner");
    await expect(tile(page, "Success rate")).toHaveCount(0);
    await expect(tile(page, "Success by slice")).toHaveText(/Nominal 3\/5\s*More items 0\/5\s*Eval 2 · Planner sim$/);
    const row = region(page, "Latency and autonomy");
    await expect(provenance(row)).toHaveText([...SETUP, "Runner: Mac", "Transport: Portal relay"]);
    const slice = row.getByRole("radiogroup", { name: "Slice" });
    await expect(slice.getByRole("radio", { name: "Nominal" })).toBeChecked();
    const [latency, autonomy] = [region(row, "Latency budget"), region(row, "Autonomy")];
    await expect(latency.locator(".cv-ev__sub")).toHaveText("Nominal · per planner decision · p50 · linear scale");
    await expect(latency.locator(".cv-lb__value")).toHaveText(["2,411 ms", "60 ms", "100 ms", "120 ms"]);
    await expect(autonomy.locator(".cv-au__calls li")).toHaveText(["Valid142", "Refused54", "Device / transport0"]);
    await expect(row.locator(".cv-ev__source a")).toHaveText(["Eval 2 · Planner sim", "Eval 2 · Planner sim"]);
    const [a, b] = await Promise.all([latency.boundingBox(), autonomy.boundingBox()]);
    expect(Math.abs(a!.height - b!.height), "equal in height").toBeLessThanOrEqual(1);
    await axe(page);
    await shot(page, testInfo, "dashboard-1440");

    await slice.getByText("More items", { exact: true }).click();
    await expect(latency.locator(".cv-lb__value").first()).toHaveText("2,428 ms");
    await expect(autonomy.locator(".cv-au__calls li")).toHaveText(["Valid159", "Refused60", "Device / transport0"]);
    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow(page);
    expect(await row.locator(".cv-provenance li, .cv-segment__item").evaluateAll(nodes => nodes.filter(node => node.getBoundingClientRect().right > document.documentElement.clientWidth).length)).toBe(0);
    await shot(page, testInfo, "dashboard-390");
  });

  test("the configurations list: a card shows each slice's successes of its newest eval, never one rate", async ({ page }) => {
    await mockApi(page, { document: slicesDocument() });
    await mockTwoRuns(page);
    await page.goto("/app/configurations");
    const card = page.locator(".cv-config").filter({ hasText: "Arm · Edge planner" });
    await expect(card.locator(".cv-config__eval")).toHaveText("Eval 2: Nominal 3/5More items 0/5");
    await expect(card.locator(".cv-config__foot")).not.toContainText("%");
    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow(page);
  });
});
