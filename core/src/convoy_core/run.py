"""Run-level types: policy, steering, budget, and the durable RunState."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from convoy_core.agent import AgentSpec
from convoy_core.artifacts import ArtifactRef, StepSummaryRef
from convoy_core.land import LandReport
from convoy_core.plan import Plan


class RunPolicy(BaseModel):
    plan_shape: Literal["linear", "linear_fanout", "dag"] = "linear_fanout"
    require_plan_approval: bool = True  # sandbox-env default False
    approval_scope: Literal["initial", "major_revisions", "all_revisions"] = "major_revisions"
    on_budget_exhausted: Literal["pause", "land", "abort"] = "pause"
    on_group_partial_failure: Literal["join_with_partials", "block_on_human", "fail_group"] = (
        "join_with_partials"
    )
    max_steps: int = 50
    max_parallel: int = 5  # capped by agent.max_children
    max_depth: int = 1  # subagent tree depth below root; 1 = flat fan-out


class SteerMessage(BaseModel):
    id: str
    author: Literal["human", "agent", "system"]
    author_id: str
    mode: Literal["note", "redirect"]  # redirect forces plan assessment
    body: str


class BudgetState(BaseModel):
    cap_usd: Decimal
    spent_usd: Decimal = 0  # pyright: ignore[reportAssignmentType]  # pydantic coerces int -> Decimal
    reserved_usd: Decimal = 0  # pyright: ignore[reportAssignmentType]  # pydantic coerces int -> Decimal


class RunState(BaseModel):  # everything that survives continue_as_new
    run_id: str
    tenant_id: str
    environment_id: str
    binding_ref: ArtifactRef  # EnvironmentBinding snapshot pinned at start
    status: Literal[
        "planning",
        "awaiting_approval",
        "running",
        "paused",
        "blocked_on_human",
        "landing",
        "completed",
        "failed",
    ]
    agent: AgentSpec
    policy: RunPolicy
    plan: Plan | None
    budget: BudgetState
    pinned_ref: ArtifactRef  # goal, criteria, compact plan render
    step_summaries: list[StepSummaryRef] = []
    working_transcript_ref: ArtifactRef | None = None
    pending_steers: list[SteerMessage] = []
    turn_count: int = 0
    virtual_now: datetime | None = None  # virtual clock state (sandbox bindings)


class RunResult(BaseModel):
    """Terminal result returned by `AgentRunWorkflow.run`."""

    run_id: str
    status: Literal["completed", "failed"]
    land_report: LandReport | None = None
    error: str | None = None
