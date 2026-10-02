import { AxeBuilder } from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { demoDocument, mockApi, noOverflow, PROJECT } from "./support/configurations";

test("a saved configuration links to a project configuration from Details, preserves its results, flags edits and can be unlinked", async ({ page }, testInfo) => {
  const document = demoDocument();
  const config = document.configurations[0];
  const { documents } = await mockApi(page, { document, revision: 4 });
  const application = { id: "app_execution", project_id: PROJECT, name: "Arm target experiment" };
  const writes: { path: string; body: unknown }[] = [];
  const association = { id: "wcl_one", configuration_id: config.id, source_name: config.name, source_revision: 4,
    document_revision: 4, source_state: "unchanged", application, project_name: "Contract project" };
  let linked = false;
  let conflict = true;
  await page.route("**/api/platform/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/platform/", "");
    if (request.method() === "GET") {
      if (path === "workspace-configuration-links") return route.fulfill({ json: linked ? [association] : [] });
      if (path === "applications") return route.fulfill({ json: [application] });
      if (path === "applications/app_execution") return route.fulfill({ json: application });
      if (path === "applications/app_execution/releases" || path === "deployments") return route.fulfill({ json: [] });
    }
    if (request.method() === "POST" && path.startsWith("workspace-configuration-links")) {
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      writes.push({ path, body: request.postDataJSON() });
      if (path.endsWith("/remove")) {
        linked = false;
        return route.fulfill({ json: { id: association.id, deleted: true } });
      }
      if (conflict) { conflict = false; return route.fulfill({ status: 409, json: { error: "workspace changed; reload and review it before linking" } }); }
      expect(request.postDataJSON()).toEqual({ configuration_id: config.id, application_id: application.id, document_revision: 4, expected_link_id: null });
      linked = true;
      return route.fulfill({ status: 201, json: association });
    }
    return route.fallback();
  });
  await page.goto(`/app/configurations/${config.id}`);
  // The link lives under Details as one row, not in the dashboard's main flow.
  await expect(page.getByText("Project execution")).toHaveCount(0);
  await page.getByRole("tab", { name: "Details" }).click();
  const linking = page.getByRole("region", { name: "Project configuration" });
  await expect(linking).toContainText("Not linked");
  await linking.getByRole("button", { name: "Link", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Link to a project configuration" });
  // The only runnable configuration is chosen; linking stays an explicit step.
  await expect(dialog.getByLabel("Project configuration")).toHaveValue(application.id);
  await dialog.getByRole("button", { name: "Link", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("workspace changed");
  await expect(linking.getByRole("link", { name: "Open" })).toHaveCount(0);
  // A retry remains explicit; no deployment or workspace write is made.
  await dialog.getByRole("button", { name: "Link", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(linking).toContainText(application.name);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await linking.getByRole("link", { name: "Open" }).click();
  await expect(page).toHaveURL(/app_execution\?source=project$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(application.name);
  await page.getByRole("region", { name: "Linked saved configurations" }).getByRole("link", { name: config.name }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(config.name);
  await expect(page.getByRole("table", { name: "Robots", exact: true })).toContainText("Bench 01");
  association.source_state = "changed";
  await page.goto(`/app/configurations/${config.id}?tab=details`);
  await expect(linking).toContainText("Changed since linking");
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("saved-configuration-link-mobile.png"), fullPage: true });
  await linking.getByRole("button", { name: "Unlink" }).click();
  await expect(linking.getByRole("link", { name: "Open" })).toHaveCount(0);
  await expect(linking).toContainText("Not linked");
  await expect(linking.getByRole("button", { name: "Link", exact: true })).toBeVisible();
  expect(documents.writes).toHaveLength(0);
  expect(documents.document).toEqual(document);
  expect(writes.map(write => write.path)).toEqual(["workspace-configuration-links", "workspace-configuration-links", "workspace-configuration-links/wcl_one/remove"]);
});
