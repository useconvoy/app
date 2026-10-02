import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { emptyWorkspace } from "../../src/lib/configurations/mutations";
import { mockDocuments } from "./support/documents";
import { noOverflow } from "./support/configurations";

// Stateful HTTP fixtures cover navigation and project creation. Registration,
// configuration compatibility and deployment acknowledgements have their own suites.
test("projects are equal cards with a New project dialog; a project's sections are tabs under its title and fleets lead to their robots", async ({ page }, info) => {
  const projects = [
    { id: "prj_arm", name: "Assembly lab" },
    { id: "prj_warehouse", name: "Warehouse lab" },
  ];
  const profile = {
    id: "rpf_arm", project_id: "prj_arm", name: "Assembly arm", revision: 1, digest: "a".repeat(64),
    spec: { embodiment: "arm", adapter: "ros2", command_interface: "joint-position", control_rate_hz: 50,
      joints: [{ name: "shoulder", kind: "revolute", lower: -1, upper: 1 }], sensors: [],
      simulations: [{ engine: "mujoco", engine_version: "3.3.0", controller: "position",
        asset: { uri: "artifact:assembly-arm", sha256: "c".repeat(64), format: "mjcf" } }] },
    simulation: { state: "assets-declared", engines: ["mujoco"], dynamics_source: "imported", runtime_verified: false, detail: "Imported arm model." },
  };
  const robot = {
    id: "rob_arm", project_id: "prj_arm", name: "Arm 01", profile_id: profile.id,
    profile: "registered-joint-policy-v1", simulated: true, simulation_engine: "mujoco",
    fleet_id: "flt_assembly", generation: 1, qualification: { state: "passed", report: null }, evaluation_id: null,
  };
  const fleet = { id: "flt_assembly", project_id: "prj_arm", name: "Assembly line", robot_ids: [robot.id] };
  const application = { id: "app_reach", project_id: "prj_arm", name: "Reach target" };
  const release = {
    id: "apr_reach", application_id: application.id, digest: "b".repeat(64),
    manifest: { schema_version: 3, profile: robot.profile,
      environment: { engine: "mujoco", version: "3.3.0", robot_profile_sha256: profile.digest, asset_sha256: "c".repeat(64) },
      interface: { joint_names: ["shoulder"], action_bounds: [[-1, 1]], command_interface: "joint-position", control_rate_hz: 50 },
      policy: { runtime: "convoy-joint-target-reference-v1" },
      task: { instruction: "Reach the shoulder target", target_joint_positions: [0.25], position_tolerance: 0.02, velocity_tolerance: 0.05 },
      execution: { timing: { mode: "lockstep" } } },
  };
  const deployment = { id: "dep_arm", project_id: "prj_arm", robot_id: robot.id, release_id: release.id,
    generation: 1, state: "ready", detail: "Configuration ready." };
  const writes: { path: string; body: unknown }[] = [];
  await mockDocuments(page, { document: emptyWorkspace(Date.now()) });
  await page.route("**/api/platform/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/platform/", "");
    // The documents fixture handles this route and its write preconditions.
    if (path.startsWith("workspace-documents/")) return route.fallback();
    if (request.method() === "POST" && path === "projects") {
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      const body = request.postDataJSON();
      writes.push({ path, body });
      const project = { id: "prj_new", name: body.name };
      projects.push(project);
      return route.fulfill({ status: 201, json: project });
    }
    if (request.method() !== "GET") {
      writes.push({ path, body: request.postDataJSON() });
      return route.fulfill({ status: 409, json: { error: "Navigation must not dispatch an action." } });
    }
    const inArmProject = !url.searchParams.has("project_id") || url.searchParams.get("project_id") === "prj_arm";
    const resources: Record<string, unknown> = {
      "auth/me": { user: { email: "operator@example.test", role: "operator" }, installation: { simulator: true } },
      projects,
      robots: inArmProject ? [robot] : [], "robots/rob_arm": robot,
      "robot-profiles": inArmProject ? [profile] : [], "robot-profiles/rpf_arm": profile,
      fleets: inArmProject ? [fleet] : [], "robot-connections": [],
      applications: inArmProject ? [application] : [], "applications/app_reach": application,
      "applications/app_reach/releases": [release], deployments: inArmProject ? [deployment] : [],
      "applications/app_reach/qualification": { release_id: release.id, deployment_allowed: true },
      missions: [], "workspace-configuration-links": [],
    };
    return route.fulfill({ status: path in resources ? 200 : 404, json: resources[path] ?? { error: "Unexpected request" } });
  });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/app/projects");
  const listing = page.getByRole("region", { name: "Your projects", exact: true });
  const creation = page.getByRole("form", { name: "Create project", exact: true });
  await expect(listing.getByRole("link", { name: /Assembly lab/ })).toContainText("1 · 1 simulated");
  await expect(listing.getByRole("link", { name: /Warehouse lab/ })).toBeVisible();
  // Equal cards, as on Configurations; creation is a dialog, not a standing form.
  const heights = await listing.locator(".cv-config").evaluateAll(nodes => nodes.map(node => Math.round(node.getBoundingClientRect().height)));
  expect(new Set(heights).size, `equal card heights ${heights.join(", ")}`).toBe(1);
  await expect(creation).toHaveCount(0);
  await expect(page.locator("aside")).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("projects-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.getByRole("button", { name: "New project" }).click();
  await expect(page.getByRole("dialog", { name: "New project" })).toBeVisible();
  await expect(creation.getByLabel("Project name", { exact: true })).toBeFocused();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("projects-mobile.png"), fullPage: true });

  await creation.getByLabel("Project name", { exact: true }).fill("New manipulation lab");
  await page.getByRole("dialog", { name: "New project" }).getByRole("button", { name: "Create project", exact: true }).click();
  await expect(page).toHaveURL(/\/app\/projects\/prj_new$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("New manipulation lab");
  expect(writes).toEqual([{ path: "projects", body: { name: "New manipulation lab" } }]);
  await page.getByRole("navigation", { name: "Breadcrumb", exact: true }).getByRole("link", { name: "Projects", exact: true }).click();
  await expect(listing.getByRole("link", { name: /New manipulation lab/ })).toBeVisible();
  await listing.getByRole("link", { name: /Assembly lab/ }).click();
  await page.setViewportSize({ width: 1280, height: 900 });
  const navigation = page.getByRole("navigation", { name: "Project sections", exact: true });
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Assembly lab");
  await expect(navigation.getByRole("link", { name: "Overview", exact: true })).toHaveAttribute("aria-current", "page");
  // The sections are tabs under the title, in the same top-bar frame as Configurations: no sidebar.
  const navBox = await navigation.boundingBox(), titleBox = await page.getByRole("heading", { level: 1 }).boundingBox();
  expect(navBox).not.toBeNull(); expect(titleBox).not.toBeNull();
  expect(navBox!.y).toBeGreaterThanOrEqual(titleBox!.y + titleBox!.height);
  await expect(page.locator("aside")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("listitem")).toHaveText(["Projects", "Assembly lab"]);
  // The overview's fleets lead to their robots too.
  const overview = page.getByRole("region", { name: "Project fleets", exact: true });
  await overview.locator("summary").filter({ hasText: "Assembly line" }).click();
  await expect(overview.getByRole("link", { name: "Arm 01", exact: true })).toBeVisible();

  await navigation.getByRole("link", { name: "Fleets", exact: true }).click();
  await expect(navigation.getByRole("link", { name: "Fleets", exact: true })).toHaveAttribute("aria-current", "page");
  const directory = page.getByRole("region", { name: "Project fleets", exact: true });
  const disclosure = directory.locator("summary").filter({ hasText: "Assembly line" });
  const memberLink = directory.getByRole("link", { name: "Arm 01", exact: true });
  await expect(disclosure).toBeVisible();
  await expect(memberLink).toBeHidden();
  await disclosure.click();
  await expect(memberLink).toBeVisible();
  await expect(memberLink).toHaveAttribute("href", "/app/projects/prj_arm/robots/rob_arm");
  await expect(directory.getByRole("link", { name: "Reach target", exact: true })).toBeVisible();
  await expect(directory.getByText(/^Ready · release/)).toBeVisible();
  await disclosure.click();
  await expect(memberLink).toBeHidden();
  await disclosure.click();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("fleets-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(memberLink).toBeVisible();
  await noOverflow(page);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("fleets-mobile.png"), fullPage: true });

  await memberLink.click();
  await expect(page).toHaveURL(/\/app\/projects\/prj_arm\/robots\/rob_arm$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Arm 01");
  const trail = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(trail.getByRole("listitem")).toHaveText(["Projects", "Assembly lab", "Arm 01"]);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Arm 01");
  await trail.getByRole("link", { name: "Assembly lab" }).click();
  await expect(navigation.getByRole("link", { name: "Overview", exact: true })).toHaveAttribute("aria-current", "page");
  await noOverflow(page);
  // Browsing a fleet and opening a robot must not deploy software or start motion.
  expect(writes).toHaveLength(1);
});
