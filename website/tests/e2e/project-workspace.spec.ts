import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { demoDocument, mockApi, noOverflow, PROJECT } from "./support/configurations";
import type { ConvoyWorkspace } from "@/lib/configurations/types";

test("project assignment preserves an existing setup and results across navigation and reload", async ({ page }, info) => {
  const document = demoDocument();
  const config = document.configurations[2];
  const { documents } = await mockApi(page, { document });
  await page.route(/\/api\/platform\/(robot-profiles|fleets|robot-connections|deployments)(\?|$)/, route => route.fulfill({ json: [] }));
  await page.goto(`/app/projects/${PROJECT}?section=configurations`);
  await page.getByLabel("Unassigned setup").selectOption(config.id);
  await page.getByRole("button", { name: "Assign to project", exact: true }).click();
  await expect(page.getByRole("region", { name: "Model setups", exact: true }).getByRole("link", { name: new RegExp(config.name) })).toBeVisible();
  const saved = documents.document as ConvoyWorkspace;
  expect(saved.configurations.find(c => c.id === config.id)?.projectId).toBe(PROJECT);
  for (const key of ["robots", "runs", "rollouts", "traces", "logs"] as const) expect(saved[key]).toEqual(document[key]);
  expect(saved.configurations[0]).toEqual(document.configurations[0]);
  await page.getByRole("region", { name: "Model setups", exact: true }).getByRole("link", { name: new RegExp(config.name) }).click();
  await expect(page.getByRole("navigation", { name: "Project sections" })).toBeVisible();
  await page.getByRole("navigation", { name: "Project sections" }).getByRole("link", { name: "Runs", exact: true }).click();
  await expect(page.getByRole("region", { name: "Saved evaluations" }).getByText(config.name, { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Sim runner · evaluations and traces →" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sim runner");
  await expect(page.getByRole("navigation", { name: "Project sections" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("navigation", { name: "Project sections" })).toBeVisible();
  await page.getByRole("navigation", { name: "Project sections" }).getByRole("link", { name: "Overview", exact: true }).click();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("project-overview-mobile.png"), fullPage: true });
});

test("project model setup is a draft and never dispatches execution", async ({ page }) => {
  const { documents, platform } = await mockApi(page, { document: null });
  await page.goto(`/app/configurations/new?project_id=${PROJECT}&setup=draft`);
  await page.getByRole("textbox", { name: "Name", exact: true }).fill("New paired setup");
  await page.getByRole("textbox", { name: "Robot", exact: true }).fill("Custom arm");
  await page.getByLabel("Edge model", { exact: true }).fill("SmolVLA");
  await page.getByRole("textbox", { name: "Cloud model", exact: true }).fill("Cloud planner");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("New paired setup");
  const config = (documents.document as ConvoyWorkspace).configurations[0];
  expect(config.projectId).toBe(PROJECT);
  expect(config.status).toBe("draft");
  expect(platform.paths.some(path => path === "deployments" || path.includes("/missions"))).toBe(false);
  await expect(page.getByRole("navigation", { name: "Project sections" })).toBeVisible();
});
