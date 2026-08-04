"""Control-plane request/response models (OpenAPI surface; the TS client is
generated from this later)."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from convoy_core import HumanGate, RunPolicy
from convoy_runtime.activities.plan import FanoutFixture


class CreateRunRequest(BaseModel):
    goal: str = Field(min_length=1)
    success_criteria: list[str] = []
    environment_id: str = "stub-local"
    budget_usd: Decimal = Decimal("10")
    run_id: str | None = None  # client-supplied ID makes retries idempotent
    # Model to run on; defaults to the deployment's configured default. Must
    # be approved by the model gateway when real execution is configured.
    model: str | None = None
    # Tool ids requested for the agent, resolved against the environment's
    # registry at creation; unknown or invalid requests are rejected.
    tools: list[str] = []
    # How many subagent children the root agent may hold at once; fan-out
    # groups larger than this are rejected.
    max_children: int = Field(default=5, ge=0, le=10)
    # Run policy overrides (approval, budget action, etc.). Plan approval
    # follows the environment kind (off for sandbox, on for production)
    # unless the policy sets require_plan_approval explicitly.
    policy: RunPolicy | None = None
    # Human gates to attach to named fixture-plan steps, honored by the stub
    # planner so gate flows can be exercised end to end without a real model
    # planner producing gated plans.
    fixture_gates: dict[str, HumanGate] = {}
    # Fan-out group to hang into the fixture plan, honored by the stub
    # planner so subagent flows can be exercised end to end.
    fixture_fanout: FanoutFixture | None = None


class CreateRunResponse(BaseModel):
    run_id: str
    status: str


class SteerRequest(BaseModel):
    """A steer for the run's mailbox: a note rides into the next turn's
    context; a redirect additionally forces the agent to assess the plan
    against it."""

    mode: Literal["note", "redirect"]
    body: str = Field(min_length=1)


class SteerResponse(BaseModel):
    run_id: str
    steer_id: str
    mode: Literal["note", "redirect"]
    accepted: bool = True


class ApprovePlanRequest(BaseModel):
    """Approve or reject the plan version awaiting approval. The version pin
    guarantees the decision targets the plan the human actually reviewed;
    rejections must say why."""

    plan_version: int = Field(ge=1)
    approve: bool = True
    reason: str | None = None


class GateRespondRequest(BaseModel):
    """Answer one step's open human gate."""

    response: str = Field(min_length=1)


class SignalResponse(BaseModel):
    run_id: str
    signal: str
    accepted: bool = True


class BudgetView(BaseModel):
    cap_usd: Decimal | None = None
    spent_usd: Decimal | None = None
    reserved_usd: Decimal | None = None


class StepView(BaseModel):
    step_id: str
    description: str = ""
    status: str
    attempt: int = 0
    model_used: str | None = None
    cost_usd: Decimal | None = None
    updated_at: datetime | None = None


class RunView(BaseModel):
    """Served from Postgres projections only — never from Temporal. Subagent
    child runs are rows of their own, linked back by `parent_run_id`."""

    run_id: str
    tenant_id: str
    parent_run_id: str | None = None
    status: str
    goal: str
    plan: dict[str, Any] | None = None
    land_report: dict[str, Any] | None = None
    budget: BudgetView | None = None
    steps: list[StepView] = []
