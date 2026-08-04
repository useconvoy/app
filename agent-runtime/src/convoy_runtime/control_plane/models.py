"""Control-plane request/response models (OpenAPI surface; the TS client is
generated from this later)."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from convoy_core import RunPolicy


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
    # Run policy overrides (budget action, etc.). Plan approval cannot be
    # enabled yet: the approval flow is not built, so it is forced off.
    policy: RunPolicy | None = None


class CreateRunResponse(BaseModel):
    run_id: str
    status: str


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
    """Served from Postgres projections only — never from Temporal."""

    run_id: str
    tenant_id: str
    status: str
    goal: str
    plan: dict[str, Any] | None = None
    land_report: dict[str, Any] | None = None
    budget: BudgetView | None = None
    steps: list[StepView] = []
