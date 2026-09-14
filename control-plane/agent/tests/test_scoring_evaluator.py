from __future__ import annotations

import pytest
from convoy_agent.evaluator import evaluate, summarize, validate_gates
from convoy_agent.scoring import rescore_from_record, score_case, validate_case


def test_scorers_are_deterministic_and_rescorable():
    cases = [
        ({"id": "a", "match": "exact", "expected": "56"}, " 56 ", True),
        ({"id": "b", "match": "label", "expected": "Paris"}, "paris.", True),
        ({"id": "c", "match": "any_of", "expected": ["STOP", "HALT"]}, "Halt!", True),
        (
            {"id": "d", "match": "json_field", "field": "action", "expected": "dock"},
            'Sure: {"action": "dock", "x": 1}',
            True,
        ),
        ({"id": "e", "match": "exact", "expected": "56"}, "57", False),
        ({"id": "f", "match": "json_field", "field": "action", "expected": "dock"}, "no json here", False),
    ]
    for case, out, want in cases:
        rec = score_case(case, out)
        assert rec["passed"] is want, (case, out)
        assert rescore_from_record(case, rec) is want  # server recomputation agrees
        assert "output" not in rec  # raw output is not part of the structured record
    rec = score_case({"id": "t", "match": "exact", "expected": "x"}, None, status="timeout")
    assert (
        rec["passed"] is False
        and rec["reason"] == "timeout"
        and rescore_from_record({"id": "t", "match": "exact", "expected": "x"}, rec) is False
    )
    assert validate_case({"id": "r", "prompt": "p", "match": "regex", "expected": ".*"})
    assert validate_case({"id": "r", "prompt": "p", "match": "contains", "expected": "x"})
    assert not validate_case({"id": "ok", "prompt": "p", "match": "label", "expected": "x"})


def test_gate_validation_rejects_unknowns_and_nonfinite():
    assert validate_gates([{"metric": "quality.pass_rate", "op": "min", "limit": 0.9}]) == []
    errs = validate_gates(
        [
            {"metric": "nope", "op": "min", "limit": 1},
            {"metric": "latency.p95_ms", "op": "gte", "limit": 1},
            {"metric": "latency.p95_ms", "op": "max", "limit": float("nan")},
            {"metric": "thermal.max_c", "op": "max", "limit": True},
        ]
    )
    assert len(errs) == 4


def test_evaluator_intrinsic_completeness_and_ordering():
    # partial coverage fails regardless of gates (R20)
    s = summarize([{"status": "ok", "passed": True}], [], [], expected_cases=2)
    assert evaluate(s, [])[0] == "failed"
    assert evaluate({}, [])[0] == "failed"
    # all timeouts cannot pass
    s = summarize([{"status": "timeout", "passed": False}], [{"status": "timeout"}], [], expected_cases=1)
    assert evaluate(s, [])[0] == "failed"
    # complete + missing required sensor => inconclusive; failed beats inconclusive
    recs = [{"status": "ok", "passed": True}, {"status": "ok", "passed": True}]
    tim = [{"status": "ok", "latency_ms": 5}, {"status": "ok", "latency_ms": 9}]
    s = summarize(recs, tim, [], expected_cases=2)
    assert s["thermal"]["max_c"] is None  # missing sensor stays None, never 0
    assert evaluate(s, [{"metric": "thermal.max_c", "op": "max", "limit": 85}])[0] == "inconclusive"
    assert (
        evaluate(
            s,
            [
                {"metric": "thermal.max_c", "op": "max", "limit": 85},
                {"metric": "latency.p95_ms", "op": "max", "limit": 1},
            ],
        )[0]
        == "failed"
    )
    assert (
        evaluate(
            s,
            [
                {"metric": "thermal.max_c", "op": "max", "limit": 85, "required": False},
                {"metric": "latency.p95_ms", "op": "max", "limit": 100},
            ],
        )[0]
        == "passed"
    )
    # cancelled never passes; non-finite value fails
    assert evaluate(s, [], cancelled=True)[0] == "failed"
    s2 = {**s, "latency": {**s["latency"], "p95_ms": float("inf")}}
    assert evaluate(s2, [{"metric": "latency.p95_ms", "op": "max", "limit": 100}])[0] == "failed"
    # percentiles are computed per device from raw samples, not averaged p95s
    assert s["latency"]["p95_ms"] == 9 and s["latency"]["p50_ms"] == 5


@pytest.mark.parametrize("n", [1, 2, 10])
def test_percentile_nearest_rank(n):
    from convoy_agent.scoring import percentile

    xs = [float(i) for i in range(1, n + 1)]
    assert percentile(xs, 50) in xs and percentile(xs, 95) == float(n) and percentile([], 95) is None
