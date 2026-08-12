"""Pure schedule types shared by the workflow sandbox and the client side.

No I/O imports here: ``workflows/scheduled_run.py`` imports this module
inside the Temporal workflow sandbox. Client-side schedule management
(Temporal Schedule upsert, the starter activity) lives in ``schedules.py``.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

SCHEDULE_ID_PREFIX = "agent-schedule-"
SCHEDULED_RUN_WORKFLOW = "ScheduledRunWorkflow"


class ScheduledRunTemplate(BaseModel):
    """The frozen facts a schedule needs to start a run. ``tenant_id`` is
    stamped server-side from the authenticated caller, never accepted from
    a request body."""

    tenant_id: str
    agent_id: str
    goal: str = Field(min_length=1)
    environment_id: str
    budget_usd: str
    tools: list[str] = []
    instructions: list[str] = []
    started_by: str | None = None


class AgentScheduleSpec(BaseModel):
    """What the console saves: a standard 5-field cron in a named timezone,
    plus whether the schedule is live."""

    cron: str = Field(min_length=9, max_length=100)
    timezone: str = "UTC"
    enabled: bool = True


def validate_schedule_spec(spec: AgentScheduleSpec) -> None:
    """Cheap validation with actionable errors; Temporal re-validates the
    cron on create, this keeps the obvious mistakes at the API edge."""
    fields = spec.cron.split()
    if len(fields) != 5:
        raise ValueError("cron must have exactly 5 fields (minute hour day month weekday)")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(spec.timezone)
    except (KeyError, ValueError) as err:
        raise ValueError(f"unknown timezone {spec.timezone!r}") from err


def schedule_id_for_agent(agent_id: str) -> str:
    return f"{SCHEDULE_ID_PREFIX}{agent_id}"


def run_id_for_firing(workflow_id: str) -> str:
    """Deterministic run id from the per-firing workflow id (Temporal appends
    the fire timestamp to the action's workflow id), so retries of the
    starter activity and control-plane retries converge on one run."""
    digest = hashlib.sha256(workflow_id.encode()).hexdigest()[:12]
    return f"run-sch-{digest}"
