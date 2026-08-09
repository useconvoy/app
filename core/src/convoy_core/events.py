"""RunEvent — the audited event log.

Every state change emits a `RunEvent` through the outbox activity — if it
didn't emit, it didn't happen. Every event carries actor identity, dual
timestamps (`ts` real; `virtual_ts` under virtual clocks), and a `sandbox`
flag so rehearsal trajectories are never mistaken for production.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RunEventType = Literal[
    "run_started",
    "plan_created",
    "revision_applied",
    "revision_approved",
    "revision_rejected",
    "step_started",
    "step_done",
    "step_failed",
    "step_skipped",
    "gate_opened",
    "gate_answered",
    "gate_timed_out",
    "steer_received",
    "paused",
    "resumed",
    "budget_warning",
    "budget_exhausted",
    "child_spawned",
    "child_landed",
    "compaction_applied",
    "environment_provisioned",
    "environment_checkpointed",
    "environment_hibernated",
    "environment_restored",
    "environment_terminated",
    "landing_started",
    "run_completed",
    "run_failed",
]


class RunEvent(BaseModel):
    id: str  # deterministic: "{run_id}:{seq}"
    run_id: str
    tenant_id: str
    seq: int = Field(ge=0)  # per-run monotonic sequence
    type: RunEventType
    ts: datetime  # real timestamp (via RunClock)
    virtual_ts: datetime | None = None  # dual stamp under virtual clocks
    sandbox: bool = False  # rehearsal trajectories flagged
    actor: str  # verified actor identity
    actor_type: Literal["human", "agent", "system"] = "system"
    payload: dict[str, Any] = {}
