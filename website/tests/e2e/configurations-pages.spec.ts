import { expect, test, type Page } from "@playwright/test";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { demoDocument, h1, mockApi, PLATFORM_ROBOT, PROJECT, tile } from "./support/configurations";

// One page at a time: configurations, new configuration, the config dashboard, robots and evals.

const table = (page: Page, name: string) => page.getByRole("table", { name });
const rows = (page: Page, name: string) => table(page, name).locator("tbody tr");
const VLA = "/app/configurations/arm-edge-vla";
const RUNNER = `${VLA}/robots/sim-runner`;

test.describe("configurations", () => {
  test("three equal cards: the stack, the robots and the latest eval in one line each", async ({ page }) => {
    await mockApi(page);
    await page.goto("/app/configurations");
    const cards = page.locator(".cv-config");
    await expect(cards.locator("h2")).toHaveText(["Edge planner", "Cloud planner", "Edge VLA"]);
    const edge = cards.filter({ hasText: "Edge planner" });
    await expect(edge.locator("dd")).toHaveText(["Tabletop arm", "Jetson Orin Nano Super 8 GB", "Qwen2.5-1.5B", "–"]);
    await expect(edge.locator(".cv-config__foot")).toContainText("1 robot");
    await expect(edge.locator(".cv-config__foot")).toContainText("1 online");
    await expect(edge.locator(".cv-config__foot")).toContainText("No evals");
    await expect(cards.filter({ hasText: "Cloud planner" }).locator(".cv-badge").last()).toHaveText("Running 24/60");
    await expect(cards.filter({ hasText: "Edge VLA" }).locator(".cv-config__eval")).toHaveText("78 %Passed");
    const heights = await cards.evaluateAll(nodes => nodes.map(node => Math.round(node.getBoundingClientRect().height)));
    expect(new Set(heights).size, `equal card heights ${heights.join(", ")}`).toBe(1);
    await cards.filter({ hasText: "Edge VLA" }).click();
    await expect(h1(page)).toHaveText("Edge VLA");
  });

  test("an empty workspace says so and offers New configuration", async ({ page }) => {
    const empty = createSampleWorkspace();
    empty.meta = { ...empty.meta, sample: false };
    empty.configurations = []; empty.robots = []; empty.runs = []; empty.rollouts = [];
    await mockApi(page, { document: empty });
    await page.goto("/app/configurations");
    await expect(page.getByText("No configurations yet.")).toBeVisible();
    await page.locator(".cv-empty").getByRole("link", { name: "New configuration" }).click();
    await expect(h1(page)).toHaveText("New configuration");
  });
});

test.describe("new configuration", () => {
  test("checks the name, robot and models; the route follows from the models; Create saves r1 and opens it", async ({ page }) => {
    const { documents } = await mockApi(page, { document: demoDocument() });
    await page.goto("/app/configurations/new");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByText("Enter a name.")).toBeVisible();
    await expect(page.getByText("Enter the robot.")).toBeVisible();
    await expect(page.getByText("Add an edge or a cloud model.")).toBeVisible();
    await expect(page.getByLabel("Name")).toBeFocused();
    expect(documents.writes).toHaveLength(0);
    await page.getByLabel("Name").fill("arm · edge planner");
    await expect(page.getByText("A configuration has this name.")).toBeVisible();
    await page.getByLabel("Name").fill("Arm · Hybrid");
    await page.getByLabel("Robot").fill("Arm");
    await page.getByLabel("Edge hardware").selectOption({ label: "Jetson Orin NX 16 GB" });
    await page.getByLabel("Edge model").fill("SmolVLA (450M)");
    await expect(page.getByLabel("Role").first()).toHaveValue("policy");
    await expect(page.getByText("Route: Edge only")).toBeVisible();
    await page.getByLabel("Cloud model").fill("Hosted planner");
    await expect(page.getByText("Route: Edge + cloud")).toBeVisible();
    await page.getByRole("button", { name: "Create" }).click();
    await expect(h1(page)).toHaveText("Arm · Hybrid");
    await expect(page.locator(".cv-head .cv-badge")).toHaveText("Testing");
    const saved = documents.writes.at(-1)!;
    expect(saved).toMatchObject({ ifMatch: "\"1\"", status: 200 });
    const config = saved.body.configurations.find(item => item.name === "Arm · Hybrid")!;
    expect([config.status, config.candidateRev, config.revisions[0].edgeHardware.name, config.revisions[0].routing.mode]).toEqual(["testing", "r1", "Jetson Orin NX 16 GB", "hybrid"]);
    expect(config.revisions[0].edgeModels.map(model => [model.name, model.role])).toEqual([["SmolVLA (450M)", "policy"]]);
    expect(config.revisions[0].cloudModels.map(model => [model.name, model.role])).toEqual([["Hosted planner", "planner"]]);
    await page.getByRole("tab", { name: "Details" }).click();
    await expect(page.locator(".cv-facts")).toContainText("Edge + cloud");
  });
});

test.describe("config dashboard", () => {
  test("four KPI tiles, the live device's telemetry row and the robots table", async ({ page }) => {
    await mockApi(page);
    await page.goto("/app/configurations/edge-planner");
    await expect(h1(page)).toHaveText("Edge planner");
    await expect(page.locator(".cv-tiles").first().getByRole("group")).toHaveCount(4);
    await expect(tile(page, "Robots")).toContainText("1 online");
    await expect(tile(page, "Evals")).toContainText("None yet");
    await expect(tile(page, "Inference p50")).toContainText("210ms");
    await expect(tile(page, "Inference p50")).toContainText("3 requests");
    const row = page.getByRole("region", { name: "Bench 01 telemetry" });
    await expect(row.getByRole("group")).toHaveCount(4);
    await expect(row.getByRole("group", { name: "SoC temperature" })).toContainText("47.1°C");
    await expect(row.locator(".cv-badge")).toHaveText("Online");
    await expect(rows(page, "Robots")).toHaveCount(1);
    await expect(rows(page, "Robots").first()).toContainText("Bench 01Live deviceOnline0");
    const heights = await page.locator(".cv-tile").evaluateAll(nodes => nodes.map(node => Math.round(node.getBoundingClientRect().height)));
    expect(new Set(heights).size, "tiles are equal").toBe(1);
    await page.getByRole("tab", { name: "Details" }).click();
    await expect(page).toHaveURL(/\?tab=details$/);
    await expect(page.locator(".cv-facts dt")).toHaveText(["Robot", "Edge hardware", "Power mode", "Edge model", "Cloud model", "Route", "Revision", "Created"]);
    await page.getByRole("tab", { name: /Robots/ }).click();
    await rows(page, "Robots").getByRole("link", { name: "Bench 01" }).click();
    await expect(h1(page)).toHaveText("Bench 01");
  });

  test("a simulator's evals feed the tiles; an unreachable device says No data, never zero", async ({ page }) => {
    await mockApi(page, { device: "unavailable" });
    await page.goto("/app/configurations/edge-vla");
    await expect(tile(page, "Robots"), "simulators are idle, not offline").toContainText("Idle");
    await expect(tile(page, "Evals")).toContainText("3");
    await expect(tile(page, "Success rate")).toContainText("78%");
    await expect(tile(page, "Success rate")).toContainText("Eval 3 · 47/60");
    await expect(tile(page, "Inference p50")).toContainText("No live device");
    await expect(page.getByRole("region", { name: /telemetry/ })).toHaveCount(0);
    await expect(rows(page, "Robots").first()).toContainText("Sim 01SimulatorIdle3Passed");
    await page.goto("/app/configurations/edge-planner");
    await expect(rows(page, "Robots").first().locator(".cv-badge")).toHaveText("No data");
    await expect(tile(page, "Inference p50").locator(".cv-tile__value")).toHaveText("–Not reported");
  });

  test("Add robot binds a live device or a project's evals, and saves to the stored revision", async ({ page }) => {
    const { documents } = await mockApi(page, { document: demoDocument() });
    await page.goto("/app/configurations/arm-cloud");
    await page.getByRole("button", { name: "Add robot" }).click();
    const dialog = page.getByRole("dialog", { name: "Add robot" });
    await expect(dialog.getByLabel("Name")).toBeFocused();
    await dialog.getByRole("button", { name: "Add robot" }).click();
    await expect(dialog.getByText("Enter a name.")).toBeVisible();
    await dialog.getByLabel("Name").fill("bench 02");
    await expect(dialog.getByText("This configuration has a robot with this name.")).toBeVisible();
    await dialog.getByLabel("Name").fill("Sim 02");
    await expect(dialog.getByLabel("Device", { exact: true }).locator("option"), "simulated devices are not live devices").toHaveText(["Contract Jetson · online"]);
    await dialog.getByText("Simulator", { exact: true }).click();
    await expect(dialog.getByLabel("Device", { exact: true })).toHaveCount(0);
    await dialog.getByLabel("Evals from").selectOption({ label: "Contract project" });
    await dialog.getByLabel("Platform robot").selectOption({ label: "Contract simulator" });
    await dialog.getByRole("button", { name: "Add robot" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText("Sim 02 added.")).toBeVisible();
    await expect(rows(page, "Robots")).toHaveCount(2);
    const write = documents.writes.at(-1)!;
    expect(write).toMatchObject({ ifMatch: "\"1\"", status: 200 });
    expect(write.body.robots.find(robot => robot.name === "Sim 02")).toMatchObject({ configId: "arm-cloud", role: "test", rev: "r1", kind: "simulator", projectId: PROJECT, platformRobotId: PLATFORM_ROBOT });
    await expect(rows(page, "Robots").filter({ hasText: "Sim 02" })).toContainText("4");
  });

  test("Delete configuration asks first, removes it with its robots and returns to the list", async ({ page }) => {
    const { documents } = await mockApi(page, { document: demoDocument() });
    await page.goto("/app/configurations/arm-cloud?tab=details");
    await page.getByRole("button", { name: "Delete configuration" }).click();
    const dialog = page.getByRole("dialog", { name: "Delete Arm · Cloud?" });
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
    await dialog.getByRole("button", { name: "Delete" }).click();
    await expect(h1(page)).toHaveText("Configurations");
    await expect(page.locator(".cv-config h2")).toHaveText(["Arm · Edge planner", "Arm · Edge VLA"]);
    const body = documents.writes.at(-1)!.body;
    expect(body.robots.map(robot => robot.name)).toEqual(["Bench 01", "Sim runner"]);
  });
});

test.describe("robot", () => {
  test("a live device: measured tiles, its traces first when it has no evals, and its details", async ({ page }) => {
    await mockApi(page);
    await page.goto("/app/configurations/edge-planner/robots/bench-01");
    await expect(h1(page)).toHaveText("Bench 01");
    await expect(page.locator(".cv-head .cv-badge")).toHaveText(["Online", "Live device"]);
    await expect(page.locator(".cv-tile .cv-tile__label")).toHaveText(["Inference p50", "CPU", "Memory free", "SoC temperature"]);
    await expect(tile(page, "Memory free")).toContainText("3.4GiB");
    await expect(page.getByRole("tab", { name: /Traces/ })).toHaveAttribute("aria-selected", "true");
    await expect(rows(page, "Traces")).toHaveCount(3);
    await expect(rows(page, "Traces").first()).toContainText("tr_contract_0");
    await expect(rows(page, "Traces").first()).toContainText("181 ms");
    await expect(rows(page, "Traces").first().locator(".cv-badge")).toHaveText("OK");
    await page.getByRole("tab", { name: /Evals/ }).click();
    await expect(page.getByText("No evals yet.")).toBeVisible();
    await page.getByRole("tab", { name: "Details" }).click();
    await expect(page.locator(".cv-facts")).toContainText("dev_contract");
    await expect(page.locator(".cv-facts")).toContainText("Contract model");
    await expect(page.locator(".cv-facts")).toContainText("llama.cpp · cuda");
  });

  test("a simulator's stored evals: newest first, one line each, each opens its eval", async ({ page }) => {
    await mockApi(page);
    await page.goto("/app/configurations/edge-vla/robots/sim-01");
    await expect(page.locator(".cv-head .cv-badge")).toHaveText(["Idle", "Simulator"]);
    await expect(page.getByRole("tab", { name: "Traces" })).toHaveCount(0);
    await expect(rows(page, "Evals")).toHaveCount(3);
    await expect(rows(page, "Evals").locator("th")).toHaveText(["Eval 3", "Eval 2", "Eval 1"]);
    await expect(rows(page, "Evals").locator(".cv-badge")).toHaveText(["Passed", "Passed", "Below gate"]);
    await expect(tile(page, "Episodes")).toContainText("180");
    await rows(page, "Evals").first().click();
    await expect(h1(page)).toHaveText("Eval 3");
  });

  test("a robot linked to a project lists the platform's evaluations and episodes as evals", async ({ page }) => {
    const { platform } = await mockApi(page, { document: demoDocument() });
    await page.goto(RUNNER);
    await expect(rows(page, "Evals")).toHaveCount(4);
    await expect(rows(page, "Evals").locator("th")).toHaveText(["Eval 4", "Eval 3", "Eval 2", "Eval 1"]);
    await expect(rows(page, "Evals").locator(".cv-badge")).toHaveText(["Passed", "Passed", "Passed", "Passed"]);
    expect(platform.paths).toEqual(expect.arrayContaining([`evaluations?project_id=${PROJECT}`, `missions?project_id=${PROJECT}`, "episodes/epi_contract03", "episodes/epi_contract04"]));
    expect(platform.paths.some(path => path.includes("prj_elsewhere")), "only the robot's project is read").toBe(false);
    await expect(tile(page, "Success rate")).toContainText("Eval 4 · 1/1");
    await page.getByRole("tab", { name: "Details" }).click();
    await expect(page.locator(".cv-facts")).toContainText("Contract project");
    await expect(page.locator(".cv-facts")).toContainText("Contract simulator");
  });

  test("Remove robot asks first and removes it", async ({ page }) => {
    const { documents } = await mockApi(page, { document: demoDocument() });
    await page.goto(`${RUNNER}?tab=details`);
    await page.getByRole("button", { name: "Remove robot" }).click();
    await page.getByRole("dialog", { name: "Remove Sim runner?" }).getByRole("button", { name: "Remove" }).click();
    await expect(h1(page)).toHaveText("Arm · Edge VLA");
    await expect(page.getByText("No robots yet. Add one to start.")).toBeVisible();
    expect(documents.writes.at(-1)!.body.robots.map(robot => robot.name)).toEqual(["Bench 01", "Bench 02"]);
  });
});

test.describe("eval", () => {
  test("a stored sample eval: tiles, slices and rollouts with no replay", async ({ page }) => {
    await mockApi(page);
    await page.goto("/app/configurations/edge-vla/robots/sim-01/evals/run-3");
    await expect(h1(page)).toHaveText("Eval 3");
    await expect(page.locator(".cv-head .cv-badge")).toHaveText("Passed");
    await expect(tile(page, "Success rate")).toContainText("78%");
    await expect(tile(page, "Success rate")).toContainText("47 of 60");
    await expect(tile(page, "Median time")).toContainText("Simulated");
    await expect(rows(page, "Slices")).toHaveCount(6);
    await expect(rows(page, "Slices").first()).toContainText("Normal light");
    await expect(rows(page, "Rollouts")).toHaveCount(8);
    await expect(page.getByRole("button", { name: /^Replay/ }), "no fake replay").toHaveCount(0);
    await page.goto("/app/configurations/edge-vla/robots/sim-01/evals/run-3?rollout=run-3-ep-01");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByText("No replay for this rollout.")).toBeVisible();
    await page.getByRole("tab", { name: "Details" }).click();
    await expect(page.locator(".cv-facts")).toContainText("Pick and place v1");
  });

  test("a platform evaluation: its cases, and the replay in a bottom panel with the episode's frames", async ({ page }) => {
    const { platform } = await mockApi(page, { document: demoDocument() });
    await page.goto(`${RUNNER}/evals/eva_contract02`);
    await expect(h1(page)).toHaveText("Eval 3");
    await expect(tile(page, "Median time")).toContainText("41.2s");
    await expect(rows(page, "Rollouts")).toHaveCount(1);
    await expect(rows(page, "Rollouts").first()).toContainText("epi_contract02");
    const replay = page.getByRole("button", { name: "Replay epi_contract02, seed 0" });
    await replay.click();
    await expect(page).toHaveURL(/[?&]rollout=epi_contract02$/);
    const sheet = page.getByRole("dialog", { name: "Seed 0" });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByRole("img", { name: "Recorded robot camera at action 0 of 54" })).toBeVisible();
    await expect(sheet.locator(".cv-player__readout")).toContainText("pick_place_puck");
    await sheet.getByRole("button", { name: "Next step" }).click();
    await expect(sheet.getByRole("img", { name: "Recorded robot camera at action 1 of 54" })).toBeVisible();
    await expect(sheet.locator(".cv-player__readout")).toContainText("512 ms");
    await expect(sheet.locator(".cv-player__action")).toContainText("0.12");
    await sheet.getByRole("button", { name: "Play", exact: true }).click();
    await expect(sheet.getByRole("button", { name: "Pause" })).toBeVisible();
    await expect(sheet.locator(".cv-player__step")).not.toHaveText("1 / 54", { timeout: 5000 });
    await sheet.getByRole("button", { name: "Pause" }).click();
    await sheet.getByRole("slider", { name: "Replay position" }).press("End");
    await expect(sheet.getByRole("button", { name: "Replay", exact: true })).toBeVisible();
    expect(platform.frames).toEqual(expect.arrayContaining([0, 1, 2, 54]));
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
    await expect(page).not.toHaveURL(/rollout=/);
    await expect(replay).toBeFocused();
    // A deep link opens it; Close returns to the eval.
    await page.goto(`${RUNNER}/evals/eva_contract02?rollout=epi_contract02`);
    await expect(page.getByRole("dialog", { name: "Seed 0" })).toBeVisible();
    await page.getByRole("button", { name: "Close replay" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(h1(page)).toHaveText("Eval 3");
  });

  test("an episode run outside an evaluation is an eval of one replayable rollout; a missing recording says so", async ({ page }) => {
    await mockApi(page, { document: demoDocument(), missingRecording: "epi_contract04" });
    await page.goto(`${RUNNER}/evals/mis_contract04`);
    await expect(h1(page)).toHaveText("Eval 4");
    await expect(tile(page, "Episodes")).toContainText("Seed 0");
    await expect(rows(page, "Rollouts").first()).toContainText("epi_contract04");
    await page.getByRole("button", { name: /^Replay epi_contract04/ }).click();
    await expect(page.getByRole("dialog").getByText("No recording for this episode.")).toBeVisible();
  });

  test("evals of another project or platform robot are not this robot's; a stored run uses its recorded evaluation", async ({ page }) => {
    const document = demoDocument();
    const runner = document.robots.find(robot => robot.id === "sim-runner")!;
    await mockApi(page, { document });
    await page.goto(`${RUNNER}/evals/eva_elsewhere`);
    await expect(page.getByText("Eval not found.")).toBeVisible();
    await page.goto(`${RUNNER}/evals/eva_missing`);
    await expect(page.getByText("Eval not found.")).toBeVisible();
    // A stored run that names a recorded evaluation shows the evaluation's report and real episodes.
    const sample = createSampleWorkspace();
    document.suites = sample.suites;
    const run = { ...sample.runs[2], id: "run-7", number: 7, configId: "arm-edge-vla", robotId: runner.id, recordedEvaluationId: "eva_contract01", provenance: { kind: "recorded" as const, at: "2026-10-01T04:00:39Z" } };
    document.runs = [run];
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await mockApi(page, { document });
    await page.goto(`${RUNNER}/evals/run-7`);
    await expect(h1(page)).toHaveText("Eval 7");
    await expect(tile(page, "Success rate")).toContainText("1 of 1");
    await expect(rows(page, "Slices")).toHaveCount(6);
    await expect(rows(page, "Rollouts")).toHaveCount(1);
    await expect(page.getByRole("button", { name: /^Replay epi_contract01/ })).toBeVisible();
  });
});
