"""Signal payloads shared by the control plane and the workflow.

Every external mutation is a signal that carries the verified actor identity;
the workflow validates and enqueues these payloads, and its loop drains them.
Steering reuses the shared `SteerMessage` type directly; the payloads here
cover the remaining unblock keys, which stay distinct on purpose so the
audit trail always shows which kind of wait a human resolved.
"""

from datetime import datetime

from pydantic import BaseModel


class PlanApprovalDecision(BaseModel):
    """Approve or reject the plan version currently awaiting approval.

    The version pin makes stale decisions harmless: a decision for any other
    version is dropped by the workflow without changing state.
    """

    plan_version: int
    approve: bool
    reason: str | None = None
    actor: str


class GateResponse(BaseModel):
    """Answer exactly one step's open human gate.

    `at_virtual` scripts a simulated human under a virtual clock: the answer
    is held until virtual time reaches that instant, so a rehearsal can model
    "the reviewer replies on day 3" and idle auto-advance can compress the
    wait. Ignored (delivered immediately) under a real clock.
    """

    step_id: str
    response: str
    actor: str
    at_virtual: datetime | None = None


class ClockAdvance(BaseModel):
    """Move a virtual-clock run's time forward to `to`, resolving every
    virtual timer with a deadline at or before it. Only sandbox-kind runs
    with a virtual clock accept advances; time never moves backward, so a
    stale or duplicate advance is a no-op."""

    to: datetime
    actor: str
