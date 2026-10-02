import { expect, test } from "@playwright/test";
import { AxeBuilder } from "@axe-core/playwright";

// Stateful HTTP fixtures exercise browser requests and profile lineage. Real database/ownership
// cases live in server/tests/test_robot_registry.py and its PostgreSQL counterpart.
test("project onboarding registers a physical robot and its simulated instance, then assigns a fleet", async ({ page }, testInfo) => {
  const projects: Record<string, unknown>[] = [];
  const profiles: Record<string, unknown>[] = [];
  const robots: Record<string, unknown>[] = [];
  const fleets: { id: string; name: string; robot_ids: string[] }[] = [];
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  let readiness: Record<string, unknown> | undefined;
  await page.route("**/api/platform/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/platform/", "");
    const method = route.request().method();
    if (path === "auth/me") return route.fulfill({ json: { user: { email: "operator@example.test", role: "operator" }, installation: { simulator: true } } });
    if (method === "GET") {
      const resources: Record<string, unknown> = { projects, "robot-profiles": profiles, robots, fleets, "robot-connections": [
        { id: "dev_physical", name: "Jetson", simulated: false, status: "online" },
        { id: "dev_simulated", name: "MuJoCo runner", simulated: true, status: "online" },
      ] };
      return route.fulfill({ status: path in resources ? 200 : 404, json: resources[path] ?? { error: "Not found" } });
    }
    const body = route.request().postDataJSON();
    expect(route.request().headers()["idempotency-key"]).toBeTruthy();
    writes.push({ path, body });
    let result;
    if (path === "projects") { result = { id: "prj_lab", ...body }; projects.push(result); }
    if (path === "robot-profiles") {
      result = { id: "rpf_arm", revision: 1, digest: "a".repeat(64), ...body,
        simulation: { state: "assets-declared", engines: ["mujoco"], runtime_verified: false, dynamics_source: "unknown", detail: "Runner verification required." } };
      profiles.push(result);
    }
    if (path === "robot-registrations") { result = { id: `rob_${robots.length + 1}`, ...body, simulated: body.kind === "simulated", generation: 0 }; robots.push(result); }
    if (path === "robots/rob_2/qualification") {
      readiness = { id: "rqc_one", state: "requested", engine: "mujoco", created_at: new Date().toISOString(), completed_at: null, report: null };
      robots[1].qualification = readiness;
      result = readiness;
    }
    if (path === "fleets") { result = { id: "flt_line", ...body, robot_ids: [] }; fleets.push(result); }
    if (path === "fleets/flt_line/members") {
      for (const assignment of body.assignments) {
        const robot = robots.find(r => r.id === assignment.robot_id)!;
        expect(assignment.expected_fleet_id).toBeNull();
        robot.fleet_id = "flt_line"; fleets[0].robot_ids.push(assignment.robot_id);
      }
      result = fleets[0];
    }
    await route.fulfill({ status: result ? 201 : 404, json: result ?? { error: "Unexpected mutation" } });
  });
  await page.goto("/app");
  await expect(page).toHaveURL(/\/app\/projects$/);
  await expect(page.getByRole("heading", { name: "Create your first project" })).toBeVisible();
  await page.getByLabel("Project name").fill("Manipulation lab");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Manipulation lab");
  await page.getByRole("button", { name: "Add a profile" }).click();
  await page.getByLabel("Profile name").fill("Custom arm");
  const spec = { embodiment: "arm", adapter: "ros2", command_interface: "joint-position", control_rate_hz: 50,
    joints: [{ name: "shoulder", kind: "revolute", lower: -1, upper: 1, evidence: { source: "imported" } }], sensors: [],
    dynamics: { source: "unknown" }, simulations: [{ engine: "mujoco", engine_version: "3.3.0", controller: "position",
      asset: { uri: "artifact:arm", sha256: "a".repeat(64), format: "mjcf" }, evidence: { source: "imported" } }] };
  await page.getByLabel("Profile JSON").setInputFiles({ name: "arm.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(spec)) });
  await page.getByRole("button", { name: "Save profile revision" }).click();
  await expect(page.getByRole("heading", { name: "Custom arm · revision 1" })).toBeVisible();
  await page.getByRole("tab", { name: "Robots", exact: true }).click();
  await page.getByRole("button", { name: "Add robot", exact: true }).click();
  await page.getByLabel("Robot name", { exact: true }).fill("Arm 1");
  await page.getByLabel("Robot profile", { exact: true }).selectOption("rpf_arm");
  await page.getByLabel("Enrolled robot computer").selectOption("dev_physical");
  await page.getByRole("button", { name: "Register robot", exact: true }).click();
  await expect(page.getByRole("row", { name: /Arm 1 Physical/ })).toBeVisible();
  await page.getByRole("button", { name: "Create simulated instance" }).click();
  await expect(page.getByLabel("Robot profile", { exact: true })).toBeDisabled();
  await page.getByLabel("Simulation engine").selectOption("mujoco");
  await page.getByLabel("Enrolled simulator runner").selectOption("dev_simulated");
  await page.getByRole("button", { name: "Register robot", exact: true }).click();
  await expect(page.getByRole("row", { name: /Arm 1 · simulation Simulated/ })).toBeVisible();
  expect(writes.filter(w => w.path === "robot-registrations").map(w => [w.body.profile_id, w.body.source_robot_id])).toEqual([["rpf_arm", null], ["rpf_arm", "rob_1"]]);
  await page.getByRole("button", { name: "Verify simulator", exact: true }).click();
  await expect(page.getByText("Waiting for runner", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Verification requested" })).toBeDisabled();
  Object.assign(readiness!, { state: "passed", completed_at: new Date().toISOString(), report: {
    detail: "Imported model ran successfully.", evidence: { engine_version: "3.3.0", steps: 200, sim_seconds: 0.4, wall_seconds: 0.12, checks: ["physics-step"] },
  } });
  await expect(page.getByText("Simulator checks passed", { exact: true })).toBeVisible({ timeout: 10000 });
  await page.getByText("Verification results", { exact: true }).click();
  await expect(page.getByText(/200 physics steps/)).toBeVisible();
  await expect(page.getByText(/Timing and physical accuracy require separate experiments/)).toBeVisible();
  Object.assign(readiness!, { state: "stale" });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByText("Connection changed — verify again", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Verify again", exact: true })).toBeEnabled();
  await page.getByRole("tab", { name: "Fleets", exact: true }).click();
  await page.getByLabel("Fleet name").fill("Line 1");
  await page.getByRole("button", { name: "Create fleet" }).click();
  await expect(page.getByRole("heading", { name: "Line 1" })).toBeVisible();
  await page.getByRole("tab", { name: "Robots", exact: true }).click();
  await page.getByRole("checkbox", { name: "Select Arm 1", exact: true }).check();
  await page.getByLabel("Assign selected robots to fleet").selectOption("flt_line");
  await page.getByRole("button", { name: "Assign 1 robots" }).click();
  await expect(page.getByRole("row", { name: /Arm 1 Physical/ })).toContainText("Line 1");
  await page.reload();
  await expect(page.getByRole("row", { name: /Arm 1 Physical/ })).toContainText("Line 1");
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("project-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("project-mobile.png"), fullPage: true });
});
