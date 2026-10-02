import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { h1, mockApi, PROJECT, tile } from "./support/configurations";

/*
 * User journeys through Configurations, each recorded as a video and a trace with
 * a screenshot at each milestone (test-results/<journey>/). Contract fixtures only:
 * the generic sample, a contract device and contract control-plane ids; every API
 * is mocked, including the workspace documents API (contract v2), so the journeys
 * are deterministic.
 */
test.use({ video: { mode: "on", size: { width: 1280, height: 800 } }, trace: "on", viewport: { width: 1280, height: 800 } });

const rows = (page: Page, name: string) => page.getByRole("table", { name }).locator("tbody tr");

/** A screenshot of a milestone, kept with the journey's video (the viewport while a dialog or the replay is open). */
async function milestone(page: Page, testInfo: TestInfo, name: string, options: { overlay?: boolean } = {}) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), `${name}: no sideways scroll`).toBe(true);
  const path = testInfo.outputPath(`${name}.png`);
  if (!options.overlay) await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path, fullPage: !options.overlay, animations: "disabled" });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

test("J1 · explore: sign in, open a configuration, its simulated robot and an eval's slices and rollouts", async ({ page }, testInfo) => {
  await mockApi(page, { signedIn: false });

  await test.step("Sign in to Projects and open existing Configurations", async () => {
    await page.goto("/app");
    await page.getByLabel("Email", { exact: true }).fill("fixture@example.test");
    await page.getByLabel("Password", { exact: true }).fill("fixture-only");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(h1(page)).toHaveText("Projects");
    await page.getByRole("link", { name: "Existing configurations", exact: true }).click();
    await expect(h1(page)).toHaveText("Configurations");
    await expect(page.locator(".cv-config")).toHaveCount(3);
    await milestone(page, testInfo, "J1-01-configurations");
  });

  await test.step("Open a configuration: four tiles and its robot", async () => {
    await page.locator(".cv-config").filter({ hasText: "Edge VLA" }).click();
    await expect(h1(page)).toHaveText("Edge VLA");
    await expect(tile(page, "Success rate")).toContainText("78%");
    await milestone(page, testInfo, "J1-02-configuration");
  });

  await test.step("Open the robot: its evals, newest first", async () => {
    await rows(page, "Robots").getByRole("link", { name: "Sim 01" }).click();
    await expect(h1(page)).toHaveText("Sim 01");
    await expect(rows(page, "Evals")).toHaveCount(3);
    await milestone(page, testInfo, "J1-03-robot");
  });

  await test.step("Open the newest eval: slices and rollouts, no replay without a real episode", async () => {
    await rows(page, "Evals").first().click();
    await expect(h1(page)).toHaveText("Eval 3");
    await expect(rows(page, "Slices")).toHaveCount(6);
    await expect(rows(page, "Rollouts")).toHaveCount(8);
    await expect(page.getByRole("button", { name: /^Replay/ })).toHaveCount(0);
    await milestone(page, testInfo, "J1-04-eval");
  });
});

test("J2 · set up: create a configuration, add a robot whose evals come from the platform, replay a real episode", async ({ page }, testInfo) => {
  const { documents } = await mockApi(page);

  await test.step("Create a configuration: the account's own workspace starts", async () => {
    await page.goto("/app/configurations");
    await page.getByRole("link", { name: "New configuration" }).click();
    await page.getByLabel("Name").fill("Arm · Edge VLA");
    await page.getByLabel("Robot").fill("Arm");
    await page.getByLabel("Edge model").fill("SmolVLA (450M)");
    await milestone(page, testInfo, "J2-01-new-configuration");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(h1(page)).toHaveText("Arm · Edge VLA");
    await expect(page.getByText("No robots yet. Add one to start.")).toBeVisible();
    await milestone(page, testInfo, "J2-02-created");
  });

  await test.step("Add a simulated robot whose evals come from a platform project", async () => {
    await page.getByRole("button", { name: "Add robot" }).click();
    const dialog = page.getByRole("dialog", { name: "Add robot" });
    await dialog.getByLabel("Name").fill("Sim runner");
    await dialog.getByText("Simulator", { exact: true }).click();
    await dialog.getByLabel("Evals from").selectOption({ label: "Contract project" });
    await milestone(page, testInfo, "J2-03-add-robot", { overlay: true });
    await dialog.getByRole("button", { name: "Add robot" }).click();
    await expect(dialog).toBeHidden();
    await expect(rows(page, "Robots").filter({ hasText: "Sim runner" })).toContainText("4");
    expect(documents.writes.map(write => write.status)).toEqual([200, 200]);
    expect(documents.writes[1].body.robots[0]).toMatchObject({ name: "Sim runner", projectId: PROJECT });
    await milestone(page, testInfo, "J2-04-robot-added");
  });

  await test.step("The robot's evals are the project's evaluations and episodes", async () => {
    await rows(page, "Robots").getByRole("link", { name: "Sim runner" }).click();
    await expect(rows(page, "Evals")).toHaveCount(4);
    await milestone(page, testInfo, "J2-05-robot-evals");
    await rows(page, "Evals").filter({ hasText: "Eval 3" }).click();
    await expect(h1(page)).toHaveText("Eval 3");
    await expect(tile(page, "Episodes")).toContainText("Seed 0");
  });

  await test.step("Replay the episode in the bottom panel: real frames, played and stepped", async () => {
    await page.getByRole("button", { name: /^Replay epi_contract02/ }).click();
    const sheet = page.getByRole("dialog", { name: "Seed 0" });
    await expect(sheet.getByRole("img", { name: "Recorded robot camera at action 0 of 54" })).toBeVisible();
    await sheet.getByRole("button", { name: "Play", exact: true }).click();
    await expect(sheet.locator(".cv-player__step")).not.toHaveText("0 / 54");
    await sheet.getByRole("button", { name: "Pause" }).click();
    await milestone(page, testInfo, "J2-06-replay", { overlay: true });
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
  });
});

test("J3 · live device: a configuration's telemetry, the robot's traces and details", async ({ page }, testInfo) => {
  await mockApi(page);

  await test.step("The configuration shows the device's telemetry row", async () => {
    await page.goto("/app/configurations/edge-planner");
    await expect(page.getByRole("region", { name: "Bench 01 telemetry" })).toBeVisible();
    await expect(tile(page, "Inference p50")).toContainText("3 requests");
    await milestone(page, testInfo, "J3-01-configuration");
  });

  await test.step("The robot opens on its traces", async () => {
    await rows(page, "Robots").getByRole("link", { name: "Bench 01" }).click();
    await expect(page.getByRole("tab", { name: /Traces/ })).toHaveAttribute("aria-selected", "true");
    await expect(rows(page, "Traces")).toHaveCount(3);
    await milestone(page, testInfo, "J3-02-traces");
  });

  await test.step("Details: the device, its model and runtime", async () => {
    await page.getByRole("tab", { name: "Details" }).click();
    await expect(page.locator(".cv-facts")).toContainText("Contract model");
    await milestone(page, testInfo, "J3-03-details");
  });
});
