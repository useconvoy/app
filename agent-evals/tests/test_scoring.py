"""Scoring-layer tests: item aggregation (weighted q, missing items), decay
stats on a hand-made sagging curve, trial status mapping, the invariant rule
(one violation in any trial is red), aggregate thresholds — including the
poll-cap rule that a failed run-scope quality verdict blocks an otherwise
healthy curve — and suite_green blocking on error verdicts / cost regression.

Python port of tests/scoring.test.ts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from convoy_evals.schema.scenario import EvalSetConfig, Scenario
from convoy_evals.schema.verdict import ScenarioVerdict, TrialResult, Verdict
from convoy_evals.scoring import (
    build_item_verdicts,
    build_scenario_verdict,
    build_suite_result,
    build_trial_result,
    decay_stats,
    mean_decay_across_trials,
    suite_green,
    trial_status_from_report,
)

# ---------------------------------------------------------------------------
# Fixture builders (kept local — mirrors tests/graders.fixtures.ts makeScenario)
# ---------------------------------------------------------------------------


def make_scenario(**overrides: Any) -> Scenario:
    base: Dict[str, Any] = {
        "id": "scn-test",
        "title": "test scenario",
        "missionType": "renewal",
        "kind": "single",
        "fixture": {"pack": "pack-a", "packHash": "ph-a"},
        "t0": "2026-01-05",
        "seed": 42,
        "bindings": {},
        "trigger": {
            "kind": "api",
            "missionSpec": {"missionType": "renewal", "goal": "renew the policy"},
        },
        "counterparties": [],
        "approvals": {"mode": "auto_approve", "maxGates": 10},
        "budgets": {"usd": 10, "simTime": "45d", "wallClock": "10m", "onExhaustion": "fail"},
        "answerKeyRef": {"path": "keys/scn-test.json", "hash": "kh"},
        "graders": [],
        "provenance": {"kind": "authored"},
        "tags": [],
    }
    base.update(overrides)
    return Scenario.model_validate(base)


def v(grader_id: str, **partial: Any) -> Verdict:
    base: Dict[str, Any] = {
        "graderId": grader_id,
        "graderVersion": "gv",
        "class": "quality",
        "scope": {"runId": "r1"},
        "status": "pass",
        "score": 1,
        "evidence": [],
    }
    base.update(partial)
    if base["status"] == "fail" and len(base["evidence"]) == 0:
        base["evidence"] = [{"kind": "note", "text": "violated"}]
    return Verdict.model_validate(base)


def tmpl(id_: str, weight: float) -> Dict[str, Any]:
    return {
        "id": id_,
        "scope": "item",
        "weight": weight,
        "class": "quality",
        "grader": "end_state",
        "asserts": [{"kind": "artifact_exists", "selector": {"tag": "x/%s" % id_}}],
    }


def items(n: int) -> List[Dict[str, Any]]:
    return [
        {"itemId": "POL-%d" % (i + 1), "ordinal": i + 1, "keyRef": "POL-%d" % (i + 1), "graders": "inherit"}
        for i in range(n)
    ]


def gauntlet(n: int, **over: Any) -> Scenario:
    base: Dict[str, Any] = {
        "kind": "gauntlet",
        "items": items(n),
        "itemGraderTemplate": [tmpl("g-a", 3), tmpl("g-b", 1)],
    }
    base.update(over)
    return make_scenario(**base)


CONFIG = EvalSetConfig.model_validate(
    {
        "name": "convoy-core",
        "version": "1.0.0",
        "scenarios": [],
        "thresholds": {"slopeMin": -0.02, "aucMin": 0.7, "itemFloor": 0.3},
        "defaultTrials": {
            "single": {"n": 3, "passRule": {"kind": "at_least", "k": 2}},
            "gauntlet": {"n": 3, "passRule": {"kind": "aggregate", "thresholdsRef": "eval-set"}},
        },
        "costRegressionGuardPct": 25,
    }
)

# ---------------------------------------------------------------------------
# build_item_verdicts
# ---------------------------------------------------------------------------


def test_build_item_verdicts_weighted_q_advisory_excluded_missing_item():
    scenario = gauntlet(3)
    verdicts = [
        # POL-1: both pass; an advisory judge fail must not gate or drag q.
        v("g-a", scope={"runId": "r1", "itemId": "POL-1"}),
        v("g-b", scope={"runId": "r1", "itemId": "POL-1"}),
        v("judge-1", scope={"runId": "r1", "itemId": "POL-1"}, status="fail", score=0, advisory=True),
        # POL-2: weight-3 grader passes, weight-1 fails -> q = 3/4.
        v("g-a", scope={"runId": "r1", "itemId": "POL-2"}),
        v("g-b", scope={"runId": "r1", "itemId": "POL-2"}, status="fail", score=0),
        # POL-3: nothing — the agent never touched it.
    ]
    iv = build_item_verdicts(scenario, verdicts)
    assert len(iv) == 3

    assert iv[0].itemId == "POL-1"
    assert iv[0].status == "pass"
    assert iv[0].q == 1

    assert iv[1].status == "fail"
    assert iv[1].q == 0.75

    assert iv[2].itemId == "POL-3"
    assert iv[2].status == "missing"
    assert iv[2].q == 0
    assert len(iv[2].verdicts) == 0


def test_build_item_verdicts_error_verdict_marks_item_error():
    scenario = gauntlet(1)
    iv = build_item_verdicts(
        scenario, [v("g-a", scope={"runId": "r1", "itemId": "POL-1"}, status="error", score=0)]
    )
    assert iv[0].status == "error"


# ---------------------------------------------------------------------------
# decay_stats
# ---------------------------------------------------------------------------


def test_decay_stats_on_sagging_curve():
    # q(n) = 1 - 0.04*(n-1) over 20 items: 1.00 down to 0.24, slope exactly -0.04.
    pts = [{"ordinal": i + 1, "q": 1 - 0.04 * i} for i in range(20)]
    d = decay_stats(pts)
    assert d.slope == pytest.approx(-0.04, abs=1e-9)
    assert d.slope < 0
    assert d.auc == pytest.approx((1 + 0.24) / 2, abs=1e-9)  # mean of a linear ramp
    assert d.itemCount == 20
    assert d.firstDecileMean == pytest.approx((1 + 0.96) / 2, abs=1e-9)  # ceil(20/10) = 2 items
    assert d.lastDecileMean == pytest.approx((0.28 + 0.24) / 2, abs=1e-9)
    assert d.minQ == pytest.approx(0.24, abs=1e-9)


def test_decay_stats_handles_flat_and_tiny_inputs():
    flat = decay_stats([{"ordinal": 1, "q": 0.9}, {"ordinal": 2, "q": 0.9}])
    assert flat.slope == 0
    assert flat.auc == pytest.approx(0.9)

    single = decay_stats([{"ordinal": 1, "q": 0.7}])
    assert single.slope == 0
    assert single.firstDecileMean == pytest.approx(0.7)
    assert single.lastDecileMean == pytest.approx(0.7)

    empty = decay_stats([])
    assert empty.itemCount == 0


# ---------------------------------------------------------------------------
# build_trial_result — status mapping
# ---------------------------------------------------------------------------


def test_build_trial_result_maps_run_reports_to_statuses():
    scenario = make_scenario()  # onExhaustion: 'fail'

    def mk(report: Dict[str, Any]) -> str:
        return build_trial_result(
            scenario=scenario,
            trial_idx=0,
            run_id="r1",
            verdicts=[],
            cost_usd=1,
            sim_days=45,
            wall_ms=1000,
            report=report,
        ).status

    assert mk({}) == "completed"
    assert mk({"terminal": "landed"}) == "completed"
    assert mk({"deadlock": True}) == "deadlock"
    assert mk({"guardTripped": "wall"}) == "guard_tripped"
    assert mk({"guardTripped": "usd"}) == "budget_exceeded"  # onExhaustion 'fail'

    grade_partial = make_scenario(
        budgets={"usd": 10, "simTime": "45d", "wallClock": "10m", "onExhaustion": "grade_partial"}
    )
    assert trial_status_from_report({"guardTripped": "usd"}, grade_partial) == "guard_tripped"


def test_build_trial_result_carries_precomputed_status_and_diagnosis():
    trial = build_trial_result(
        scenario=make_scenario(),
        trial_idx=2,
        run_id="r2",
        status="deadlock",
        deadlock_diagnosis="no timers, no gates, not terminal",
        verdicts=[],
        cost_usd=0.5,
        sim_days=3,
        wall_ms=100,
    )
    assert trial.status == "deadlock"
    assert trial.deadlockDiagnosis == "no timers, no gates, not terminal"
    assert trial.decay is None  # single scenario, no items


# ---------------------------------------------------------------------------
# build_scenario_verdict — invariant rule, at_least, aggregate
# ---------------------------------------------------------------------------


def quality_trial(
    scenario: Scenario, idx: int, verdicts: List[Verdict], status: str = "completed"
) -> TrialResult:
    return build_trial_result(
        scenario=scenario,
        trial_idx=idx,
        run_id="r%d" % idx,
        status=status,
        verdicts=verdicts,
        cost_usd=1,
        sim_days=45,
        wall_ms=1000,
    )


def test_invariant_rule_one_fail_in_one_of_three_trials_turns_scenario_red():
    scenario = make_scenario(trials={"n": 3, "passRule": {"kind": "at_least", "k": 2}})

    def passing() -> List[Verdict]:
        return [v("q1")]

    trials = [
        quality_trial(scenario, 0, passing()),
        quality_trial(
            scenario,
            1,
            passing() + [v("two-phase", **{"class": "invariant"}, status="fail", score=0)],
        ),
        quality_trial(scenario, 2, passing()),
    ]
    verdict = build_scenario_verdict(scenario, trials, CONFIG)
    assert verdict.invariantViolation is True
    assert verdict.status == "failed"

    # Sanity: without the invariant verdict the same trials pass at_least k=2.
    clean_trials = [
        quality_trial(scenario, 0, passing()),
        quality_trial(scenario, 1, passing()),
        quality_trial(scenario, 2, passing()),
    ]
    clean = build_scenario_verdict(scenario, clean_trials, CONFIG)
    assert clean.status == "passed"
    assert clean.invariantViolation is False


def test_at_least_counts_only_completed_trials_with_all_quality_passes():
    scenario = make_scenario(trials={"n": 3, "passRule": {"kind": "at_least", "k": 2}})
    trials = [
        quality_trial(scenario, 0, [v("q1")]),
        quality_trial(scenario, 1, [v("q1", status="fail", score=0)]),
        quality_trial(scenario, 2, [v("q1")], "deadlock"),  # completed only
    ]
    verdict = build_scenario_verdict(scenario, trials, CONFIG)
    assert verdict.status == "failed"  # only 1 qualifying trial

    advisory_only_fail = [
        quality_trial(scenario, 0, [v("q1")]),
        quality_trial(
            scenario, 1, [v("q1"), v("judge", status="fail", score=0, advisory=True)]
        ),
        quality_trial(scenario, 2, [v("q1")]),
    ]
    assert build_scenario_verdict(scenario, advisory_only_fail, CONFIG).status == "passed"


def gauntlet_trial(scenario: Scenario, idx: int, q_by_ordinal: List[float]) -> TrialResult:
    verdicts: List[Verdict] = []
    for i, q in enumerate(q_by_ordinal):
        verdicts.append(v("g-a", scope={"runId": "r%d" % idx, "itemId": "POL-%d" % (i + 1)}, score=q))
        verdicts.append(v("g-b", scope={"runId": "r%d" % idx, "itemId": "POL-%d" % (i + 1)}, score=q))
    return quality_trial(scenario, idx, verdicts)


def test_aggregate_rule_passes_healthy_decay_and_fails_sagging_curve():
    scenario = gauntlet(
        10, trials={"n": 2, "passRule": {"kind": "aggregate", "thresholdsRef": "eval-set"}}
    )

    healthy_q = [0.9] * 10
    healthy = build_scenario_verdict(
        scenario,
        [gauntlet_trial(scenario, 0, healthy_q), gauntlet_trial(scenario, 1, healthy_q)],
        CONFIG,
    )
    assert healthy.status == "passed"
    assert healthy.meanDecay is not None
    assert healthy.meanDecay.auc == pytest.approx(0.9, abs=1e-9)

    # Sagging: slope -0.08 < slopeMin -0.02, last items under the 0.3 floor.
    sagging_q = [1 - 0.08 * i for i in range(10)]
    sagging = build_scenario_verdict(
        scenario,
        [gauntlet_trial(scenario, 0, sagging_q), gauntlet_trial(scenario, 1, sagging_q)],
        CONFIG,
    )
    assert sagging.status == "failed"
    assert sagging.meanDecay.slope < CONFIG.thresholds.slopeMin
    assert "aggregate" in sagging.passDetail


def test_aggregate_rule_fails_on_failed_run_scope_quality_verdict_despite_flat_curve():
    # The poll-cap fix: a gauntlet with a perfectly healthy decay curve but a
    # failed run-scope quality obligation (e.g. a poll cap) is NOT a pass.
    scenario = gauntlet(
        10, trials={"n": 2, "passRule": {"kind": "aggregate", "thresholdsRef": "eval-set"}}
    )
    healthy_q = [0.9] * 10
    t0 = gauntlet_trial(scenario, 0, healthy_q)
    t1_verdicts = [
        v("g-a", scope={"runId": "r1", "itemId": "POL-%d" % (i + 1)}, score=0.9) for i in range(10)
    ] + [
        v("g-b", scope={"runId": "r1", "itemId": "POL-%d" % (i + 1)}, score=0.9) for i in range(10)
    ] + [v("poll-cap", scope={"runId": "r1"}, status="fail", score=0)]
    t1 = quality_trial(scenario, 1, t1_verdicts)

    verdict = build_scenario_verdict(scenario, [t0, t1], CONFIG)
    assert verdict.status == "failed"
    assert "run-scope quality verdict(s) failed" in verdict.passDetail
    assert "poll-cap" in verdict.passDetail

    # An advisory run-scope fail does not gate.
    t1_advisory = quality_trial(
        scenario,
        1,
        [
            v("g-a", scope={"runId": "r1", "itemId": "POL-%d" % (i + 1)}, score=0.9)
            for i in range(10)
        ]
        + [
            v("g-b", scope={"runId": "r1", "itemId": "POL-%d" % (i + 1)}, score=0.9)
            for i in range(10)
        ]
        + [v("judge", scope={"runId": "r1"}, status="fail", score=0, advisory=True)],
    )
    assert build_scenario_verdict(scenario, [t0, t1_advisory], CONFIG).status == "passed"


def test_mean_decay_across_trials_averages_q_per_ordinal():
    scenario = gauntlet(
        2, trials={"n": 2, "passRule": {"kind": "aggregate", "thresholdsRef": "eval-set"}}
    )
    trials = [gauntlet_trial(scenario, 0, [1, 0.5]), gauntlet_trial(scenario, 1, [0.5, 0.5])]
    stats = mean_decay_across_trials(scenario, trials)
    assert stats is not None
    assert stats.itemCount == 2
    assert stats.auc == pytest.approx(0.625, abs=1e-9)  # ((1+0.5)/2 + (0.5+0.5)/2) / 2


def test_error_verdicts_are_never_green():
    scenario = make_scenario(trials={"n": 1, "passRule": {"kind": "at_least", "k": 1}})
    trials = [quality_trial(scenario, 0, [v("q1"), v("judge", status="error", score=0)])]
    verdict = build_scenario_verdict(scenario, trials, CONFIG)
    assert verdict.status == "error"

    # Advisory errors (uncalibrated judge cache miss) do not flip the status.
    advisory_trials = [
        quality_trial(scenario, 0, [v("q1"), v("judge", status="error", score=0, advisory=True)])
    ]
    assert build_scenario_verdict(scenario, advisory_trials, CONFIG).status == "passed"


def test_quarantined_scenarios_keep_the_invariant_flag():
    scenario = make_scenario(trials={"n": 1, "passRule": {"kind": "at_least", "k": 1}})
    trials = [
        quality_trial(
            scenario, 0, [v("inv", **{"class": "invariant"}, status="fail", score=0)]
        )
    ]
    verdict = build_scenario_verdict(scenario, trials, CONFIG, quarantined=True)
    assert verdict.status == "quarantined"
    assert verdict.invariantViolation is True


# ---------------------------------------------------------------------------
# suite_green + build_suite_result
# ---------------------------------------------------------------------------


def scn_verdict(scenario_id: str, **partial: Any) -> ScenarioVerdict:
    base: Dict[str, Any] = {
        "scenarioId": scenario_id,
        "status": "passed",
        "invariantViolation": False,
        "trials": [],
        "meanDecay": None,
        "passDetail": "",
        "totalCostUsd": 0,
    }
    base.update(partial)
    return ScenarioVerdict.model_validate(base)


def test_suite_green_iff_no_violations_all_passed_zero_errors():
    all_good = suite_green([scn_verdict("s1"), scn_verdict("s2")], CONFIG)
    assert all_good.green is True

    failed = suite_green([scn_verdict("s1", status="failed")], CONFIG)
    assert failed.green is False

    invariant = suite_green(
        [scn_verdict("s1", status="failed", invariantViolation=True)], CONFIG
    )
    assert invariant.green is False
    assert any("invariant" in d for d in invariant.detail)


def test_suite_green_blocks_on_non_advisory_error_verdicts_in_trials():
    scenario = make_scenario()
    trial = quality_trial(scenario, 0, [v("q1"), v("boom", status="error", score=0)])
    # Even if aggregation mislabeled the scenario passed, suite_green still blocks.
    out = suite_green([scn_verdict("s1", status="passed", trials=[trial])], CONFIG)
    assert out.green is False
    assert any("error verdict" in d for d in out.detail)

    advisory_trial = quality_trial(
        scenario, 1, [v("q1"), v("judge", status="error", score=0, advisory=True)]
    )
    ok = suite_green([scn_verdict("s1", status="passed", trials=[advisory_trial])], CONFIG)
    assert ok.green is True
    assert any("advisory" in d for d in ok.detail)  # excluded-but-noted


def test_suite_green_excludes_quarantined_but_notes_them():
    out = suite_green(
        [
            scn_verdict("s1"),
            scn_verdict("s-flaky", status="quarantined", invariantViolation=True),
        ],
        CONFIG,
    )
    assert out.green is True
    assert any("quarantined" in d for d in out.detail)
    assert any("WARNING" in d and "s-flaky" in d for d in out.detail)


def test_suite_green_enforces_cost_regression_guard():
    within = suite_green(
        [scn_verdict("s1", totalCostUsd=12)], CONFIG, baseline_cost_usd=10
    )  # cap 12.5
    assert within.green is True

    over = suite_green([scn_verdict("s1", totalCostUsd=13)], CONFIG, baseline_cost_usd=10)
    assert over.green is False
    assert any("cost regression" in d for d in over.detail)


def test_build_suite_result_assembles_valid_suite_result():
    result = build_suite_result(
        config=CONFIG,
        subject={"kind": "scripted", "label": "golden"},
        started_at="2026-08-02T00:00:00.000Z",
        finished_at="2026-08-02T00:05:00.000Z",
        scenarios=[scn_verdict("s1", totalCostUsd=2.5)],
    )
    assert result.green is True
    assert result.totalCostUsd == 2.5
    assert result.evalSet.name == "convoy-core"
    # Round-trips through the schema (pydantic re-validation).
    result.__class__.model_validate(result.model_dump(by_alias=True))
