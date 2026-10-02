/*
 * Contract fixture for slices and planner calls: ten offline episodes whose metrics have the simulator's
 * names and the values a second real on-device planner eval recorded (the same 1.5B model on a Jetson,
 * run from another machine; two task slices, five seeds each; every call answered, so no device or
 * transport failure). Generic: no account, device, release or customer data. The unit tests and the
 * e2e suite (tests/e2e/support/slices.ts) share it.
 */
import type { OfflineOutcome } from "../../src/lib/platform/client";
import type { FixtureEpisode } from "./evidence-fixture";

/** Items, calls, refusals and latencies of one episode; device or transport failures are 0, as recorded. */
const row = (id: string, seed: number, outcome: OfflineOutcome, steps: number, simSeconds: number, values: number[]): FixtureEpisode => {
  const [total, placed, calls, valid, invalidChoice, failed, e2e50, e2e95, device50, device95, ttft50, tokensIn, tokensOut] = values;
  return {
    id, seed, outcome, steps, simSeconds, metrics: {
      pills_total: total, pills_placed: placed,
      planner_calls: calls, planner_valid_replies: valid,
      planner_invalid_json: 0, planner_invalid_schema: 0, planner_invalid_choice: invalidChoice,
      planner_device_errors: 0, planner_http_errors: 0, planner_timeouts: 0,
      planner_failed_decisions: failed,
      planner_e2e_p50_ms: e2e50, planner_e2e_p95_ms: e2e95, planner_device_p50_ms: device50, planner_device_p95_ms: device95,
      planner_ttft_p50_ms: ttft50, planner_tokens_in_p50: tokensIn, planner_tokens_out_p50: tokensOut,
      protective_stops: 0, arm_arm_contacts: 0, slice: seed < 100 ? "nominal" : "more_items",
    },
  };
};

/** Simulated seconds, then: items, placed, calls, valid, invalid choice, failed decisions, e2e p50/p95, device p50/p95, ttft p50, tokens in/out p50. */
export const SECOND_RUN_EPISODES: FixtureEpisode[] = [
  row("oep_contract0200", 0, "timeout", 299, 150, [24, 23, 43, 32, 11, 1, 2402.5, 3125.6, 1335.7, 1392.7, 772, 861, 22]),
  row("oep_contract0201", 1, "timeout", 299, 150, [24, 23, 41, 29, 12, 2, 2413.5, 2823.3, 1322.8, 1405.9, 754.9, 841, 21]),
  row("oep_contract0202", 2, "success", 273, 136.1, [24, 24, 36, 25, 11, 3, 2410.7, 2831.6, 1336, 1393.5, 761.9, 843.5, 22]),
  row("oep_contract0203", 3, "success", 296, 147.6, [24, 24, 42, 27, 15, 3, 2429.8, 2858.9, 1332.2, 1397.2, 764.1, 855, 22]),
  row("oep_contract0204", 4, "success", 272, 135.9, [24, 24, 34, 29, 5, 0, 2408.6, 2884.9, 1322.8, 1404.3, 755.3, 832.5, 22]),
  row("oep_contract0205", 200, "timeout", 299, 150, [30, 23, 43, 27, 16, 3, 2427.9, 3158.4, 1355.2, 1417.8, 801.2, 942, 21]),
  row("oep_contract0206", 201, "timeout", 299, 150, [30, 29, 42, 33, 9, 2, 2388.2, 2800.8, 1365.8, 1452.1, 801.4, 900.5, 22]),
  row("oep_contract0207", 202, "timeout", 299, 150, [30, 24, 45, 39, 6, 0, 2426.3, 2895.2, 1399, 1488, 835.5, 923, 22]),
  row("oep_contract0208", 203, "timeout", 299, 150, [30, 22, 43, 25, 18, 5, 2440.6, 2918.1, 1382.8, 1457.8, 826.8, 939, 21]),
  row("oep_contract0209", 204, "timeout", 299, 150, [30, 26, 46, 35, 11, 0, 2455.3, 2885.2, 1379.1, 1468, 812, 925, 21.5]),
];
