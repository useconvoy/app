"""Scoring — the pure aggregation layers above Verdict:
ItemVerdict -> DecayStats -> TrialResult -> ScenarioVerdict -> SuiteResult.

Port of src/scoring/index.ts. Load-bearing rules encoded here:
 - INVARIANT RULE: one non-advisory invariant-class fail/error in ANY trial
   turns the scenario red, no matter what the pass rates say.
 - 'error' is never green — a scenario whose only problem is error verdicts
   gets status 'error', not 'passed'.
 - advisory verdicts (uncalibrated judges) are reported, never gating.
 - an item the agent never touched scores q=0, status 'missing'.
 - decay thresholds come from EvalSetConfig — the ONE place they live.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .schema.scenario import EvalSetConfig, Scenario, TrialPolicy
from .schema.verdict import (
    DecayStats,
    EvalSetId,
    ItemVerdict,
    ScenarioVerdict,
    SuiteResult,
    SuiteSubject,
    TrialResult,
    Verdict,
)


def _clamp01(n: float) -> float:
    if n != n:  # NaN
        return 0.0
    return min(1.0, max(0.0, n))


def _fmt(n: Any) -> str:
    """JS-style number rendering for detail strings (25.0 -> '25')."""
    if isinstance(n, float) and n == int(n):
        return str(int(n))
    return str(n)


# ---------------------------------------------------------------------------
# Item verdicts
# ---------------------------------------------------------------------------


def _weight_map(scenario: Scenario) -> Dict[str, float]:
    """graderId -> weight, harvested from every place a spec can live in the scenario."""
    m: Dict[str, float] = {}

    def add(specs: Optional[Sequence[Any]]) -> None:
        for s in specs or []:
            m[s.id] = s.weight if s.weight is not None else 1.0

    add(scenario.graders)
    add(scenario.itemGraderTemplate)
    for item in scenario.items or []:
        if item.graders != "inherit":
            add(item.graders)
    return m


def build_item_verdicts(scenario: Scenario, verdicts: List[Verdict]) -> List[ItemVerdict]:
    """Group item-scoped verdicts by itemId; q = weighted mean of non-advisory
    scores (skipped excluded). Items with zero verdicts are synthesized from
    scenario.items with q=0, status 'missing' — the agent never touched them."""
    weights = _weight_map(scenario)
    by_item: Dict[str, List[Verdict]] = {}
    for v in verdicts:
        if v.scope.itemId is None:
            continue
        by_item.setdefault(v.scope.itemId, []).append(v)

    items = sorted(scenario.items or [], key=lambda i: i.ordinal)
    out: List[ItemVerdict] = []
    for item in items:
        vs = by_item.get(item.itemId, [])
        if len(vs) == 0:
            out.append(
                ItemVerdict(
                    itemId=item.itemId, ordinal=item.ordinal, q=0.0, status="missing", verdicts=[]
                )
            )
            continue

        non_advisory = [v for v in vs if not v.advisory]
        scored = [v for v in non_advisory if v.status != "skipped"]
        q = 0.0
        if len(scored) > 0:
            w_sum = 0.0
            acc = 0.0
            for v in scored:
                w = weights.get(v.graderId, 1.0)
                w_sum += w
                acc += w * v.score
            q = 0.0 if w_sum == 0 else _clamp01(acc / w_sum)

        if any(v.status == "error" for v in non_advisory):
            status = "error"
        elif all(v.status in ("pass", "skipped") for v in non_advisory):
            status = "pass"
        else:
            status = "fail"

        out.append(
            ItemVerdict(itemId=item.itemId, ordinal=item.ordinal, q=q, status=status, verdicts=vs)
        )
    return out


# ---------------------------------------------------------------------------
# Decay stats
# ---------------------------------------------------------------------------


def _oq(p: Any) -> Tuple[float, float]:
    if isinstance(p, dict):
        return p["ordinal"], p["q"]
    return p.ordinal, p.q


def decay_stats(items: Sequence[Any]) -> DecayStats:
    """OLS slope of q vs ordinal, auc = mean q, decile means over ceil(n/10) items.
    Accepts anything with ordinal/q (ItemVerdict models or plain dicts)."""
    pts = sorted((_oq(p) for p in items), key=lambda t: t[0])
    n = len(pts)
    if n == 0:
        return DecayStats(
            slope=0.0, auc=0.0, firstDecileMean=0.0, lastDecileMean=0.0, itemCount=0, minQ=0.0
        )
    ords = [p[0] for p in pts]
    qs = [p[1] for p in pts]
    mx = sum(ords) / n
    my = sum(qs) / n
    cov = 0.0
    var_x = 0.0
    for i in range(n):
        cov += (ords[i] - mx) * (qs[i] - my)
        var_x += (ords[i] - mx) ** 2
    slope = 0.0 if var_x == 0 else cov / var_x
    decile = math.ceil(n / 10)
    return DecayStats(
        slope=slope,
        auc=_clamp01(my),
        firstDecileMean=_clamp01(sum(qs[:decile]) / decile),
        lastDecileMean=_clamp01(sum(qs[n - decile :]) / decile),
        itemCount=n,
        minQ=_clamp01(min(qs)),
    )


# ---------------------------------------------------------------------------
# Trial result
# ---------------------------------------------------------------------------


def _rget(report: Any, key: str, default: Any = None) -> Any:
    if report is None:
        return default
    if isinstance(report, dict):
        return report.get(key, default)
    return getattr(report, key, default)


def trial_status_from_report(report: Any, scenario: Scenario) -> str:
    """Map a RunReport-ish (dict or object with terminal/deadlock/guardTripped)
    to a TrialResult status."""
    if _rget(report, "deadlock"):
        return "deadlock"
    if _rget(report, "guardTripped") == "usd" and scenario.budgets.onExhaustion == "fail":
        return "budget_exceeded"
    if _rget(report, "guardTripped"):
        return "guard_tripped"
    return "completed"


def build_trial_result(
    scenario: Scenario,
    trial_idx: int,
    run_id: str,
    verdicts: List[Verdict],
    cost_usd: float,
    sim_days: float,
    wall_ms: float,
    status: Optional[str] = None,
    report: Optional[Any] = None,
    deadlock_diagnosis: Optional[str] = None,
) -> TrialResult:
    """Either pass a precomputed `status` or a RunReport-ish `report` to map from."""
    final_status = status if status is not None else trial_status_from_report(report, scenario)
    items = build_item_verdicts(scenario, verdicts)
    decay = decay_stats(items) if len(scenario.items or []) > 0 else None
    diagnosis = (
        deadlock_diagnosis
        if deadlock_diagnosis is not None
        else _rget(report, "deadlockDiagnosis")
    )
    return TrialResult(
        trialIdx=trial_idx,
        runId=run_id,
        status=final_status,
        verdicts=verdicts,
        items=items,
        decay=decay,
        costUsd=cost_usd,
        simDays=sim_days,
        wallMs=wall_ms,
        deadlockDiagnosis=diagnosis,
    )


# ---------------------------------------------------------------------------
# Scenario verdict
# ---------------------------------------------------------------------------


def _trial_policy_of(scenario: Scenario, config: EvalSetConfig) -> TrialPolicy:
    if scenario.trials is not None:
        return scenario.trials
    return getattr(config.defaultTrials, scenario.kind)


def mean_decay_across_trials(
    scenario: Scenario, trials: List[TrialResult]
) -> Optional[DecayStats]:
    """Mean q per ordinal across trials -> the curve the chart plots."""
    items = scenario.items or []
    if len(items) == 0 or len(trials) == 0:
        return None
    points = []
    for item in items:
        qs = []
        for t in trials:
            iv = next((i for i in t.items if i.itemId == item.itemId), None)
            qs.append(iv.q if iv is not None else 0.0)
        points.append({"ordinal": item.ordinal, "q": sum(qs) / len(qs)})
    return decay_stats(points)


def _trial_passed(trial: TrialResult, ignore_errors: bool = False) -> bool:
    """A trial counts toward at_least k iff completed AND every non-advisory
    quality verdict passed. `ignore_errors` recounts treating error verdicts as
    neutral — used to decide whether harness errors were the ONLY problem
    (scenario status 'error') or the subject genuinely failed ('failed')."""
    if trial.status != "completed":
        return False
    return all(
        v.status == "pass" or v.status == "skipped" or (ignore_errors and v.status == "error")
        for v in trial.verdicts
        if not v.advisory and v.class_ == "quality"
    )


def build_scenario_verdict(
    scenario: Scenario,
    trials: List[TrialResult],
    config: EvalSetConfig,
    quarantined: bool = False,
) -> ScenarioVerdict:
    policy = _trial_policy_of(scenario, config)
    all_verdicts = [v for t in trials for v in t.verdicts]
    non_advisory = [v for v in all_verdicts if not v.advisory]

    # INVARIANT RULE — one violation anywhere is red, regardless of pass rates.
    invariant_violation = any(
        v.class_ == "invariant" and v.status in ("fail", "error") for v in non_advisory
    )
    has_error = any(v.status == "error" for v in non_advisory)

    mean_decay = mean_decay_across_trials(scenario, trials)

    detail: List[str] = []
    if policy.passRule.kind == "at_least":
        k = policy.passRule.k
        good = sum(1 for t in trials if _trial_passed(t))
        rule_passed = good >= k
        rule_would_pass_ignoring_errors = (
            sum(1 for t in trials if _trial_passed(t, True)) >= k
        )
        detail.append("at_least: %d/%d passing trials (need %d)" % (good, len(trials), k))
    else:
        th = config.thresholds
        if mean_decay is None:
            rule_passed = False
            rule_would_pass_ignoring_errors = False
            detail.append("aggregate: no per-item decay data (scenario has no items)")
        else:
            slope_ok = mean_decay.slope >= th.slopeMin
            auc_ok = mean_decay.auc >= th.aucMin
            floor_ok = mean_decay.minQ >= th.itemFloor
            # Run-scoped quality graders (poll caps, budget shape, landed-eventually)
            # are pass/fail obligations on top of the decay thresholds — a gauntlet
            # with a flat curve but a failed run-scope check is not a pass.
            run_scope_failed = [
                v
                for v in non_advisory
                if v.scope.itemId is None and v.class_ == "quality" and v.status == "fail"
            ]
            rule_passed = slope_ok and auc_ok and floor_ok and len(run_scope_failed) == 0
            rule_would_pass_ignoring_errors = rule_passed
            detail.append(
                "aggregate: slope=%.4f (min %s), auc=%.4f (min %s), minQ=%.4f (floor %s)"
                % (
                    mean_decay.slope,
                    _fmt(th.slopeMin),
                    mean_decay.auc,
                    _fmt(th.aucMin),
                    mean_decay.minQ,
                    _fmt(th.itemFloor),
                )
            )
            if len(run_scope_failed) > 0:
                detail.append(
                    "aggregate: %d run-scope quality verdict(s) failed: %s"
                    % (len(run_scope_failed), ", ".join(v.graderId for v in run_scope_failed))
                )

    if invariant_violation:
        detail.append("invariant violation: at least one invariant-class verdict failed/errored")
    if has_error:
        detail.append("error verdicts present: never green")

    if quarantined:
        status = "quarantined"
    elif invariant_violation:
        status = "failed"
    # Subject genuinely failed even with harness errors set aside -> failed.
    elif not rule_would_pass_ignoring_errors:
        status = "failed"
    # Errors were the only problem (harness fault, never subject fault) -> error.
    elif has_error or not rule_passed:
        status = "error"
    else:
        status = "passed"

    return ScenarioVerdict(
        scenarioId=scenario.id,
        status=status,
        invariantViolation=invariant_violation,
        trials=trials,
        meanDecay=mean_decay,
        passDetail="; ".join(detail),
        totalCostUsd=sum(t.costUsd for t in trials),
    )


# ---------------------------------------------------------------------------
# Suite green + result
# ---------------------------------------------------------------------------


@dataclass
class SuiteGreen:
    green: bool
    detail: List[str]


def suite_green(
    scenarios: List[ScenarioVerdict],
    config: EvalSetConfig,
    baseline_cost_usd: Optional[float] = None,
) -> SuiteGreen:
    detail: List[str] = []
    green = True

    quarantined = [s for s in scenarios if s.status == "quarantined"]
    active = [s for s in scenarios if s.status != "quarantined"]
    if len(quarantined) > 0:
        detail.append(
            "quarantined (advisory, excluded from green): %s"
            % ", ".join(s.scenarioId for s in quarantined)
        )
        for s in quarantined:
            if s.invariantViolation:
                detail.append(
                    "WARNING: quarantined scenario %s has an invariant violation" % s.scenarioId
                )

    for s in active:
        if s.invariantViolation:
            green = False
            detail.append("invariant violation in scenario %s" % s.scenarioId)

    for s in active:
        if s.status != "passed" and not s.invariantViolation:
            green = False
            detail.append("scenario %s status=%s" % (s.scenarioId, s.status))

    all_verdicts = [v for s in active for t in s.trials for v in t.verdicts]
    error_verdicts = [v for v in all_verdicts if not v.advisory and v.status == "error"]
    if len(error_verdicts) > 0:
        green = False
        seen: List[str] = []
        for v in error_verdicts:
            if v.graderId not in seen:
                seen.append(v.graderId)
        detail.append("%d error verdict(s): %s" % (len(error_verdicts), ", ".join(seen)))

    # Advisory verdicts never gate — structurally excluded above. Uncalibrated
    # judges can therefore never have gated: any judge verdict allowed to gate
    # was emitted non-advisory, i.e. calibrated.
    advisory_count = sum(1 for v in all_verdicts if v.advisory)
    if advisory_count > 0:
        detail.append(
            "%d advisory verdict(s) (uncalibrated judges) excluded from gating" % advisory_count
        )

    total_cost = sum(sc.totalCostUsd for sc in scenarios)
    if baseline_cost_usd is not None:
        cap = baseline_cost_usd * (1 + config.costRegressionGuardPct / 100)
        if total_cost > cap:
            green = False
            detail.append(
                "cost regression: $%.2f exceeds baseline $%.2f +%s%% (cap $%.2f)"
                % (total_cost, baseline_cost_usd, _fmt(config.costRegressionGuardPct), cap)
            )
        else:
            detail.append("cost within guard: $%.2f <= cap $%.2f" % (total_cost, cap))

    if green:
        detail.append(
            "green: no invariant violations, all scenarios passed, zero error verdicts"
        )
    return SuiteGreen(green=green, detail=detail)


def build_suite_result(
    config: EvalSetConfig,
    subject: Union[SuiteSubject, dict],
    started_at: str,
    finished_at: str,
    scenarios: List[ScenarioVerdict],
    baseline_cost_usd: Optional[float] = None,
) -> SuiteResult:
    subj = subject if isinstance(subject, SuiteSubject) else SuiteSubject.model_validate(subject)
    result = suite_green(scenarios, config, baseline_cost_usd=baseline_cost_usd)
    return SuiteResult(
        evalSet=EvalSetId(name=config.name, version=config.version),
        subject=subj,
        startedAt=started_at,
        finishedAt=finished_at,
        scenarios=scenarios,
        green=result.green,
        greenDetail=result.detail,
        totalCostUsd=sum(sc.totalCostUsd for sc in scenarios),
    )
