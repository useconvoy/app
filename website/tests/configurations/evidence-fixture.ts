/*
 * Contract fixture for the evidence panels: ten offline episodes whose metrics have the simulator's
 * names and the values a real on-device planner eval recorded (a 1.5B model on a Jetson, two task
 * slices, five seeds each). Generic: no account, device or customer data. The unit tests and the
 * e2e suite (tests/e2e/support/evidence.ts) share it.
 */
import type { OfflineOutcome } from "../../src/lib/platform/client";

export interface FixtureEpisode { id: string; seed: number; outcome: OfflineOutcome; steps: number; simSeconds: number; metrics: Record<string, number | string> }

const row = (id: string, seed: number, outcome: OfflineOutcome, steps: number, simSeconds: number, values: number[]): FixtureEpisode => {
  const [calls, valid, invalid, failed, e2e50, e2e95, device50, device95, ttft50, tokensIn, tokensOut, stops, contacts] = values;
  return {
    id, seed, outcome, steps, simSeconds, metrics: {
      planner_calls: calls, planner_valid_replies: valid, planner_invalid_choice: invalid, planner_failed_decisions: failed,
      planner_e2e_p50_ms: e2e50, planner_e2e_p95_ms: e2e95, planner_device_p50_ms: device50, planner_device_p95_ms: device95,
      planner_ttft_p50_ms: ttft50, planner_tokens_in_p50: tokensIn, planner_tokens_out_p50: tokensOut,
      protective_stops: stops, arm_arm_contacts: contacts, slice: seed < 100 ? "nominal" : "more_items",
    },
  };
};

/** Simulated seconds, then: calls, valid, invalid choice, failed decisions, e2e p50/p95, device p50/p95, ttft p50, tokens in/out p50, protective stops, arm–arm contacts. */
export const PLANNER_EPISODES: FixtureEpisode[] = [
  row("oep_contract0100", 0, "timeout", 299, 150, [39, 33, 6, 1, 2675.3, 3308, 1344.6, 1407.8, 775.8, 870, 22, 8, 140]),
  row("oep_contract0101", 1, "success", 288, 143.6, [39, 30, 9, 2, 2672.7, 3275.7, 1333.2, 1404.7, 770.8, 845, 21, 0, 0]),
  row("oep_contract0102", 2, "success", 292, 145.6, [36, 25, 11, 3, 2653.6, 3427.1, 1330, 1396, 763.9, 843.5, 22, 0, 0]),
  row("oep_contract0103", 3, "success", 275, 137.3, [35, 26, 9, 2, 2671, 3587.9, 1328.2, 1391.9, 765.5, 855, 22, 0, 0]),
  row("oep_contract0104", 4, "success", 269, 134.4, [36, 26, 10, 1, 2692.3, 3405, 1337.4, 1402.8, 766.5, 851, 22, 0, 0]),
  row("oep_contract0105", 200, "timeout", 299, 150, [40, 26, 14, 2, 2801.1, 3463.9, 1389.5, 1466.2, 833.7, 956, 21, 0, 0]),
  row("oep_contract0106", 201, "timeout", 299, 150, [27, 27, 0, 0, 2738.3, 17821.7, 1406.2, 1452, 849.4, 956, 22, 0, 0]),
  row("oep_contract0107", 202, "timeout", 299, 150, [46, 41, 5, 0, 2658, 3173.2, 1390.7, 1478.9, 826.8, 922, 22, 0, 0]),
  row("oep_contract0108", 203, "timeout", 299, 150, [40, 25, 15, 4, 2722.1, 3718.7, 1379.9, 1471.9, 825.6, 936, 22, 0, 0]),
  row("oep_contract0109", 204, "timeout", 299, 150, [40, 34, 6, 0, 2719.3, 3381.2, 1397.6, 1461.2, 833.6, 943.5, 22, 0, 0]),
];
