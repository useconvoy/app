"""Scenario — the canonical eval-task format (pure data).

Port of src/schema/scenario.ts. The committed corpus under scenarios/ MUST
load through these models unchanged — that is the acceptance test for this
file. Field names are camelCase to match the JSON exactly.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing_extensions import Annotated

from .match import ArgsMatcher, KeyRef

# --------------------------------------------------------------------------
# Durations
# --------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^\d+(\.\d+)?(d|h|m|s)$")
_UNIT_MS = {"d": 86_400_000, "h": 3_600_000, "m": 60_000, "s": 1_000}

Duration = str


def parse_duration_ms(d: Duration) -> float:
    if not _DURATION_RE.match(d):
        raise ValueError(f"bad duration: {d}")
    return float(d[:-1]) * _UNIT_MS[d[-1]]


class UniformDelay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dist: Literal["uniform"]
    min: Duration
    max: Duration


SimDelay = Union[Duration, UniformDelay]


def _strict(cls_name: str) -> ConfigDict:
    return ConfigDict(extra="forbid")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# World fixtures / bindings
# --------------------------------------------------------------------------


class FixtureRef(_Model):
    pack: str
    packHash: str


class EmulatorBinding(_Model):
    kind: Literal["emulator"]


class ReplayBinding(_Model):
    kind: Literal["replay"]
    cassette: str
    miss: Literal["fail", "emulator", "record"]


class LiveReadBinding(_Model):
    kind: Literal["live-read"]


Binding = Annotated[
    Union[EmulatorBinding, ReplayBinding, LiveReadBinding], Field(discriminator="kind")
]

# --------------------------------------------------------------------------
# Trigger (v1: api | schedule only — the harness starts missions itself)
# --------------------------------------------------------------------------


class MissionSpecInput(_Model):
    missionType: str
    goal: str
    params: Optional[Dict[str, Any]] = None


class ApiTrigger(_Model):
    kind: Literal["api"]
    missionSpec: MissionSpecInput


class ScheduleTrigger(_Model):
    kind: Literal["schedule"]
    atSim: Duration
    missionSpec: MissionSpecInput


Trigger = Annotated[Union[ApiTrigger, ScheduleTrigger], Field(discriminator="kind")]

# --------------------------------------------------------------------------
# Counterparties
# --------------------------------------------------------------------------


class MessageTemplate(_Model):
    subject: str
    body: str
    from_: Optional[str] = Field(default=None, alias="from")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CounterpartyMatch(_Model):
    toContains: Optional[str] = None
    subjectRegex: Optional[str] = None
    bodyRegex: Optional[str] = None
    hasAttachment: Optional[bool] = None
    # 1-based nth matching message for this rule's actor.
    occurrence: Optional[int] = Field(default=None, gt=0)


class CounterpartyRespond(_Model):
    afterSim: SimDelay
    message: MessageTemplate
    attachments: Optional[List[str]] = None


class CounterpartyRule(_Model):
    id: str
    match: CounterpartyMatch
    maxFires: int = Field(default=1, gt=0)
    respond: List[CounterpartyRespond]


CounterpartyProfile = Literal[
    "cooperative", "slow", "wrong-document", "silent", "adversarial-injection", "custom"
]


class UnsolicitedSend(_Model):
    atSim: Duration
    message: MessageTemplate
    attachments: Optional[List[str]] = None


class CounterpartyScript(_Model):
    actorId: str
    channel: Literal["email", "portal"]
    owns: List[str]
    profile: CounterpartyProfile
    rules: Optional[List[CounterpartyRule]] = None
    params: Optional[Dict[str, Any]] = None
    unsolicited: Optional[List[UnsolicitedSend]] = None
    default: Literal["silent", "bounce"] = "silent"


# --------------------------------------------------------------------------
# Approval script — resolver + assertion in one object
# --------------------------------------------------------------------------


class GateMatcher(_Model):
    kind: Optional[
        Literal["action-approval", "input-request", "plan-approval", "budget-raise"]
    ] = None
    stepTag: Optional[str] = None
    payload: Optional[ArgsMatcher] = None


class ApproveResolution(_Model):
    kind: Literal["approve"]


class RejectResolution(_Model):
    kind: Literal["reject"]
    reason: str


class ProvideInputResolution(_Model):
    kind: Literal["provide_input"]
    payload: Any = None


class RaiseBudgetResolution(_Model):
    kind: Literal["raise_budget"]
    addUsd: float = Field(gt=0)


class EditThenApproveResolution(_Model):
    kind: Literal["edit_then_approve"]
    patch: Any = None


class ExpireResolution(_Model):
    kind: Literal["expire"]


GateResolution = Annotated[
    Union[
        ApproveResolution,
        RejectResolution,
        ProvideInputResolution,
        RaiseBudgetResolution,
        EditThenApproveResolution,
        ExpireResolution,
    ],
    Field(discriminator="kind"),
]


class GateScriptStep(_Model):
    id: str
    expect: GateMatcher
    resolve: GateResolution
    afterSim: Optional[SimDelay] = None  # simulated human latency
    optional: bool = False
    ordered: bool = True  # must fire in listed order relative to other ordered steps
    maxFires: int = Field(default=1, gt=0)


class AutoApproveScript(_Model):
    mode: Literal["auto_approve"]
    maxGates: int = Field(gt=0)
    afterSim: Optional[SimDelay] = None


class AutoRejectScript(_Model):
    mode: Literal["auto_reject"]
    reason: str


class UnexpectedGateResolve(_Model):
    resolve: GateResolution


class ScriptedApprovals(_Model):
    mode: Literal["scripted"]
    steps: List[GateScriptStep]
    onUnexpectedGate: Union[Literal["fail_scenario"], UnexpectedGateResolve] = "fail_scenario"


ApprovalScript = Annotated[
    Union[AutoApproveScript, AutoRejectScript, ScriptedApprovals], Field(discriminator="mode")
]

# --------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------


class PerItemBudget(_Model):
    usd: Optional[float] = None
    simTime: Optional[Duration] = None


class Budgets(_Model):
    usd: float = Field(gt=0)
    tokens: Optional[int] = Field(default=None, gt=0)
    simTime: Duration  # mission deadline on the sim clock
    wallClock: Duration  # harness kill switch
    perItem: Optional[PerItemBudget] = None
    onExhaustion: Literal["fail", "grade_partial"] = "fail"


# --------------------------------------------------------------------------
# Graders
# --------------------------------------------------------------------------

Cmp = Literal["eq", "neq", "lt", "lte", "gt", "gte", "contains", "regex", "in", "exists", "absent"]


class ArtifactSelector(_Model):
    tag: Optional[str] = None  # "{itemRef}" interpolates the item domain key
    mime: Optional[str] = None
    minBytes: Optional[int] = Field(default=None, gt=0)


class JsonPathExtractor(_Model):
    kind: Literal["json_path"]
    path: str


class RegexExtractor(_Model):
    kind: Literal["regex"]
    pattern: str
    group: int = Field(ge=0)


FieldExtractor = Annotated[Union[JsonPathExtractor, RegexExtractor], Field(discriminator="kind")]


class ArtifactExistsAssert(_Model):
    kind: Literal["artifact_exists"]
    selector: ArtifactSelector


class ArtifactFieldAssert(_Model):
    kind: Literal["artifact_field"]
    selector: ArtifactSelector
    extract: FieldExtractor
    op: Cmp
    expected: Any = None


class WorldQueryAssert(_Model):
    kind: Literal["world_query"]
    query: str
    op: Cmp
    expected: Any = None


class ChecklistAssert(_Model):
    kind: Literal["checklist"]
    checklistRef: str


EndStateAssertion = Annotated[
    Union[ArtifactExistsAssert, ArtifactFieldAssert, WorldQueryAssert, ChecklistAssert],
    Field(discriminator="kind"),
]


class EventMatcher(_Model):
    type: Optional[Union[str, List[str]]] = None
    tool: Optional[str] = None
    args: Optional[ArgsMatcher] = None
    itemRef: Optional[str] = None


class NeverAssert(_Model):
    kind: Literal["never"]
    where: EventMatcher


class AlwaysAssert(_Model):
    kind: Literal["always"]
    where: EventMatcher
    require: ArgsMatcher


class EventCountAssert(_Model):
    kind: Literal["event_count"]
    where: EventMatcher
    op: Cmp
    n: int = Field(ge=0)


class SequenceAssert(_Model):
    kind: Literal["sequence"]
    steps: List[EventMatcher]
    scope: Optional[EventMatcher] = None


class PausedAtGateAssert(_Model):
    kind: Literal["paused_at_gate"]
    gate: GateMatcher
    effect: EventMatcher


class EventuallyAssert(_Model):
    kind: Literal["eventually"]
    where: EventMatcher
    withinSim: Optional[Duration] = None


class BudgetShapeAssert(_Model):
    kind: Literal["budget_shape"]
    metric: Literal["usd", "tokens", "tool_calls"]
    op: Cmp
    limit: float = Field(ge=0)
    per: Literal["run", "item"] = "run"


TrajectoryAssertion = Annotated[
    Union[
        NeverAssert,
        AlwaysAssert,
        EventCountAssert,
        SequenceAssert,
        PausedAtGateAssert,
        EventuallyAssert,
        BudgetShapeAssert,
    ],
    Field(discriminator="kind"),
]


class ArtifactJudgeInput(_Model):
    kind: Literal["artifact"]
    selector: ArtifactSelector


class KeyExcerptJudgeInput(_Model):
    kind: Literal["key_excerpt"]
    ref: Dict[str, str]  # {"$key": "..."}


class WorldJudgeInput(_Model):
    kind: Literal["world"]
    query: str


# Deliberately NO constructor for transcript, messages, plan text, or
# model_call events — the worker's reasoning cannot leak into the judge
# because the type system cannot express it.
JudgeInput = Annotated[
    Union[ArtifactJudgeInput, KeyExcerptJudgeInput, WorldJudgeInput], Field(discriminator="kind")
]

GraderScope = Literal["run", "item"]
GraderClass = Literal["invariant", "quality"]


class _GraderBase(_Model):
    id: str
    scope: GraderScope
    weight: float = Field(default=1, gt=0)
    # invariant: must hold in EVERY trial. quality: goes through pass rates.
    class_: GraderClass = Field(alias="class")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EndStateGrader(_GraderBase):
    grader: Literal["end_state"]
    asserts: List[EndStateAssertion]


class TrajectoryGrader(_GraderBase):
    grader: Literal["trajectory"]
    asserts: List[TrajectoryAssertion]


class RubricEntry(_Model):
    id: str
    criterion: str
    weight: float = Field(gt=0)


class JudgeGrader(_GraderBase):
    grader: Literal["judge"]
    judgeId: str
    model: str
    promptFile: str
    promptHash: str
    rubric: List[RubricEntry]
    inputs: List[JudgeInput]
    samples: int = Field(default=3, gt=0)
    passAt: float = Field(ge=0, le=1)


class ProbeGrader(_GraderBase):
    grader: Literal["probe"]
    probeId: str


class TsGrader(_GraderBase):
    """Escape hatch: allowed, discouraged, hash-pinned."""

    grader: Literal["ts"]
    module: str
    moduleHash: str


GraderSpec = Annotated[
    Union[EndStateGrader, TrajectoryGrader, JudgeGrader, ProbeGrader, TsGrader],
    Field(discriminator="grader"),
]

# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------


class ProbeVia(_Model):
    actorId: str
    message: MessageTemplate


class ExpectReply(_Model):
    matcher: ArgsMatcher


class CriteriaRetentionProbe(_Model):
    kind: Literal["criteria_retention"]
    probeId: str
    atItemOrdinal: int = Field(gt=0)
    via: ProbeVia
    expectReply: ExpectReply


class InjectionResistanceProbe(_Model):
    kind: Literal["injection_resistance"]
    probeId: str
    payload: str
    mustNot: List[TrajectoryAssertion]
    mustStill: List[EndStateAssertion]


ProbeSpec = Annotated[
    Union[CriteriaRetentionProbe, InjectionResistanceProbe], Field(discriminator="kind")
]

# --------------------------------------------------------------------------
# Trials & items
# --------------------------------------------------------------------------


class AtLeastRule(_Model):
    kind: Literal["at_least"]
    k: int = Field(gt=0)


class AggregateRule(_Model):
    kind: Literal["aggregate"]
    thresholdsRef: Literal["eval-set"] = "eval-set"


class TrialPolicy(_Model):
    n: int = Field(gt=0)
    passRule: Union[AtLeastRule, AggregateRule]


class ItemSpec(_Model):
    itemId: str
    ordinal: int = Field(gt=0)  # n in Q(n)
    keyRef: str  # subtree of answerKey.perItem
    graders: Union[List[GraderSpec], Literal["inherit"]]


# --------------------------------------------------------------------------
# Answer key (sealed, content-addressed, never reachable from the environment)
# --------------------------------------------------------------------------


class ChecklistEntry(_Model):
    id: str
    description: str
    assert_: EndStateAssertion = Field(alias="assert")
    weight: float = Field(default=1, gt=0)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AnswerKey(_Model):
    scenarioId: str
    facts: Dict[str, Any]
    perItem: Optional[Dict[str, Dict[str, Any]]] = None
    checklists: Optional[Dict[str, List[ChecklistEntry]]] = None
    rubricExcerpts: Optional[Dict[str, str]] = None


# --------------------------------------------------------------------------
# Scenario + eval set
# --------------------------------------------------------------------------


class AnswerKeyRef(_Model):
    path: str
    hash: str


class AuthoredProvenance(_Model):
    kind: Literal["authored"]


class CapturedProvenance(_Model):
    kind: Literal["captured"]
    runId: str
    redacted: bool


class Scenario(_Model):
    id: str
    title: str
    missionType: str
    kind: Literal["single", "gauntlet"]
    fixture: FixtureRef
    t0: str  # sim epoch, ISO date
    seed: int
    bindings: Dict[str, Binding] = Field(default_factory=dict)
    trigger: Trigger
    counterparties: List[CounterpartyScript]
    approvals: ApprovalScript
    budgets: Budgets
    answerKeyRef: AnswerKeyRef
    graders: List[GraderSpec]
    items: Optional[List[ItemSpec]] = None
    itemGraderTemplate: Optional[List[GraderSpec]] = None
    probes: Optional[List[ProbeSpec]] = None
    trials: Optional[TrialPolicy] = None
    provenance: Annotated[
        Union[AuthoredProvenance, CapturedProvenance], Field(discriminator="kind")
    ]
    tags: List[str] = Field(default_factory=list)


class Thresholds(_Model):
    """Decay thresholds live HERE and only here — fixed ex-ante, versioned."""

    slopeMin: float
    aucMin: float = Field(ge=0, le=1)
    itemFloor: float = Field(ge=0, le=1)


class DefaultTrials(_Model):
    single: TrialPolicy
    gauntlet: TrialPolicy


class EvalSetConfig(_Model):
    name: str
    version: str
    scenarios: List[str]
    thresholds: Thresholds
    defaultTrials: DefaultTrials
    costRegressionGuardPct: float = Field(default=25, gt=0)
