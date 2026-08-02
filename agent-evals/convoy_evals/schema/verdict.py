"""Verdict — the leaf grading record — plus the aggregation layers above it.

Port of src/schema/verdict.ts. Contracts:
 - status 'fail' MUST carry >= 1 evidence (validator);
 - 'error' is a harness/grader failure, never a subject failure, never green;
 - invariant-class verdicts must pass in EVERY trial.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Annotated


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventEvidence(_Model):
    kind: Literal["event"]
    eventId: str
    note: Optional[str] = None


class ArtifactEvidence(_Model):
    kind: Literal["artifact"]
    hash: str
    excerpt: Optional[str] = None


class WorldEvidence(_Model):
    kind: Literal["world"]
    query: str
    result: Any = None


class JudgeRationaleEvidence(_Model):
    kind: Literal["judge_rationale"]
    sampleIdx: int = Field(ge=0)
    text: str


class NoteEvidence(_Model):
    kind: Literal["note"]
    text: str


Evidence = Annotated[
    Union[EventEvidence, ArtifactEvidence, WorldEvidence, JudgeRationaleEvidence, NoteEvidence],
    Field(discriminator="kind"),
]

VerdictStatus = Literal["pass", "fail", "error", "skipped", "missing"]


class VerdictScope(_Model):
    runId: str
    itemId: Optional[str] = None


class Verdict(_Model):
    graderId: str
    # Content hash of the grader spec (+ prompt file for judges) — makes taint a query.
    graderVersion: str
    class_: Literal["invariant", "quality"] = Field(alias="class")
    scope: VerdictScope
    status: VerdictStatus
    score: float = Field(ge=0, le=1)  # deterministic graders emit 0|1 unless weighted
    evidence: List[Evidence]
    costUsd: Optional[float] = Field(default=None, ge=0)
    lowConfidence: Optional[bool] = None  # judge sample split
    advisory: Optional[bool] = None  # uncalibrated judge: reported, never gating
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _fail_needs_evidence(self) -> "Verdict":
        if self.status == "fail" and len(self.evidence) < 1:
            raise ValueError("fail verdicts must carry at least one piece of evidence")
        return self


class ItemVerdict(_Model):
    itemId: str
    ordinal: int = Field(gt=0)
    q: float = Field(ge=0, le=1)  # weighted mean of non-advisory scores; missing → 0
    status: Literal["pass", "fail", "missing", "error"]
    verdicts: List[Verdict]
    costUsd: Optional[float] = Field(default=None, ge=0)


class DecayStats(_Model):
    slope: float  # OLS slope of Q vs ordinal
    auc: float = Field(ge=0, le=1)  # mean Q
    firstDecileMean: float = Field(ge=0, le=1)
    lastDecileMean: float = Field(ge=0, le=1)
    itemCount: int = Field(ge=0)
    minQ: float = Field(ge=0, le=1)


TrialStatus = Literal["completed", "deadlock", "guard_tripped", "budget_exceeded", "harness_error"]


class TrialResult(_Model):
    trialIdx: int = Field(ge=0)
    runId: str
    status: TrialStatus
    verdicts: List[Verdict]
    items: List[ItemVerdict]
    decay: Optional[DecayStats]
    costUsd: float = Field(ge=0)
    simDays: float = Field(ge=0)
    wallMs: float = Field(ge=0)
    deadlockDiagnosis: Optional[str] = None


class ScenarioVerdict(_Model):
    scenarioId: str
    status: Literal["passed", "failed", "error", "quarantined", "skipped_budget"]
    invariantViolation: bool
    trials: List[TrialResult]
    meanDecay: Optional[DecayStats]
    passDetail: str
    totalCostUsd: float = Field(ge=0)


class SuiteSubject(_Model):
    kind: Literal["scripted", "baseline", "runtime", "replay"]
    label: str
    modelId: Optional[str] = None


class EvalSetId(_Model):
    name: str
    version: str


class SuiteResult(_Model):
    evalSet: EvalSetId
    subject: SuiteSubject
    startedAt: str
    finishedAt: str
    scenarios: List[ScenarioVerdict]
    green: bool
    greenDetail: List[str]
    totalCostUsd: float = Field(ge=0)
