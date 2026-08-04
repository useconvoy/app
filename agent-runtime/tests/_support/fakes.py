"""Fake activity implementations for workflow-layer tests.

Same names and signatures as the real activities; providers are faked so the
time-skipping lane needs no network or containers. Turn behavior is scripted
per call (cost, outcome, model, gate requests, proposed revisions), which is
how budget, approval, gate, and steer workflow behavior is driven
deterministically. The fixture plan can carry human gates on named steps.
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from temporalio import activity
from temporalio.exceptions import ApplicationError

from convoy_core import (
    ArtifactRef,
    HumanGate,
    LandReport,
    Plan,
    PlanPatchOp,
    RunEvent,
    RunState,
    TokenCounts,
    TurnInput,
    TurnResult,
)
from convoy_runtime.activities import names
from convoy_runtime.activities.plan import build_fixture_plan
from convoy_runtime.providers.turn_executor import SCRIPTED_MODEL, TurnContext

from .common import fixture_ref as support_ref

FakeActivity = Callable[..., Coroutine[Any, Any, Any]]


@dataclass(frozen=True)
class ScriptedTurn:
    """One scripted run_turn response; outcome "fail" raises a non-retryable
    activity error instead of returning a result. A "needs_human" turn may
    carry the gate it requests; a "propose_revision" turn carries its ops."""

    cost_usd: Decimal = Decimal("0.0001")
    outcome: Literal["continue", "step_done", "fail", "needs_human", "propose_revision"] = (
        "step_done"
    )
    model_used: str = SCRIPTED_MODEL
    input_tokens: int = 12
    output_tokens: int = 7
    gate: HumanGate | None = None
    ops: tuple[PlanPatchOp, ...] = ()


class FakeRuntime:
    """In-memory activity set: records emitted events, scripts turn results,
    can gate the first turn to catch a run mid-flight, and can attach human
    gates to the fixture plan's steps."""

    def __init__(
        self,
        *,
        gate_first_turn: bool = False,
        turns: list[ScriptedTurn] | None = None,
        plan_gates: dict[str, HumanGate] | None = None,
    ) -> None:
        self.events: list[RunEvent] = []
        self.turn_calls = 0
        self.turn_inputs: list[TurnInput] = []
        self.turn_contexts: list[TurnContext] = []
        self.turn_cancelled = False
        self.gate_first_turn = gate_first_turn
        self.turns = turns
        self.plan_gates = plan_gates or {}
        self.provision_calls: list[tuple[str, Decimal]] = []
        self.assemble_calls = 0
        self.snapshots: list[Plan] = []
        self.sent_steer_ids: list[str] = []
        self.first_turn_started = asyncio.Event()
        self.release_first_turn = asyncio.Event()

    @activity.defn(name=names.CREATE_PLAN)
    async def create_plan(self, state: RunState) -> Plan:
        return build_fixture_plan(
            "test goal",
            ["it lands"],
            support_ref(f"runs/{state.run_id}/plans/v1.json"),
            self.plan_gates,
        )

    @activity.defn(name=names.ARCHIVE_PLAN_SNAPSHOT)
    async def archive_plan_snapshot(self, run_id: str, plan: Plan) -> ArtifactRef:
        self.snapshots.append(plan)
        return support_ref(f"runs/{run_id}/plans/v{plan.version}.json")

    @activity.defn(name=names.PROVISION_MODEL_KEY)
    async def provision_model_key(self, run_id: str, cap_usd: Decimal) -> None:
        self.provision_calls.append((run_id, cap_usd))

    @activity.defn(name=names.ASSEMBLE_PINNED_HEADER)
    async def assemble_pinned_header(self, state: RunState) -> ArtifactRef:
        self.assemble_calls += 1
        return support_ref(f"runs/{state.run_id}/pinned/turn-{state.turn_count + 1}.json")

    @activity.defn(name=names.RUN_TURN)
    async def run_turn(self, turn: TurnInput, ctx: TurnContext) -> TurnResult:
        self.turn_calls += 1
        self.turn_inputs.append(turn)
        self.turn_contexts.append(ctx)
        if self.gate_first_turn and self.turn_calls == 1:
            self.first_turn_started.set()
            try:
                await self.release_first_turn.wait()
            except asyncio.CancelledError:
                self.turn_cancelled = True
                raise
        script = ScriptedTurn()
        if self.turns is not None:
            index = min(self.turn_calls - 1, len(self.turns) - 1)
            script = self.turns[index]
        if script.outcome == "fail":
            raise ApplicationError("scripted turn failure", non_retryable=True)
        return TurnResult(
            transcript_ref=support_ref(
                f"runs/{turn.run_id}/transcripts/{turn.step_id}/turn-{ctx.turn}.json"
            ),
            tokens=TokenCounts(
                input_tokens=script.input_tokens, output_tokens=script.output_tokens
            ),
            cost_usd=script.cost_usd,
            model_used=script.model_used,
            outcome=script.outcome,
            step_outputs=(
                [support_ref(f"runs/{turn.run_id}/outputs/{turn.step_id}.json")]
                if script.outcome == "step_done"
                else []
            ),
            proposed_revision=list(script.ops) if script.ops else None,
            gate_request=script.gate,
        )

    @activity.defn(name=names.LAND_RUN)
    async def land_run(self, state: RunState, tokens: TokenCounts) -> LandReport:
        plan = state.plan
        assert plan is not None
        done = [s for s in plan.steps if s.status == "done"]
        return LandReport(
            run_id=state.run_id,
            status="completed" if len(done) == len(plan.steps) else "landed_partial",
            goal=plan.goal,
            success_criteria=plan.success_criteria,
            deliverables=[ref for s in done for ref in s.outputs],
            steps_done=len(done),
            steps_skipped=len([s for s in plan.steps if s.status == "skipped"]),
            steps_failed=len([s for s in plan.steps if s.status == "failed"]),
            cost_usd=state.budget.spent_usd,
            tokens=tokens,
        )

    @activity.defn(name=names.EMIT_RUN_EVENTS)
    async def emit_run_events(self, events: list[RunEvent]) -> None:
        known = {e.id for e in self.events}
        self.events.extend(e for e in events if e.id not in known)

    @property
    def activities(self) -> list[FakeActivity]:
        return [
            self.create_plan,
            self.archive_plan_snapshot,
            self.provision_model_key,
            self.assemble_pinned_header,
            self.run_turn,
            self.land_run,
            self.emit_run_events,
        ]

    @property
    def event_types(self) -> list[str]:
        return [e.type for e in self.events]

    def events_of(self, event_type: str) -> list[RunEvent]:
        return [e for e in self.events if e.type == event_type]
