"""run_turn activity behavior with a faked executor: heartbeats flow at turn
start and per inline tool with {turn, tool_index}."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from _support.common import fixture_ref, fixture_run_state
from temporalio.testing import ActivityEnvironment

from convoy_core import TokenCounts, TurnInput, TurnResult
from convoy_runtime.activities.turn import TurnActivities
from convoy_runtime.providers.turn_executor import HeartbeatFn, TurnContext

pytestmark = pytest.mark.anyio


class ToolLoopExecutor:
    """Fake executor that reports two inline tool executions via heartbeat."""

    async def execute_turn(
        self,
        turn: TurnInput,
        ctx: TurnContext,
        heartbeat: HeartbeatFn | None = None,
    ) -> TurnResult:
        assert heartbeat is not None
        for tool_index in range(2):
            heartbeat({"turn": ctx.turn, "tool_index": tool_index})
        return TurnResult(
            transcript_ref=fixture_ref("t.json"),
            tokens=TokenCounts(input_tokens=1, output_tokens=1),
            cost_usd=Decimal("0.01"),
            model_used="fake-model",
            outcome="step_done",
        )


async def test_run_turn_heartbeats_turn_and_tool_progress() -> None:
    env = ActivityEnvironment()
    beats: list[Any] = []
    env.on_heartbeat = lambda *details: beats.append(details[0])

    state = fixture_run_state(run_id="run-act-1")
    turn = TurnInput(
        run_id=state.run_id,
        step_id="step-1",
        pinned_ref=fixture_ref("pinned.json"),
        step_summaries=[],
        working_transcript_ref=None,
        steers=[],
        budget_remaining=Decimal("5"),
        now=datetime(2026, 8, 4, tzinfo=UTC),
    )
    ctx = TurnContext(agent=state.agent, binding_ref=state.binding_ref, turn=3)

    activities = TurnActivities(ToolLoopExecutor())
    result = await env.run(activities.run_turn, turn, ctx)

    assert result.outcome == "step_done"
    assert beats[0] == {"step_id": "step-1", "turn": 3}
    assert beats[1:] == [
        {"turn": 3, "tool_index": 0},
        {"turn": 3, "tool_index": 1},
    ]
