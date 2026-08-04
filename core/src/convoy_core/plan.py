"""Plan, step, and revision types. Revisions are immutable, constrained-op audit records."""

from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from convoy_core.agent import AgentSpec
from convoy_core.artifacts import ArtifactRef

StepStatus = Literal["pending", "ready", "running", "blocked_on_human", "done", "failed", "skipped"]


class HumanGate(BaseModel):
    kind: Literal["approval", "input", "action"]
    prompt: str
    timeout: timedelta | None = None  # durable Temporal timer
    on_timeout: Literal["pause", "skip", "fail"] = "pause"


class EvalGate(BaseModel):  # agent-evals/ hook; MVP stubs to flag
    suite_id: str
    threshold: float
    on_fail: Literal["block", "retry", "flag"] = "flag"


class PlanStep(BaseModel):
    id: str
    description: str
    status: StepStatus = "pending"
    depends_on: list[str] = []
    group_id: str | None = None  # fan-out group membership
    executor: Literal["self", "subagent"] = "self"
    subagent: AgentSpec | None = None
    budget_slice: Decimal | None = None  # reservation against parent budget
    human_gate: HumanGate | None = None
    eval_gate: EvalGate | None = None
    attempt: int = 0
    max_attempts: int = 2
    required: bool = True  # optional steps skippable on land
    outputs: list[ArtifactRef] = []
    error_ref: ArtifactRef | None = None


class PlanPatchOp(BaseModel):  # constrained ops, not arbitrary JSON patch
    op: Literal["add_step", "skip_step", "retry_step", "edit_step", "set_budget_slice"]
    step: PlanStep | None = None
    step_id: str | None = None
    after: str | None = None
    changes: dict | None = None  # pyright: ignore[reportMissingTypeArgument]  # open-ended edit payload; validated by the revision validator
    reason: str


class PlanRevision(BaseModel):  # the audit trail, one per version
    version: int
    author: Literal["system", "agent", "human"]
    author_id: str | None = None
    reason: Literal["initial", "steer", "replan", "failure", "budget", "human_edit"]
    ops: list[PlanPatchOp]
    snapshot_ref: ArtifactRef  # full plan vN archived to S3
    approved_by: str | None = None


class Plan(BaseModel):
    version: int
    goal: str
    success_criteria: list[str]  # feeds land_run and evals
    steps: list[PlanStep]
    revisions: list[PlanRevision]  # metadata only; snapshots claim-checked
