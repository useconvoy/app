import { AxeBuilder } from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/*
 * A simulated robot that runs an existing execution profile (no registered robot profile)
 * keeps its policy and evaluation tools on its robot page. Contract fixtures only.
 */
test("an existing-runner robot keeps its policy and evaluation tools on its robot page", async ({ page }) => {
  const profile = "metaworld-sawyer-pick-place-v1";
  const at = new Date().toISOString();
  const robot = { id: "rob_runner", project_id: "prj_lab", device_id: "dev_runner", name: "Virtual Sawyer", simulated: true, profile_id: null, profile, generation: 2, evaluation_id: null };
  const application = { id: "app_runner", project_id: "prj_lab", name: "Pick and place" };
  const release = { id: "apr_runner", application_id: application.id, digest: "b".repeat(64),
    manifest: { schema_version: 1, profile, policy: { runtime: "contract-scripted-v1", artifact_sha256: "a".repeat(64) }, environment: {}, execution: {} } };
  const deployment = { id: "dep_runner", robot_id: robot.id, release_id: release.id, generation: 2, state: "ready", detail: "", observed_at: at };
  const mission = { id: "mis_runner", project_id: "prj_lab", robot_id: robot.id, deployment_id: deployment.id, release_id: release.id, state: "completed",
    detail: "benchmark success reached", episode_id: "epi_runner", seed: 0, expires_at: 0, created_at: at, updated_at: at };
  const writes: string[] = [];
  await page.route("**/api/platform/**", async route => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/platform/", "");
    if (route.request().method() !== "GET") { writes.push(path); return route.fulfill({ status: 404, json: { error: "Unexpected request" } }); }
    const resources: Record<string, unknown> = {
      "auth/me": { user: { email: "operator@example.test", role: "operator" }, installation: { simulator: true, execution_profiles: [profile] } },
      projects: [{ id: "prj_lab", name: "Lab" }], robots: [robot], [`robots/${robot.id}`]: robot, devices: [],
      "robot-connections/dev_runner": { id: "dev_runner", name: "Sawyer runner", simulated: true, status: "online", metadata: null, heartbeat_at: null },
      applications: [application], [`applications/${application.id}`]: application, [`applications/${application.id}/releases`]: [release],
      [`applications/${application.id}/evaluation-suites`]: [],
      [`applications/${application.id}/qualification`]: { release_id: url.searchParams.get("release_id"), gate: null, promotion: null, deployment_allowed: true },
      deployments: [deployment], missions: [mission], evaluations: [],
      "episodes/epi_runner": { id: "epi_runner", mission_id: mission.id, state: "completed", detail: "", release_digest: release.digest, summary: { final_success: true, steps: 500 } },
    };
    return route.fulfill({ status: path in resources ? 200 : 404, json: resources[path] ?? { error: "This resource is unavailable in your project." } });
  });
  await page.goto(`/app/projects/prj_lab/robots/${robot.id}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Virtual Sawyer");
  await page.locator("summary", { hasText: "Advanced policy and evaluation tools" }).click();
  const tools = page.getByRole("region", { name: "Existing policy runtime" });
  for (const heading of ["Robots", "Applications & releases", "Release qualification", "Deployment", "Missions", "Mission history & episodes"]) {
    await expect(tools.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  }
  await expect(tools.getByRole("combobox", { name: "Selected robot", exact: true })).toHaveValue(robot.id);
  await expect(tools.locator(".console-release-details")).toContainText(release.digest);
  await expect(tools.getByTestId("qualification-state")).toContainText("No evaluation gate is configured");
  await expect(tools.getByRole("button", { name: "Request deployment", exact: true })).toBeVisible();
  await tools.getByRole("button", { name: "View episode", exact: true }).click();
  await expect(tools.locator(".console-episode")).toContainText("Succeeded");
  await expect(tools.getByText("No recording for this episode.")).toBeVisible();
  expect(writes).toEqual([]);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "no sideways scroll").toBeTruthy();
  await page.setViewportSize({ width: 1280, height: 720 });
  await expect(page.locator('a[href*="/app/applications"], a[href*="/app/device"]')).toHaveCount(0);

  // The configuration's release page sends an existing-interface release to that robot.
  await page.goto(`/app/configurations/${application.id}?source=project`);
  await expect(page.getByText(`This release uses the existing ${profile} interface.`)).toBeVisible();
  await expect(page.getByRole("link", { name: "Open Virtual Sawyer" })).toHaveAttribute("href", `/app/projects/prj_lab/robots/${robot.id}`);
});
