"""Signal payloads shared by the control plane and the workflow.

Every external mutation is a signal that carries the verified actor identity;
the workflow validates and enqueues these payloads, and its loop drains them.
Steering reuses the shared `SteerMessage` type directly; the payloads here
cover the two remaining unblock keys, which stay distinct on purpose so the
audit trail always shows which kind of wait a human resolved.
"""

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
    """Answer exactly one step's open human gate."""

    step_id: str
    response: str
    actor: str
