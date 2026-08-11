"""The MISSION event taxonomy — the append-only per-mission log the evals
harness and the environments gateway write and graders replay.

Renamed from `convoy_core.events` when the runtime merged: `events.py` now
holds the runtime RunEvent catalog; this module is the evals/audit-plane
vocabulary. Additive module — owned like everything in core by no service.

Python port of the co-signed schema (reference: the TS implementation on branch
agent-evals-harness, src/runtime/events.ts). Field names are camelCase ON
PURPOSE: they match the JSON wire format exactly, so the committed scenario
corpus loads verbatim and event logs recorded by the TS harness replay here.

Every event carries two timestamps:
  ts     — DOMAIN time, from ClockPort (virtualized in the sandbox)
  wallTs — MECHANICAL wall-clock time (latency/cost telemetry; never graded)

Two-phase side effects: effectful tools emit intent → approved → executed →
result. Pure/read tools emit a single collapsed `tool_call` event.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated

MissionId = str
GateId = str
EventId = str

GateKind = Literal["action-approval", "input-request", "plan-approval", "budget-raise"]
GateResolutionKind = Literal[
    "approve", "reject", "provide_input", "raise_budget", "edit_then_approve", "expire"
]


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str
    missionId: str
    # Per-mission monotonic sequence; ties on `ts` resolve by `seq`.
    seq: int = Field(ge=0)
    ts: str
    wallTs: str
    # Gauntlet item attribution by domain key, e.g. "packet/POL-1042".
    itemRef: Optional[str] = None
    stepId: Optional[str] = None
    attemptId: Optional[str] = None


class MissionSpecSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modelId: str
    promptHashes: Dict[str, str]
    policyHash: Optional[str] = None
    toolManifestHash: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


class MissionStarted(_EventBase):
    type: Literal["mission_started"]
    missionType: str
    environmentId: str
    goal: str
    # Immutable spec snapshot rider: model + prompt hashes for the certified tuple.
    spec: MissionSpecSnapshot


class PlanVersion(_EventBase):
    type: Literal["plan_version"]
    version: int = Field(gt=0)
    plan: Any = None
    diff: Any = None
    author: Literal["agent", "human"]
    # Default None so exclude_none serialization round-trips (absent == null).
    causeEventId: Optional[str] = None


class StepStarted(_EventBase):
    type: Literal["step_started"]
    goal: Optional[str] = None


class StepCompleted(_EventBase):
    type: Literal["step_completed"]
    status: Literal["done", "failed", "skipped"]
    memo: Optional[str] = None


class ModelCall(_EventBase):
    type: Literal["model_call"]
    modelId: str
    tokensIn: int = Field(ge=0)
    tokensOut: int = Field(ge=0)
    costUsd: float = Field(ge=0)
    # Exact list of what was assembled into the prompt (the week-1 rider).
    contextManifest: Optional[List[str]] = None


class ToolCall(_EventBase):
    """Collapsed event for pure/read tools."""

    type: Literal["tool_call"]
    tool: str
    args: Any = None
    result: Any = None
    error: Optional[str] = None


class ToolIntent(_EventBase):
    type: Literal["tool_intent"]
    tool: str
    args: Any = None
    idempotencyKey: str


class ToolApproved(_EventBase):
    type: Literal["tool_approved"]
    tool: str
    idempotencyKey: str
    gateId: Optional[str] = None


class ToolExecuted(_EventBase):
    type: Literal["tool_executed"]
    tool: str
    idempotencyKey: str
    args: Any = None


class ToolResult(_EventBase):
    type: Literal["tool_result"]
    tool: str
    idempotencyKey: str
    result: Any = None
    error: Optional[str] = None


class SimulatedEffect(_EventBase):
    """A rehearsal-only write captured instead of sent to the provider."""

    type: Literal["simulated_effect"]
    tool: str
    provider: str
    connectionId: str
    idempotencyKey: str
    args: Any = None
    result: Any = None


class ToolDenied(_EventBase):
    type: Literal["tool_denied"]
    tool: str
    args: Any = None
    reason: str


class GateRaised(_EventBase):
    type: Literal["gate_raised"]
    gateId: str
    kind: GateKind
    payload: Any = None
    # Default None so exclude_none serialization round-trips (absent == null).
    deadlineAt: Optional[str] = None
    stepTag: Optional[str] = None


class GateResolved(_EventBase):
    type: Literal["gate_resolved"]
    gateId: str
    resolution: GateResolutionKind
    resolvedBy: str
    reason: Optional[str] = None
    patch: Any = None


class Steer(_EventBase):
    type: Literal["steer"]
    message: str
    priority: Literal["advisory", "directive"]
    author: str


class BudgetDebit(_EventBase):
    type: Literal["budget_debit"]
    usd: float = Field(ge=0)
    tokens: Optional[int] = Field(default=None, ge=0)
    resource: Literal["model", "tool", "other"]


class HumanIntervention(_EventBase):
    type: Literal["human_intervention"]
    kind: Literal["gate_resolution", "verdict_override", "steer", "plan_edit"]
    reason: Optional[str] = None
    before: Any = None
    after: Any = None


class ArtifactCreated(_EventBase):
    type: Literal["artifact_created"]
    artifactId: str
    hash: str
    tag: str
    mime: Optional[str] = None
    bytes: Optional[int] = Field(default=None, ge=0)


class TimerScheduled(_EventBase):
    type: Literal["timer_scheduled"]
    timerId: str
    fireAt: str
    note: Optional[str] = None


class TimerFired(_EventBase):
    type: Literal["timer_fired"]
    timerId: str


class TerminalOutcome(_EventBase):
    type: Literal["terminal_outcome"]
    status: Literal["landed", "cancelled", "failed", "budget_exhausted"]
    judgedBy: Literal["human", "agent", "timeout", "harness"]
    summary: Optional[str] = None


ConvoyEvent = Annotated[
    Union[
        MissionStarted,
        PlanVersion,
        StepStarted,
        StepCompleted,
        ModelCall,
        ToolCall,
        ToolIntent,
        ToolApproved,
        ToolExecuted,
        ToolResult,
        SimulatedEffect,
        ToolDenied,
        GateRaised,
        GateResolved,
        Steer,
        BudgetDebit,
        HumanIntervention,
        ArtifactCreated,
        TimerScheduled,
        TimerFired,
        TerminalOutcome,
    ],
    Field(discriminator="type"),
]


class _EventEnvelope(BaseModel):
    """Validator helper: parse one event of any type."""

    event: ConvoyEvent


def parse_event(data: Dict[str, Any]) -> Any:
    """Validate a raw dict into the right event model."""
    return _EventEnvelope.model_validate({"event": data}).event
