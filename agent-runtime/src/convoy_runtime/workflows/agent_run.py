"""AgentRunWorkflow — the durable outer loop.

Deterministic orchestration only: no I/O, no clock reads outside `RunClock`,
no randomness. Activities are scheduled by name so the workflow sandbox never
imports I/O clients, and every activity call declares an explicit retry
policy and timeouts.

The loop owns everything the run must never get wrong: plan mutation, budget
accounting and threshold actions, pause/land precedence, and the audited
event stream. Turn intelligence lives behind the run_turn activity.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from convoy_core import (
        ArtifactRef,
        LandReport,
        Plan,
        PlanStep,
        RunEvent,
        RunEventType,
        RunResult,
        RunState,
        TokenCounts,
        TurnInput,
        TurnResult,
    )
    from convoy_runtime.activities import names
    from convoy_runtime.clock import PassthroughClock, RunClock
    from convoy_runtime.providers.turn_executor import TurnContext

# Fraction of the budget cap that triggers the one-time warning event.
BUDGET_WARNING_THRESHOLD = Decimal("0.8")

# Explicit per-activity retry/timeout matrix (values tuned under load later —
# the shape is fixed: no defaults-by-omission).
_CREATE_PLAN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_CREATE_PLAN_TIMEOUT = timedelta(seconds=60)

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
    """pending -> ready once every dependency is done or skipped."""
    terminal_ok = {s.id for s in plan.steps if s.status in ("done", "skipped")}
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


def _plan_complete(plan: Plan) -> bool:
    return all(s.status in ("done", "skipped") for s in plan.steps)


@workflow.defn
class AgentRunWorkflow:
    def __init__(self) -> None:
        # All workflow time flows through the RunClock seam.
        # TODO: virtual clock selection for sandbox bindings with virtual time.
        self._clock: RunClock = PassthroughClock()
        self._state: RunState | None = None
        self._paused = False
        self._landing = False
        self._pause_actor = "system"
        self._pause_actor_type: Literal["human", "agent", "system"] = "system"
        self._resume_actor = "system"
        self._resume_actor_type: Literal["human", "agent", "system"] = "system"
        self._land_actor = "system"
        self._land_actor_type: Literal["human", "agent", "system"] = "system"
        self._event_seq = 0
        # Budget threshold edges are one-shot per run segment.
        # TODO: carry these flags (and token totals) across continue_as_new.
        self._budget_warned = False
        self._budget_exhausted_emitted = False
        self._tokens = TokenCounts()

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

        # Per-run virtual model key with a hard dollar cap — the infra-side
        # budget backstop exists before the first turn can spend anything.
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
            await self._emit(
                "plan_created",
                payload={
                    "plan": state.plan.model_dump(mode="json"),
                    "plan_version": state.plan.version,
                    "run_status": "running",
                },
            )
            # TODO: plan approval gate — awaiting_approval status, the
            # approve_plan signal, and approval scope on revisions.

        state.status = "running"

        while not _plan_complete(state.plan):
            if await self._enforce_budget() == "abort":
                state.status = "failed"
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

            _recompute_ready(state.plan)
            step = _next_step(state.plan)
            if step is None:
                # No ready or running step and the plan is incomplete: with
                # the linear fixture plan this is unreachable; fail loudly.
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

            # TODO: subagent fan-out for executor == "subagent" steps.

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

            try:
                result = cast(
                    TurnResult,
                    await workflow.execute_activity(
                        names.RUN_TURN,
                        args=[self._turn_input(state, step), self._turn_context(state)],
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
                # TODO: audited retry_step path instead of run failure.
                return RunResult(
                    run_id=state.run_id, status="failed", error=f"step {step.id} failed"
                )

            # TODO: promoted-tool loop while outcome == "promote" (the call
            # runs as its own activity with an idempotency key, then the turn
            # resumes with the result).
            self._apply_turn(state, step, result)
            if result.outcome == "step_done":
                await self._emit(
                    "step_done",
                    payload={
                        "step_id": step.id,
                        "outcome": result.outcome,
                        "model_used": result.model_used,
                        "model_requested": state.agent.model,
                        "model_fallback": result.model_used != state.agent.model,
                        "cost_usd": str(result.cost_usd),
                        "budget": self._budget_snapshot(),
                    },
                )

            # TODO: continue_as_new at the turn limit with full RunState carry.

        state.status = "landing"
        await self._emit(
            "landing_started",
            actor=self._land_actor,
            actor_type=self._land_actor_type,
            payload={"run_status": "landing"},
        )
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

    # TODO: steer, approve_plan, human_response signals.

    # -------------------------------------------------------------- queries

    @workflow.query
    def get_status(self) -> str:
        # Precedence: landing > paused > derived from steps.
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
        await self._emit(
            "paused",
            actor=self._pause_actor,
            actor_type=self._pause_actor_type,
            payload={"run_status": "paused"},
        )
        await workflow.wait_condition(lambda: not self._paused or self._landing)
        if not self._landing:
            self._state.status = "running"
            await self._emit(
                "resumed",
                actor=self._resume_actor,
                actor_type=self._resume_actor_type,
                payload={"run_status": "running"},
            )
        return True

    def _budget_snapshot(self) -> dict[str, str]:
        assert self._state is not None
        budget = self._state.budget
        return {
            "cap_usd": str(budget.cap_usd),
            "spent_usd": str(budget.spent_usd),
            "reserved_usd": str(budget.reserved_usd),
        }

    def _turn_context(self, state: RunState) -> TurnContext:
        return TurnContext(
            agent=state.agent,
            binding_ref=state.binding_ref,
            turn=state.turn_count + 1,
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

    def _apply_turn(self, state: RunState, step: PlanStep, result: TurnResult) -> None:
        """Only the workflow mutates the plan; the activity returned a
        proposal. Budget spend and token totals accumulate here, off the
        turn's actual cost."""
        state.turn_count += 1
        state.budget.spent_usd += result.cost_usd
        self._tokens = TokenCounts(
            input_tokens=self._tokens.input_tokens + result.tokens.input_tokens,
            output_tokens=self._tokens.output_tokens + result.tokens.output_tokens,
        )
        if result.outcome == "step_done":
            step.status = "done"
            step.outputs = list(result.step_outputs)
            # The working transcript is per step; a fresh step starts clean.
            state.working_transcript_ref = None
        elif result.outcome == "continue":
            state.working_transcript_ref = result.transcript_ref
        else:
            # needs_human / propose_revision / spawn_group / promote arrive
            # with later capabilities; the executors cannot produce them yet.
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
        actor_type: Literal["human", "agent", "system"] = "system",
    ) -> None:
        """Every state change emits a RunEvent through the outbox activity.
        Event identity is deterministic: {run_id}:{seq}."""
        assert self._state is not None
        self._event_seq += 1
        event = RunEvent(
            id=f"{self._state.run_id}:{self._event_seq}",
            run_id=self._state.run_id,
            tenant_id=self._state.tenant_id,
            seq=self._event_seq,
            type=event_type,
            ts=self._clock.now(),
            virtual_ts=None,  # TODO: dual stamps once virtual clocks exist
            sandbox=False,  # TODO: flag rehearsal (sandbox-binding) runs
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
