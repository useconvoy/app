"""SubagentWorkflow — one fan-out child executing a scoped brief.

A child workflow runs one plan step's brief: a scoped goal, the artifact refs
it may start from, a tool set already intersected with its parent's grants,
and its budget slice as its own hard cap. It runs its own turn loop through
the same executor seam as the root run and lands a `SubagentResult` — the
compaction contract. The parent never sees the child's transcript inline;
full fidelity stays in the artifact store behind `transcript_ref`.

The child emits its own RunEvents under its own run id (linked to the parent
by payload), so projections show child progress as a run row of its own.

Wrap-up rules:
- A `land` signal (the parent forwarding its own landing, or any caller)
  is edge-triggered at the turn boundary: the in-flight turn finishes, then
  the child wraps up with status "landed_partial".
- Spending at or past the budget slice wraps up as "landed_partial" too — a
  child that ran out of money still reports everything it produced. A slice
  of zero or less means no per-child cap; the parent's own budget accounting
  and policy remain the outer guard.
- A failed turn (after activity retries) or an unsupported turn outcome wraps
  up as "failed" with the error recorded; the parent applies its group
  partial-failure policy to the result.

TODO: mid-run compaction and continue_as_new for long child runs.
TODO: promoted tool calls and human gates inside child runs.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from convoy_core import (
        AgentSpec,
        ArtifactRef,
        RunEvent,
        RunEventType,
        SubagentResult,
        TokenCounts,
        TurnInput,
        TurnResult,
    )
    from convoy_runtime.activities import names
    from convoy_runtime.clock import PassthroughClock, RunClock
    from convoy_runtime.providers.turn_executor import TurnContext

# Explicit per-activity retry/timeout matrix (no defaults-by-omission).
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

_WRAP_RESULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_WRAP_RESULT_TIMEOUT = timedelta(seconds=30)

_OUTBOX_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=10,
)
_OUTBOX_TIMEOUT = timedelta(seconds=30)

# Hard stop for runaway scripted loops; generous next to real briefs.
# TODO: continue_as_new instead of failing once children run for days.
DEFAULT_MAX_TURNS = 20

_ActorType = Literal["human", "agent", "system"]


class SubagentBrief(BaseModel):
    """Everything a child needs to execute one step: the scoped goal, input
    artifact refs, the already-intersected tool grants inside its `agent`
    spec, and its budget slice as its own cap. Small by construction — large
    material rides as refs, never inline."""

    run_id: str  # the child's own run id
    parent_run_id: str
    tenant_id: str
    environment_id: str
    binding_ref: ArtifactRef
    step_id: str  # the parent plan step this child executes
    group_id: str | None = None
    goal: str
    input_refs: list[ArtifactRef] = []
    agent: AgentSpec  # tools already intersected with the parent's grants
    spawned_by: str  # parent agent id, for the audit chain
    budget_cap_usd: Decimal  # this child's slice; <= 0 means no per-child cap
    max_turns: int = DEFAULT_MAX_TURNS


class SubagentWrapUp(BaseModel):
    """What the child's loop hands the wrap-up activity: outcome plus the
    accumulated turn bookkeeping the `SubagentResult` is distilled from."""

    brief: SubagentBrief
    status: Literal["done", "failed", "landed_partial"]
    transcript_ref: ArtifactRef | None
    outputs: list[ArtifactRef]
    cost_usd: Decimal
    tokens: TokenCounts
    turns: int
    error: str | None = None


@dataclass
class _TurnLedger:
    """Accumulated truth across the child's turns."""

    spent: Decimal
    tokens: TokenCounts
    transcript_ref: ArtifactRef | None
    outputs: list[ArtifactRef]
    turns: int


@workflow.defn
class SubagentWorkflow:
    def __init__(self) -> None:
        self._clock: RunClock = PassthroughClock()
        self._brief: SubagentBrief | None = None
        self._status = "running"
        self._landing = False
        self._land_actor = "system"
        self._land_actor_type: _ActorType = "system"
        self._event_seq = 0

    # ------------------------------------------------------------------ run

    @workflow.run
    async def run(self, brief: SubagentBrief) -> SubagentResult:
        self._brief = brief
        await self._emit(
            "run_started",
            actor=brief.spawned_by,
            actor_type="agent",
            payload={
                "run_status": "running",
                "parent_run_id": brief.parent_run_id,
                "step_id": brief.step_id,
                "group_id": brief.group_id,
                "layer": brief.agent.layer,
                "budget_cap_usd": str(brief.budget_cap_usd),
                "goal": brief.goal,
            },
        )

        # The child's own virtual model key, hard-capped at its slice — the
        # infra backstop that keeps a child inside its share of the budget.
        # A non-positive slice means no per-child cap, so no key either.
        if brief.budget_cap_usd > 0:
            await workflow.execute_activity(
                names.PROVISION_MODEL_KEY,
                args=[brief.run_id, brief.budget_cap_usd],
                start_to_close_timeout=_PROVISION_KEY_TIMEOUT,
                retry_policy=_PROVISION_KEY_RETRY,
            )

        header_ref = cast(
            ArtifactRef,
            await workflow.execute_activity(
                names.ASSEMBLE_SUBAGENT_HEADER,
                brief,
                result_type=ArtifactRef,
                start_to_close_timeout=_ASSEMBLE_HEADER_TIMEOUT,
                retry_policy=_ASSEMBLE_HEADER_RETRY,
            ),
        )
        await self._emit("step_started", payload={"step_id": brief.step_id, "attempt": 1})

        ledger = _TurnLedger(
            spent=Decimal(0),
            tokens=TokenCounts(),
            transcript_ref=None,
            outputs=[],
            turns=0,
        )
        status, error = await self._turn_loop(brief, header_ref, ledger)

        if self._landing:
            await self._emit(
                "landing_started",
                actor=self._land_actor,
                actor_type=self._land_actor_type,
                payload={"run_status": "landing"},
            )

        result = cast(
            SubagentResult,
            await workflow.execute_activity(
                names.WRAP_SUBAGENT_RESULT,
                SubagentWrapUp(
                    brief=brief,
                    status=status,
                    transcript_ref=ledger.transcript_ref,
                    outputs=ledger.outputs,
                    cost_usd=ledger.spent,
                    tokens=ledger.tokens,
                    turns=ledger.turns,
                    error=error,
                ),
                result_type=SubagentResult,
                start_to_close_timeout=_WRAP_RESULT_TIMEOUT,
                retry_policy=_WRAP_RESULT_RETRY,
            ),
        )

        if status == "failed":
            self._status = "failed"
            await self._emit(
                "step_failed",
                payload={"step_id": brief.step_id, "cost_usd": str(ledger.spent), "error": error},
            )
            await self._emit(
                "run_failed",
                payload={
                    "run_status": "failed",
                    "result_status": status,
                    "headline": result.headline,
                    "cost_usd": str(ledger.spent),
                },
            )
        else:
            self._status = "completed"
            if status == "done":
                await self._emit(
                    "step_done",
                    payload={"step_id": brief.step_id, "cost_usd": str(ledger.spent)},
                )
            await self._emit(
                "run_completed",
                payload={
                    "run_status": "completed",
                    "result_status": status,
                    "headline": result.headline,
                    "cost_usd": str(ledger.spent),
                },
            )
        return result

    async def _turn_loop(
        self, brief: SubagentBrief, header_ref: ArtifactRef, ledger: _TurnLedger
    ) -> tuple[Literal["done", "failed", "landed_partial"], str | None]:
        """The child's turn loop: land requests and the budget cap are
        edge-triggered at the boundary, so an in-flight turn always finishes
        and is accounted before the child wraps up."""
        cap = brief.budget_cap_usd
        while True:
            if self._landing:
                return "landed_partial", None
            if 0 < cap <= ledger.spent:
                await self._emit(
                    "budget_exhausted",
                    payload={
                        "action": "land",
                        "budget": {
                            "cap_usd": str(cap),
                            "spent_usd": str(ledger.spent),
                            "reserved_usd": "0",
                        },
                    },
                )
                return "landed_partial", None
            if ledger.turns >= brief.max_turns:
                return "failed", f"turn limit reached after {ledger.turns} turns"

            ledger.turns += 1
            turn_input = TurnInput(
                run_id=brief.run_id,
                step_id=brief.step_id,
                pinned_ref=header_ref,
                step_summaries=[],
                working_transcript_ref=ledger.transcript_ref,
                steers=[],
                budget_remaining=(cap - ledger.spent) if cap > 0 else Decimal(0),
                now=self._clock.now(),
            )
            try:
                result = cast(
                    TurnResult,
                    await workflow.execute_activity(
                        names.RUN_TURN,
                        args=[
                            turn_input,
                            TurnContext(
                                agent=brief.agent,
                                binding_ref=brief.binding_ref,
                                turn=ledger.turns,
                            ),
                        ],
                        result_type=TurnResult,
                        start_to_close_timeout=_RUN_TURN_TIMEOUT,
                        heartbeat_timeout=_RUN_TURN_HEARTBEAT,
                        retry_policy=_RUN_TURN_RETRY,
                    ),
                )
            except ActivityError:
                return "failed", f"turn {ledger.turns} failed"

            ledger.spent += result.cost_usd
            ledger.tokens = TokenCounts(
                input_tokens=ledger.tokens.input_tokens + result.tokens.input_tokens,
                output_tokens=ledger.tokens.output_tokens + result.tokens.output_tokens,
            )
            ledger.transcript_ref = result.transcript_ref
            ledger.outputs.extend(result.step_outputs)

            if result.outcome == "step_done":
                return "done", None
            if result.outcome != "continue":
                # Gates, revisions, promotion, and nested fan-out have no
                # surface inside a child run yet.
                # TODO: promoted tools and human gates inside child runs.
                return "failed", f"unsupported subagent turn outcome {result.outcome!r}"

    # -------------------------------------------------------------- signals
    # Signals do no heavy work: validate, enqueue, return. The loop drains.

    @workflow.signal
    def land(self, actor: str = "parent") -> None:
        self._landing = True
        self._land_actor = actor
        self._land_actor_type = "system"

    # -------------------------------------------------------------- queries

    @workflow.query
    def get_status(self) -> str:
        if self._status != "running":
            return self._status
        if self._landing:
            return "landing"
        return "running"

    # -------------------------------------------------------------- helpers

    async def _emit(
        self,
        event_type: RunEventType,
        *,
        payload: dict[str, Any] | None = None,
        actor: str = "system",
        actor_type: _ActorType = "system",
    ) -> None:
        """Child events ride the same outbox as the parent's, under the
        child's own run id with its own monotonic seq."""
        assert self._brief is not None
        self._event_seq += 1
        event = RunEvent(
            id=f"{self._brief.run_id}:{self._event_seq}",
            run_id=self._brief.run_id,
            tenant_id=self._brief.tenant_id,
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
