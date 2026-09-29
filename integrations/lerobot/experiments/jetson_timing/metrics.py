"""Measured timing contract, separate from task success and simulation speed."""

import math


def distribution(values):
    if not values:
        return {"count": 0, "p50": None, "p95": None, "p99": None, "min": None, "max": None}
    ordered = sorted(values)
    return {"count": len(ordered), **{name: ordered[math.ceil(len(ordered) * q) - 1]
            for name, q in (("p50", .5), ("p95", .95), ("p99", .99))}, "min": ordered[0], "max": ordered[-1]}


def assess(events, period, max_age, horizon, learned, max_fallback=.05, min_results=10):
    steps = [e for e in events if e["type"] == "step"]
    results = [e for e in events if e["type"] == "result"]
    durations = {
        "snapshot_to_render_start_s": [e["render_started_at"] - e["observed_at"] for e in results],
        "camera_s": [e["render_s"] for e in results],
        "camera_to_inference_start_s": [e["inference_started_at"] - e["render_finished_at"]
                                        for e in results if "inference_started_at" in e],
        "inference_s": [e["inference_s"] for e in results if "inference_s" in e],
        "result_delivery_s": [e["received_at"] - e["inference_finished_at"]
                              for e in results if "inference_finished_at" in e],
        "observation_to_result_s": [e["received_at"] - e["observed_at"] for e in results],
        "result_interval_s": [b["received_at"] - a["received_at"] for a, b in zip(results, results[1:], strict=False)],
        "observation_to_action_s": [e["observation_age_s"] for e in steps if "observation_age_s" in e],
        "tick_dispatch_lag_s": [e["dispatch_lag_s"] for e in steps],
        "tick_completion_lag_s": [e["completion_lag_s"] for e in steps],
        "physics_step_s": [e["physics_s"] for e in steps],
        "remaining_chunk_budget_s": [min(max_age, horizon) - (e["received_at"] - e["observed_at"])
                                     for e in results if "actions_count" in e],
    }
    measured = {name: distribution(values) for name, values in durations.items()}
    first_use = next((i for i, e in enumerate(steps) if "source_sequence" in e), None)
    steady = steps[first_use:] if first_use is not None else []
    fallback = sum(bool(e.get("fallback")) for e in steps)
    steady_fallback = sum(bool(e.get("fallback")) for e in steady)
    reasons = []
    if not steps:
        reasons.append("no_control_steps")
    if steps and max(measured["tick_completion_lag_s"]["p99"], measured["tick_dispatch_lag_s"]["p99"]) > period:
        reasons.append("physics_deadline_missed")
    if learned:
        if first_use is None:
            reasons.append("no_useful_policy_action")
        if results and measured["observation_to_result_s"]["p99"] >= min(max_age, horizon):
            reasons.append("policy_results_exceed_freshness_budget")
        if steady and steady_fallback / len(steady) > max_fallback:
            reasons.append("steady_state_buffer_starvation")
    insufficient = len(steps) < 200 or (learned and len(results) < min_results)
    latency = measured["observation_to_result_s"]["p99"]
    interval = measured["result_interval_s"]["p99"]
    span = latency + interval + period if learned and latency is not None and interval is not None else None
    return {
        "status": "failed" if reasons else "insufficient_evidence" if insufficient else "passed_observed_contract",
        "reasons": reasons, "contract": {"period_s": period, "max_observation_age_s": max_age,
            "chunk_horizon_s": horizon, "max_steady_fallback_fraction": max_fallback,
            "min_control_steps": 200, "min_policy_results": min_results if learned else 0,
            "physics_completion_p99_limit_s": period, "physics_dispatch_p99_limit_s": period},
        "stages": measured, "fallback_ticks": fallback,
        "fallback_fraction": fallback / len(steps) if steps else None,
        "startup_fallback_ticks": first_use if first_use is not None else len(steps) if learned else 0,
        "steady_fallback_fraction": steady_fallback / len(steady) if steady else None,
        "estimated_continuous_buffer_span_s": span,
        "estimated_actions_for_continuity": math.ceil(span / period) if span is not None else None,
        "estimate_scope": "p99 latency + p99 result interval + one tick; one in-flight request; not a guarantee or permission to change trained action cadence",
    }
