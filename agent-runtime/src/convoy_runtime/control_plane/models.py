"""Control-plane request/response models (OpenAPI surface; TS client is
generated from this in a later milestone)."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    goal: str = Field(min_length=1)
    success_criteria: list[str] = []
    environment_id: str = "stub-local"
    budget_usd: Decimal = Decimal("10")
    run_id: str | None = None  # client-supplied ID makes retries idempotent


class CreateRunResponse(BaseModel):
    run_id: str
    status: str


class SignalResponse(BaseModel):
    run_id: str
    signal: str
    accepted: bool = True


class RunView(BaseModel):
    """Served from Postgres projections only (CLAUDE.md rule 11)."""

    run_id: str
    tenant_id: str
    status: str
    goal: str
    plan: dict[str, Any] | None = None
    land_report: dict[str, Any] | None = None
