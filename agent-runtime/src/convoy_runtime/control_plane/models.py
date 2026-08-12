"""Control-plane request/response models (OpenAPI surface; the TS client is
generated from this later)."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from convoy_core import HumanGate, RunPolicy
from convoy_runtime.activities.plan import FanoutFixture
from convoy_runtime.schedule_template import AgentScheduleSpec


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
    # The agent's authored step list. Lines become the run's initial plan
    # (a "checkpoint: ..." line becomes a human approval gate on the step
    # after it); empty means the planner decides.
    instructions: list[str] = []
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
    # Console attribution, stored on the run projection and echoed by
    # GET /runs: which agent definition launched this run, who asked for
    # it (defaults to the authenticated actor), and the trigger class.
    agent_id: str | None = None
    started_by: str | None = None
    started_via: Literal["manual", "schedule", "event"] = "manual"


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
    """Answer one step's open human gate. `at_virtual` scripts a simulated
    human on virtual-clock rehearsal runs: the answer is delivered when
    virtual time reaches that instant (timezone-aware, or it is rejected)."""

    response: str = Field(min_length=1)
    at_virtual: AwareDatetime | None = None


class ClockAdvanceRequest(BaseModel):
    """Move a virtual-clock rehearsal run's time forward to `to`
    (timezone-aware, or it is rejected)."""

    to: AwareDatetime


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


class ExecutionSessionView(BaseModel):
    """Observable cloud compute/checkpoint state for a run."""

    environment_id: str
    status: str
    sandbox_id: str | None = None
    sandbox_provider: str | None = None
    sandbox_template: str | None = None
    generation: int = 0
    latest_checkpoint_id: str | None = None
    latest_snapshot_ref: dict[str, Any] | None = None
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
    execution_session: ExecutionSessionView | None = None
    steps: list[StepView] = []
    # The binding target the run was created against ("env_x" or
    # "env_x/sandbox") plus console attribution — empty/None for runs that
    # predate these columns.
    environment_id: str = ""
    agent_id: str | None = None
    started_by: str | None = None
    started_via: str = "manual"
    created_at: datetime | None = None


class RunSummary(BaseModel):
    """One row of GET /runs — the list shape the console renders. Full plan,
    land report, and steps stay on GET /runs/{run_id}."""

    run_id: str
    tenant_id: str
    parent_run_id: str | None = None
    status: str
    goal: str
    land_report: dict[str, Any] | None = None
    budget: BudgetView | None = None
    environment_id: str = ""
    agent_id: str | None = None
    started_by: str | None = None
    started_via: str = "manual"
    steps_done: int = 0
    steps_total: int = 0
    execution_session: ExecutionSessionView | None = None
    created_at: datetime
    updated_at: datetime


class ScheduleRunTemplateRequest(BaseModel):
    """The run the schedule should start on each firing — what the console
    resolved from the agent at save time. Tenant is taken from the
    authenticated caller, never this body."""

    goal: str = Field(min_length=1)
    environment_id: str
    budget_usd: str
    tools: list[str] = []
    instructions: list[str] = []
    started_by: str | None = None


class AgentScheduleRequest(BaseModel):
    schedule: AgentScheduleSpec
    template: ScheduleRunTemplateRequest


class AgentScheduleView(BaseModel):
    agent_id: str
    cron: str
    timezone: str
    enabled: bool
    next_run_times: list[str] = []


class RunListResponse(BaseModel):
    """Newest-first, keyset-paginated: pass `next_created_before` back as
    `created_before` to fetch the next page; null means the list is done."""

    runs: list[RunSummary]
    next_created_before: datetime | None = None
