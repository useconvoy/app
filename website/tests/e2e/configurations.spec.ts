import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { demoDocument, h1, mockApi, noOverflow, PROJECT } from "./support/configurations";

// The frame and the workspace document: sign-in, top bar, routes, fallbacks, import, accessibility.

const crumbs = (page: Page) => page.getByRole("navigation", { name: "Breadcrumb" });
const areas = (page: Page) => page.locator("header.cv-bar").getByRole("navigation", { name: "Workspace" });

async function signIn(page: Page) {
  await page.getByLabel("Email", { exact: true }).fill("fixture@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fixture-only");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

test("the Configurations journey keeps the plain frame: no project sidebar or sections, no account address, its own breadcrumbs", async ({ page }) => {
  const document = demoDocument();
  // Even a configuration assigned to a project opens without the project's navigation.
  document.configurations[2] = { ...document.configurations[2], projectId: PROJECT };
  await mockApi(page, { document });
  let applications = [{ id: "app_contract01", project_id: PROJECT, name: "Contract reach" }];
  await page.route(/\/api\/platform\/applications\?project_id=/, route => route.fulfill({ json: applications }));
  async function frame(trail: string[]) {
    await expect(crumbs(page).getByRole("listitem")).toHaveText(trail);
    await expect(page.locator("aside")).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Project sections" })).toHaveCount(0);
    await expect(page.getByText(/All projects/)).toHaveCount(0);
    await expect(areas(page).getByRole("link", { name: "Configurations" })).toHaveAttribute("aria-current", /page|true/);
    await expect(page.locator("header.cv-bar")).not.toContainText("fixture@example.test");
    await expect(page.getByText("fixture@example.test")).toHaveCount(0);
  }

  await page.goto("/app/configurations");
  await frame(["Configurations"]);
  await expect(page.getByRole("combobox")).toHaveCount(0);
  await expect(page.getByText(/Saved workspace configurations|Configuration releases pin|Create runnable configuration/)).toHaveCount(0);
  // One compact row for the projects' runnable configurations, after the cards.
  await expect(page.locator(".cv-config")).toHaveCount(3);
  await expect(page.getByRole("link", { name: /^1 project configuration/ })).toHaveAttribute("href", "/app/configurations/app_contract01?source=project");

  await page.locator(".cv-config").filter({ hasText: "Arm · Edge VLA" }).click();
  await frame(["Configurations", "Arm · Edge VLA"]);
  await expect(page.getByText(/Project execution|Link this saved setup/)).toHaveCount(0);
  const link = page.getByRole("region", { name: "Project configuration" });
  await expect(link, "the link lives under Details only").toHaveCount(0);
  await page.getByRole("tab", { name: "Details" }).click();
  await expect(link).toContainText("Not linked");
  await expect(link.getByRole("button", { name: "Link" })).toBeVisible();
  await page.getByRole("tab", { name: /^Robots/ }).click();

  await page.getByRole("table", { name: "Robots" }).getByRole("link", { name: "Sim runner" }).click();
  await frame(["Configurations", "Arm · Edge VLA", "Sim runner"]);
  await page.getByRole("table", { name: "Evals" }).locator("tbody tr").first().click();
  await expect(h1(page)).toHaveText(/^Eval \d+$/);
  const label = (await h1(page).textContent()) ?? "";
  await frame(["Configurations", "Arm · Edge VLA", "Sim runner", label]);
  await crumbs(page).getByRole("link", { name: "Configurations" }).click();
  await expect(h1(page)).toHaveText("Configurations");

  // Without runnable configurations there is no row and no link.
  applications = [];
  await page.reload();
  await expect(page.locator(".cv-config")).toHaveCount(3);
  await expect(page.getByText(/project configuration/)).toHaveCount(0);
  await page.goto("/app/configurations/arm-edge-vla?tab=details");
  await expect(page.getByRole("region", { name: "Specification" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Project configuration" })).toHaveCount(0);
});

test("/app lands on Configurations for an account with saved configurations, after sign-in and for an open session", async ({ page }) => {
  await mockApi(page, { signedIn: false, document: demoDocument() });
  await page.goto("/app");
  await expect(page).toHaveURL(/\/app\/projects\/?$/);
  await signIn(page);
  await expect(page).toHaveURL(/\/app\/configurations\/?$/);
  await expect(h1(page)).toHaveText("Configurations");
  // Projects from the bar stays on Projects.
  await areas(page).getByRole("link", { name: "Projects" }).click();
  await expect(h1(page)).toHaveText("Projects");
  await expect(page).toHaveURL(/\/app\/projects\/?$/);
  await page.reload();
  await expect(h1(page)).toHaveText("Projects");
  await page.goto("/app");
  await expect(page).toHaveURL(/\/app\/configurations\/?$/);
  await expect(h1(page)).toHaveText("Configurations");
});

test("/app keeps an account without saved configurations on Projects", async ({ page }) => {
  await mockApi(page);
  await page.goto("/app");
  await expect(h1(page)).toHaveText("Projects");
  await expect(page).toHaveURL(/\/app\/projects\/?$/);
  await expect(areas(page).getByRole("link", { name: "Projects" })).toHaveAttribute("aria-current", "page");
});

test("Configurations remains available behind the shared sign-in, in a top bar with no sidebar", async ({ page }) => {
  await mockApi(page, { signedIn: false });
  await page.goto("/app/configurations");
  await expect(page).toHaveURL(/\/app\/configurations\/?$/);
  await expect(page.getByRole("heading", { name: "One workspace for your robots." })).toBeVisible();
  await page.getByLabel("Email", { exact: true }).fill("fixture@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fixture-only");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(h1(page)).toHaveText("Configurations");
  expect(await page.title()).toBe("Convoy | Configurations");
  const bar = page.locator("header.cv-bar");
  await expect(bar.getByRole("link", { name: "Convoy" })).toHaveAttribute("href", "/app/configurations");
  await expect(bar.getByText("Sample", { exact: true })).toBeVisible();
  // The two areas in the bar; the account shows initials, never its address.
  const areas = bar.getByRole("navigation", { name: "Workspace" });
  await expect(areas.getByRole("link", { name: "Configurations" })).toHaveAttribute("aria-current", "page");
  await expect(areas.getByRole("link", { name: "Projects" })).toHaveAttribute("href", "/app/projects");
  await expect(page.getByText("fixture@example.test")).toHaveCount(0);
  await expect(page.locator("aside")).toHaveCount(0);
  await expect(page.locator('a[href^="/app/applications"], a[href*="section=device"]')).toHaveCount(0);
  const account = bar.getByRole("button", { name: "Account" });
  await expect(account).toHaveText("F");
  await account.click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "One workspace for your robots." })).toBeVisible();
});

test("every page renders from the sample; breadcrumbs lead back up; unknown ids are not found", async ({ page }) => {
  await mockApi(page);
  await page.goto("/app/configurations/edge-vla/robots/sim-01/evals/run-3");
  await expect(h1(page)).toHaveText("Eval 3");
  await expect(crumbs(page).locator("[aria-current=page]")).toHaveText("Eval 3");
  await crumbs(page).getByRole("link", { name: "Sim 01" }).click();
  await expect(h1(page)).toHaveText("Sim 01");
  await crumbs(page).getByRole("link", { name: "Edge VLA" }).click();
  await expect(h1(page)).toHaveText("Edge VLA");
  await crumbs(page).getByRole("link", { name: "Configurations" }).click();
  await expect(h1(page)).toHaveText("Configurations");

  await page.goto("/app/configurations/not-a-config");
  await expect(page.getByText("Configuration not found.")).toBeVisible();
  await page.goto("/app/configurations/cloud-planner/robots/sim-01");
  await expect(page.getByText("Robot not found.")).toBeVisible();
  await page.getByRole("link", { name: "Back to Cloud planner" }).click();
  await expect(h1(page)).toHaveText("Cloud planner");
  await page.goto("/app/configurations/edge-vla/robots/sim-01/evals/run-404");
  await expect(page.getByText("Eval not found.")).toBeVisible();
  await page.goto("/app/configurations/edge-vla/robots/sim-01/evals/eva_contract01");
  await expect(page.getByText("Eval not found."), "a robot without a project has no platform evals").toBeVisible();
});

test("a valid stored document is used; an invalid one falls back to the sample with one short notice", async ({ page }) => {
  await mockApi(page, { document: demoDocument() });
  await page.goto("/app/configurations");
  await expect(page.locator(".cv-config h2")).toHaveText(["Arm · Edge planner", "Arm · Cloud", "Arm · Edge VLA"]);
  await expect(page.locator(".cv-sample")).toHaveCount(0);
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mockApi(page, { document: { ...createSampleWorkspace(), schemaVersion: 2 } });
  await page.reload();
  await expect(page.locator(".cv-notice")).toHaveCount(1);
  await expect(page.locator(".cv-notice")).toContainText("Saved workspace is not valid. Showing sample.");
  await expect(page.locator(".cv-config h2")).toHaveText(["Edge planner", "Cloud planner", "Edge VLA"]);
});

test("import creates the document with If-None-Match, and replaces a stored one only after asking, with If-Match", async ({ page }) => {
  const { documents } = await mockApi(page);
  await page.goto("/app/configurations");
  const input = page.getByLabel("Import workspace");
  await input.setInputFiles({ name: "broken.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ schemaVersion: 1 })) });
  await expect(page.locator(".cv-import__error")).toContainText("Not imported: meta: is required");
  await input.setInputFiles({ name: "huge.json", mimeType: "application/json", buffer: Buffer.alloc(2 * 1024 * 1024 + 1, 32) });
  await expect(page.locator(".cv-import__error")).toContainText("the limit is 2 MiB");
  expect(documents.writes).toHaveLength(0);
  const document = demoDocument();
  await input.setInputFiles({ name: "workspace.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(document)) });
  await expect(page.locator(".cv-config h2").first()).toHaveText("Arm · Edge planner");
  expect(documents.writes).toHaveLength(1);
  expect(documents.writes[0]).toMatchObject({ ifNoneMatch: "*", ifMatch: null, client: "web", status: 200 });
  expect(documents.writes[0].idempotencyKey).toMatch(/^[\x21-\x7e]{1,128}$/);
  await expect(page.locator(".cv-sample")).toHaveCount(0);

  const replacement = createSampleWorkspace();
  replacement.meta = { ...replacement.meta, sample: false };
  await input.setInputFiles({ name: "replacement.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(replacement)) });
  const dialog = page.getByRole("dialog", { name: "Replace the saved workspace?" });
  await expect(dialog).toContainText("replacement.json replaces this account's workspace.");
  await dialog.getByRole("button", { name: "Replace" }).click();
  await expect(page.locator(".cv-config h2").first()).toHaveText("Edge planner");
  expect(documents.writes[1]).toMatchObject({ ifMatch: "\"1\"", ifNoneMatch: null, status: 200 });
});

test("the sample is never saved: New configuration starts the account's own workspace with only that configuration", async ({ page }) => {
  const { documents } = await mockApi(page);
  await page.goto("/app/configurations/edge-vla");
  await expect(h1(page)).toHaveText("Edge VLA");
  await expect(page.getByRole("button", { name: "Add robot" }), "sample configurations are read-only").toHaveCount(0);
  await page.goto("/app/configurations/new");
  await page.getByLabel("Name").fill("Arm · Edge planner");
  await page.getByLabel("Robot").fill("Arm");
  await page.getByLabel("Edge model").fill("Qwen2.5-1.5B-Instruct Q4_K_M");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(h1(page)).toHaveText("Arm · Edge planner");
  await expect(page).toHaveURL(/\/app\/configurations\/arm-edge-planner$/);
  expect(documents.writes).toHaveLength(1);
  expect(documents.writes[0]).toMatchObject({ ifNoneMatch: "*", status: 200 });
  expect(documents.writes[0].body.configurations.map(config => config.name)).toEqual(["Arm · Edge planner"]);
  expect([documents.writes[0].body.robots, documents.writes[0].body.runs, documents.writes[0].body.meta.sample]).toEqual([[], [], undefined]);
  await expect(page.locator(".cv-sample")).toHaveCount(0);
  await crumbs(page).getByRole("link", { name: "Configurations" }).click();
  await expect(page.locator(".cv-config h2")).toHaveText(["Arm · Edge planner"]);
});

const PAGES: Array<[string, string]> = [
  ["index", "/app/configurations"],
  ["new", "/app/configurations/new"],
  ["dashboard", "/app/configurations/edge-planner"],
  ["dashboard details", "/app/configurations/edge-planner?tab=details"],
  ["live robot", "/app/configurations/edge-planner/robots/bench-01"],
  ["simulated robot", "/app/configurations/edge-vla/robots/sim-01"],
  ["eval", "/app/configurations/edge-vla/robots/sim-01/evals/run-3"],
];
for (const width of [390, 1440]) {
  test(`no accessibility violations and no sideways scroll at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    for (const [name, path] of PAGES) {
      await page.goto(path);
      await expect(h1(page), name).toBeVisible();
      await page.waitForLoadState("networkidle");
      await noOverflow(page);
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations.map(violation => `${name}: ${violation.id} ${violation.nodes.map(node => node.target.join(" ")).join(", ")}`)).toEqual([]);
    }
    // The replay panel and a dialog, from the account's own workspace.
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await mockApi(page, { document: demoDocument() });
    await page.goto("/app/configurations/arm-edge-vla/robots/sim-runner/evals/eva_contract02?rollout=epi_contract02");
    await expect(page.getByRole("img", { name: "Recorded robot camera at action 0 of 54" })).toBeVisible();
    await noOverflow(page);
    // Check settled colors, not a partially transparent entrance animation.
    await page.locator(".cv-sheet").evaluate(async node => { await Promise.all(node.getAnimations().map(animation => animation.finished)); });
    expect((await new AxeBuilder({ page }).analyze()).violations.map(violation => `replay: ${violation.id} ${violation.nodes.map(node => `${node.target.join(" ")}: ${node.failureSummary}`).join(", ")}`)).toEqual([]);
    await page.goto("/app/configurations/arm-edge-vla");
    await page.getByRole("button", { name: "Add robot" }).click();
    await expect(page.getByRole("dialog", { name: "Add robot" })).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations.map(violation => `add robot: ${violation.id}`)).toEqual([]);
  });
}
