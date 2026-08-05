"""Replay-mode grading — the Tier-0 path: re-run graders over a recorded
events JSONL + exported world bundle, zero runtime, bit-stable. Grading is a
pure function of (eventLog, worldBundle, scenario) — the grader cannot tell a
fixture from a live run.

Port of src/graders/replay.ts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional, Union

from ..runtime.events import parse_event
from ..sandbox.api import GateScriptReport, GradeRecord, WorldBundle
from ..schema.scenario import AnswerKey, Scenario
from ..schema.verdict import Verdict
from .util import order_events
from .world_query import bundle_query


def empty_gate_report() -> GateScriptReport:
    """Absent for runs recorded without gate scripts -> empty report."""
    return GateScriptReport(neverRaised=[], unexpected=[], resolutions=[])


def read_events_jsonl(path: Union[str, Path]) -> List[Any]:
    events: List[Any] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        events.append(parse_event(json.loads(line)))
    return order_events(events)


def grade_replay(
    events_jsonl_path: Union[str, Path],
    world_bundle_json: Union[WorldBundle, dict],
    scenario: Scenario,
    answer_key: AnswerKey,
    gate_report: Optional[GateScriptReport] = None,
    judge_mode: str = "cache-only",
    cache_dir: Optional[str] = None,
    calibration_path: Optional[str] = None,
) -> List[Verdict]:
    from . import grade_trial  # local import: replay is re-exported by the package

    bundle = (
        world_bundle_json
        if isinstance(world_bundle_json, WorldBundle)
        else WorldBundle.from_json(world_bundle_json)
    )
    record = GradeRecord(
        scenario=scenario,
        answerKey=answer_key,
        events=read_events_jsonl(events_jsonl_path),
        world=bundle,
        worldQuery=bundle_query(bundle),
        gateReport=gate_report if gate_report is not None else empty_gate_report(),
    )
    return grade_trial(
        record,
        judge_mode=judge_mode,
        cache_dir=cache_dir,
        calibration_path=calibration_path,
    )
