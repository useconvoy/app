import type { Page } from "@playwright/test";
import { addRobot } from "../../../src/lib/configurations/mutations";
import type { ConvoyWorkspace, LatencyTarget } from "../../../src/lib/configurations/types";
import type { OfflineEvaluationDetail } from "../../../src/lib/platform/client";
import { PLANNER_EPISODES } from "../../configurations/evidence-fixture";
import { demoDocument, OFFLINE, offlineEvaluation } from "./configurations";

/*
 * Contract fixtures for the evidence panels: an offline evaluation whose ten episodes carry the
 * planner and safety metrics of a real on-device planner eval (tests/configurations/evidence-fixture.ts),
 * linked by a simulator in the demo document's edge-planner configuration, which declares three
 * neutral latency targets. No account or customer data.
 */
export const PLANNER_EVAL = "oev_contract0009";
export const PLANNER_CONFIG = "arm-edge-planner";
export const PLANNER_ROBOT = "planner-sim";
export const TARGETS: LatencyTarget[] = [{ label: "Teleop · near", ms: 60 }, { label: "Teleop · target", ms: 100 }, { label: "Teleop · far", ms: 120 }];

/** The planner eval as `GET offline-evaluations/{id}` returns it: simulated time only, as the real import (no wall-clock time). */
function detail(episodes = PLANNER_EPISODES): OfflineEvaluationDetail {
  const base = offlineEvaluation(PLANNER_EVAL, 30, episodes.map(episode => ({ id: episode.id, seed: episode.seed, outcome: episode.outcome, steps: episode.steps, wall: null, metrics: episode.metrics })));
  const simulated = episodes.map(episode => episode.simSeconds).toSorted((a, b) => a - b);
  const middle = simulated.length % 2 ? simulated[(simulated.length - 1) / 2] : (simulated[simulated.length / 2 - 1] + simulated[simulated.length / 2]) / 2;
  return {
    ...base, source: "offline", signed: false,
    name: "Bimanual · planner on device", task: "Pills into bottle · two slices", config_label: "Edge planner r1", policy_label: "Planner on device, scripted skills",
    summary: { ...base.summary, median_sim_seconds: middle },
    episodes: base.episodes.map((episode, i) => ({ ...episode, outcome: episodes[i].outcome, metrics: episodes[i].metrics, sim_seconds: episodes[i].simSeconds })),
  };
}

/** The demo document with the planner eval linked from "Arm · Edge planner", which declares `targets`. */
export function evidenceDocument(targets: LatencyTarget[] | null = TARGETS): ConvoyWorkspace {
  const ws = demoDocument();
  const config = ws.configurations.find(item => item.id === PLANNER_CONFIG)!;
  if (targets) config.latencyTargets = targets;
  return addRobot(ws, { configId: config.id, name: "Planner sim", offlineEvaluationIds: [PLANNER_EVAL] }, Date.now() - 3_600_000).workspace;
}

/**
 * Serves the planner eval beside the contract offline evaluations (registered after `mockApi`, so it
 * answers first); `change` edits its episodes' metrics, e.g. to drop a field. Other paths fall through.
 */
export async function mockPlannerEval(page: Page, change?: (metrics: Record<string, unknown>, index: number) => void) {
  const episodes = PLANNER_EPISODES.map((episode, i) => {
    const metrics: Record<string, number | string> = { ...episode.metrics };
    change?.(metrics, i);
    return { ...episode, metrics };
  });
  const served = detail(episodes);
  const summary = (item: object) => Object.fromEntries(Object.entries(item).filter(([key]) => key !== "episodes"));
  await page.route(/\/api\/platform\/offline-evaluations(?:\/[^/?]+)?(?:\?.*)?$/, route => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\/platform\//, "");
    if (path === "offline-evaluations") return route.fulfill({ json: { items: [served, ...OFFLINE].map(summary) } });
    if (path === `offline-evaluations/${PLANNER_EVAL}`) return route.fulfill({ json: served });
    return route.fallback();
  });
}
