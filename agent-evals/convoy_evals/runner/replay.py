"""replay.py — the free deterministic PR-CI tier. Port of src/runner/replay.ts.

Re-grades previously recorded runs: for each scenario in the eval set, load the
events-<scenario>-t<i>.jsonl + world-<scenario>-t<i>.json artifacts a prior
run_suite wrote, rebuild the GradeRecord (gate report: empty default — there is
no live gate-script engine in replay), and grade + score through exactly the
same pipeline. Subject label is 'replay'. Judges default to cache-only: replay
must stay free and deterministic.

Scenarios in the set with no recorded trials in records_dir are skipped (a
filtered original run only records what it ran).
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Sibling components (written concurrently):
from convoy_evals.graders import grade_trial
from convoy_evals.graders.replay import grade_replay  # noqa: F401 — re-exported:
# the graders-side replay convenience wrapper; the runner builds its GradeRecord
# from the recorded artifacts and grades through grade_trial directly, exactly
# like the live suite runner, so live and replay share one grading path.
from convoy_evals.graders.world_query import bundle_query
from convoy_evals.sandbox.api import GradeRecord, RunReport, WorldBundle
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
    empty_gate_report,
    ensure_suite_result,
    events_file_name,
    harness_error_trial,
    load_answer_key,
    load_eval_set,
    load_quarantine,
    load_scenario,
    parse_ts,
    read_jsonl_events,
    resolve_answer_key_path,
    sim_days_of,
    sort_events,
    sum_budget_debits,
    world_file_name,
)

_EVENTS_FILE_RE = re.compile(r"^events-(.+)-t(\d+)\.jsonl$")


def _iso_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _report_from_events(events: List[Any]) -> RunReport:
    """Synthesize the status-bearing run-report subset from the log alone. A
    budget_exhausted terminal maps to guardTripped 'usd' (scoring turns that
    into budget_exceeded when onExhaustion is 'fail'); no terminal event means
    the recorded run never landed -> deadlock. A live guard_tripped run is not
    reconstructible from events and replays as its event-visible status."""
    sim_now = None
    if events:
        sim_now = parse_ts(events[-1].ts)
    if sim_now is None:
        sim_now = datetime.now(timezone.utc)

    terminal: Optional[str] = None
    deadlock = True
    guard_tripped: Optional[str] = None
    for e in events:
        if e.type == "terminal_outcome":
            deadlock = False
            if e.status == "budget_exhausted":
                guard_tripped = "usd"
            else:
                terminal = e.status
            break
    return RunReport(
        terminal=terminal,
        deadlock=deadlock,
        guardTripped=guard_tripped,
        openGates=[],
        simNow=sim_now,
        wallMs=0.0,
        stepsExecuted=0,
    )


async def _replay_trial(
    scenario: Scenario,
    answer_key: AnswerKey,
    records_dir: str,
    trial_idx: int,
    judge_mode: str,
) -> TrialResult:
    wall_start = time.time()
    try:
        events = sort_events(
            read_jsonl_events(os.path.join(records_dir, events_file_name(scenario.id, trial_idx)))
        )
        world_path = os.path.join(records_dir, world_file_name(scenario.id, trial_idx))
        with open(world_path, "r", encoding="utf-8") as fh:
            world = WorldBundle.from_json(json.load(fh))
        record = GradeRecord(
            scenario=scenario,
            answerKey=answer_key,
            events=events,
            world=world,
            worldQuery=bundle_query(world),
            gateReport=empty_gate_report(),
        )
        res = call_grade_trial(grade_trial, record, judge_mode)
        verdicts = await res if inspect.isawaitable(res) else res
        run_id = events[0].missionId if events else "{0}-t{1}".format(scenario.id, trial_idx)
        return call_build_trial_result(
            build_trial_result,
            trial_idx,
            run_id,
            _report_from_events(events),
            verdicts,
            scenario,
            sum_budget_debits(events),
            sim_days_of(events),
            (time.time() - wall_start) * 1000.0,
        )
    except Exception as err:
        return harness_error_trial(scenario, trial_idx, err, (time.time() - wall_start) * 1000.0)


async def replay_suite(
    eval_set_path: str,
    records_dir: str,
    judge_mode: str = "cache-only",
) -> SuiteResult:
    started_at = _iso_now()
    loaded = load_eval_set(eval_set_path)
    config = loaded.config
    quarantined = set(
        load_quarantine(os.path.join(loaded.scenarios_dir, "quarantine.yaml"))["quarantined"]
    )

    if not os.path.isdir(records_dir):
        raise ValueError("records directory not found: {0}".format(records_dir))

    # Index recorded trials by scenario id.
    trials_by_scenario: Dict[str, List[int]] = {}
    for f in sorted(os.listdir(records_dir)):
        m = _EVENTS_FILE_RE.match(f)
        if not m:
            continue
        trials_by_scenario.setdefault(m.group(1), []).append(int(m.group(2)))

    entries: List[Tuple[Scenario, AnswerKey, List[int]]] = []
    for sid in config.scenarios:
        trial_idxs = trials_by_scenario.get(sid)
        if not trial_idxs:
            continue  # not recorded — skip
        path = loaded.scenario_paths.get(sid)
        if path is None:
            print(
                '[replay] recorded scenario "{0}" has no scenario file under {1} — skipping'.format(
                    sid, loaded.scenarios_dir
                ),
                file=sys.stderr,
            )
            continue
        scenario = load_scenario(path)
        key_path = resolve_answer_key_path(scenario.answerKeyRef.path, path, loaded.scenarios_dir)
        answer_key = load_answer_key(key_path, scenario.answerKeyRef.hash)
        entries.append((scenario, answer_key, sorted(trial_idxs)))
    # Same admission order as run_suite so scenario ordering is comparable.
    entries.sort(key=lambda e: e[0].budgets.usd)

    scenarios: List[ScenarioVerdict] = []
    for scenario, answer_key, trial_idxs in entries:
        trials: List[TrialResult] = []
        for trial_idx in trial_idxs:
            trials.append(
                await _replay_trial(scenario, answer_key, records_dir, trial_idx, judge_mode)
            )
        verdict = call_build_scenario_verdict(
            build_scenario_verdict,
            scenario,
            trials,
            config,
            quarantined=scenario.id in quarantined,
        )
        if not isinstance(verdict, ScenarioVerdict):
            verdict = ScenarioVerdict.model_validate(verdict)
        scenarios.append(verdict)

    return ensure_suite_result(
        call_build_suite_result(
            build_suite_result,
            config,
            {"kind": "replay", "label": "replay"},
            started_at,
            _iso_now(),
            scenarios,
        )
    )
