"""Fake activity implementations for workflow-layer tests.

Same names and signatures as the real activities; providers are faked so the
time-skipping lane needs no network or containers (TESTING.md section 1).
"""

import asyncio
from collections.abc import Callable, Coroutine
from decimal import Decimal
from typing import Any

from temporalio import activity

from convoy_core import (
    LandReport,
    Plan,
    RunEvent,
    RunState,
    TokenCounts,
    TurnInput,
    TurnResult,
)
from convoy_runtime.activities import names
from convoy_runtime.activities.plan import build_fixture_plan

from .common import fixture_ref as support_ref

FakeActivity = Callable[..., Coroutine[Any, Any, Any]]


class FakeRuntime:
    """In-memory activity set: records emitted events, can gate the first turn."""

    def __init__(self, *, gate_first_turn: bool = False) -> None:
        self.events: list[RunEvent] = []
        self.turn_calls = 0
        self.turn_cancelled = False
        self.gate_first_turn = gate_first_turn
        self.first_turn_started = asyncio.Event()
        self.release_first_turn = asyncio.Event()

    @activity.defn(name=names.CREATE_PLAN)
    async def create_plan(self, state: RunState) -> Plan:
        return build_fixture_plan(
            "test goal", ["it lands"], support_ref(f"runs/{state.run_id}/plans/v1.json")
        )

    @activity.defn(name=names.RUN_TURN)
    async def run_turn(self, turn: TurnInput) -> TurnResult:
        self.turn_calls += 1
        if self.gate_first_turn and self.turn_calls == 1:
            self.first_turn_started.set()
            try:
                await self.release_first_turn.wait()
            except asyncio.CancelledError:
                self.turn_cancelled = True
                raise
        return TurnResult(
            transcript_ref=support_ref(f"runs/{turn.run_id}/transcripts/{turn.step_id}.json"),
            tokens=TokenCounts(input_tokens=12, output_tokens=7),
            cost_usd=Decimal("0.0001"),
            model_used="scripted-echo-1",
            outcome="step_done",
            step_outputs=[support_ref(f"runs/{turn.run_id}/outputs/{turn.step_id}.json")],
        )

    @activity.defn(name=names.LAND_RUN)
    async def land_run(self, state: RunState) -> LandReport:
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
            tokens=TokenCounts(),
        )

    @activity.defn(name=names.EMIT_RUN_EVENTS)
    async def emit_run_events(self, events: list[RunEvent]) -> None:
        known = {e.id for e in self.events}
        self.events.extend(e for e in events if e.id not in known)

    @property
    def activities(self) -> list[FakeActivity]:
        return [self.create_plan, self.run_turn, self.land_run, self.emit_run_events]

    @property
    def event_types(self) -> list[str]:
        return [e.type for e in self.events]
