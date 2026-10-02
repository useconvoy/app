import { expect, test } from "@playwright/test";
import { AxeBuilder } from "@axe-core/playwright";
import type { Application, Release } from "../../src/lib/platform/client";
import type { RegisteredManifest } from "../../src/lib/platform/manifest";

// Browser interaction fixtures; server and real MuJoCo pipeline tests verify compilation/execution.
test("project configuration creates an executable release, revises it and opens the selected robot deployment", async ({ page }, testInfo) => {
  const project = { id: "prj_lab", name: "Manipulation lab" };
  const profile = { id: "rpf_arm", project_id: project.id, name: "Custom arm", revision: 1, digest: "a".repeat(64),
    spec: { command_interface: "joint-position", control_rate_hz: 50, joints: [{ name: "shoulder", kind: "revolute", lower: -1, upper: 1 }], sensors: [],
      simulations: [{ engine: "mujoco", engine_version: "3.3.0", controller: "position", asset: { sha256: "b".repeat(64) } }] },
    simulation: { engines: ["mujoco"], dynamics_source: "unknown" } };
  const robot = { id: "rob_sim", project_id: project.id, name: "Arm simulation", profile_id: profile.id, simulated: true,
    profile: "registered-joint-policy-v1", generation: 0, qualification: { state: "passed", profile_digest: profile.digest }, evaluation_id: null };
  const applications: Application[] = [];
  const releases: Release[] = [];
  const writes: string[] = [];
  await page.route("**/api/portal/snapshot", route => route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "No physical device in this fixture." } } }));
  await page.route("**/api/platform/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/platform/", "");
    if (route.request().method() === "GET") {
      const resources: Record<string, unknown> = {
        "auth/me": { user: { email: "operator@example.test", role: "operator" }, installation: { simulator: true } },
        projects: [project], "robot-profiles": [profile], "robot-profiles/rpf_arm": profile, robots: [robot], "robots/rob_sim": robot,
        applications, "applications/app_one": applications[0], "applications/app_one/releases": releases,
        deployments: [], missions: [], devices: [], "robot-connections": [], fleets: [], "workspace-configuration-links": [],
      };
      if (path.endsWith("/setup")) {
        const release = releases.find(r => path.includes(r.id))!;
        return route.fulfill({ json: { release_id: release.id, manifest_json: JSON.stringify(release.manifest), reference_policy_json: '{"target_joint_positions":[0.25]}' } });
      }
      return route.fulfill({ status: path in resources ? 200 : 404, json: resources[path] ?? { error: "Not found" } });
    }
    expect(route.request().headers()["idempotency-key"]).toBeTruthy();
    writes.push(path);
    const body = route.request().postDataJSON();
    if (path === "configurations" || path === "applications/app_one/configuration-releases") {
      const config = body.configuration ?? body;
      expect(config.profile_id).toBe(profile.id);
      expect(config.policy).toEqual({ kind: "reference" });
      expect(config.execution.timing).toEqual({ mode: "realtime", max_observation_age_ms: 200, max_physics_lag_ms: 20, fallback: "hold-position" });
      expect(config.targets).toEqual({ shoulder: releases.length ? -0.4 : 0.25 });
      if (!applications.length) {
        expect(body.project_id).toBe(project.id);
        applications.push({ id: "app_one", name: body.name, project_id: body.project_id });
      }
      const manifest: RegisteredManifest = { schema_version: 3, profile: "registered-joint-policy-v1",
        policy: { runtime: "convoy-joint-target-reference-v1", artifact_sha256: "c".repeat(64) },
        environment: { engine: "mujoco", version: "3.3.0", robot_profile_sha256: profile.digest, asset_sha256: "b".repeat(64) },
        interface: { joint_names: ["shoulder"], action_bounds: [[-1, 1]], command_interface: "joint-position", control_rate_hz: 50 },
        task: { instruction: config.instruction, target_joint_positions: [config.targets.shoulder], position_tolerance: config.position_tolerance, velocity_tolerance: config.velocity_tolerance }, execution: config.execution };
      const release = { id: `apr_${releases.length + 1}`, application_id: "app_one", digest: String(releases.length + 1).repeat(64), manifest };
      releases.unshift(release);
      return route.fulfill({ status: 201, json: { application: applications[0], release } });
    }
    return route.fulfill({ status: 404, json: { error: "Unexpected mutation" } });
  });
  await page.goto("/app/projects/prj_lab");
  await page.getByRole("tab", { name: "Configurations", exact: true }).click();
  await page.getByRole("link", { name: "Create runnable configuration" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Create runnable configuration");
  await page.getByLabel("Configuration name", { exact: true }).fill("Arm target experiment");
  await page.getByLabel("Task instruction").fill("Reach the shoulder target");
  await page.getByLabel("shoulder (radians, -1 to 1)").fill("0.25");
  await page.getByLabel("Simulation timing").selectOption("realtime");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Create configuration", exact: true }).click();
  await expect(page).toHaveURL(/\/app\/configurations\/app_one\?source=project$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Arm target experiment");
  await expect(page.getByText("Joint-position reference controller · no learned model inference", { exact: true })).toBeVisible();
  await page.getByText("Operator setup files", { exact: true }).click();
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download release manifest" }).click();
  expect((await downloadEvent).suggestedFilename()).toBe("release-manifest.json");
  await expect(page.getByText("Maximum observation age: 200 ms · Maximum physics lag: 20 ms · Fallback: simulated position hold", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Create new release" }).click();
  await expect(page.getByLabel("Simulation timing")).toHaveValue("realtime");
  await page.getByLabel("shoulder (radians, -1 to 1)").fill("-0.4");
  await page.getByRole("button", { name: "Save new release" }).click();
  await expect(page.getByRole("combobox", { name: "Configuration release", exact: true })).toHaveValue("apr_2");
  await expect(page.getByRole("cell", { name: "-0.4", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Configuration release", exact: true }).selectOption("apr_1");
  await expect(page.getByRole("cell", { name: "0.25", exact: true })).toBeVisible();
  expect(writes).toEqual(["configurations", "applications/app_one/configuration-releases"]);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo({ top: 0, behavior: "instant" }); });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("configuration-release-mobile.png"), fullPage: true });
  await page.getByRole("link", { name: "Open deployment and task controls" }).click();
  await expect(page.getByRole("combobox", { name: "Configuration", exact: true })).toHaveValue("app_one");
  await expect(page.getByRole("combobox", { name: "Release", exact: true })).toHaveValue("apr_1");
  await expect(page.getByRole("button", { name: "Start task", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Deploy selected release", exact: true })).toBeEnabled();
  await page.goto("/app/configurations");
  await expect(page.getByRole("region", { name: "Project configurations", exact: true }).getByRole("link", { name: /Arm target experiment/ })).toBeVisible();
});
