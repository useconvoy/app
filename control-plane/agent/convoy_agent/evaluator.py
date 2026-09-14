"""Versioned deterministic gate evaluator shared by device and server (§8.10).

Input: a `summary` produced by `summarize()` from per-case records, per-request timing samples and
sensor samples, plus the plan's `gates`. Output: verdict passed | failed | inconclusive with a per-gate
report. Rules:
* unknown metric key or operator, NaN/Infinity limits -> rejected at plan validation (validate_gates)
* any known violation -> failed
* else any required gate whose metric is unavailable -> inconclusive
* every attempt counts in the error denominator; incomplete/cancelled/scorer-error/all-timeout runs
  cannot pass (coverage gate)"""

from __future__ import annotations

import math
from typing import Any

from .scoring import percentile

EVALUATOR_VERSION = "1"

# metric -> (unit, evidence method that supports it)
METRICS: dict[str, tuple[str, str]] = {
    "quality.pass_rate": ("ratio", "per_case_records"),
    "quality.passed": ("count", "per_case_records"),
    "errors.count": ("count", "per_case_records"),
    "errors.rate": ("ratio", "per_case_records"),
    "coverage.completed_ratio": ("ratio", "per_case_records"),
    "latency.p50_ms": ("ms", "request_timings"),
    "latency.p95_ms": ("ms", "request_timings"),
    "latency.ttft_p50_ms": ("ms", "request_timings"),
    "latency.ttft_p95_ms": ("ms", "request_timings"),
    "latency.queue_p95_ms": ("ms", "request_timings"),
    "throughput.tok_s_mean": ("tok/s", "request_timings"),
    "memory.peak_used_mb": ("MiB", "sensor_samples"),
    "memory.min_available_mb": ("MiB", "sensor_samples"),
    "thermal.max_c": ("C", "sensor_samples"),
    "power.avg_w": ("W", "sensor_samples"),
    "power.peak_w": ("W", "sensor_samples"),
    "backend.gpu_offloaded_layers": ("count", "runtime_props"),
}
OPS = {"min", "max"}


def validate_gates(gates: list[dict[str, Any]]) -> list[str]:
    errs: list[str] = []
    seen = set()
    for i, g in enumerate(gates):
        m = g.get("metric")
        if m not in METRICS:
            errs.append(f"gate {i}: unknown metric {m!r}")
            continue
        if g.get("op") not in OPS:
            errs.append(f"gate {i}: op must be min or max")
        lim = g.get("limit")
        if isinstance(lim, bool) or not isinstance(lim, (int, float)) or math.isnan(lim) or math.isinf(lim):
            errs.append(f"gate {i}: limit must be a finite number")
        if (m, g.get("op")) in seen:
            errs.append(f"gate {i}: duplicate gate for {m}/{g.get('op')}")
        seen.add((m, g.get("op")))
        ev = g.get("evidence") or METRICS[m][1]
        if ev != METRICS[m][1]:
            errs.append(f"gate {i}: metric {m} is supported by evidence {METRICS[m][1]!r}, not {ev!r}")
    return errs


def _get(summary: dict[str, Any], dotted: str) -> Any:
    cur: Any = summary
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def summarize(
    case_records: list[dict[str, Any]],
    timings: list[dict[str, Any]],
    sensors: list[dict[str, Any]],
    runtime_props: dict[str, Any] | None = None,
    expected_cases: int | None = None,
) -> dict[str, Any]:
    """Build the metric summary. Missing sensors stay None (never 0)."""
    n_expected = expected_cases if expected_cases is not None else len(case_records)
    n_done = len(case_records)
    n_ok = sum(1 for c in case_records if c.get("status") == "ok")
    n_pass = sum(1 for c in case_records if c.get("passed"))
    # R20 follow-up: errors are counted over the SAME population as cases. Timing samples must
    # correspond one-to-one with case records (by case_id when present); inconsistent evidence is
    # flagged and treated as an error so it can never inflate the denominator or hide failures.
    case_ids = [c.get("id") for c in case_records]
    timing_ids = [t.get("case_id") for t in timings]
    consistent = len(timings) == len(case_records) and (
        all(i is None for i in timing_ids)
        or sorted(str(x) for x in timing_ids) == sorted(str(x) for x in case_ids)
    )
    n_err = sum(1 for c in case_records if c.get("status") != "ok")
    if not consistent:
        n_err = max(n_err, len(case_records) + abs(len(timings) - len(case_records)))
    attempts = len(case_records)
    lat = [
        float(t["latency_ms"]) for t in timings if t.get("latency_ms") is not None and t.get("status") == "ok"
    ]
    ttft = [float(t["ttft_ms"]) for t in timings if t.get("ttft_ms") is not None and t.get("status") == "ok"]
    queue = [
        float(t["queue_ms"]) for t in timings if t.get("queue_ms") is not None and t.get("status") == "ok"
    ]
    toks = [float(t["tok_s"]) for t in timings if t.get("tok_s") is not None and t.get("status") == "ok"]

    def col(key: str) -> list[float]:
        return [float(s[key]) for s in sensors if s.get(key) is not None]

    mem_used = col("mem_used_mb")
    mem_avail = col("mem_available_mb")
    temps = col("temp_max_c")
    power = col("power_w")
    return {
        "quality": {
            "cases": n_expected,
            "scored": n_done,
            "passed": n_pass,
            "pass_rate": (n_pass / n_expected) if n_expected else None,
        },
        "errors": {
            "count": n_err,
            "attempts": attempts,
            "rate": (n_err / attempts) if attempts else None,
            "evidence_consistent": consistent,
        },
        "coverage": {
            "expected": n_expected,
            "completed": n_ok,
            "completed_ratio": (n_ok / n_expected) if n_expected else None,
            "all_timeout": bool(attempts) and all(t.get("status") == "timeout" for t in timings)
            if timings
            else False,
        },
        "latency": {
            "p50_ms": percentile(lat, 50),
            "p95_ms": percentile(lat, 95),
            "ttft_p50_ms": percentile(ttft, 50),
            "ttft_p95_ms": percentile(ttft, 95),
            "queue_p95_ms": percentile(queue, 95),
            "requests": len(timings),
            "cold_first_ms": (lat[0] if lat else None),
        },
        "throughput": {"tok_s_mean": (sum(toks) / len(toks)) if toks else None, "samples": len(toks)},
        "memory": {
            "peak_used_mb": max(mem_used) if mem_used else None,
            "min_available_mb": min(mem_avail) if mem_avail else None,
            "samples": len(sensors),
        },
        "thermal": {"max_c": max(temps) if temps else None, "samples": len(temps)},
        "power": {
            "avg_w": (sum(power) / len(power)) if power else None,
            "peak_w": max(power) if power else None,
            "samples": len(power),
        },
        "backend": {
            "gpu_offloaded_layers": (runtime_props or {}).get("gpu_offloaded_layers"),
            "backend": (runtime_props or {}).get("backend"),
        },
    }


def evaluate(
    summary: dict[str, Any], gates: list[dict[str, Any]], *, complete: bool = True, cancelled: bool = False
) -> tuple[str, list[dict[str, Any]]]:
    """Return (verdict, report). Verdict: passed | failed | inconclusive.
    Intrinsic (gate-independent) rules, R20: the run must have expected > 0 cases, and scored ==
    completed == expected; otherwise it FAILS regardless of gates. Non-finite metric values fail."""
    report: list[dict[str, Any]] = []
    failed = False
    inconclusive = False
    cov = summary.get("coverage") or {}
    q = summary.get("quality") or {}
    expected = cov.get("expected")
    completed = cov.get("completed")
    scored = q.get("scored")
    if cancelled:
        report.append({"metric": "run", "status": "fail", "reason": "cancelled"})
        failed = True
    if not complete:
        report.append({"metric": "coverage", "status": "fail", "reason": "run reported incomplete"})
        failed = True
    if not isinstance(expected, int) or isinstance(expected, bool) or expected <= 0:
        report.append({"metric": "coverage", "status": "fail", "reason": "no expected cases"})
        failed = True
    elif completed != expected or scored != expected:
        report.append(
            {
                "metric": "coverage",
                "status": "fail",
                "reason": f"incomplete: scored={scored} completed={completed} expected={expected}",
            }
        )
        failed = True
    if cov.get("all_timeout"):
        report.append({"metric": "coverage", "status": "fail", "reason": "all requests timed out"})
        failed = True
    for g in gates:
        m, op, lim = g["metric"], g["op"], g["limit"]
        required = bool(g.get("required", True))
        val = _get(summary, m)
        entry = {
            "metric": m,
            "op": op,
            "limit": lim,
            "value": val,
            "required": required,
            "evidence": g.get("evidence", METRICS.get(m, ("", ""))[1]),
        }
        if isinstance(val, bool) or (val is not None and not isinstance(val, (int, float))):
            entry["status"] = "fail"
            entry["reason"] = "non-numeric metric"
            failed = True
        elif isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            entry["status"] = "fail"
            entry["reason"] = "non-finite metric"
            failed = True
        elif val is None:
            entry["status"] = "unavailable"
            if required:
                inconclusive = True
        else:
            ok = (val >= lim) if op == "min" else (val <= lim)
            entry["status"] = "pass" if ok else "fail"
            failed = failed or not ok
        report.append(entry)
    if failed:
        return "failed", report
    if inconclusive:
        return "inconclusive", report
    return "passed", report
