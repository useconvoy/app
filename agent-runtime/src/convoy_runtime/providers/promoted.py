"""Promoted tool call plumbing: idempotency keys and request/result shapes.

A promoted call runs as its own activity with its own timeout/retry policy.
Its idempotency key is a pure hash of (run_id, step_id, turn, call_index),
so a retry — or a whole workflow replay — reaches the side-effecting system
with the same key and can never double-fire the effect. Results are
claim-checked: the activity returns refs, never bodies.
"""

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

from convoy_core import ArtifactRef, SandboxHandle, SandboxJobResult, ToolCallRequest

# The only activities a promoted call may name; the workflow rejects anything
# else deterministically instead of scheduling an unknown activity. The
# activity-name registry mirrors these literals (importing it from here would
# cycle through the activities package); a unit test pins them equal.
PROMOTED_TOOL_ACTIVITY = "run_promoted_tool"
SANDBOX_JOB_ACTIVITY = "run_sandbox_job"
PROMOTED_ACTIVITIES = frozenset({PROMOTED_TOOL_ACTIVITY, SANDBOX_JOB_ACTIVITY})

# Tools that execute inside the run's sandbox follow this id convention until
# the environment registry carries richer execution metadata per tool.
SANDBOX_TOOL_PREFIX = "sandbox_"


def promoted_call_key(run_id: str, step_id: str, turn: int, call_index: int) -> str:
    """Deterministic idempotency key for one promoted call."""
    material = f"{run_id}|{step_id}|{turn}|{call_index}".encode()
    return hashlib.sha256(material).hexdigest()


class PromotedResume(BaseModel):
    """How a turn resumes after its promoted call: which call completed and
    where its claim-checked result lives. `call_index` numbers promoted calls
    within the turn (0-based) so the executor keys the next one correctly."""

    tool_id: str
    idempotency_key: str
    call_index: int = Field(ge=0)
    result_ref: ArtifactRef


class PromotedToolRequest(BaseModel):
    """One promoted data-plane tool execution."""

    run_id: str
    call: ToolCallRequest


class PromotedToolOutcome(BaseModel):
    """Claim-checked result of a promoted data-plane tool. `replayed` means
    the environment had already executed this key and returned the recorded
    result instead of acting again."""

    tool_id: str
    idempotency_key: str
    result_ref: ArtifactRef
    replayed: bool = False


class SandboxJobRequest(BaseModel):
    """One promoted sandbox job: the call, the template to build the sandbox
    from, and the last workspace snapshot — the truth a lost sandbox rebuilds
    from before executing."""

    run_id: str
    call: ToolCallRequest
    template: str
    snapshot_ref: ArtifactRef | None = None
    # The workflow carries the active handle, so a fresh activity worker can
    # rediscover an existing Fargate task instead of depending on process
    # memory. Older histories omit it and retain the create-from-snapshot path.
    handle: SandboxHandle | None = None


class SandboxJobOutcome(BaseModel):
    """Result of a promoted sandbox job plus the workspace snapshot taken
    right after it — the new truth the workflow carries forward."""

    tool_id: str
    idempotency_key: str
    result: SandboxJobResult
    result_ref: ArtifactRef
    snapshot_ref: ArtifactRef
    # Returned on every job and carried durably by the workflow. A handle is
    # an opaque provider locator, never a credential.
    handle: SandboxHandle | None = None
    replayed: bool = False


SandboxReleaseReason = Literal["pause", "land", "completed", "failed"]


class SandboxHibernateRequest(BaseModel):
    """Checkpoint an active execution session and release its compute.

    `checkpoint_id` is deterministic workflow input. The activity records a
    completion marker under that id before destroying compute, making retries
    safe across the snapshot/destroy crash window.
    """

    run_id: str
    checkpoint_id: str
    reason: SandboxReleaseReason
    handle: SandboxHandle
    snapshot_ref: ArtifactRef | None = None


class SandboxHibernateOutcome(BaseModel):
    checkpoint_id: str
    reason: SandboxReleaseReason
    released_sandbox_id: str
    snapshot_ref: ArtifactRef | None = None


class SandboxRestoreRequest(BaseModel):
    """Allocate fresh compute and restore the latest workspace checkpoint."""

    run_id: str
    restore_id: str
    template: str
    snapshot_ref: ArtifactRef | None = None


class SandboxRestoreOutcome(BaseModel):
    restore_id: str
    handle: SandboxHandle
    snapshot_ref: ArtifactRef | None = None
