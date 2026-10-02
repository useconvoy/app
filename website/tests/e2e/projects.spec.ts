import { expect, test } from "@playwright/test";
import { AxeBuilder } from "@axe-core/playwright";
import { createHash } from "node:crypto";

// Stateful HTTP fixtures exercise browser requests and profile lineage. Real database/ownership
// cases live in server/tests/test_robot_registry.py and its PostgreSQL counterpart.
test("project onboarding registers a physical robot and its simulated instance, then assigns a fleet", async ({ page }, testInfo) => {
  const projects: Record<string, unknown>[] = [];
  const profiles: Record<string, unknown>[] = [];
  const robots: Record<string, unknown>[] = [];
  const fleets: { id: string; name: string; robot_ids: string[] }[] = [];
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  let readiness: Record<string, unknown> | undefined;
  const model = Buffer.from('<mujoco model="custom-arm"/>');
  let assetStored = false;
  let connected = false;
  let connectionStatus = "open";
  const computer = { id: "dev_physical", name: "Jetson", simulated: false, status: "never_seen",
    hardware: { arch: "aarch64", mem_total_mb: 7619, cpu_count: 6, gpu_name: null, synthetic: false } };
  const enrollment = () => ({ enrollment: { id: "enr_setup", status: connectionStatus, expires_at: new Date(Date.now() + 900000).toISOString() }, device: connected ? computer : null });
  await page.route("**/api/platform/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/platform/", "");
    const method = route.request().method();
    if (path === "auth/me") return route.fulfill({ json: { user: { email: "operator@example.test", role: "operator" }, installation: { simulator: true } } });
    if (path === "robot-connections/enrollments" && method === "POST") {
      expect(route.request().postDataJSON()).toEqual({ project_id: "prj_lab", name: "Arm 1", simulated: false });
      connectionStatus = "open";
      return route.fulfill({ status: 201, json: { ...enrollment(), command: "convoy-agent --data-dir ./convoy-connections/enr_setup enroll --token one-use-test-token",
        run_command: "convoy-agent --data-dir ./convoy-connections/enr_setup run --no-robot-sim", data_dir: "./convoy-connections/enr_setup" } });
    }
    if (path === "robot-connections/enrollments/enr_setup/cancel" && method === "POST") {
      connectionStatus = "revoked";
      return route.fulfill({ json: enrollment() });
    }
    if (path === "robot-profiles/rpf_arm/simulation-assets/mujoco" && method === "POST") {
      expect(route.request().headers()["content-type"]).toBe("application/octet-stream");
      expect(route.request().postDataBuffer()).toEqual(model);
      assetStored = true;
      return route.fulfill({ status: 201, json: { engine: "mujoco", stored: true, size_bytes: model.length } });
    }
    if (method === "GET") {
      const resources: Record<string, unknown> = { projects, "robot-profiles": profiles, robots, fleets,
        "robot-profiles/rpf_arm/simulation-assets": [{ engine: "mujoco", stored: assetStored, size_bytes: assetStored ? model.length : null }], "robot-connections": [
        ...(connected ? [computer] : []),
        { id: "dev_simulated", name: "MuJoCo runner", simulated: true, status: "online" },
      ], "robot-connections/enrollments/enr_setup": enrollment(), "robot-connections/dev_physical": computer,
        "robot-connections/dev_simulated": { id: "dev_simulated", name: "MuJoCo runner", simulated: true, status: "online", hardware: { arch: "arm64", synthetic: false } } };
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
      asset: { uri: "artifact:arm", sha256: createHash("sha256").update(model).digest("hex"), format: "mjcf" }, evidence: { source: "imported" } }] };
  await page.getByLabel("Profile JSON").setInputFiles({ name: "arm.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(spec)) });
  await page.getByRole("button", { name: "Save profile revision" }).click();
  await expect(page.getByRole("heading", { name: "Custom arm · revision 1" })).toBeVisible();
  await expect(page.getByText("mujoco · mjcf · File needed")).toBeVisible();
  await page.getByLabel("Upload mujoco model").setInputFiles({ name: "wrong.xml", mimeType: "text/xml", buffer: Buffer.from("wrong") });
  await expect(page.getByRole("region", { name: "Simulation files for Custom arm revision 1" }).getByRole("alert")).toContainText("does not match the saved profile");
  expect(assetStored).toBe(false);
  await page.getByLabel("Upload mujoco model").setInputFiles({ name: "arm.xml", mimeType: "text/xml", buffer: model });
  await expect(page.getByText(/mujoco · mjcf · Stored/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("profile-assets-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("profile-assets-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("tab", { name: "Robots", exact: true }).click();
  await page.getByRole("button", { name: "Add robot", exact: true }).click();
  await page.getByLabel("Robot name", { exact: true }).fill("Arm 1");
  await page.getByLabel("Robot profile", { exact: true }).selectOption("rpf_arm");
  await expect(page.getByText("No unassigned connections yet. Connect a computer below.")).toBeVisible();
  await page.getByRole("button", { name: "Create connection token" }).click();
  await expect(page.getByLabel("Connection command")).toContainText("one-use-test-token");
  await page.getByRole("button", { name: "Cancel connection token" }).click();
  await expect(page.getByText("The token was cancelled.")).toBeVisible();
  await expect(page.getByLabel("Connection command")).toHaveCount(0);
  await page.getByRole("button", { name: "Create connection token" }).click();
  await expect(page.getByLabel("Connection command")).toBeVisible();
  connected = true; connectionStatus = "consumed";
  await page.getByRole("button", { name: "Check connection" }).click();
  await expect(page.getByLabel("Enrolled robot computer")).toHaveValue("dev_physical");
  await expect(page.getByLabel("Connection command")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Computer details" })).toContainText("7619 MiB");
  await expect(page.getByRole("region", { name: "Computer details" })).toContainText("Enrolled; waiting for the agent heartbeat");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo({ top: 0, behavior: "instant" }); });
  await page.screenshot({ path: testInfo.outputPath("connection-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await expect.poll(async () => (await page.getByRole("banner").boundingBox())?.y).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("connection-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
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

test("registered robot tasks wait for deployment and stop acknowledgements", async ({ page }, testInfo) => {
  const robot = { id: "rob_sim", project_id: "prj_lab", name: "Arm simulation", simulated: true, profile_id: "rpf_arm",
    profile: "registered-joint-policy-v1", simulation_engine: "mujoco", generation: 0, qualification: { state: "passed", report: null }, evaluation_id: null };
  const deployment = { id: "dep_one", robot_id: robot.id, generation: 1, state: "requested", detail: "", release_id: "apr_one" };
  const task = { id: "mis_one", robot_id: robot.id, state: "requested", detail: "", updated_at: new Date().toISOString(), episode_id: null as string | null };
  let deployed = false, started = false;
  const summary: Record<string, unknown> = { final_success: false, steps: 3, execution_mode: "lockstep_offline", simulated_duration_s: 0.06, wall_duration_s: 1.2 };
  const manifest = { schema_version: 3, profile: robot.profile, environment: { robot_profile_sha256: "a".repeat(64) },
    policy: { runtime: "convoy-joint-target-reference-v1" }, task: { instruction: "Reach the shoulder target" } };
  await page.route("**/api/platform/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/platform/", "");
    if (route.request().method() === "GET") {
      const resources: Record<string, unknown> = {
        "auth/me": { user: { email: "operator@example.test", role: "operator" }, installation: { simulator: true } },
        "robots/rob_sim": robot, "robot-profiles/rpf_arm": { name: "Custom arm", revision: 1, digest: "a".repeat(64) },
        applications: [{ id: "app_one", name: "Reach target" }],
        "applications/app_one/releases": [{ id: "apr_one", digest: "b".repeat(64), manifest }, { id: "apr_two", digest: "c".repeat(64), manifest }],
        deployments: deployed ? [deployment] : [], missions: started ? [task] : [],
        "episodes/epi_one": { summary },
      };
      return route.fulfill({ status: path in resources ? 200 : 404, json: resources[path] ?? {} });
    }
    expect(route.request().headers()["idempotency-key"]).toBeTruthy();
    if (path === "deployments") {
      expect(route.request().postDataJSON()).toEqual({ robot_id: robot.id, release_id: "apr_one", expected_generation: 0 });
      deployed = true; robot.generation = 1;
      return route.fulfill({ status: 201, json: deployment });
    }
    if (path === "robots/rob_sim/missions") { started = true; return route.fulfill({ status: 201, json: task }); }
    if (path === "missions/mis_one/cancel") { task.state = "cancel_requested"; return route.fulfill({ json: task }); }
    return route.fulfill({ status: 404, json: { error: "Unexpected request" } });
  });
  await page.goto("/app/projects/prj_lab/robots/rob_sim");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Arm simulation");
  await expect(page.getByRole("button", { name: "Start task", exact: true })).toBeDisabled();
  await page.getByRole("combobox", { name: "Release", exact: true }).selectOption("apr_one");
  await expect(page.getByText("Joint-position reference controller · no learned model inference.")).toBeVisible();
  await page.getByRole("button", { name: "Deploy selected release" }).click();
  await expect(page.getByText("Deployment 1 · requested")).toBeVisible();
  await expect(page.getByRole("button", { name: "Start task", exact: true })).toBeDisabled();
  deployment.state = "ready";
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByRole("combobox", { name: "Release", exact: true }).selectOption("apr_two");
  await expect(page.getByRole("button", { name: "Start task", exact: true })).toBeDisabled();
  await page.getByRole("combobox", { name: "Release", exact: true }).selectOption("apr_one");
  await page.getByRole("button", { name: "Start task", exact: true }).click();
  await expect(page.getByRole("button", { name: "Start task", exact: true })).toBeDisabled();
  task.state = "running";
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByRole("button", { name: "Stop task", exact: true }).click();
  await expect(page.getByText("Stop requested — waiting for robot")).toBeVisible();
  await expect(page.getByRole("button", { name: "Start task", exact: true })).toBeDisabled();
  task.state = "cancelled"; task.episode_id = "epi_one";
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("button", { name: "Start task", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "View task result" }).click();
  await expect(page.getByText("3 control steps")).toBeVisible();
  Object.assign(summary, { execution_mode: "independent_realtime_simulation", timing: {
    status: "failed", reasons: ["policy_deadline_missed"], physics_control_steps: 12, applied_actions: 3,
    fallback_ticks: 9, physics_wall_s: .25, contract: { max_observation_age_ms: 100, max_physics_lag_ms: 20 },
    observation_to_action_ms: { p50: 35, p95: 90, max: 95 },
  } });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByText("3 applied policy actions")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Timing requirements not met" })).toBeVisible();
  await expect(page.getByRole("row", { name: /Observation to applied action/ })).toContainText("90.00");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo({ top: 0, behavior: "instant" }); });
  await page.screenshot({ path: testInfo.outputPath("robot-tasks-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo({ top: 0, behavior: "instant" }); });
  await expect.poll(async () => (await page.getByRole("banner").boundingBox())?.y).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("robot-tasks-mobile.png"), fullPage: true });
});
