"""grade_trial — run EVERY grader over one GradeRecord and return the flat
Verdict list. No short-circuiting: all graders always run; a grader that
raises yields a status 'error' verdict (harness failure, never subject
failure) and the rest continue.

Also emits the two synthetic gate verdicts from the GateScriptReport:
  gate:never-raised:<stepId>  — a non-optional scripted step never matched
  gate:unexpected             — gates hit onUnexpectedGate: 'fail_scenario'
Both are invariant-class: one violation in any trial is red.

Port of src/graders/index.ts.
"""

from __future__ import annotations

from typing import Any, List, Optional, Set

from ..sandbox.api import GradeRecord
from ..schema.scenario import GraderSpec, ItemSpec, ProbeGrader, Scenario
from ..schema.verdict import Verdict, VerdictScope
from .calibration import CalibrationStore, load_calibration_store
from .end_state import run_end_state_grader
from .judge import run_judge
from .probes import run_probe_grader
from .trajectory import run_trajectory_grader
from .util import (
    GraderOutcome,
    ItemCtx,
    canonical_json,
    clamp01,
    ev_note,
    grader_version_of,
    sha256_hex,
)


def _run_ts_grader(spec: Any, record: GradeRecord, item_ctx: Optional[ItemCtx]) -> GraderOutcome:
    """Hash-pinned TS escape hatch. The Python harness verifies the pin but
    cannot execute a TypeScript module — surfaced as a harness error."""
    with open(spec.module, "r", encoding="utf-8") as fh:
        source = fh.read()
    actual_hash = sha256_hex(source)
    if actual_hash != spec.moduleHash:
        raise ValueError(
            "ts grader module hash mismatch: %s hashes to %s, spec pins %s"
            % (spec.module, actual_hash, spec.moduleHash)
        )
    raise NotImplementedError(
        "ts grader module %s cannot execute in the Python harness" % spec.module
    )


def _item_ctx_of(item: ItemSpec) -> ItemCtx:
    return ItemCtx(itemId=item.itemId, keyRef=item.keyRef)


def _item_graders(scenario: Scenario, item: ItemSpec) -> List[Any]:
    if item.graders == "inherit":
        return scenario.itemGraderTemplate or []
    return item.graders


def _synthetic_version(descriptor: Any) -> str:
    return sha256_hex(canonical_json(descriptor))


def grade_trial(
    record: GradeRecord,
    judge_mode: str = "cache-only",
    cache_dir: Optional[str] = None,
    calibration_path: Optional[str] = None,
) -> List[Verdict]:
    calibration: CalibrationStore = load_calibration_store(calibration_path)
    run_id = record.events[0].missionId if record.events else record.scenario.id
    verdicts: List[Verdict] = []
    probe_ids_referenced: Set[str] = set()

    def run_one(spec: Any, item_ctx: Optional[ItemCtx]) -> None:
        grader_version = grader_version_of(spec)
        scope = VerdictScope(runId=run_id, itemId=item_ctx.itemId if item_ctx else None)
        try:
            if spec.grader == "end_state":
                outcome = run_end_state_grader(spec, record, item_ctx)
            elif spec.grader == "trajectory":
                outcome = run_trajectory_grader(spec, record, item_ctx)
            elif spec.grader == "judge":
                outcome = run_judge(
                    spec,
                    record,
                    item_ctx,
                    mode=judge_mode,
                    cache_dir=cache_dir,
                    calibration=calibration,
                )
            elif spec.grader == "probe":
                probe_ids_referenced.add(spec.probeId)
                probe = next(
                    (p for p in (record.scenario.probes or []) if p.probeId == spec.probeId),
                    None,
                )
                if probe is None:
                    raise ValueError(
                        'probe "%s" not defined in scenario.probes' % spec.probeId
                    )
                outcome = run_probe_grader(probe, record)
            elif spec.grader == "ts":
                outcome = _run_ts_grader(spec, record, item_ctx)
            else:
                raise ValueError("unknown grader kind: %s" % spec.grader)
        except Exception as err:  # noqa: BLE001 — error verdicts isolate grader faults
            outcome = GraderOutcome(
                status="error", score=0.0, evidence=[ev_note("grader error: %s" % err)]
            )
        verdicts.append(
            Verdict(
                graderId=spec.id,
                graderVersion=grader_version,
                class_=spec.class_,
                scope=scope,
                status=outcome.status,
                score=clamp01(outcome.score),
                evidence=outcome.evidence,
                lowConfidence=outcome.lowConfidence,
                advisory=outcome.advisory,
                costUsd=outcome.costUsd,
            )
        )

    scenario = record.scenario
    items = scenario.items or []

    # Scenario-level graders. A scenario grader declaring scope 'item' fans out
    # over every item; everything else runs once at run scope.
    for spec in scenario.graders:
        if spec.scope == "item" and len(items) > 0:
            for item in items:
                run_one(spec, _item_ctx_of(item))
        else:
            run_one(spec, None)

    # Per-item graders ('inherit' -> scenario.itemGraderTemplate).
    for item in items:
        for spec in _item_graders(scenario, item):
            run_one(spec, _item_ctx_of(item))

    # Probe graders not already wired through an explicit probe GraderSpec.
    for probe in scenario.probes or []:
        if probe.probeId in probe_ids_referenced:
            continue
        spec = ProbeGrader.model_validate(
            {
                "id": "probe:%s" % probe.probeId,
                "scope": "run",
                "weight": 1,
                "class": "invariant" if probe.kind == "injection_resistance" else "quality",
                "grader": "probe",
                "probeId": probe.probeId,
            }
        )
        run_one(spec, None)

    # Synthetic gate verdicts from the gate-script report.
    for step_id in record.gateReport.neverRaised:
        verdicts.append(
            Verdict(
                graderId="gate:never-raised:%s" % step_id,
                graderVersion=_synthetic_version(
                    {"synthetic": "gate:never-raised", "stepId": step_id}
                ),
                class_="invariant",
                scope=VerdictScope(runId=run_id),
                status="fail",
                score=0.0,
                evidence=[
                    ev_note('scripted gate step "%s" was expected but never raised' % step_id)
                ],
            )
        )
    approvals = scenario.approvals
    if (
        len(record.gateReport.unexpected) > 0
        and approvals.mode == "scripted"
        and approvals.onUnexpectedGate == "fail_scenario"
    ):
        verdicts.append(
            Verdict(
                graderId="gate:unexpected",
                graderVersion=_synthetic_version({"synthetic": "gate:unexpected"}),
                class_="invariant",
                scope=VerdictScope(runId=run_id),
                status="fail",
                score=0.0,
                evidence=[
                    ev_note(
                        "unexpected gate %s (%s) hit onUnexpectedGate: fail_scenario"
                        % (u.get("gateId"), u.get("kind"))
                    )
                    for u in record.gateReport.unexpected
                ],
            )
        )

    return verdicts


from .replay import empty_gate_report, grade_replay, read_events_jsonl  # noqa: E402

__all__ = [
    "grade_trial",
    "grade_replay",
    "empty_gate_report",
    "read_events_jsonl",
    "GradeRecord",
    "ItemCtx",
]
