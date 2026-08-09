"""RunCarry — runtime-internal state that rides continue_as_new beside RunState.

`RunState` is the shared, frozen contract for what survives a hop; the fields
here are runtime-internal bookkeeping the workflow also needs to keep a hop
lossless. `convoy_core` types are never extended or forked — anything the
shared state cannot hold travels in this object as a second continue_as_new
argument (the control plane passes the initial one at run start).

Exactly what rides in the carry, and why:

- `tuning` — turn limit and compaction thresholds. Workflow decisions must be
  replay-deterministic, so limits arrive as recorded input data, never from
  the environment.
- `binding` — the environment facts the workflow itself branches on (kind,
  clock config, sandbox template). The full binding snapshot is claim-checked
  behind `RunState.binding_ref` and workflow code cannot read artifacts.
- `hops`, `turns_at_segment_start` — hop count and the turn count at the last
  hop, so the turn limit measures the current history segment, not the run.
- `event_seq` — the outbox is idempotent on (run_id, seq); a hop must continue
  the sequence or post-hop events would collide with pre-hop ones and vanish.
- `tokens` — aggregate token totals for the land report (no aggregate counts
  in the shared run state).
- `step_tokens`, `fold_seq` — tokens accumulated in the current step's
  working transcript since the last mid-step fold (drives the next fold
  decision) and the monotonic fold counter that keys fold artifacts.
- `budget_warned`, `budget_exhausted_emitted` — one-shot budget threshold
  edges; re-firing them after a hop would duplicate audit events.
- `awaiting_approval_version` — an approval still owed must survive the hop,
  or a hop would execute an unapproved plan.
- `paused`/`landing` flags with their actors — control flags are overlays, not
  derived state; a pause requested just before a hop must hold after it.
- `resolved_step_gates`, `open_gates` — which step gates were already
  satisfied (so they never reopen) and which are open right now, with their
  armed absolute deadlines so timers re-arm against the same instant.
- `gate_feed_seq`, `group_note_seq` — counters minting unique ids for
  synthesized steer notes; a reset would reuse ids.
- `released_failures`, `join_gaps`, `pending_gap_release` — fan-out
  partial-failure bookkeeping: which failed members count as terminal, which
  join flags which gaps, and which gaps a pending gate answer releases.
- `seen_steer_ids` — steers already drained into the run. Temporal re-delivers
  signals buffered during the hop, so drains dedupe by id.
- `steer_inbox`, `approval_inbox`, `gate_response_inbox`, `advance_requests`,
  `scheduled_responses` — undrained mailboxes. Signals validate + enqueue and
  the loop drains; anything enqueued but undrained at the hop must carry.
- `sandbox_snapshot_ref` — the run's last workspace snapshot: the workspace
  is cache, the snapshot is truth, and a rebuilt sandbox starts from it.
- `sandbox_handle`/`sandbox_status` — the opaque locator of currently leased
  compute plus its logical lifecycle state. The handle contains no secrets;
  carrying it lets a fresh worker rediscover and release the Fargate task.
- `sandbox_checkpoint_seq`/`sandbox_generation`/`sandbox_checkpoint_id` —
  deterministic lifecycle operation ids and the latest durable checkpoint,
  so activity retries and continue-as-new never duplicate lifecycle changes.
- `ratio_real_anchor`, `ratio_virtual_anchor` — the fixed anchors of the
  ratio-clock mapping (virtual = anchor + real elapsed x ratio); a hop must
  not restart the mapping.

Everything here is small, serializable data — refs and scalars only, per the
claim-check discipline.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from convoy_core import (
    ArtifactRef,
    ClockConfig,
    HumanGate,
    SandboxHandle,
    SteerMessage,
    TokenCounts,
)
from convoy_runtime.signals import ClockAdvance, GateResponse, PlanApprovalDecision

# Segment ceiling before the workflow hops via continue_as_new, and the
# working-transcript token threshold that triggers a mid-step fold. Values are
# deploy-tunable; these defaults target multi-day runs staying well under
# Temporal's history caps.
DEFAULT_TURN_LIMIT = 200
DEFAULT_MIDSTEP_COMPACTION_TOKENS = 40_000
DEFAULT_KEEP_RECENT_TURNS = 2


class RunTuning(BaseModel):
    """Deterministic runtime limits, recorded as workflow input."""

    turn_limit: int = Field(default=DEFAULT_TURN_LIMIT, ge=1)
    midstep_compaction_tokens: int = Field(default=DEFAULT_MIDSTEP_COMPACTION_TOKENS, ge=1)
    keep_recent_turns: int = Field(default=DEFAULT_KEEP_RECENT_TURNS, ge=1)


class BindingFacts(BaseModel):
    """The slice of the pinned environment binding the workflow branches on.

    The immutable binding snapshot stays claim-checked behind
    `RunState.binding_ref`; these values are copied out of that same snapshot
    at run creation so deterministic decisions (virtual clock eligibility,
    rehearsal flagging, sandbox template) never require artifact reads.
    """

    kind: Literal["sandbox", "production"] = "production"
    clock: ClockConfig = ClockConfig()
    sandbox_template: str = ""


class CarriedGate(BaseModel):
    """One open human gate surviving a hop: its definition and, when the gate
    carries a timeout, the absolute deadline its timer re-arms against."""

    step_id: str
    gate: HumanGate
    deadline: datetime | None = None


class RunCarry(BaseModel):
    """Runtime-internal continue_as_new carry (see module docstring)."""

    tuning: RunTuning = RunTuning()
    binding: BindingFacts = BindingFacts()

    hops: int = Field(default=0, ge=0)
    turns_at_segment_start: int = Field(default=0, ge=0)
    event_seq: int = Field(default=0, ge=0)

    tokens: TokenCounts = TokenCounts()
    step_tokens: int = Field(default=0, ge=0)
    fold_seq: int = Field(default=0, ge=0)

    budget_warned: bool = False
    budget_exhausted_emitted: bool = False

    awaiting_approval_version: int | None = None

    paused: bool = False
    pause_actor: str = "system"
    pause_actor_type: Literal["human", "agent", "system"] = "system"
    landing: bool = False
    land_actor: str = "system"
    land_actor_type: Literal["human", "agent", "system"] = "system"

    resolved_step_gates: list[str] = []
    open_gates: list[CarriedGate] = []
    gate_feed_seq: int = Field(default=0, ge=0)
    group_note_seq: int = Field(default=0, ge=0)

    released_failures: list[str] = []
    join_gaps: dict[str, list[str]] = {}
    pending_gap_release: dict[str, list[str]] = {}

    seen_steer_ids: list[str] = []
    steer_inbox: list[SteerMessage] = []
    approval_inbox: list[PlanApprovalDecision] = []
    gate_response_inbox: list[GateResponse] = []
    advance_requests: list[ClockAdvance] = []
    scheduled_responses: list[GateResponse] = []

    sandbox_snapshot_ref: ArtifactRef | None = None
    sandbox_handle: SandboxHandle | None = None
    sandbox_status: Literal["unprovisioned", "active", "hibernated", "terminated"] = "unprovisioned"
    sandbox_checkpoint_seq: int = Field(default=0, ge=0)
    sandbox_generation: int = Field(default=0, ge=0)
    sandbox_checkpoint_id: str | None = None

    ratio_real_anchor: datetime | None = None
    ratio_virtual_anchor: datetime | None = None
