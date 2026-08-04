"""AgentRunWorkflow — the durable outer loop (DESIGN.md section 8.1, M0 skeleton).

Deterministic orchestration only: no I/O, no clock reads outside `RunClock`,
no randomness (CLAUDE.md rules 1 and 13). Activities are scheduled by name so
the workflow sandbox never imports I/O clients. Every activity call declares
an explicit retry policy and timeouts (CLAUDE.md conventions).
"""

from datetime import timedelta
from typing import Any, Literal, cast

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from convoy_core import (
        LandReport,
        Plan,
        PlanStep,
        RunEvent,
        RunEventType,
        RunResult,
        RunState,
        TurnInput,
        TurnResult,
    )
    from convoy_runtime.activities import names
    from convoy_runtime.clock import PassthroughClock, RunClock

# Explicit per-activity retry/timeout matrix (values tuned in later milestones,
# DESIGN.md section 18 — the *shape* is fixed: no defaults-by-omission).
_CREATE_PLAN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_CREATE_PLAN_TIMEOUT = timedelta(seconds=60)

_RUN_TURN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)
_RUN_TURN_TIMEOUT = timedelta(seconds=120)
_RUN_TURN_HEARTBEAT = timedelta(seconds=60)

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


def _recompute_ready(plan: Plan) -> None:
    """pending -> ready when all depends_on are terminal-successful (DESIGN §6.1)."""
    terminal_ok = {s.id for s in plan.steps if s.status in ("done", "skipped")}
    for step in plan.steps:
        if step.status == "pending" and all(d in terminal_ok for d in step.depends_on):
            step.status = "ready"


def _next_step(plan: Plan) -> PlanStep | None:
    """The step to work: an in-flight step first (serialized self-execution),
    else the first ready one."""
    for step in plan.steps:
        if step.status == "running":
            return step
    for step in plan.steps:
        if step.status == "ready":
            return step
    return None


def _plan_complete(plan: Plan) -> bool:
    return all(s.status in ("done", "skipped") for s in plan.steps)


@workflow.defn
class AgentRunWorkflow:
    def __init__(self) -> None:
        # All workflow time flows through the RunClock seam (CLAUDE.md rule 13).
        # TODO(milestone-4): select VirtualClock for sandbox bindings (DESIGN §8.4).
        self._clock: RunClock = PassthroughClock()
        self._state: RunState | None = None
        self._paused = False
        self._landing = False
        self._pause_actor = "system"
        self._resume_actor = "system"
        self._land_actor = "system"
        self._event_seq = 0

    # ------------------------------------------------------------------ run

    @workflow.run
    async def run(self, state: RunState, requested_by: str) -> RunResult:
        self._state = state
        await self._emit(
            "run_started",
            actor=requested_by,
            actor_type="human",
            payload={"run_status": state.status, "environment_id": state.environment_id},
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
            await self._emit(
                "plan_created",
                payload={
                    "plan": state.plan.model_dump(mode="json"),
                    "plan_version": state.plan.version,
                    "run_status": "running",
                },
            )
            # TODO(milestone-2): plan approval gate — awaiting_approval status,
            # approve_plan signal, approval_scope on revisions (DESIGN §6.2).

        state.status = "running"

        while not _plan_complete(state.plan):
            await self._control_boundary()
            if self._landing:
                break

            # TODO(milestone-1): enforce_budget() — thresholds + on_budget_exhausted.

            _recompute_ready(state.plan)
            step = _next_step(state.plan)
            if step is None:
                # No ready or running step and the plan is incomplete: with
                # M0's linear fixture this is unreachable; fail loudly if not.
                state.status = "failed"
                await self._emit(
                    "run_failed",
                    payload={"run_status": "failed", "reason": "plan deadlock"},
                )
                return RunResult(run_id=state.run_id, status="failed", error="plan deadlock")

            if step.status != "running":
                step.status = "running"
                step.attempt += 1
                await self._emit(
                    "step_started", payload={"step_id": step.id, "attempt": step.attempt}
                )

            # TODO(milestone-3): executor == "subagent" fan-out (spawn_children).
            try:
                result = cast(
                    TurnResult,
                    await workflow.execute_activity(
                        names.RUN_TURN,
                        self._turn_input(state, step),
                        result_type=TurnResult,
                        start_to_close_timeout=_RUN_TURN_TIMEOUT,
                        heartbeat_timeout=_RUN_TURN_HEARTBEAT,
                        retry_policy=_RUN_TURN_RETRY,
                    ),
                )
            except ActivityError:
                step.status = "failed"
                state.status = "failed"
                await self._emit("step_failed", payload={"step_id": step.id})
                await self._emit(
                    "run_failed", payload={"run_status": "failed", "reason": "step failed"}
                )
                # TODO(milestone-2): audited retry_step path instead of run failure.
                return RunResult(
                    run_id=state.run_id, status="failed", error=f"step {step.id} failed"
                )

            # TODO(milestone-4): promoted-tool loop while outcome == "promote"
            # (own activity, idempotency key, re-enter run_turn with the result).
            self._apply_turn(state, step, result)
            if result.outcome == "step_done":
                await self._emit(
                    "step_done",
                    payload={
                        "step_id": step.id,
                        "outcome": result.outcome,
                        "model_used": result.model_used,
                        "cost_usd": str(result.cost_usd),
                    },
                )

            # TODO(milestone-4): continue_as_new at TURN_LIMIT with RunState carry.

        state.status = "landing"
        await self._emit(
            "landing_started",
            actor=self._land_actor if self._landing else "system",
            actor_type="human" if self._landing else "system",
            payload={"run_status": "landing"},
        )
        report = cast(
            LandReport,
            await workflow.execute_activity(
                names.LAND_RUN,
                state,
                result_type=LandReport,
                start_to_close_timeout=_LAND_RUN_TIMEOUT,
                retry_policy=_LAND_RUN_RETRY,
            ),
        )
        state.status = "completed"
        await self._emit(
            "run_completed",
            payload={
                "run_status": "completed",
                "land_report": report.model_dump(mode="json"),
            },
        )
        return RunResult(run_id=state.run_id, status="completed", land_report=report)

    # -------------------------------------------------------------- signals
    # Signals do no heavy work: validate, enqueue, return (CLAUDE.md rule 6).
    # The loop drains at its boundary.

    @workflow.signal
    def pause(self, actor: str = "unknown") -> None:
        if self._landing:
            return  # landing takes precedence (DESIGN §6.2)
        self._paused = True
        self._pause_actor = actor

    @workflow.signal
    def resume(self, actor: str = "unknown") -> None:
        # Clears the paused flag only; idempotent; non-cascading (DESIGN §7).
        self._paused = False
        self._resume_actor = actor

    @workflow.signal
    def land(self, actor: str = "unknown") -> None:
        self._landing = True
        self._land_actor = actor

    # TODO(milestone-2): steer, approve_plan, human_response signals.

    # -------------------------------------------------------------- queries

    @workflow.query
    def get_status(self) -> str:
        # Precedence: landing > paused > derived-from-steps (DESIGN §6.2).
        if self._state is None:
            return "planning"
        if self._state.status in ("completed", "failed"):
            return self._state.status
        if self._landing:
            return "landing"
        if self._paused:
            return "paused"
        return self._state.status

    @workflow.query
    def get_plan(self) -> Plan | None:
        return self._state.plan if self._state else None

    # -------------------------------------------------------------- helpers

    async def _control_boundary(self) -> None:
        """Edge-triggered pause at the loop boundary — never cancels an
        in-flight activity (CLAUDE.md rule 7)."""
        assert self._state is not None
        if self._paused and not self._landing:
            self._state.status = "paused"
            await self._emit(
                "paused",
                actor=self._pause_actor,
                actor_type="human",
                payload={"run_status": "paused"},
            )
            await workflow.wait_condition(lambda: not self._paused or self._landing)
            if not self._landing:
                self._state.status = "running"
                await self._emit(
                    "resumed",
                    actor=self._resume_actor,
                    actor_type="human",
                    payload={"run_status": "running"},
                )

    def _turn_input(self, state: RunState, step: PlanStep) -> TurnInput:
        steers = list(state.pending_steers)
        state.pending_steers = []  # mailbox drains into the turn (DESIGN §9)
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

    def _apply_turn(self, state: RunState, step: PlanStep, result: TurnResult) -> None:
        """Only the workflow mutates the plan; activities returned a proposal
        (CLAUDE.md rule 3)."""
        state.turn_count += 1
        state.budget.spent_usd += result.cost_usd  # accounting only; TODO(milestone-1) thresholds
        state.working_transcript_ref = result.transcript_ref
        if result.outcome == "step_done":
            step.status = "done"
            step.outputs = list(result.step_outputs)
        elif result.outcome == "continue":
            pass  # same step continues next turn
        else:
            # needs_human / propose_revision / spawn_group / promote are later
            # milestones; the scripted executor cannot produce them in M0.
            raise ApplicationError(
                f"turn outcome {result.outcome!r} is not supported in M0",
                non_retryable=True,
            )

    async def _emit(
        self,
        event_type: RunEventType,
        *,
        payload: dict[str, Any] | None = None,
        actor: str = "system",
        actor_type: Literal["human", "agent", "system"] = "system",
    ) -> None:
        """Every state change emits a RunEvent through the outbox activity
        (CLAUDE.md rule 5). Event identity is deterministic: {run_id}:{seq}."""
        assert self._state is not None
        self._event_seq += 1
        event = RunEvent(
            id=f"{self._state.run_id}:{self._event_seq}",
            run_id=self._state.run_id,
            tenant_id=self._state.tenant_id,
            seq=self._event_seq,
            type=event_type,
            ts=self._clock.now(),
            virtual_ts=None,  # TODO(milestone-4): dual stamps under virtual clocks
            sandbox=False,  # TODO(milestone-4): flag rehearsal (sandbox-kind) runs
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
