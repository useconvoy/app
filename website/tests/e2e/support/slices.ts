import type { Page } from "@playwright/test";
import { linkOfflineEvaluations } from "../../../src/lib/configurations/mutations";
import type { ConvoyWorkspace, EvalProvenance } from "../../../src/lib/configurations/types";
import type { OfflineEvaluationDetail } from "../../../src/lib/platform/client";
import { PLANNER_EPISODES } from "../../configurations/evidence-fixture";
import type { FixtureEpisode } from "../../configurations/evidence-fixture";
import { SECOND_RUN_EPISODES } from "../../configurations/slices-fixture";
import { OFFLINE, offlineEvaluation } from "./configurations";
import { evidenceDocument, PLANNER_EVAL, PLANNER_ROBOT } from "./evidence";

/*
 * Contract fixtures for slices, planner calls and provenance: two offline evals of one simulator, the
 * same task run from two machines. The first has the evidence fixture's episodes, the second the slices
 * fixture's (tests/configurations/slices-fixture.ts); both report every call result, as the simulator
 * exports them, and each has its provenance declared on the robot. No account or customer data.
 */
export const SECOND_EVAL = "oev_contract0010";
const SETUP = { run: "MuJoCo planner run", perception: "Simulator-state perception", control: "Scripted IK", planner: "Qwen on Jetson (real calls)" };
export const PROVENANCE: Record<string, EvalProvenance> = {
  [PLANNER_EVAL]: { ...SETUP, runner: "Linux cloud runner", transport: "Portal relay" },
  [SECOND_EVAL]: { ...SETUP, runner: "Mac", transport: "Portal relay" },
};

/** The evidence fixture's episodes with the call results it leaves out, as recorded: no JSON or schema refusal, no device or transport failure. */
const withCallResults = (rows: readonly FixtureEpisode[]): FixtureEpisode[] => rows.map(row => ({
  ...row, metrics: { planner_invalid_json: 0, planner_invalid_schema: 0, planner_device_errors: 0, planner_http_errors: 0, planner_timeouts: 0, ...row.metrics },
}));

/** An offline eval as `GET offline-evaluations/{id}` returns it: simulated time only, as the real import. */
function detail(id: string, minutes: number, rows: readonly FixtureEpisode[], name: string): OfflineEvaluationDetail {
  const base = offlineEvaluation(id, minutes, rows.map(row => ({ id: row.id, seed: row.seed, outcome: row.outcome, steps: row.steps, wall: null, metrics: row.metrics })));
  const simulated = rows.map(row => row.simSeconds).toSorted((a, b) => a - b);
  const middle = simulated.length % 2 ? simulated[(simulated.length - 1) / 2] : (simulated[simulated.length / 2 - 1] + simulated[simulated.length / 2]) / 2;
  return {
    ...base, source: "offline", signed: false, name, task: "Pills into bottle · two slices", config_label: "Edge planner r1", policy_label: "Planner on device, scripted skills",
    summary: { ...base.summary, median_sim_seconds: middle },
    episodes: base.episodes.map((episode, i) => ({ ...episode, outcome: rows[i].outcome, metrics: rows[i].metrics, sim_seconds: rows[i].simSeconds })),
  };
}
export const FIRST_RUN = detail(PLANNER_EVAL, 30, withCallResults(PLANNER_EPISODES), "Bimanual · planner on device · first runner");
export const SECOND_RUN = detail(SECOND_EVAL, 10, SECOND_RUN_EPISODES, "Bimanual · planner on device · second runner");

/** The evidence document with both evals linked from its "Planner sim" robot, each with its declared provenance. */
export function slicesDocument(): ConvoyWorkspace {
  const ws = linkOfflineEvaluations(evidenceDocument(), PLANNER_ROBOT, [PLANNER_EVAL, SECOND_EVAL], Date.now() - 3_000_000);
  ws.robots.find(robot => robot.id === PLANNER_ROBOT)!.offlineEvaluationProvenance = structuredClone(PROVENANCE);
  return ws;
}

/** Serves both evals beside the contract offline evaluations (registered after `mockApi`, so it answers first). */
export async function mockTwoRuns(page: Page) {
  const summary = (item: object) => Object.fromEntries(Object.entries(item).filter(([key]) => key !== "episodes"));
  await page.route(/\/api\/platform\/offline-evaluations(?:\/[^/?]+)?(?:\?.*)?$/, route => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\/platform\//, "");
    if (path === "offline-evaluations") return route.fulfill({ json: { items: [SECOND_RUN, FIRST_RUN, ...OFFLINE].map(summary) } });
    const found = [FIRST_RUN, SECOND_RUN].find(item => path === `offline-evaluations/${item.id}`);
    return found ? route.fulfill({ json: found }) : route.fallback();
  });
}
