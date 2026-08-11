"""AgentRunWorkflow — the durable outer loop.

Deterministic orchestration only: no I/O, no clock reads outside `RunClock`,
no randomness. Activities are scheduled by name so the workflow sandbox never
imports I/O clients, and every activity call declares an explicit retry
policy and timeouts.

The loop owns everything the run must never get wrong: plan mutation (turns
return proposals; the loop validates and applies), plan approval and human
gates, budget accounting and threshold actions, pause/land precedence, and
the audited event stream. Turn intelligence lives behind the run_turn
activity. Fan-out groups spawn child workflows whose budget slices are
reservations against this run's cap; the parent only ever sees each child's
compacted result, never its transcript.

Long runs survive by construction: promoted tool calls run as their own
keyed activities so side effects never double-fire; completed steps distill
into step summaries and long working transcripts fold mid-step, keeping
context bounded while raw archives stay lossless; and when a history segment
reaches its turn limit the workflow hops via continue_as_new, carrying the
shared `RunState` plus the runtime-internal `RunCarry` so nothing —
mailboxes, armed gate deadlines, budget edges, clock state — is lost.

Signals never do work here: they validate their payload and enqueue it; the
loop drains every mailbox at its boundaries, so external actions land between
turns and never interrupt one. Temporal re-delivers signals buffered during
a continue_as_new transition, so every drain is idempotent.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from convoy_core import (
        ArtifactRef,
        HumanGate,
        LandReport,
        Plan,
        PlanPatchOp,
        PlanRevision,
        PlanStep,
        RunEvent,
        RunEventType,
        RunResult,
        RunState,
        SandboxHandle,
        SteerMessage,
        StepSummaryRef,
        SubagentResult,
        TokenCounts,
        ToolCallRequest,
        TurnInput,
        TurnResult,
    )
    from convoy_runtime.activities import names
    from convoy_runtime.carry import CarriedGate, RunCarry
    from convoy_runtime.clock import PassthroughClock, RatioClock, RunClock, VirtualClock
    from convoy_runtime.providers.compaction import CompactRequest, CompactResult
    from convoy_runtime.providers.grants import intersect_grants
    from convoy_runtime.providers.promoted import (
        PROMOTED_ACTIVITIES,
        SANDBOX_JOB_ACTIVITY,
        PromotedResume,
        PromotedToolOutcome,
        PromotedToolRequest,
        SandboxHibernateOutcome,
        SandboxHibernateRequest,
        SandboxJobOutcome,
        SandboxJobRequest,
        SandboxReleaseReason,
        SandboxRestoreOutcome,
        SandboxRestoreRequest,
    )
    from convoy_runtime.providers.turn_executor import TurnContext
    from convoy_runtime.signals import ClockAdvance, GateResponse, PlanApprovalDecision
    from convoy_runtime.workflows.plan_engine import requires_approval, validate_revision
    from convoy_runtime.workflows.subagent import SubagentBrief, SubagentWorkflow

# Fraction of the budget cap that triggers the one-time warning event.
BUDGET_WARNING_THRESHOLD = Decimal("0.8")

# A single turn may promote at most this many calls before the step is
# treated as runaway and failed.
MAX_PROMOTED_CALLS_PER_TURN = 8

# Temporal patch id: checked-in and live histories recorded before execution
# hibernation retain their exact command sequence during replay.
SANDBOX_LIFECYCLE_PATCH = "sandbox-session-lifecycle-v1"

# Actor recorded when idle auto-advance moves a virtual clock.
ON_IDLE_ACTOR = "clock:on_idle"

# Explicit per-activity retry/timeout matrix (values tuned under load later —
# the shape is fixed: no defaults-by-omission).
_CREATE_PLAN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_CREATE_PLAN_TIMEOUT = timedelta(seconds=60)

_ARCHIVE_SNAPSHOT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_ARCHIVE_SNAPSHOT_TIMEOUT = timedelta(seconds=30)

_PROVISION_KEY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_PROVISION_KEY_TIMEOUT = timedelta(seconds=30)

_ASSEMBLE_HEADER_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_ASSEMBLE_HEADER_TIMEOUT = timedelta(seconds=30)

_RUN_TURN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)
_RUN_TURN_TIMEOUT = timedelta(seconds=120)
_RUN_TURN_HEARTBEAT = timedelta(seconds=60)

_COMPACT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_COMPACT_TIMEOUT = timedelta(seconds=60)

# Promoted calls are keyed, so retries are safe; the effect system replays a
# journaled key instead of acting twice.
_PROMOTED_TOOL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_PROMOTED_TOOL_TIMEOUT = timedelta(seconds=120)
_PROMOTED_TOOL_HEARTBEAT = timedelta(seconds=60)

_SANDBOX_JOB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_SANDBOX_JOB_TIMEOUT = timedelta(seconds=300)
_SANDBOX_JOB_HEARTBEAT = timedelta(seconds=120)

_SANDBOX_LIFECYCLE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_SANDBOX_LIFECYCLE_TIMEOUT = timedelta(seconds=300)

_LAND_RUN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_LAND_RUN_TIMEOUT = timedelta(seconds=60)

_OUTBOX_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=10,
)
_OUTBOX_TIMEOUT = timedelta(seconds=30)

_ARCHIVE_RESULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_ARCHIVE_RESULT_TIMEOUT = timedelta(seconds=30)

# A child run executes exactly once: its failure is real signal handled by
# the group partial-failure policy, and re-running a whole child would double
# its spend against an already-refunded reservation.
_CHILD_WORKFLOW_RETRY = RetryPolicy(maximum_attempts=1)
_CHILD_EXECUTION_TIMEOUT = timedelta(hours=1)

_ActorType = Literal["human", "agent", "system"]


def _ensure_model[ModelT: BaseModel](value: Any, model: type[ModelT]) -> ModelT:
    """Validate an argument into its model when the payload converter had no
    type hints to apply (older two-argument start shapes)."""
    if isinstance(value, model):
        return value
    return model.model_validate(value)


@dataclass
class _OpenGate:
    """One step's open human gate: its definition, and the armed durable
    timer when the gate carries a timeout. A fired timer acts exactly once;
    after an on-timeout pause the gate stays open, unarmed, until answered."""

    step_id: str
    gate: HumanGate
    deadline: datetime | None = None
    timer: asyncio.Task[None] | None = None

    @property
    def timer_fired(self) -> bool:
        return self.timer is not None and self.timer.done() and not self.timer.cancelled()

    def disarm(self) -> None:
        if self.timer is not None and not self.timer.done():
            self.timer.cancel()
        self.timer = None
        self.deadline = None


@dataclass
class _ChildFlight:
    """One in-flight child workflow: its handle, the reservation carved for
    it, and the gather task that queues its landing for the loop to absorb."""

    step_id: str
    child_run_id: str
    group_label: str
    slice_requested: Decimal
    slice_reserved: Decimal
    handle: "workflow.ChildWorkflowHandle[SubagentWorkflow, SubagentResult]"
    gather: asyncio.Task[None] | None = None


@dataclass
class _ChildLanding:
    """A landed child, queued by its gather task for the group loop to
    absorb in arrival order. `result` is None only when the child workflow
    itself failed without producing a `SubagentResult`."""

    step_id: str
    result: SubagentResult | None = None
    error: str | None = None


@dataclass
class _GroupPlan:
    """A fan-out group as the loop sees it: every member step sharing the
    group, and the subset that is ready to spawn right now."""

    label: str
    members: list[PlanStep] = field(default_factory=lambda: cast(list[PlanStep], []))
    to_spawn: list[PlanStep] = field(default_factory=lambda: cast(list[PlanStep], []))


@dataclass
class _Gather:
    """Live spawn/gather state for one group pass: who is still queued, who
    is in flight, the concurrency window, and whether the land request has
    already been forwarded to in-flight children."""

    window: int
    queue: list[PlanStep] = field(default_factory=lambda: cast(list[PlanStep], []))
    in_flight: dict[str, _ChildFlight] = field(
        default_factory=lambda: cast(dict[str, _ChildFlight], {})
    )
    land_signalled: bool = False


def _recompute_ready(plan: Plan, released_failures: set[str]) -> None:
    """pending -> ready once every dependency is done, skipped, or a failed
    fan-out member the partial-failure policy released the join from."""
    terminal_ok = {
        s.id
        for s in plan.steps
        if s.status in ("done", "skipped") or (s.status == "failed" and s.id in released_failures)
    }
    for step in plan.steps:
        if step.status == "pending" and all(d in terminal_ok for d in step.depends_on):
            step.status = "ready"


def _next_step(plan: Plan) -> PlanStep | None:
    """The step to work: an in-flight step first (self-executed steps always
    serialize on the one root transcript), else the first ready one."""
    for step in plan.steps:
        if step.status == "running":
            return step
    for step in plan.steps:
        if step.status == "ready":
            return step
    return None


def _plan_complete(plan: Plan, released_failures: set[str]) -> bool:
    """Every step is terminal: done, skipped, or a failed member whose gap
    the group partial-failure policy accepted."""
    return all(
        s.status in ("done", "skipped") or (s.status == "failed" and s.id in released_failures)
        for s in plan.steps
    )


def _find_join(plan: Plan, members: list[PlanStep]) -> PlanStep | None:
    """The group's join step: depends on every member without being one."""
    member_ids = {member.id for member in members}
    for step in plan.steps:
        if step.id not in member_ids and member_ids <= set(step.depends_on):
            return step
    return None


def _collect_group(plan: Plan, first_ready: PlanStep) -> _GroupPlan:
    """The fan-out group the ready step belongs to. A subagent step outside
    any group runs as a group of one."""
    if first_ready.group_id is None:
        return _GroupPlan(label=first_ready.id, members=[first_ready], to_spawn=[first_ready])
    members = [s for s in plan.steps if s.group_id == first_ready.group_id]
    return _GroupPlan(
        label=first_ready.group_id,
        members=members,
        to_spawn=[s for s in members if s.status == "ready"],
    )


def _dependency_outputs(plan: Plan, member: PlanStep) -> list[ArtifactRef]:
    """The artifact refs a member starts from: its dependencies' outputs."""
    by_id = {s.id: s for s in plan.steps}
    refs: list[ArtifactRef] = []
    for dep in member.depends_on:
        dep_step = by_id.get(dep)
        if dep_step is not None:
            refs.extend(dep_step.outputs)
    return refs


@workflow.defn
class AgentRunWorkflow:
    def __init__(self) -> None:
        # All workflow time flows through the RunClock seam. The clock is
        # re-selected from the carried binding facts at the top of run();
        # events always stamp real time, plus virtual time when one exists.
        self._real_clock = PassthroughClock()
        self._clock: RunClock = self._real_clock
        self._clock_kind: Literal["real", "virtual", "ratio"] = "real"
        self._virtual_clock: VirtualClock | None = None
        self._state: RunState | None = None
        self._carry = RunCarry()
        self._paused = False
        self._landing = False
        self._pause_actor = "system"
        self._pause_actor_type: _ActorType = "system"
        self._resume_actor = "system"
        self._resume_actor_type: _ActorType = "system"
        self._land_actor = "system"
        self._land_actor_type: _ActorType = "system"
        self._event_seq = 0
        # Budget threshold edges are one-shot per run (carried across hops).
        self._budget_warned = False
        self._budget_exhausted_emitted = False
        self._tokens = TokenCounts()
        # Tokens accumulated in the current step's working transcript since
        # the last mid-step fold; drives the next fold decision.
        self._step_tokens = 0
        self._fold_seq = 0
        # Signal mailboxes: signals validate + enqueue, the loop drains.
        # Temporal re-delivers signals buffered during a continue_as_new
        # transition, so drains dedupe (steers by id; the other keys are
        # idempotent by version/step matching).
        self._steer_inbox: list[SteerMessage] = []
        self._seen_steer_ids: set[str] = set()
        self._approval_inbox: list[PlanApprovalDecision] = []
        self._gate_response_inbox: list[GateResponse] = []
        self._advance_requests: list[ClockAdvance] = []
        # Simulated humans: gate responses held until virtual time reaches
        # their timestamp, so rehearsals can script "answered on day 3".
        self._scheduled_responses: list[GateResponse] = []
        self._last_advance_actor: str | None = None
        # The plan version currently blocked on human approval, if any.
        self._awaiting_approval_version: int | None = None
        # Open human gates by step id, and gates already satisfied so a
        # step's attached gate opens at most once.
        self._open_gates: dict[str, _OpenGate] = {}
        self._resolved_step_gates: set[str] = set()
        self._gate_feed_seq = 0
        # Fan-out bookkeeping. Landed children queue here (in arrival order)
        # for the group loop to absorb. Failed members the partial-failure
        # policy released count as terminal for readiness and completion.
        # Join gaps ride the join step's start event; gates opened on a join
        # under block_on_human release their gaps when answered.
        self._child_landings: list[_ChildLanding] = []
        self._released_failures: set[str] = set()
        self._join_gaps: dict[str, list[str]] = {}
        self._pending_gap_release: dict[str, list[str]] = {}
        self._group_note_seq = 0
        # Turn-limit segmentation and the sandbox workspace truth.
        self._hops = 0
        self._turns_at_segment_start = 0
        self._sandbox_snapshot_ref: ArtifactRef | None = None
        self._sandbox_handle: SandboxHandle | None = None
        self._sandbox_status: Literal["unprovisioned", "active", "hibernated", "terminated"] = (
            "unprovisioned"
        )
        self._sandbox_checkpoint_seq = 0
        self._sandbox_generation = 0
        self._sandbox_checkpoint_id: str | None = None
        self._sandbox_lifecycle_enabled = False

    # ------------------------------------------------------------------ run

    @workflow.run
    async def run(
        self, state: RunState, requested_by: str, carry: RunCarry | None = None
    ) -> RunResult:
        # Starts recorded before the carry argument existed deliver two
        # payloads; the SDK skips type hints on an arity mismatch, so the
        # arguments arrive as plain data and are validated back into their
        # models here. Deterministic: pure validation of recorded input.
        state = _ensure_model(state, RunState)
        carry = _ensure_model(carry, RunCarry) if carry is not None else None
        self._state = state
        self._restore_carry(carry)
        self._sandbox_lifecycle_enabled = workflow.patched(SANDBOX_LIFECYCLE_PATCH)
        self._init_clock(state)
        self._rearm_carried_gates()

        if self._hops == 0:
            await self._emit(
                "run_started",
                actor=requested_by,
                actor_type="human",
                payload={"run_status": state.status, "environment_id": state.environment_id},
            )

            # Per-run virtual model key with a hard dollar cap — the
            # infra-side budget backstop exists before the first turn can
            # spend anything.
            await workflow.execute_activity(
                names.PROVISION_MODEL_KEY,
                args=[state.run_id, state.budget.cap_usd],
                start_to_close_timeout=_PROVISION_KEY_TIMEOUT,
                retry_policy=_PROVISION_KEY_RETRY,
            )

        if state.plan is None:
            state.status = "planning"
            state.plan = cast(
                Plan,
                await workflow.execute_activity(
                    names.CREATE_PLAN,
                    state,
                    result_type=Plan,
                    start_to_close_timeout=_CREATE_PLAN_TIMEOUT,
                    retry_policy=_CREATE_PLAN_RETRY,
                ),
            )
            if state.policy.require_plan_approval:
                # The initial plan blocks until a human approves this exact
                # version, whatever the approval scope narrows later on.
                self._awaiting_approval_version = state.plan.version
                state.status = "awaiting_approval"
            else:
                state.status = "running"
            await self._emit(
                "plan_created",
                payload={
                    "plan": state.plan.model_dump(mode="json"),
                    "plan_version": state.plan.version,
                    "run_status": state.status,
                },
            )
        elif self._hops == 0:
            state.status = "running"

        while not _plan_complete(state.plan, self._released_failures):
            if self._should_hop(state):
                # Segment ceiling reached: hop with the shared state plus the
                # runtime carry. No children are in flight here (fan-out
                # groups settle before the loop re-checks), armed gate
                # deadlines re-arm on the other side, and buffered signals
                # are re-delivered by the server into idempotent drains.
                workflow.continue_as_new(args=[state, requested_by, self._snapshot_carry(state)])
            await self._drain_steer_mailbox()
            self._drain_clock_requests()
            if await self._enforce_budget() == "abort":
                state.status = "failed"
                await self._hibernate_sandbox("failed")
                await self._emit(
                    "run_failed",
                    payload={
                        "run_status": "failed",
                        "reason": "budget_exhausted",
                        "budget": self._budget_snapshot(),
                    },
                )
                return RunResult(run_id=state.run_id, status="failed", error="budget exhausted")
            waited = await self._control_boundary()
            if self._landing:
                break
            if waited:
                # A pause happened at this boundary; whatever changed while
                # waiting (e.g. a resume over an exhausted budget) must pass
                # budget enforcement again before any turn is scheduled.
                continue

            if await self._approval_boundary():
                # An approval wait (or decision) consumed this boundary; the
                # loop re-runs pause/budget checks before scheduling work.
                continue

            _recompute_ready(state.plan, self._released_failures)
            step = _next_step(state.plan)
            if step is None:
                gate_outcome = await self._gate_boundary()
                if isinstance(gate_outcome, RunResult):
                    return gate_outcome
                if gate_outcome == "deadlock":
                    # No ready, running, or human-blocked step and the plan
                    # is incomplete: fail loudly.
                    state.status = "failed"
                    await self._hibernate_sandbox("failed")
                    await self._emit(
                        "run_failed",
                        payload={"run_status": "failed", "reason": "plan deadlock"},
                    )
                    return RunResult(run_id=state.run_id, status="failed", error="plan deadlock")
                continue

            if step.executor == "subagent":
                # Fan-out: the ready group spawns child workflows and the
                # loop resumes once the group settles (or pauses/lands).
                # TODO: honor human gates attached to individual members.
                fanout_failure = await self._run_fanout_group(state, step)
                if fanout_failure is not None:
                    return fanout_failure
                continue

            if step.status != "running":
                step.status = "running"
                step.attempt += 1
                started_payload: dict[str, Any] = {"step_id": step.id, "attempt": step.attempt}
                gap = self._join_gaps.get(step.id)
                if gap:
                    # This step is a join proceeding over failed members —
                    # the gap is flagged on its start event.
                    started_payload["joined_with_failures"] = list(gap)
                await self._emit("step_started", payload=started_payload)

            # A step that carries a human gate blocks before its first turn:
            # the gate's answer becomes context for the work itself.
            if (
                step.human_gate is not None
                and step.id not in self._resolved_step_gates
                and step.id not in self._open_gates
            ):
                await self._open_gate(step, step.human_gate, actor="system", actor_type="system")
                continue

            # The pinned header is rebuilt from current state before every
            # turn so budget, plan, and steers are never stale.
            header_ref = cast(
                ArtifactRef,
                await workflow.execute_activity(
                    names.ASSEMBLE_PINNED_HEADER,
                    state,
                    result_type=ArtifactRef,
                    start_to_close_timeout=_ASSEMBLE_HEADER_TIMEOUT,
                    retry_policy=_ASSEMBLE_HEADER_RETRY,
                ),
            )
            state.pinned_ref = header_ref

            turn_input = self._turn_input(state, step)
            redirect_ids = [s.id for s in turn_input.steers if s.mode == "redirect"]
            try:
                result = await self._execute_step_turn(state, step, turn_input)
            except ActivityError:
                step.status = "failed"
                state.status = "failed"
                await self._emit("step_failed", payload={"step_id": step.id})
                await self._hibernate_sandbox("failed")
                await self._emit(
                    "run_failed", payload={"run_status": "failed", "reason": "step failed"}
                )
                # TODO: audited retry_step path instead of run failure.
                return RunResult(
                    run_id=state.run_id, status="failed", error=f"step {step.id} failed"
                )

            self._apply_turn(state, step, result)
            if result.outcome != "step_done":
                await self._maybe_fold_midstep(state, step)

            proposed = list(result.proposed_revision or [])
            if proposed:
                await self._process_proposal(proposed, redirect_ids)
                # Applying a revision replaces the plan object; re-resolve
                # the in-flight step so later transitions hit the live copy.
                step = next((s for s in state.plan.steps if s.id == step.id), step)
            elif redirect_ids:
                # A redirect was delivered and the turn proposed no change:
                # record the assessment so the steer never forks silently.
                await self._emit(
                    "revision_rejected",
                    actor=state.agent.id,
                    actor_type="agent",
                    payload={
                        "kind": "steer_assessment",
                        "reason": "plan_already_covers",
                        "steer_ids": redirect_ids,
                        "plan_version": state.plan.version,
                        "transcript_ref": result.transcript_ref.model_dump(mode="json"),
                    },
                )

            if result.outcome == "step_done":
                await self._emit(
                    "step_done",
                    payload=self._step_done_payload(state, step, result),
                )
                await self._compact_step_end(state, step, result)
            elif result.outcome == "needs_human":
                gate = result.gate_request or HumanGate(
                    kind="input", prompt=f"Agent requested human input for step {step.id}"
                )
                await self._open_gate(step, gate, actor=state.agent.id, actor_type="agent")

        # Landing never races gate timers: every armed timer is cancelled
        # before wrap-up so no timeout action can fire while landing.
        for record in self._open_gates.values():
            record.disarm()
        state.status = "landing"
        await self._emit(
            "landing_started",
            actor=self._land_actor,
            actor_type=self._land_actor_type,
            payload={"run_status": "landing"},
        )
        try:
            report = cast(
                LandReport,
                await workflow.execute_activity(
                    names.LAND_RUN,
                    args=[state, self._tokens],
                    result_type=LandReport,
                    start_to_close_timeout=_LAND_RUN_TIMEOUT,
                    retry_policy=_LAND_RUN_RETRY,
                ),
            )
        except ActivityError:
            state.status = "failed"
            await self._hibernate_sandbox("failed")
            await self._emit(
                "run_failed", payload={"run_status": "failed", "reason": "landing failed"}
            )
            return RunResult(run_id=state.run_id, status="failed", error="landing failed")
        await self._hibernate_sandbox("land" if self._landing else "completed")
        state.status = "completed"
        await self._emit(
            "run_completed",
            payload={
                "run_status": "completed",
                "land_report": report.model_dump(mode="json"),
                "budget": self._budget_snapshot(),
            },
        )
        return RunResult(run_id=state.run_id, status="completed", land_report=report)

    # ------------------------------------------------- carry & clock set-up

    def _restore_carry(self, carry: RunCarry | None) -> None:
        """Rehydrate runtime-internal state after a hop. Mailboxes merge in
        front of anything signals already enqueued for this execution, and
        the fresh-start path (no carry, or a hopless initial carry) leaves
        the defaults untouched."""
        if carry is None:
            return
        self._carry = carry
        self._hops = carry.hops
        self._turns_at_segment_start = carry.turns_at_segment_start
        self._event_seq = carry.event_seq
        self._tokens = carry.tokens
        self._step_tokens = carry.step_tokens
        self._fold_seq = carry.fold_seq
        self._budget_warned = carry.budget_warned
        self._budget_exhausted_emitted = carry.budget_exhausted_emitted
        self._awaiting_approval_version = carry.awaiting_approval_version
        self._paused = carry.paused
        self._pause_actor = carry.pause_actor
        self._pause_actor_type = carry.pause_actor_type
        self._landing = carry.landing
        self._land_actor = carry.land_actor
        self._land_actor_type = carry.land_actor_type
        self._resolved_step_gates = set(carry.resolved_step_gates)
        self._gate_feed_seq = carry.gate_feed_seq
        self._group_note_seq = carry.group_note_seq
        self._released_failures = set(carry.released_failures)
        self._join_gaps = {k: list(v) for k, v in carry.join_gaps.items()}
        self._pending_gap_release = {k: list(v) for k, v in carry.pending_gap_release.items()}
        self._seen_steer_ids = set(carry.seen_steer_ids)
        self._steer_inbox = [*carry.steer_inbox, *self._steer_inbox]
        self._approval_inbox = [*carry.approval_inbox, *self._approval_inbox]
        self._gate_response_inbox = [*carry.gate_response_inbox, *self._gate_response_inbox]
        self._advance_requests = [*carry.advance_requests, *self._advance_requests]
        self._scheduled_responses = [*carry.scheduled_responses, *self._scheduled_responses]
        self._sandbox_snapshot_ref = carry.sandbox_snapshot_ref
        self._sandbox_handle = carry.sandbox_handle
        self._sandbox_status = carry.sandbox_status
        self._sandbox_checkpoint_seq = carry.sandbox_checkpoint_seq
        self._sandbox_generation = carry.sandbox_generation
        self._sandbox_checkpoint_id = carry.sandbox_checkpoint_id

    def _snapshot_carry(self, state: RunState) -> RunCarry:
        """Everything runtime-internal the next execution needs, captured at
        the hop boundary. Armed gate deadlines are recorded as absolute
        instants so they re-arm unchanged."""
        open_gates = [
            CarriedGate(step_id=record.step_id, gate=record.gate, deadline=record.deadline)
            for record in self._open_gates.values()
        ]
        for record in self._open_gates.values():
            record.disarm()
        return self._carry.model_copy(
            update={
                "hops": self._hops + 1,
                "turns_at_segment_start": state.turn_count,
                "event_seq": self._event_seq,
                "tokens": self._tokens,
                "step_tokens": self._step_tokens,
                "fold_seq": self._fold_seq,
                "budget_warned": self._budget_warned,
                "budget_exhausted_emitted": self._budget_exhausted_emitted,
                "awaiting_approval_version": self._awaiting_approval_version,
                "paused": self._paused,
                "pause_actor": self._pause_actor,
                "pause_actor_type": self._pause_actor_type,
                "landing": self._landing,
                "land_actor": self._land_actor,
                "land_actor_type": self._land_actor_type,
                "resolved_step_gates": sorted(self._resolved_step_gates),
                "open_gates": open_gates,
                "gate_feed_seq": self._gate_feed_seq,
                "group_note_seq": self._group_note_seq,
                "released_failures": sorted(self._released_failures),
                "join_gaps": {k: list(v) for k, v in self._join_gaps.items()},
                "pending_gap_release": {k: list(v) for k, v in self._pending_gap_release.items()},
                "seen_steer_ids": sorted(self._seen_steer_ids),
                "steer_inbox": list(self._steer_inbox),
                "approval_inbox": list(self._approval_inbox),
                "gate_response_inbox": list(self._gate_response_inbox),
                "advance_requests": list(self._advance_requests),
                "scheduled_responses": list(self._scheduled_responses),
                "sandbox_snapshot_ref": self._sandbox_snapshot_ref,
                "sandbox_handle": self._sandbox_handle,
                "sandbox_status": self._sandbox_status,
                "sandbox_checkpoint_seq": self._sandbox_checkpoint_seq,
                "sandbox_generation": self._sandbox_generation,
                "sandbox_checkpoint_id": self._sandbox_checkpoint_id,
            }
        )

    def _should_hop(self, state: RunState) -> bool:
        """The current history segment reached its turn ceiling. Landing
        wins: a landing run wraps up instead of hopping."""
        if self._landing:
            return False
        return state.turn_count - self._turns_at_segment_start >= self._carry.tuning.turn_limit

    def _init_clock(self, state: RunState) -> None:
        """Select the run's clock from the carried binding facts. Virtual
        time is only legal on sandbox-kind bindings; everything else runs on
        the passthrough clock."""
        facts = self._carry.binding
        if facts.kind != "sandbox" or facts.clock.mode != "virtual":
            self._clock = self._real_clock
            self._clock_kind = "real"
            return
        if facts.clock.advance == "ratio" and facts.clock.ratio is not None:
            if self._carry.ratio_real_anchor is None or self._carry.ratio_virtual_anchor is None:
                anchor = self._real_clock.now()
                self._carry = self._carry.model_copy(
                    update={"ratio_real_anchor": anchor, "ratio_virtual_anchor": anchor}
                )
            clock = RatioClock(
                real_anchor=self._carry.ratio_real_anchor or self._real_clock.now(),
                virtual_anchor=self._carry.ratio_virtual_anchor or self._real_clock.now(),
                ratio=facts.clock.ratio,
                base=self._real_clock,
            )
            self._clock = clock
            self._clock_kind = "ratio"
            state.virtual_now = clock.now()
            return
        if state.virtual_now is None:
            state.virtual_now = self._real_clock.now()
        virtual = VirtualClock(state.virtual_now)
        self._virtual_clock = virtual
        self._clock = virtual
        self._clock_kind = "virtual"

    def _rearm_carried_gates(self) -> None:
        """Reopen gates that were open at the hop, re-arming their timers
        against the same absolute deadlines."""
        for carried in self._carry.open_gates:
            record = _OpenGate(step_id=carried.step_id, gate=carried.gate)
            if carried.deadline is not None:
                record.deadline = carried.deadline
                record.timer = asyncio.ensure_future(self._clock.timer(carried.deadline))
            self._open_gates[carried.step_id] = record

    def _virtual_now(self) -> datetime | None:
        """The run's current virtual instant, when a virtual clock exists."""
        if self._clock_kind == "real":
            return None
        return self._clock.now()

    # -------------------------------------------------------------- signals
    # Signals do no heavy work: validate, enqueue, return. The loop drains.

    @workflow.signal
    def pause(self, actor: str = "unknown") -> None:
        if self._landing:
            return  # landing takes precedence over pause
        self._paused = True
        self._pause_actor = actor
        self._pause_actor_type = "human"

    @workflow.signal
    def resume(self, actor: str = "unknown") -> None:
        # Clears the paused flag only; idempotent; non-cascading.
        self._paused = False
        self._resume_actor = actor
        self._resume_actor_type = "human"

    @workflow.signal
    def land(self, actor: str = "unknown") -> None:
        self._landing = True
        self._land_actor = actor
        self._land_actor_type = "human"

    @workflow.signal
    def steer(self, message: SteerMessage) -> None:
        if not message.id or not message.body:
            return  # malformed steer — dropped without state change
        if message.id in self._seen_steer_ids or any(m.id == message.id for m in self._steer_inbox):
            return  # already delivered (e.g. re-sent across a hop) — no-op
        self._steer_inbox.append(message)

    @workflow.signal
    def approve_plan(self, decision: PlanApprovalDecision) -> None:
        self._approval_inbox.append(decision)

    @workflow.signal
    def human_response(self, response: GateResponse) -> None:
        if not response.step_id:
            return  # malformed response — dropped without state change
        if response.at_virtual is not None:
            if response.at_virtual.tzinfo is None:
                return  # naive instants cannot compare against the clock
            # A simulated human answering at a virtual instant: held until
            # the clock reaches it (delivered immediately on real clocks).
            self._scheduled_responses.append(response)
            return
        self._gate_response_inbox.append(response)

    @workflow.signal
    def advance_time(self, request: ClockAdvance) -> None:
        if request.to.tzinfo is None:
            return  # naive instants cannot compare against the clock
        # Guarded again at drain time: only sandbox-kind virtual clocks move,
        # and never backward — so duplicates and stale requests are no-ops.
        self._advance_requests.append(request)

    # -------------------------------------------------------------- queries

    @workflow.query
    def get_status(self) -> str:
        # Precedence: landing > paused > derived from plan/approval state.
        if self._state is None:
            return "planning"
        if self._state.status in ("completed", "failed", "landing"):
            return self._state.status
        if self._landing:
            return "landing"
        if self._paused:
            return "paused"
        if self._state.plan is None:
            return "planning"
        return self._base_status()

    @workflow.query
    def get_plan(self) -> Plan | None:
        return self._state.plan if self._state else None

    @workflow.query
    def get_durability(self) -> dict[str, Any]:
        """Runtime-internal durability facts for tests and diagnostics."""
        virtual = self._virtual_now()
        return {
            "hops": self._hops,
            "event_seq": self._event_seq,
            "turn_count": self._state.turn_count if self._state else 0,
            "segment_turns": (
                self._state.turn_count - self._turns_at_segment_start if self._state else 0
            ),
            "clock_kind": self._clock_kind,
            "virtual_now": virtual.isoformat() if virtual else None,
            "step_summaries": len(self._state.step_summaries) if self._state else 0,
            "sandbox_status": self._sandbox_status,
            "sandbox_id": self._sandbox_handle.sandbox_id if self._sandbox_handle else None,
            "sandbox_generation": self._sandbox_generation,
            "sandbox_checkpoint_id": self._sandbox_checkpoint_id,
            "sandbox_snapshot_ref": (
                self._sandbox_snapshot_ref.model_dump(mode="json")
                if self._sandbox_snapshot_ref
                else None
            ),
        }

    # -------------------------------------------------- boundaries & waits

    def _base_status(self) -> Literal["running", "awaiting_approval", "blocked_on_human"]:
        """Run status with the pause/landing overlays stripped: approval
        waits win, then blocked-on-human derives from the steps (at least
        one blocked step and nothing ready or running)."""
        assert self._state is not None
        if self._awaiting_approval_version is not None:
            return "awaiting_approval"
        plan = self._state.plan
        if plan is not None:
            active = any(s.status in ("ready", "running") for s in plan.steps)
            blocked = any(s.status == "blocked_on_human" for s in plan.steps)
            if blocked and not active:
                return "blocked_on_human"
        return "running"

    async def _drain_steer_mailbox(self) -> None:
        """Move newly signalled steers into the run's pending mailbox and
        emit their audit events; they ride into the next turn's context.
        Steer ids already seen (a hop re-delivered them) drop silently."""
        assert self._state is not None
        while self._steer_inbox:
            message = self._steer_inbox.pop(0)
            if message.id in self._seen_steer_ids:
                continue
            self._seen_steer_ids.add(message.id)
            self._state.pending_steers.append(message)
            await self._emit(
                "steer_received",
                actor=message.author_id,
                actor_type=message.author,
                payload={"steer_id": message.id, "mode": message.mode, "body": message.body},
            )

    def _drain_clock_requests(self) -> None:
        """Apply queued manual clock advances. Only sandbox-kind virtual
        clocks move (ratio and real clocks drop requests), and time never
        moves backward. The applying actor is remembered so timer-driven
        events can attribute the advance.

        TODO: a dedicated audited clock event once the shared RunEvent
        catalog grows a type for it; today the advance is attributed on the
        events its resolved timers emit.
        """
        while self._advance_requests:
            request = self._advance_requests.pop(0)
            if self._virtual_clock is None:
                continue
            if self._virtual_clock.advance_to(request.to):
                assert self._state is not None
                self._state.virtual_now = self._virtual_clock.now()
                self._last_advance_actor = request.actor

    def _deliver_due_responses(self) -> None:
        """Hand scheduled (simulated-human) responses to the gate inbox once
        their instant arrives. Under a real clock they deliver immediately;
        under virtual clocks they wait for their step's gate to be open and
        the clock to reach their timestamp."""
        if not self._scheduled_responses:
            return
        if self._clock_kind == "real":
            self._gate_response_inbox.extend(self._scheduled_responses)
            self._scheduled_responses.clear()
            return
        now_v = self._virtual_now()
        if now_v is None:
            return
        still: list[GateResponse] = []
        for response in self._scheduled_responses:
            due = response.at_virtual is not None and response.at_virtual <= now_v
            if due and response.step_id in self._open_gates:
                self._gate_response_inbox.append(response)
            else:
                still.append(response)
        self._scheduled_responses = still

    def _scheduled_due(self) -> bool:
        now_v = self._virtual_now()
        if now_v is None:
            return bool(self._scheduled_responses)
        return any(
            r.at_virtual is not None and r.at_virtual <= now_v and r.step_id in self._open_gates
            for r in self._scheduled_responses
        )

    def _idle_advance_target(self) -> datetime | None:
        """Where idle auto-advance may move the virtual clock: the earliest
        deadline that unblocks something, but only while every open gate is
        auto-resolvable (an armed timeout timer or a scheduled simulated
        response). A gate with neither is a rehearsal checkpoint — a real
        human decision — and pauses auto-advance."""
        if self._virtual_clock is None or self._carry.binding.clock.advance != "on_idle":
            return None
        if self._paused or self._landing or not self._open_gates:
            return None
        now_v = self._virtual_clock.now()
        candidates: list[datetime] = []
        for record in self._open_gates.values():
            sources: list[datetime] = []
            if record.deadline is not None and record.timer is not None:
                sources.append(record.deadline)
            sources.extend(
                r.at_virtual
                for r in self._scheduled_responses
                if r.step_id == record.step_id and r.at_virtual is not None
            )
            future = [s for s in sources if s > now_v]
            if not future:
                return None  # a human checkpoint (or an already-due source)
            candidates.extend(future)
        return min(candidates) if candidates else None

    def _apply_idle_advance(self, target: datetime) -> None:
        if self._virtual_clock is not None and self._virtual_clock.advance_to(target):
            assert self._state is not None
            self._state.virtual_now = self._virtual_clock.now()
            self._last_advance_actor = ON_IDLE_ACTOR

    async def _enforce_budget(self) -> Literal["continue", "abort"]:
        """Threshold checks at the loop boundary, before the next turn is
        scheduled: one-time warning at the soft threshold; at 100% the
        configured action (pause / land / abort). The committed amount counts
        reservations so child slices can never overdraw the cap."""
        assert self._state is not None
        state = self._state
        budget = state.budget
        if self._landing or budget.cap_usd <= 0:
            return "continue"
        committed = budget.spent_usd + budget.reserved_usd
        if committed >= budget.cap_usd:
            action = state.policy.on_budget_exhausted
            if not self._budget_exhausted_emitted:
                self._budget_exhausted_emitted = True
                await self._emit(
                    "budget_exhausted",
                    payload={
                        "action": action,
                        "budget": self._budget_snapshot(),
                        "policy": {"on_budget_exhausted": action},
                    },
                )
            if action == "abort":
                return "abort"
            if action == "land":
                self._landing = True
            else:  # pause — re-arms every boundary while still over cap
                self._paused = True
                self._pause_actor = "system"
                self._pause_actor_type = "system"
        elif not self._budget_warned and committed >= BUDGET_WARNING_THRESHOLD * budget.cap_usd:
            self._budget_warned = True
            await self._emit(
                "budget_warning",
                payload={
                    "threshold": str(BUDGET_WARNING_THRESHOLD),
                    "budget": self._budget_snapshot(),
                },
            )
        return "continue"

    async def _control_boundary(self) -> bool:
        """Edge-triggered pause at the loop boundary — never cancels an
        in-flight activity. Returns whether a pause wait actually happened,
        so the loop can re-run budget enforcement before scheduling work."""
        assert self._state is not None
        if not self._paused or self._landing:
            return False
        self._state.status = "paused"
        await self._hibernate_sandbox("pause")
        await self._emit(
            "paused",
            actor=self._pause_actor,
            actor_type=self._pause_actor_type,
            payload={"run_status": "paused"},
        )
        while self._paused and not self._landing:
            await workflow.wait_condition(
                lambda: (
                    not self._paused
                    or self._landing
                    or bool(self._steer_inbox)
                    or bool(self._advance_requests)
                )
            )
            await self._drain_steer_mailbox()
            self._drain_clock_requests()
        if not self._landing:
            # Runs that never used compute remain unprovisioned across a
            # pause. Only a session that was actually hibernated is restored.
            if self._sandbox_status == "hibernated":
                await self._activate_sandbox()
            self._state.status = self._base_status()
            await self._emit(
                "resumed",
                actor=self._resume_actor,
                actor_type=self._resume_actor_type,
                payload={"run_status": self._state.status},
            )
        return True

    async def _approval_boundary(self) -> bool:
        """Block while the current plan version requires human approval.

        Decisions are drained from the mailbox; only one matching the
        awaited version counts — stale or wrong-version decisions are
        dropped without changing state. Approval records the approver on the
        revision and resumes execution; rejection records the reason and
        pauses the run for human follow-up, with the approval still owed, so
        a plain resume re-enters this wait rather than executing an
        unapproved plan.
        """
        assert self._state is not None
        state = self._state
        if self._awaiting_approval_version is None:
            # Nothing is awaiting approval: any queued decision is stale.
            self._approval_inbox.clear()
            return False
        version = self._awaiting_approval_version
        state.status = "awaiting_approval"
        if not self._approval_inbox:
            await workflow.wait_condition(
                lambda: (
                    bool(self._approval_inbox)
                    or self._paused
                    or self._landing
                    or bool(self._steer_inbox)
                    or bool(self._advance_requests)
                )
            )
            await self._drain_steer_mailbox()
            self._drain_clock_requests()
        if self._paused or self._landing:
            return True
        while self._approval_inbox:
            decision = self._approval_inbox.pop(0)
            if decision.plan_version != version:
                continue  # wrong plan version — dropped, still awaiting
            assert state.plan is not None
            if decision.approve:
                for revision in state.plan.revisions:
                    if revision.version == version:
                        revision.approved_by = decision.actor
                self._awaiting_approval_version = None
                state.status = "running"
                await self._emit(
                    "revision_approved",
                    actor=decision.actor,
                    actor_type="human",
                    payload={"plan_version": version, "run_status": "running"},
                )
            else:
                await self._emit(
                    "revision_rejected",
                    actor=decision.actor,
                    actor_type="human",
                    payload={
                        "kind": "human_rejection",
                        "plan_version": version,
                        "reason": decision.reason,
                    },
                )
                # The rejection parks the run for follow-up; approval is
                # still owed for this version.
                self._paused = True
                self._pause_actor = decision.actor
                self._pause_actor_type = "human"
            break
        return True

    async def _open_gate(
        self, step: PlanStep, gate: HumanGate, *, actor: str, actor_type: _ActorType
    ) -> None:
        """Block a step on its human gate, arming the durable timeout timer
        through the RunClock when the gate carries one. Under a virtual clock
        the deadline is a virtual instant resolved by advancement."""
        assert self._state is not None
        step.status = "blocked_on_human"
        record = _OpenGate(step_id=step.id, gate=gate)
        if gate.timeout is not None:
            record.deadline = self._clock.now() + gate.timeout
            record.timer = asyncio.ensure_future(self._clock.timer(record.deadline))
        self._open_gates[step.id] = record
        self._state.status = self._base_status()
        await self._emit(
            "gate_opened",
            actor=actor,
            actor_type=actor_type,
            payload={
                "step_id": step.id,
                "kind": gate.kind,
                "prompt": gate.prompt,
                "timeout_seconds": (
                    gate.timeout.total_seconds() if gate.timeout is not None else None
                ),
                "on_timeout": gate.on_timeout,
                "deadline": record.deadline.isoformat() if record.deadline else None,
                "run_status": self._state.status,
            },
        )

    def _any_gate_timer_fired(self) -> bool:
        return any(record.timer_fired for record in self._open_gates.values())

    async def _gate_boundary(self) -> RunResult | Literal["waited", "deadlock"]:
        """Wait while every actionable step is blocked on a human.

        Wakes on a gate response, a gate timeout, a steer to record, a clock
        advance, or a pause/land request. Responses unblock exactly the step
        they name — answers for steps that are not blocked are dropped
        without state change. Timeout actions never fire while paused or
        landing; a fired timer is processed at the first boundary where the
        run is active. Under an idle-advancing virtual clock, a wait whose
        every open gate is auto-resolvable advances time to the earliest
        deadline instead of parking.
        """
        assert self._state is not None
        state = self._state
        plan = state.plan
        assert plan is not None
        if not any(s.status == "blocked_on_human" for s in plan.steps):
            return "deadlock"
        state.status = self._base_status()

        while True:
            self._drain_clock_requests()
            self._deliver_due_responses()
            if (
                self._gate_response_inbox
                or self._any_gate_timer_fired()
                or self._paused
                or self._landing
                or self._steer_inbox
            ):
                break
            target = self._idle_advance_target()
            if target is not None:
                # Blocked only on virtual deadlines: compress the wait. Armed
                # timers observe the new instant on the next scheduler pass,
                # which the wait below yields to; delivered responses surface
                # through the drain at the top of this loop.
                self._apply_idle_advance(target)
                continue
            await workflow.wait_condition(
                lambda: (
                    bool(self._gate_response_inbox)
                    or self._any_gate_timer_fired()
                    or self._paused
                    or self._landing
                    or bool(self._steer_inbox)
                    or bool(self._advance_requests)
                    or self._scheduled_due()
                    or self._idle_advance_target() is not None
                )
            )
        await self._drain_steer_mailbox()
        self._drain_clock_requests()
        self._deliver_due_responses()
        if self._paused or self._landing:
            return "waited"

        while self._gate_response_inbox:
            response = self._gate_response_inbox.pop(0)
            step = next((s for s in plan.steps if s.id == response.step_id), None)
            record = self._open_gates.get(response.step_id)
            if step is None or step.status != "blocked_on_human" or record is None:
                continue  # response to a step that is not gated — dropped
            record.disarm()
            del self._open_gates[step.id]
            self._resolved_step_gates.add(step.id)
            step.status = "running"
            # A gate opened on a join over failed members: the answer accepts
            # the gap, releasing those members as terminal.
            self._released_failures.update(self._pending_gap_release.pop(step.id, []))
            state.status = self._base_status()
            # The answer feeds the next turn's context through the steer
            # mailbox, so the agent sees it in the pinned header and input.
            self._gate_feed_seq += 1
            state.pending_steers.append(
                SteerMessage(
                    id=f"gate-answer-{step.id}-{self._gate_feed_seq}",
                    author="human",
                    author_id=response.actor,
                    mode="note",
                    body=response.response,
                )
            )
            await self._emit(
                "gate_answered",
                actor=response.actor,
                actor_type="human",
                payload={
                    "step_id": step.id,
                    "kind": record.gate.kind,
                    "response": response.response,
                    "simulated_at": (
                        response.at_virtual.isoformat() if response.at_virtual else None
                    ),
                    "run_status": state.status,
                },
            )

        for record in list(self._open_gates.values()):
            if not record.timer_fired:
                continue
            step = next((s for s in plan.steps if s.id == record.step_id), None)
            deadline = record.deadline
            record.disarm()
            if step is None or step.status != "blocked_on_human":
                continue
            action = record.gate.on_timeout
            await self._emit(
                "gate_timed_out",
                payload={
                    "step_id": step.id,
                    "kind": record.gate.kind,
                    "on_timeout": action,
                    "deadline": deadline.isoformat() if deadline else None,
                    "advanced_by": (
                        self._last_advance_actor if self._clock_kind == "virtual" else None
                    ),
                    "run_status": state.status,
                },
            )
            if action == "pause":
                # The gate stays open (and unarmed): the run parks until a
                # human answers, lands, or resumes into the still-open gate.
                self._paused = True
                self._pause_actor = "system"
                self._pause_actor_type = "system"
            elif action == "skip":
                del self._open_gates[step.id]
                self._resolved_step_gates.add(step.id)
                step.status = "skipped"
                state.status = self._base_status()
                await self._emit("step_skipped", payload={"step_id": step.id})
            else:  # fail
                del self._open_gates[step.id]
                self._resolved_step_gates.add(step.id)
                step.status = "failed"
                state.status = "failed"
                await self._emit("step_failed", payload={"step_id": step.id})
                await self._hibernate_sandbox("failed")
                await self._emit(
                    "run_failed",
                    payload={"run_status": "failed", "reason": "gate timed out"},
                )
                # TODO: audited retry_step path instead of run failure.
                return RunResult(
                    run_id=state.run_id,
                    status="failed",
                    error=f"step {step.id} gate timed out",
                )
        return "waited"

    async def _process_proposal(self, ops: list[PlanPatchOp], steer_ids: list[str]) -> None:
        """Validate an agent-proposed revision and, when valid, apply it as
        the next plan version: snapshot archived, author and reason recorded,
        and approval required per policy scope before execution continues.
        Invalid proposals are recorded and dropped — the plan is unchanged.
        """
        assert self._state is not None
        state = self._state
        plan = state.plan
        assert plan is not None
        reason: Literal["steer", "replan"] = "steer" if steer_ids else "replan"
        remaining = state.budget.cap_usd - state.budget.spent_usd - state.budget.reserved_usd
        validation = validate_revision(
            plan,
            ops,
            policy=state.policy,
            budget_remaining=remaining,
            max_children=state.agent.max_children,
        )
        ops_payload = [op.model_dump(mode="json") for op in ops]
        if not validation.ok or validation.candidate is None:
            await self._emit(
                "revision_rejected",
                actor=state.agent.id,
                actor_type="agent",
                payload={
                    "kind": "validation",
                    "plan_version": plan.version,
                    "reasons": validation.errors,
                    "ops": ops_payload,
                    "steer_ids": steer_ids,
                },
            )
            return

        candidate = validation.candidate
        snapshot_ref = cast(
            ArtifactRef,
            await workflow.execute_activity(
                names.ARCHIVE_PLAN_SNAPSHOT,
                args=[state.run_id, candidate],
                result_type=ArtifactRef,
                start_to_close_timeout=_ARCHIVE_SNAPSHOT_TIMEOUT,
                retry_policy=_ARCHIVE_SNAPSHOT_RETRY,
            ),
        )
        revision = PlanRevision(
            version=candidate.version,
            author="agent",
            author_id=state.agent.id,
            reason=reason,
            ops=ops,
            snapshot_ref=snapshot_ref,
        )
        state.plan = candidate.model_copy(update={"revisions": [*candidate.revisions, revision]})
        needs_approval = requires_approval(ops, state.policy)
        if needs_approval:
            self._awaiting_approval_version = state.plan.version
            state.status = "awaiting_approval"
        await self._emit(
            "revision_applied",
            actor=state.agent.id,
            actor_type="agent",
            payload={
                "plan_version": state.plan.version,
                "author": "agent",
                "author_id": state.agent.id,
                "reason": reason,
                "ops": ops_payload,
                "steer_ids": steer_ids,
                "requires_approval": needs_approval,
                "plan": state.plan.model_dump(mode="json"),
                "run_status": state.status,
            },
        )

    # ---------------------------------------------------- turns & promotion

    async def _execute_step_turn(
        self, state: RunState, step: PlanStep, turn_input: TurnInput
    ) -> TurnResult:
        """One logical turn: the run_turn activity plus, while it promotes,
        the promoted calls as their own keyed activities — each result
        claim-checked and fed back into a re-entry of the same turn. Costs
        and tokens of every partial result are absorbed as they happen; the
        final outcome is returned for the loop to apply."""
        result = cast(
            TurnResult,
            await workflow.execute_activity(
                names.RUN_TURN,
                args=[turn_input, self._turn_context(state)],
                result_type=TurnResult,
                start_to_close_timeout=_RUN_TURN_TIMEOUT,
                heartbeat_timeout=_RUN_TURN_HEARTBEAT,
                retry_policy=_RUN_TURN_RETRY,
            ),
        )
        call_index = 0
        while result.outcome == "promote":
            self._absorb_turn_accounting(state, result)
            call = result.promoted_call
            if call is None or call.activity not in PROMOTED_ACTIVITIES:
                raise ApplicationError(
                    f"turn promoted an unknown activity {call.activity if call else None!r}",
                    non_retryable=True,
                )
            if call_index >= MAX_PROMOTED_CALLS_PER_TURN:
                raise ApplicationError(
                    f"turn exceeded {MAX_PROMOTED_CALLS_PER_TURN} promoted calls",
                    non_retryable=True,
                )
            # The promoting turn's transcript is the head its re-entry
            # resumes from.
            state.working_transcript_ref = result.transcript_ref
            result_ref = await self._run_promoted_call(state, call)
            resume = PromotedResume(
                tool_id=call.tool_id,
                idempotency_key=call.idempotency_key,
                call_index=call_index,
                result_ref=result_ref,
            )
            call_index += 1
            result = cast(
                TurnResult,
                await workflow.execute_activity(
                    names.RUN_TURN,
                    args=[
                        self._turn_input(state, step),
                        self._turn_context(state, resume=resume),
                    ],
                    result_type=TurnResult,
                    start_to_close_timeout=_RUN_TURN_TIMEOUT,
                    heartbeat_timeout=_RUN_TURN_HEARTBEAT,
                    retry_policy=_RUN_TURN_RETRY,
                ),
            )
        return result

    async def _run_promoted_call(self, state: RunState, call: ToolCallRequest) -> ArtifactRef:
        """Execute one promoted call as its own activity and hand back the
        claim-checked result ref. Sandbox jobs ride the workspace-snapshot
        chain: each job starts from the last snapshot (truth) and its new
        snapshot is carried forward."""
        if call.activity == SANDBOX_JOB_ACTIVITY:
            # Provision first as a distinct durable activity so the workflow
            # owns the handle before any job starts. A job failure can then
            # still checkpoint/release its compute on the terminal path.
            if self._sandbox_lifecycle_enabled:
                await self._activate_sandbox()
                if self._sandbox_handle is None:
                    raise ApplicationError("sandbox could not be activated", non_retryable=True)
            outcome = cast(
                SandboxJobOutcome,
                await workflow.execute_activity(
                    names.RUN_SANDBOX_JOB,
                    SandboxJobRequest(
                        run_id=state.run_id,
                        call=call,
                        template=self._carry.binding.sandbox_template,
                        snapshot_ref=self._sandbox_snapshot_ref,
                        handle=self._sandbox_handle,
                        browser=self._carry.binding.browser,
                    ),
                    result_type=SandboxJobOutcome,
                    start_to_close_timeout=_SANDBOX_JOB_TIMEOUT,
                    heartbeat_timeout=_SANDBOX_JOB_HEARTBEAT,
                    retry_policy=_SANDBOX_JOB_RETRY,
                ),
            )
            previous_handle = self._sandbox_handle
            previous_snapshot = self._sandbox_snapshot_ref
            self._sandbox_snapshot_ref = outcome.snapshot_ref
            if outcome.handle is not None:
                self._sandbox_handle = outcome.handle
                self._sandbox_status = "active"
            if outcome.handle is not None and (
                previous_handle is None or previous_handle.sandbox_id != outcome.handle.sandbox_id
            ):
                self._sandbox_generation += 1
                event_type: RunEventType = (
                    "environment_provisioned"
                    if previous_handle is None and previous_snapshot is None
                    else "environment_restored"
                )
                await self._emit(
                    event_type,
                    payload={
                        "execution_status": "active",
                        "sandbox_id": outcome.handle.sandbox_id,
                        "sandbox_provider": outcome.handle.provider,
                        "sandbox_template": outcome.handle.template,
                        "generation": self._sandbox_generation,
                        "checkpoint_id": self._sandbox_checkpoint_id,
                        "snapshot_ref": (
                            previous_snapshot.model_dump(mode="json")
                            if previous_snapshot is not None
                            else None
                        ),
                    },
                )
            return outcome.result_ref
        tool_outcome = cast(
            PromotedToolOutcome,
            await workflow.execute_activity(
                names.RUN_PROMOTED_TOOL,
                PromotedToolRequest(
                    run_id=state.run_id,
                    call=call,
                    endpoint_url=str(
                        self._carry.binding.connector_endpoints.get("data_plane", "")
                    ),
                ),
                result_type=PromotedToolOutcome,
                start_to_close_timeout=_PROMOTED_TOOL_TIMEOUT,
                heartbeat_timeout=_PROMOTED_TOOL_HEARTBEAT,
                retry_policy=_PROMOTED_TOOL_RETRY,
            ),
        )
        # Patch-marker keeps event insertion replay-safe for runs whose
        # histories predate the recorded stand-in result.
        if (
            workflow.patched("simulated-promoted-effect-event-v1")
            and self._carry.binding.kind == "sandbox"
            and tool_outcome.simulated_result is not None
        ):
            await self._emit(
                "simulated_effect",
                payload={
                    "tool_id": tool_outcome.tool_id,
                    "idempotency_key": tool_outcome.idempotency_key,
                    "replayed": tool_outcome.replayed,
                    "result_ref": tool_outcome.result_ref.model_dump(mode="json"),
                    "result": tool_outcome.simulated_result,
                },
            )
        return tool_outcome.result_ref

    async def _hibernate_sandbox(self, reason: SandboxReleaseReason) -> None:
        """Checkpoint active compute and release it at a workflow boundary.

        The last snapshot and opaque handle both live in durable workflow
        state. The activity writes its own idempotency marker before destroy,
        so a Temporal retry cannot lose the checkpoint or double-release.
        """

        assert self._state is not None
        if not self._sandbox_lifecycle_enabled:
            return
        handle = self._sandbox_handle
        if handle is None:
            if reason != "pause" and self._sandbox_status == "hibernated":
                self._sandbox_status = "terminated"
                await self._emit(
                    "environment_terminated",
                    payload={
                        "execution_status": "terminated",
                        "reason": reason,
                        "sandbox_id": None,
                        "generation": self._sandbox_generation,
                        "checkpoint_id": self._sandbox_checkpoint_id,
                        "snapshot_ref": (
                            self._sandbox_snapshot_ref.model_dump(mode="json")
                            if self._sandbox_snapshot_ref is not None
                            else None
                        ),
                    },
                )
            return

        self._sandbox_checkpoint_seq += 1
        checkpoint_id = f"checkpoint-{self._sandbox_checkpoint_seq}"
        outcome = cast(
            SandboxHibernateOutcome,
            await workflow.execute_activity(
                names.HIBERNATE_SANDBOX,
                SandboxHibernateRequest(
                    run_id=self._state.run_id,
                    checkpoint_id=checkpoint_id,
                    reason=reason,
                    handle=handle,
                    snapshot_ref=self._sandbox_snapshot_ref,
                ),
                result_type=SandboxHibernateOutcome,
                start_to_close_timeout=_SANDBOX_LIFECYCLE_TIMEOUT,
                retry_policy=_SANDBOX_LIFECYCLE_RETRY,
            ),
        )
        self._sandbox_snapshot_ref = outcome.snapshot_ref
        self._sandbox_checkpoint_id = outcome.checkpoint_id
        self._sandbox_handle = None
        await self._emit(
            "environment_checkpointed",
            payload={
                "execution_status": "checkpointed",
                "checkpoint_id": outcome.checkpoint_id,
                "checkpoint_sequence": self._sandbox_checkpoint_seq,
                "reason": reason,
                "released_sandbox_id": outcome.released_sandbox_id,
                "generation": self._sandbox_generation,
                "snapshot_ref": (
                    outcome.snapshot_ref.model_dump(mode="json")
                    if outcome.snapshot_ref is not None
                    else None
                ),
            },
        )
        self._sandbox_status = "hibernated" if reason == "pause" else "terminated"
        await self._emit(
            "environment_hibernated" if reason == "pause" else "environment_terminated",
            payload={
                "execution_status": self._sandbox_status,
                "reason": reason,
                "sandbox_id": None,
                "released_sandbox_id": outcome.released_sandbox_id,
                "generation": self._sandbox_generation,
                "checkpoint_id": outcome.checkpoint_id,
                "snapshot_ref": (
                    outcome.snapshot_ref.model_dump(mode="json")
                    if outcome.snapshot_ref is not None
                    else None
                ),
            },
        )

    async def _activate_sandbox(self) -> None:
        """Provision first-use compute or restore a hibernated checkpoint."""

        assert self._state is not None
        if not self._sandbox_lifecycle_enabled:
            return
        if self._sandbox_handle is not None or self._sandbox_status == "terminated":
            return
        previous_status = self._sandbox_status
        self._sandbox_generation += 1
        restore_id = f"restore-{self._sandbox_generation}"
        outcome = cast(
            SandboxRestoreOutcome,
            await workflow.execute_activity(
                names.RESTORE_SANDBOX,
                SandboxRestoreRequest(
                    run_id=self._state.run_id,
                    restore_id=restore_id,
                    template=self._carry.binding.sandbox_template,
                    snapshot_ref=self._sandbox_snapshot_ref,
                    browser=self._carry.binding.browser,
                ),
                result_type=SandboxRestoreOutcome,
                start_to_close_timeout=_SANDBOX_LIFECYCLE_TIMEOUT,
                retry_policy=_SANDBOX_LIFECYCLE_RETRY,
            ),
        )
        self._sandbox_handle = outcome.handle
        self._sandbox_status = "active"
        event_type: RunEventType = (
            "environment_provisioned"
            if previous_status == "unprovisioned" and self._sandbox_snapshot_ref is None
            else "environment_restored"
        )
        await self._emit(
            event_type,
            payload={
                "execution_status": "active",
                "restore_id": outcome.restore_id,
                "sandbox_id": outcome.handle.sandbox_id,
                "sandbox_provider": outcome.handle.provider,
                "sandbox_template": outcome.handle.template,
                "generation": self._sandbox_generation,
                "checkpoint_id": self._sandbox_checkpoint_id,
                "snapshot_ref": (
                    outcome.snapshot_ref.model_dump(mode="json")
                    if outcome.snapshot_ref is not None
                    else None
                ),
            },
        )

    async def _compact_step_end(self, state: RunState, step: PlanStep, result: TurnResult) -> None:
        """Distill the completed step's working transcript into its step
        summary; the raw transcript archive is untouched and its ref rides
        the summary. Guarded by a patch marker so histories recorded before
        compaction existed still replay."""
        if not workflow.patched("m4-step-compaction"):
            return
        compact = cast(
            CompactResult,
            await workflow.execute_activity(
                names.COMPACT_STEP,
                CompactRequest(
                    boundary="step_end",
                    run_id=state.run_id,
                    step_id=step.id,
                    transcript_ref=result.transcript_ref,
                    step_description=step.description,
                    outputs=list(result.step_outputs),
                ),
                result_type=CompactResult,
                start_to_close_timeout=_COMPACT_TIMEOUT,
                retry_policy=_COMPACT_RETRY,
            ),
        )
        summary = compact.summary
        if summary is not None:
            state.step_summaries.append(
                StepSummaryRef(
                    step_id=summary.step_id,
                    headline=summary.headline,
                    summary_ref=summary.summary_ref,
                    transcript_ref=summary.transcript_ref,
                )
            )
        await self._emit(
            "compaction_applied",
            payload={
                "boundary": "step_end",
                "step_id": step.id,
                "summary_ref": (summary.summary_ref.model_dump(mode="json") if summary else None),
                "archived_ref": compact.archived_ref.model_dump(mode="json"),
                "headline": summary.headline if summary else None,
            },
        )

    async def _maybe_fold_midstep(self, state: RunState, step: PlanStep) -> None:
        """Mid-step compaction: once the step's working transcript passes the
        token threshold, fold older turns into the rolling progress note.
        The pre-fold transcript stays archived; the folded head links back to
        it. The trigger is computed from recorded token counts, so replay
        makes the identical decision."""
        if state.working_transcript_ref is None:
            return
        if self._step_tokens < self._carry.tuning.midstep_compaction_tokens:
            return
        self._fold_seq += 1
        compact = cast(
            CompactResult,
            await workflow.execute_activity(
                names.COMPACT_STEP,
                CompactRequest(
                    boundary="mid_step",
                    run_id=state.run_id,
                    step_id=step.id,
                    transcript_ref=state.working_transcript_ref,
                    keep_recent_turns=self._carry.tuning.keep_recent_turns,
                    fold_index=self._fold_seq,
                ),
                result_type=CompactResult,
                start_to_close_timeout=_COMPACT_TIMEOUT,
                retry_policy=_COMPACT_RETRY,
            ),
        )
        if compact.working_ref is not None:
            state.working_transcript_ref = compact.working_ref
        self._step_tokens = 0
        await self._emit(
            "compaction_applied",
            payload={
                "boundary": "mid_step",
                "step_id": step.id,
                "working_ref": (
                    compact.working_ref.model_dump(mode="json") if compact.working_ref else None
                ),
                "archived_ref": compact.archived_ref.model_dump(mode="json"),
                "fold_index": self._fold_seq,
            },
        )

    def _step_done_payload(
        self, state: RunState, step: PlanStep, result: TurnResult
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step_id": step.id,
            "outcome": result.outcome,
            "model_used": result.model_used,
            "model_requested": state.agent.model,
            "model_fallback": result.model_used != state.agent.model,
            "cost_usd": str(result.cost_usd),
            "budget": self._budget_snapshot(),
        }
        if self._sandbox_snapshot_ref is not None:
            # The workspace truth after this step's sandbox jobs, so the
            # snapshot chain is externally observable.
            payload["sandbox_snapshot_ref"] = self._sandbox_snapshot_ref.model_dump(mode="json")
        return payload

    # -------------------------------------------------------------- fan-out

    async def _run_fanout_group(self, state: RunState, first_ready: PlanStep) -> RunResult | None:
        """Spawn the ready members of a fan-out group as child workflows and
        gather their results as each lands. Returns a terminal RunResult only
        when the group's failure handling fails the run.

        Concurrency stays within min(policy.max_parallel, agent.max_children).
        Reservations move on spawn and refund on landing; when committed
        money reaches the cap, spawning stops and the loop hands control back
        so budget enforcement takes its policy action. Pause is edge-triggered
        here exactly as at the turn boundary: it stops further spawns but
        never cancels an in-flight child — landings keep being absorbed, and
        the run only parks once nothing is in flight, leaving unspawned
        members ready for after the resume. A land request forwards to every
        in-flight child (they wrap up as landed_partial and their results
        still land here) and abandons unspawned members to the land report.
        Once every member is terminal, the group partial-failure policy
        decides how the join proceeds.
        """
        assert state.plan is not None
        group = _collect_group(state.plan, first_ready)
        errors = self._group_spawn_errors(state, group)
        if errors:
            # A spawn-time violation (depth, size, missing spec) is a plan
            # bug: reject deterministically and fail loudly.
            for member in group.to_spawn:
                member.status = "failed"
                await self._emit(
                    "step_failed",
                    payload={
                        "step_id": member.id,
                        "reason": "fan-out spawn rejected",
                        "errors": errors,
                    },
                )
            state.status = "failed"
            await self._hibernate_sandbox("failed")
            await self._emit(
                "run_failed",
                payload={
                    "run_status": "failed",
                    "reason": "fan-out spawn rejected",
                    "errors": errors,
                },
            )
            return RunResult(
                run_id=state.run_id,
                status="failed",
                error="fan-out spawn rejected: " + "; ".join(errors),
            )

        gather = _Gather(
            window=max(1, min(state.policy.max_parallel, state.agent.max_children)),
            queue=list(group.to_spawn),
        )

        def _wake() -> bool:
            return (
                bool(self._child_landings)
                or bool(self._steer_inbox)
                or (self._landing and not gather.land_signalled)
                or self._can_spawn(gather)
            )

        while gather.queue or gather.in_flight:
            if self._landing and not gather.land_signalled:
                # Land cascade: forward the land request once to every child
                # still in flight; they wrap up and their results still join.
                gather.land_signalled = True
                for flight in gather.in_flight.values():
                    await flight.handle.signal(SubagentWorkflow.land, self._land_actor)
            while self._can_spawn(gather):
                member = gather.queue.pop(0)
                gather.in_flight[member.id] = await self._spawn_child(state, member, group.label)
            if not gather.in_flight:
                if self._landing:
                    break  # unspawned members are abandoned to the land report
                if self._paused:
                    # Nothing in flight: park here so pause/resume events fire
                    # at the boundary, then continue spawning the remainder.
                    await self._control_boundary()
                    continue
                if self._reservations_blocked():
                    # Committed money reached the cap mid-group: leave the
                    # remaining members ready and let budget enforcement act.
                    break
            await workflow.wait_condition(_wake)
            await self._drain_steer_mailbox()
            while self._child_landings:
                landing = self._child_landings.pop(0)
                flight = gather.in_flight.pop(landing.step_id)
                await self._absorb_child_landing(state, flight, landing)

        if self._landing or gather.queue:
            # Landing, a mid-group pause that turned into landing, or a
            # budget stop: the group is not settled, so no policy decision.
            return None
        return await self._settle_group(state, group)

    def _can_spawn(self, gather: "_Gather") -> bool:
        """Another member may spawn: one is queued, the concurrency window
        has room, and no pause/land/budget stop is in effect."""
        return (
            bool(gather.queue)
            and len(gather.in_flight) < gather.window
            and not self._paused
            and not self._landing
            and not self._reservations_blocked()
        )

    def _group_spawn_errors(self, state: RunState, group: _GroupPlan) -> list[str]:
        """Deterministic spawn-time checks: the whole group fits under the
        agent's child cap, every member has a spec, and each child sits
        exactly one layer below its parent within the policy depth."""
        errors: list[str] = []
        max_children = state.agent.max_children
        if len(group.members) > max_children:
            errors.append(
                f"group {group.label!r} has {len(group.members)} members, "
                f"exceeding max_children={max_children}"
            )
        expected_layer = state.agent.layer + 1
        for member in group.to_spawn:
            spec = member.subagent
            if spec is None:
                errors.append(f"step {member.id!r} has no subagent spec")
                continue
            if spec.layer != expected_layer:
                errors.append(
                    f"step {member.id!r} subagent layer {spec.layer} "
                    f"must be {expected_layer} (one below its parent)"
                )
            if spec.layer > state.policy.max_depth:
                errors.append(
                    f"step {member.id!r} subagent layer {spec.layer} "
                    f"exceeds max_depth={state.policy.max_depth}"
                )
        return errors

    def _reservations_blocked(self) -> bool:
        """No room to carve another reservation: committed (spent + reserved)
        has reached the cap."""
        assert self._state is not None
        budget = self._state.budget
        return budget.cap_usd > 0 and budget.spent_usd + budget.reserved_usd >= budget.cap_usd

    async def _spawn_child(self, state: RunState, member: PlanStep, label: str) -> _ChildFlight:
        """Start one member's child workflow: carve its reservation, emit the
        linkage events, and hand back the in-flight record.

        The slice is clamped to the budget actually available so committed
        money can never exceed the cap at spawn; the clamped value is both
        the reservation and the child's own hard cap. The child's tools are
        the intersection of what its spec requests and what the parent
        effectively holds — delegation can only narrow.
        """
        assert state.plan is not None
        spec = member.subagent
        assert spec is not None  # spawn checks ran before any spawn
        member.status = "running"
        member.attempt += 1
        await self._emit(
            "step_started",
            payload={"step_id": member.id, "attempt": member.attempt, "executor": "subagent"},
        )
        budget = state.budget
        slice_requested = member.budget_slice if member.budget_slice is not None else Decimal(0)
        available = budget.cap_usd - budget.spent_usd - budget.reserved_usd
        slice_reserved = min(slice_requested, max(available, Decimal(0)))
        budget.reserved_usd += slice_reserved
        child_run_id = f"{state.run_id}--{member.id}-a{member.attempt}"
        child_agent = spec.model_copy(
            update={
                "parent_id": state.agent.id,
                "tools": intersect_grants(spec.tools, state.agent.tools),
            }
        )
        brief = SubagentBrief(
            run_id=child_run_id,
            parent_run_id=state.run_id,
            tenant_id=state.tenant_id,
            environment_id=state.environment_id,
            binding_ref=state.binding_ref,
            step_id=member.id,
            group_id=member.group_id,
            goal=member.description,
            input_refs=_dependency_outputs(state.plan, member),
            agent=child_agent,
            spawned_by=state.agent.id,
            budget_cap_usd=slice_reserved,
        )
        # The spawn event lands before the child starts so its run row exists
        # by the time the child's own events arrive in projections.
        await self._emit(
            "child_spawned",
            actor=state.agent.id,
            actor_type="agent",
            payload={
                "child_run_id": child_run_id,
                "step_id": member.id,
                "group_id": label,
                "layer": child_agent.layer,
                "goal": member.description,
                "budget_slice": str(slice_requested),
                "budget_reserved": str(slice_reserved),
                "budget": self._budget_snapshot(),
            },
        )
        handle = await workflow.start_child_workflow(
            SubagentWorkflow.run,
            brief,
            id=child_run_id,
            parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL,
            execution_timeout=_CHILD_EXECUTION_TIMEOUT,
            retry_policy=_CHILD_WORKFLOW_RETRY,
        )
        flight = _ChildFlight(
            step_id=member.id,
            child_run_id=child_run_id,
            group_label=label,
            slice_requested=slice_requested,
            slice_reserved=slice_reserved,
            handle=handle,
        )
        flight.gather = asyncio.ensure_future(self._gather_child(flight))
        return flight

    async def _gather_child(self, flight: _ChildFlight) -> None:
        """Await one child and queue its landing; arrival order is the order
        the loop absorbs them in."""
        try:
            result = await flight.handle
        except ChildWorkflowError as error:
            cause = error.__cause__
            detail = str(cause) if cause is not None else str(error)
            self._child_landings.append(_ChildLanding(step_id=flight.step_id, error=detail[:500]))
        else:
            self._child_landings.append(_ChildLanding(step_id=flight.step_id, result=result))

    async def _absorb_child_landing(
        self, state: RunState, flight: _ChildFlight, landing: _ChildLanding
    ) -> None:
        """Fold one landed child into run state: refund the unspent slice,
        add its actual cost to spend, keep only its compacted result (as a
        step summary ref — never the transcript), and move its step.
        """
        assert state.plan is not None
        step = next(s for s in state.plan.steps if s.id == flight.step_id)
        budget = state.budget
        result = landing.result
        budget.reserved_usd -= flight.slice_reserved
        if result is not None:
            cost = result.cost_usd
            cost_assumed = False
        else:
            # The child workflow died without reporting. Its real spend is
            # unknown but was capped at its slice, so the whole reservation
            # counts as spent — budget truth never understates.
            cost = flight.slice_reserved
            cost_assumed = True
        budget.spent_usd += cost
        refund = max(flight.slice_reserved - cost, Decimal(0))
        summary_ref: ArtifactRef | None = None
        if result is not None:
            self._tokens = TokenCounts(
                input_tokens=self._tokens.input_tokens + result.tokens.input_tokens,
                output_tokens=self._tokens.output_tokens + result.tokens.output_tokens,
            )
            summary_ref = cast(
                ArtifactRef,
                await workflow.execute_activity(
                    names.ARCHIVE_SUBAGENT_RESULT,
                    args=[state.run_id, result],
                    result_type=ArtifactRef,
                    start_to_close_timeout=_ARCHIVE_RESULT_TIMEOUT,
                    retry_policy=_ARCHIVE_RESULT_RETRY,
                ),
            )
            state.step_summaries.append(
                StepSummaryRef(
                    step_id=step.id,
                    headline=result.headline,
                    summary_ref=summary_ref,
                    transcript_ref=result.transcript_ref,
                )
            )
        if result is not None and result.status == "done":
            step.status = "done"
            step.outputs = list(result.outputs)
        elif result is not None and result.status == "landed_partial" and self._landing:
            # A land-cascade wrap-up: like a self step interrupted by
            # landing, the step stays in flight and the land report counts it
            # incomplete; its partial outputs stay visible on the step.
            step.outputs = list(result.outputs)
        else:
            # A failed child, a child that ran out of its slice mid-run, or a
            # child workflow that died: the member failed and the group's
            # partial-failure policy decides what happens at the join.
            step.status = "failed"
            step.error_ref = result.error_ref if result is not None else None
        await self._emit(
            "child_landed",
            payload={
                "child_run_id": flight.child_run_id,
                "step_id": step.id,
                "group_id": flight.group_label,
                "status": result.status if result is not None else "failed",
                "headline": result.headline if result is not None else None,
                "error": landing.error,
                "cost_usd": str(cost),
                "cost_assumed": cost_assumed,
                "slice_reserved": str(flight.slice_reserved),
                "refund_usd": str(refund),
                "summary_ref": summary_ref.model_dump(mode="json") if summary_ref else None,
                "budget": self._budget_snapshot(),
            },
        )
        if step.status == "done":
            await self._emit(
                "step_done",
                payload={
                    "step_id": step.id,
                    "outcome": "subagent",
                    "cost_usd": str(cost),
                    "budget": self._budget_snapshot(),
                },
            )
        elif step.status == "failed":
            await self._emit(
                "step_failed",
                payload={
                    "step_id": step.id,
                    "reason": landing.error
                    or (result.headline if result is not None else "child failed"),
                },
            )
        # A child overshooting its slice can push committed money past the
        # cap; declare exhaustion at this boundary — never silently — while
        # the policy action itself still fires at the loop boundary.
        if (
            budget.cap_usd > 0
            and budget.spent_usd + budget.reserved_usd >= budget.cap_usd
            and not self._budget_exhausted_emitted
        ):
            self._budget_exhausted_emitted = True
            await self._emit(
                "budget_exhausted",
                payload={
                    "action": state.policy.on_budget_exhausted,
                    "budget": self._budget_snapshot(),
                    "policy": {"on_budget_exhausted": state.policy.on_budget_exhausted},
                },
            )

    async def _settle_group(self, state: RunState, group: _GroupPlan) -> RunResult | None:
        """Apply the partial-failure policy once every member is terminal.

        No failures: the join becomes ready through normal dependency
        recompute. join_with_partials: failed members are released so the
        join proceeds, with the gap flagged on the join's start event and
        fed to the join turn as a note. block_on_human: the join opens a
        gate; answering it accepts the gap. fail_group: the join fails and
        normal failed-step semantics fail the run. A solo subagent step
        (no group) failing follows normal failed-step semantics directly.
        """
        assert state.plan is not None
        failures = [
            m for m in group.members if m.status == "failed" and m.id not in self._released_failures
        ]
        if not failures:
            return None
        failed_ids = [m.id for m in failures]
        if group.members[0].group_id is None:
            state.status = "failed"
            await self._hibernate_sandbox("failed")
            await self._emit(
                "run_failed", payload={"run_status": "failed", "reason": "step failed"}
            )
            return RunResult(
                run_id=state.run_id, status="failed", error=f"step {failed_ids[0]} failed"
            )

        policy = state.policy.on_group_partial_failure
        join = _find_join(state.plan, group.members)
        if policy == "join_with_partials" or (policy == "block_on_human" and join is None):
            self._released_failures.update(failed_ids)
            if join is not None:
                gaps = self._join_gaps.setdefault(join.id, [])
                gaps.extend(i for i in failed_ids if i not in gaps)
            self._group_note_seq += 1
            state.pending_steers.append(
                SteerMessage(
                    id=f"group-gap-{group.label}-{self._group_note_seq}",
                    author="system",
                    author_id="convoy-runtime",
                    mode="note",
                    body=(
                        f"Fan-out group {group.label} finished with failed members: "
                        f"{', '.join(failed_ids)}. Proceeding with partial results; "
                        "account for the gap."
                    ),
                )
            )
            return None
        if policy == "block_on_human":
            assert join is not None
            gaps = self._join_gaps.setdefault(join.id, [])
            gaps.extend(i for i in failed_ids if i not in gaps)
            if join.status != "running":
                join.status = "running"
                join.attempt += 1
                await self._emit(
                    "step_started",
                    payload={
                        "step_id": join.id,
                        "attempt": join.attempt,
                        "joined_with_failures": list(failed_ids),
                    },
                )
            self._pending_gap_release[join.id] = list(failed_ids)
            gate = HumanGate(
                kind="approval",
                prompt=(
                    f"Fan-out group {group.label} finished with failed members: "
                    f"{', '.join(failed_ids)}. Respond to continue the join with "
                    "partial results, or revise the plan first."
                ),
            )
            await self._open_gate(join, gate, actor="system", actor_type="system")
            return None
        # fail_group: the join fails; normal failed-step semantics apply.
        if join is not None:
            join.status = "failed"
            await self._emit(
                "step_failed",
                payload={
                    "step_id": join.id,
                    "reason": "fan-out group failed",
                    "failed_members": list(failed_ids),
                },
            )
        state.status = "failed"
        await self._hibernate_sandbox("failed")
        await self._emit(
            "run_failed",
            payload={
                "run_status": "failed",
                "reason": "fan-out group failed",
                "failed_members": list(failed_ids),
            },
        )
        return RunResult(
            run_id=state.run_id,
            status="failed",
            error=f"fan-out group {group.label} failed: {', '.join(failed_ids)}",
        )

    # -------------------------------------------------------------- helpers

    def _budget_snapshot(self) -> dict[str, str]:
        assert self._state is not None
        budget = self._state.budget
        return {
            "cap_usd": str(budget.cap_usd),
            "spent_usd": str(budget.spent_usd),
            "reserved_usd": str(budget.reserved_usd),
        }

    def _turn_context(self, state: RunState, resume: PromotedResume | None = None) -> TurnContext:
        return TurnContext(
            agent=state.agent,
            binding_ref=state.binding_ref,
            turn=state.turn_count + 1,
            resume=resume,
        )

    def _turn_input(self, state: RunState, step: PlanStep) -> TurnInput:
        steers = list(state.pending_steers)
        state.pending_steers = []  # the mailbox drains into the turn
        return TurnInput(
            run_id=state.run_id,
            step_id=step.id,
            pinned_ref=state.pinned_ref,
            step_summaries=list(state.step_summaries),
            working_transcript_ref=state.working_transcript_ref,
            steers=steers,
            budget_remaining=state.budget.cap_usd
            - state.budget.spent_usd
            - state.budget.reserved_usd,
            now=self._clock.now(),
        )

    def _absorb_turn_accounting(self, state: RunState, result: TurnResult) -> None:
        """Book one turn result's cost and tokens — including the partial
        results a promoting turn produces before its final outcome."""
        state.budget.spent_usd += result.cost_usd
        self._tokens = TokenCounts(
            input_tokens=self._tokens.input_tokens + result.tokens.input_tokens,
            output_tokens=self._tokens.output_tokens + result.tokens.output_tokens,
        )
        self._step_tokens += result.tokens.input_tokens + result.tokens.output_tokens

    def _apply_turn(self, state: RunState, step: PlanStep, result: TurnResult) -> None:
        """Only the workflow mutates the plan; the activity returned a
        proposal. The final result of a logical turn moves the step and the
        transcript head; accounting was absorbed per partial result."""
        self._absorb_turn_accounting(state, result)
        state.turn_count += 1
        if result.outcome == "step_done":
            step.status = "done"
            step.outputs = list(result.step_outputs)
            # The working transcript is per step; a fresh step starts clean.
            state.working_transcript_ref = None
            self._step_tokens = 0
        elif result.outcome in ("continue", "needs_human", "propose_revision"):
            # The step stays in flight; its transcript head advances so the
            # next turn (after any gate answer or revision) resumes it.
            state.working_transcript_ref = result.transcript_ref
        else:
            # Fan-out is plan-driven (steps with executor "subagent"), so a
            # turn-level spawn_group outcome has no path; promote never
            # reaches here — the promotion loop consumes it.
            raise ApplicationError(
                f"turn outcome {result.outcome!r} is not supported yet",
                non_retryable=True,
            )

    async def _emit(
        self,
        event_type: RunEventType,
        *,
        payload: dict[str, Any] | None = None,
        actor: str = "system",
        actor_type: _ActorType = "system",
    ) -> None:
        """Every state change emits a RunEvent through the outbox activity.
        Event identity is deterministic: {run_id}:{seq}, with the sequence
        carried across hops so the outbox's (run_id, seq) idempotency holds
        for the run's whole life. Events carry dual stamps — real time
        always, virtual time whenever the run has a virtual clock — and the
        sandbox flag so rehearsal trajectories are never mistaken for
        production."""
        assert self._state is not None
        self._event_seq += 1
        virtual = self._virtual_now()
        if virtual is not None:
            self._state.virtual_now = virtual
        event = RunEvent(
            id=f"{self._state.run_id}:{self._event_seq}",
            run_id=self._state.run_id,
            tenant_id=self._state.tenant_id,
            seq=self._event_seq,
            type=event_type,
            ts=self._real_clock.now(),
            virtual_ts=virtual,
            sandbox=self._carry.binding.kind == "sandbox",
            actor=actor,
            actor_type=actor_type,
            payload=payload or {},
        )
        await workflow.execute_activity(
            names.EMIT_RUN_EVENTS,
            [event],
            start_to_close_timeout=_OUTBOX_TIMEOUT,
            retry_policy=_OUTBOX_RETRY,
        )
