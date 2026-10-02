import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mockApi, noOverflow, OFFLINE, OFFLINE_EPISODES, OFFLINE_EVAL, offlineDocument, PNG, PROJECT } from "./support/configurations";

test("a project's imported evaluation replays exact planner state alongside actions and reported timing", async ({ page }, info) => {
  const document = offlineDocument();
  document.configurations[2].projectId = PROJECT;
  const robot = document.robots.find(item => item.name === "Offline runner")!;
  robot.offlineEvaluationIds = [OFFLINE_EVAL];
  await mockApi(page, { document });
  await page.route(/\/api\/platform\/(robot-profiles|fleets|robot-connections|deployments)(\?|$)/,
    route => route.fulfill({ json: [] }));
  const source = "Local monotonic clock on the MuJoCo runner; scripted System 1 controller";
  const detail = structuredClone(OFFLINE[0]);
  detail.episodes = [{ ...detail.episodes[0], steps: 4, images: 3, action_dim: 4,
    metrics: { planner_latency_p95_ms: 120.25, rejected_plans: 1, measurement_source: source } }];
  detail.summary = { ...detail.summary, episodes: 1, successes: 1, success_rate: 1, median_steps: 4 };
  await page.route("**/api/platform/offline-evaluations", route => route.fulfill({ json: { items: [detail] } }));
  await page.route(`**/api/platform/offline-evaluations/${OFFLINE_EVAL}`, route => route.fulfill({ json: detail }));
  const base = `/api/platform/offline-evaluations/${OFFLINE_EVAL}/episodes/${OFFLINE_EPISODES[0]}/replay`;
  const states = [
    { planner_state: "idle", task_revision: 0, active_skill: "none", target: null, planner_latency_ms: null, observation_age_ms: null, physics_lag_ms: 0.123 },
    { planner_state: "pending", task_revision: 1, active_skill: "hold", target: null, planner_latency_ms: null, observation_age_ms: 0.25, physics_lag_ms: 0.123 },
    { planner_state: "accepted", task_revision: 1, active_skill: "pick_place", target: "A", planner_latency_ms: 120.25, observation_age_ms: 40.5, physics_lag_ms: 0.123 },
    { planner_state: "stale", task_revision: 2, active_skill: "hold", target: "A", planner_latency_ms: 400, observation_age_ms: 300, physics_lag_ms: 0.1 },
    { planner_state: "accepted", task_revision: 2, active_skill: "pick_place", target: "B", planner_latency_ms: 80.6, observation_age_ms: 25, physics_lag_ms: 0.1 },
  ];
  await page.route(`**${base}**`, route => {
    const match = /\/frames\/(\d+)$/.exec(new URL(route.request().url()).pathname);
    if (!match) return route.fulfill({ json: {
      episode_id: OFFLINE_EPISODES[0], mission_id: null, release_digest: null, steps: 4, skill: null,
      planner_ms: null, wall_seconds: 1.5, sim_seconds: 0.08, source: detail.scope,
      action_labels: ["dx", "dy", "dz", "grip"], action_dim: 4, has_hierarchy: true, measurement_source: source,
    } });
    const index = Number(match[1]);
    return route.fulfill({ json: { index, image_png_base64: PNG, image_index: index - index % 2,
      action: index ? [index / 10, 0, 0, 1] : null, policy_ms: index ? 0.125 : null,
      reward: index ? 0.5 : null, success: index ? index === 4 : null, hierarchy: states[index] } });
  });

  await page.goto(`/app/projects/${PROJECT}?section=runs`);
  await page.getByRole("region", { name: "Saved evaluations" }).getByRole("link", { name: "Offline runner", exact: true }).click();
  await page.getByRole("table", { name: "Evals", exact: true }).getByRole("link", { name: /^Eval 1/ }).click();
  await expect(page.locator(".cv-head .cv-tag")).toHaveText("Offline sim");
  await expect(page.getByRole("table", { name: "Metrics", exact: true })).toContainText("120.25");
  await expect(page.getByRole("table", { name: "Metrics", exact: true })).toContainText("Planner latency · p95 (ms)");
  await expect(page.getByText("Reported simulator measurements, averaged per episode. Imported results do not qualify robot or cloud timing.")).toBeVisible();
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await expect(page.locator(".cv-facts")).toContainText(source);
  await expect(page.locator(".cv-facts")).toContainText("Offline import · unsigned");
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await page.getByRole("button", { name: `Replay ${OFFLINE_EPISODES[0]}, seed 0` }).click();
  const sheet = page.getByRole("dialog", { name: "Seed 0", exact: true });
  const readout = sheet.locator(".cv-player__readout");
  const value = (label: string) => readout.getByText(label, { exact: true }).locator("..");
  await expect(value("System 2 state")).toContainText("idle");
  await sheet.getByRole("button", { name: "Next step" }).click();
  await expect(value("System 2 state")).toContainText("pending");
  await expect(value("Active skill")).toContainText("hold");
  await sheet.getByRole("button", { name: "Next step" }).click();
  await expect(value("System 2 state")).toContainText("accepted");
  await expect(value("Active skill")).toContainText("pick_place");
  await expect(value("Target")).toContainText("A");
  await expect(value("Last planner latency")).toContainText("120.25 ms");
  await expect(value("Last proposal age")).toContainText("40.5 ms");
  await expect(value("Physics lag")).toContainText("0.123 ms");
  await expect(value("Policy")).toContainText("0.125 ms");
  await expect(readout.locator(".cv-player__action span").first()).toHaveText("dx0.20");
  await sheet.getByRole("button", { name: "Next step" }).click();
  await expect(value("System 2 state")).toContainText("stale");
  await expect(value("Active skill")).toContainText("hold");
  // The image is still from tick 2, but tick 3's rejected/held planner snapshot must be shown.
  await expect(sheet.getByRole("img", { name: "Recorded robot camera at action 2 of 4" })).toBeVisible();
  await expect(value("Task revision")).toContainText("2");
  await sheet.getByRole("button", { name: "Next step" }).click();
  await expect(value("Target")).toContainText("B");
  await expect(sheet.getByText(/Imported simulation measurements/)).toContainText(source);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("hierarchy-replay-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("hierarchy-replay-mobile.png"), fullPage: true });
});
