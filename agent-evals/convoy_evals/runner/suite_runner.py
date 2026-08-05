"""suite_runner.py — run an eval set against a subject (scripted executor or a
real runtime factory) and produce a SuiteResult. Port of src/runner/suite-runner.ts.

Flow per trial: sandbox create -> run_until(terminal) -> gate_report -> destroy ->
write artifacts (events JSONL, world bundle, verdicts) -> grade_trial ->
build_trial_result. A scenario's trials run sequentially; DIFFERENT scenarios
run under an asyncio semaphore (max_concurrent, default 2). The USD cap uses
cheapest-first admission (scenarios sorted by budgets.usd ascending) and is
checked cumulatively before every trial. Deadlocked / guard-tripped runs are
still graded — partial credit is the point. A trial that raises becomes a
harness_error TrialResult with an error verdict (never green).

Subject shapes:
    {"kind": "scripted", "executor": "golden"}
    {"kind": "runtime", "factory": <RuntimeFactory>, "label": "baseline"}
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Sibling components (written concurrently — import errors here are expected
# until they land; the CLI imports this module lazily for that reason):
from convoy_evals.executors import EXECUTORS, create_scripted_runtime_factory
from convoy_evals.graders import grade_trial
from convoy_evals.graders.world_query import bundle_query
from convoy_evals.sandbox import create_sandbox_service
from convoy_evals.sandbox.api import GradeRecord, WorldBundle
from convoy_evals.schema.scenario import AnswerKey, Scenario
from convoy_evals.schema.verdict import ScenarioVerdict, SuiteResult, TrialResult
from convoy_evals.scoring import (
    build_scenario_verdict,
    build_suite_result,
    build_trial_result,
)

from convoy_evals.runner.store import (
    call_build_scenario_verdict,
    call_build_suite_result,
    call_build_trial_result,
    call_grade_trial,
    default_out_dir,
    ensure_suite_result,
    events_file_name,
    harness_error_trial,
    load_answer_key,
    load_eval_set,
    load_quarantine,
    load_scenario,
    resolve_answer_key_path,
    sim_days_of,
    sort_events,
    sum_budget_debits,
    to_jsonl,
    verdicts_file_name,
    world_file_name,
)


def _iso_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _num(n: float) -> str:
    """JS-style number rendering for messages (0.5 not 0.5000)."""
    return "%g" % n


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _dump_verdict(v: Any) -> Any:
    if hasattr(v, "model_dump"):
        return v.model_dump(by_alias=True, exclude_none=True)
    return v


def subject_descriptor(subject: Dict[str, Any]) -> Dict[str, Any]:
    if subject.get("kind") == "scripted":
        return {"kind": "scripted", "label": "scripted:{0}".format(subject["executor"])}
    return {"kind": "runtime", "label": subject["label"]}


async def run_suite(
    eval_set_path: str,
    subject: Dict[str, Any],
    scenario_filter: Optional[List[str]] = None,
    trials_override: Optional[int] = None,
    max_concurrent: int = 2,
    usd_cap: Optional[float] = None,
    judge_mode: str = "cache-only",
    out_dir: Optional[str] = None,
) -> SuiteResult:
    started_at = _iso_now()
    loaded = load_eval_set(eval_set_path)
    config = loaded.config
    quarantined = set(
        load_quarantine(os.path.join(loaded.scenarios_dir, "quarantine.yaml"))["quarantined"]
    )

    if scenario_filter is not None:
        for sid in scenario_filter:
            if sid not in config.scenarios:
                raise ValueError(
                    'filter scenario "{0}" is not in eval set {1}@{2}'.format(
                        sid, config.name, config.version
                    )
                )
    wanted_ids = [
        sid for sid in config.scenarios if scenario_filter is None or sid in scenario_filter
    ]

    entries: List[Tuple[Scenario, AnswerKey]] = []
    for sid in wanted_ids:
        path = loaded.scenario_paths.get(sid)
        if path is None:
            raise ValueError(
                'scenario "{0}" has no *.scenario.json under {1}'.format(
                    sid, loaded.scenarios_dir
                )
            )
        scenario = load_scenario(path)
        key_path = resolve_answer_key_path(
            scenario.answerKeyRef.path, path, loaded.scenarios_dir
        )
        answer_key = load_answer_key(key_path, scenario.answerKeyRef.hash)
        entries.append((scenario, answer_key))
    # Cheapest-first admission: under a USD cap, the cheap scenarios get to run.
    entries.sort(key=lambda e: e[0].budgets.usd)

    kind = subject.get("kind")
    if kind == "scripted":
        executor_fn = EXECUTORS.get(subject["executor"])
        if executor_fn is None:
            raise ValueError(
                'unknown scripted executor "{0}" (available: {1})'.format(
                    subject["executor"], ", ".join(sorted(EXECUTORS.keys()))
                )
            )
        factory = create_scripted_runtime_factory(executor_fn)
    elif kind == "runtime":
        factory = subject["factory"]
    else:
        raise ValueError("subject.kind must be 'scripted' or 'runtime', got {0!r}".format(kind))

    out = out_dir if out_dir is not None else default_out_dir()
    os.makedirs(out, exist_ok=True)
    service = create_sandbox_service(pack_root=os.path.join(loaded.scenarios_dir, "packs"))

    state = {"total_cost_usd": 0.0}

    async def run_trial(scenario: Scenario, answer_key: AnswerKey, trial_idx: int) -> TrialResult:
        wall_start = time.time()
        try:
            instance = await service.create(scenario, factory)
            try:
                report = await instance.run_until({"kind": "terminal"})
            except Exception:
                try:
                    instance.destroy()
                except Exception:
                    pass  # teardown failure is secondary to the original error
                raise
            gate_report = instance.gate_report()
            teardown = instance.destroy()
            events = sort_events(list(teardown["events"]))
            world = teardown["world"]
            if isinstance(world, dict):
                world = WorldBundle.from_json(world)

            _write_text(os.path.join(out, events_file_name(scenario.id, trial_idx)), to_jsonl(events))
            _write_text(
                os.path.join(out, world_file_name(scenario.id, trial_idx)),
                json.dumps(world.to_json(), indent=2) + "\n",
            )

            record = GradeRecord(
                scenario=scenario,
                answerKey=answer_key,
                events=events,
                world=world,
                worldQuery=bundle_query(world),
                gateReport=gate_report,
            )
            res = call_grade_trial(grade_trial, record, judge_mode)
            verdicts = await res if inspect.isawaitable(res) else res
            _write_text(
                os.path.join(out, verdicts_file_name(scenario.id, trial_idx)),
                json.dumps([_dump_verdict(v) for v in verdicts], indent=2) + "\n",
            )

            if events:
                run_id = events[0].missionId
            else:
                run_id = getattr(instance, "missionId", "{0}-t{1}".format(scenario.id, trial_idx))
            return call_build_trial_result(
                build_trial_result,
                trial_idx,
                run_id,
                report,
                verdicts,
                scenario,
                sum_budget_debits(events),
                sim_days_of(events),
                (time.time() - wall_start) * 1000.0,
            )
        except Exception as err:
            return harness_error_trial(scenario, trial_idx, err, (time.time() - wall_start) * 1000.0)

    async def run_scenario(entry: Tuple[Scenario, AnswerKey]) -> ScenarioVerdict:
        scenario, answer_key = entry
        if trials_override is not None:
            trials_n = trials_override
        elif scenario.trials is not None:
            trials_n = scenario.trials.n
        else:
            trials_n = getattr(config.defaultTrials, scenario.kind).n
        trials: List[TrialResult] = []
        capped_out = False
        for i in range(trials_n):
            if usd_cap is not None and state["total_cost_usd"] >= usd_cap:
                capped_out = True
                break
            trial = await run_trial(scenario, answer_key, i)
            state["total_cost_usd"] += trial.costUsd
            trials.append(trial)
        if not trials and capped_out:
            return ScenarioVerdict(
                scenarioId=scenario.id,
                status="skipped_budget",
                invariantViolation=False,
                trials=[],
                meanDecay=None,
                passDetail=(
                    "skipped: cumulative cost ${0:.4f} reached the suite usd cap (${1})".format(
                        state["total_cost_usd"], _num(usd_cap)
                    )
                ),
                totalCostUsd=0,
            )
        # Quarantined scenarios still run, but only advisorily — they never gate
        # (scoring excludes status 'quarantined' from suite_green).
        verdict = call_build_scenario_verdict(
            build_scenario_verdict,
            scenario,
            trials,
            config,
            quarantined=scenario.id in quarantined,
        )
        if not isinstance(verdict, ScenarioVerdict):
            verdict = ScenarioVerdict.model_validate(verdict)
        if capped_out:
            verdict = verdict.model_copy(
                update={
                    "passDetail": "{0} [usd cap stopped {1} trial(s)]".format(
                        verdict.passDetail, trials_n - len(trials)
                    )
                }
            )
        return verdict

    # Semaphore pool over scenarios; each slot owns one scenario at a time so a
    # scenario's trials are strictly sequential.
    width = max(1, min(max_concurrent if max_concurrent else 2, len(entries) or 1))
    sem = asyncio.Semaphore(width)
    scenario_verdicts: List[Optional[ScenarioVerdict]] = [None] * len(entries)

    async def guarded(idx: int, entry: Tuple[Scenario, AnswerKey]) -> None:
        async with sem:
            scenario_verdicts[idx] = await run_scenario(entry)

    await asyncio.gather(*(guarded(i, e) for i, e in enumerate(entries)))

    result = ensure_suite_result(
        call_build_suite_result(
            build_suite_result,
            config,
            subject_descriptor(subject),
            started_at,
            _iso_now(),
            [v for v in scenario_verdicts if v is not None],
        )
    )
    _write_text(
        os.path.join(out, "suite-result.json"),
        json.dumps(result.model_dump(by_alias=True), indent=2) + "\n",
    )
    return result
