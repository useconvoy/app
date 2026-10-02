import unittest

from jetson_timing.metrics import assess


def trace(latency=.1, fallback_after=None):
    events = []
    for i in range(250):
        e = {"type": "step", "dispatch_lag_s": .0001, "completion_lag_s": 0,
             "physics_s": .002, "source_sequence": i // 20, "observation_age_s": .2}
        if i < 8 or (fallback_after is not None and i >= fallback_after):
            e.pop("source_sequence")
            e.pop("observation_age_s")
            e["fallback"] = True
        events.append(e)
    for i in range(12):
        at = i * .2
        events.append({"type": "result", "observed_at": at, "render_started_at": at + .001,
                       "render_finished_at": at + .011, "render_s": .01,
                       "inference_started_at": at + .012, "inference_finished_at": at + latency - .002,
                       "inference_s": latency - .014, "received_at": at + latency, "actions_count": 50})
    return events


class TimingContractTests(unittest.TestCase):
    def test_measured_continuity_and_startup_are_distinct(self):
        result = assess(trace(), .0125, .625, .625, True)
        self.assertEqual(result["status"], "passed_observed_contract")
        self.assertEqual(result["startup_fallback_ticks"], 8)
        self.assertEqual(result["steady_fallback_fraction"], 0)
        self.assertAlmostEqual(result["estimated_continuous_buffer_span_s"], .3125)

    def test_physics_can_pass_while_policy_is_too_late(self):
        result = assess(trace(latency=.7), .0125, .625, .625, True)
        self.assertEqual(result["status"], "failed")
        self.assertIn("policy_results_exceed_freshness_budget", result["reasons"])

    def test_buffer_starvation_is_not_hidden_by_low_inference_latency(self):
        result = assess(trace(fallback_after=100), .0125, .625, .625, True)
        self.assertEqual(result["status"], "failed")
        self.assertIn("steady_state_buffer_starvation", result["reasons"])

    def test_short_trial_cannot_claim_a_pass(self):
        result = assess(trace()[:50] + trace()[-2:], .0125, .625, .625, True)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_late_dispatch_is_not_hidden_by_a_fast_physics_step(self):
        events = trace()
        for event in events:
            if event["type"] == "step":
                event["dispatch_lag_s"] = .02
        result = assess(events, .0125, .625, .625, True)
        self.assertIn("physics_deadline_missed", result["reasons"])

    def test_scripted_camera_latency_is_not_a_policy_buffer_estimate(self):
        result = assess(trace(), .0125, .625, .625, False)
        self.assertIsNone(result["estimated_actions_for_continuity"])


if __name__ == "__main__":
    unittest.main()
