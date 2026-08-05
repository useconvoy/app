"""Tool capability types and promoted-call requests."""

from typing import Literal

from pydantic import BaseModel

from convoy_core.artifacts import ArtifactRef


class PermissionScope(BaseModel):
    """Scope a tool grant is limited to (resource pattern + allowed actions)."""

    resource: str
    actions: list[str] = []


class ToolGrant(BaseModel):
    tool_id: str
    scope: PermissionScope
    execution: Literal["inline", "promoted"]  # static → replay-safe promotion
    side_effecting: bool = False  # inline requires False


class ToolCallRequest(BaseModel):
    """A promoted tool call the workflow schedules as its own activity.

    `idempotency_key` = hash(run_id, step_id, turn, call_index), so an
    activity retry can never double-fire the side effect.
    """

    tool_id: str
    activity: str
    args_ref: ArtifactRef | None = None
    idempotency_key: str
