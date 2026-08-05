"""Judge calibration — a table, not a doc. Every judge verdict on the
calibration slice is stored against a human label; a judge version may gate
promotion only once it clears the ex-ante thresholds. Until then its verdicts
are advisory: reported, never gating.

Thresholds (fixed, versioned with the harness):
  >= 30 labels, false-accept <= 5%, false-reject <= 15%.
FA is stricter because a false accept in a trust product is the killer.
  false-accept = judge 'pass' on a human-'fail' case / human-'fail' cases
  false-reject = judge 'fail' on a human-'pass' case / human-'pass' cases

Port of src/graders/calibration.ts.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, List, Optional

CALIBRATION_MIN_LABELS = 30
FALSE_ACCEPT_MAX = 0.05
FALSE_REJECT_MAX = 0.15


@dataclass
class CalibrationLabel:
    # Pointer back to the graded artifact/run the label came from.
    ref: str
    judge: str  # 'pass' | 'fail'
    human: str  # 'pass' | 'fail'


@dataclass
class CalibrationRecord:
    judgeId: str
    promptHash: str
    labels: List[CalibrationLabel] = field(default_factory=list)


@dataclass
class CalibrationStore:
    records: List[CalibrationRecord] = field(default_factory=list)


def empty_calibration_store() -> CalibrationStore:
    return CalibrationStore(records=[])


def _label_from(raw: Any) -> CalibrationLabel:
    if isinstance(raw, CalibrationLabel):
        return raw
    return CalibrationLabel(ref=raw.get("ref", ""), judge=raw["judge"], human=raw["human"])


def _record_from(raw: Any) -> CalibrationRecord:
    if isinstance(raw, CalibrationRecord):
        return raw
    return CalibrationRecord(
        judgeId=raw["judgeId"],
        promptHash=raw["promptHash"],
        labels=[_label_from(l) for l in raw.get("labels", [])],
    )


def load_calibration_store(path: Optional[str] = None) -> CalibrationStore:
    """Missing path / missing file -> empty store (all judges advisory)."""
    if not path or not os.path.exists(str(path)):
        return empty_calibration_store()
    with open(str(path), "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, list):
        return CalibrationStore(records=[_record_from(r) for r in raw])
    if isinstance(raw, dict):
        if isinstance(raw.get("records"), list):
            return CalibrationStore(records=[_record_from(r) for r in raw["records"]])
        if isinstance(raw.get("judgeId"), str):
            return CalibrationStore(records=[_record_from(raw)])
    return empty_calibration_store()


def save_calibration_store(path: str, store: CalibrationStore) -> None:
    parent = os.path.dirname(str(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(asdict(store), fh, indent=2)


@dataclass
class CalibrationStats:
    labelCount: int
    falseAcceptRate: float
    falseRejectRate: float


def calibration_stats(labels: List[CalibrationLabel]) -> CalibrationStats:
    ls = [_label_from(l) for l in labels]
    human_fail = [l for l in ls if l.human == "fail"]
    human_pass = [l for l in ls if l.human == "pass"]
    fa = (
        0.0
        if len(human_fail) == 0
        else sum(1 for l in human_fail if l.judge == "pass") / len(human_fail)
    )
    fr = (
        0.0
        if len(human_pass) == 0
        else sum(1 for l in human_pass if l.judge == "fail") / len(human_pass)
    )
    return CalibrationStats(labelCount=len(ls), falseAcceptRate=fa, falseRejectRate=fr)


def judge_is_calibrated(store: CalibrationStore, judge_id: str, prompt_hash: str) -> bool:
    """Calibrated <=> >=30 labels AND FA <= 5% AND FR <= 15% for (judgeId, promptHash)."""
    rec = next(
        (r for r in store.records if r.judgeId == judge_id and r.promptHash == prompt_hash),
        None,
    )
    if rec is None:
        return False
    stats = calibration_stats(rec.labels)
    return (
        stats.labelCount >= CALIBRATION_MIN_LABELS
        and stats.falseAcceptRate <= FALSE_ACCEPT_MAX
        and stats.falseRejectRate <= FALSE_REJECT_MAX
    )
