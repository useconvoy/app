"""Shared TurnExecutor contract battery.

Every executor implementation must pass these, driven through a small
`ExecutorHarness` the concrete test module provides: the scripted executor in
the fast lane, the Pydantic AI executor against the compose stack. The
battery asserts only seam-level behavior — claim-checked transcripts, sane
accounting fields, steer drainage, per-turn artifact keys — never
implementation detail.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from _support.common import fixture_run_state

from convoy_core import AgentSpec, ArtifactRef, SteerMessage, TurnInput, TurnResult
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.context_assembly import build_pinned_header
from convoy_runtime.providers.turn_executor import TurnContext, TurnExecutor


@dataclass
class ExecutorHarness:
    """Everything a battery test needs to drive one executor implementation."""

    executor: TurnExecutor
    store: ArtifactStore
    run_id: str
    agent: AgentSpec
    binding_ref: ArtifactRef
    heartbeats: list[dict[str, int]] = field(default_factory=lambda: [])

    async def execute(
        self,
        *,
        goal: str = "finish the step",
        step_id: str = "step-1",
        turn: int = 1,
        steers: list[SteerMessage] | None = None,
        working_transcript_ref: ArtifactRef | None = None,
    ) -> TurnResult:
        state = fixture_run_state(run_id=self.run_id)
        state.agent = self.agent
        state.turn_count = turn - 1
        plan_goal = goal
        from convoy_runtime.activities.plan import build_fixture_plan

        state.plan = build_fixture_plan(plan_goal, ["it lands"], self.binding_ref)
        state.pending_steers = list(steers or [])
        header = build_pinned_header(state)
        pinned_ref = await self.store.put_json(
            f"runs/{self.run_id}/pinned/turn-{turn}.json", header
        )
        turn_input = TurnInput(
            run_id=self.run_id,
            step_id=step_id,
            pinned_ref=pinned_ref,
            step_summaries=[],
            working_transcript_ref=working_transcript_ref,
            steers=list(steers or []),
            budget_remaining=Decimal("5"),
            now=datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC),
        )
        ctx = TurnContext(agent=self.agent, binding_ref=self.binding_ref, turn=turn)
        return await self.executor.execute_turn(turn_input, ctx, heartbeat=self.heartbeats.append)


class TurnExecutorBattery:
    """Inherit and provide a `harness` async fixture returning ExecutorHarness."""

    async def test_returns_claim_checked_transcript(self, harness: ExecutorHarness) -> None:
        result = await harness.execute()
        transcript: dict[str, Any] = await harness.store.get_json(result.transcript_ref)
        assert transcript["run_id"] == harness.run_id
        assert transcript["step_id"] == "step-1"
        assert transcript["turn"] == 1

    async def test_transcript_key_is_per_run_step_and_turn(self, harness: ExecutorHarness) -> None:
        result = await harness.execute(step_id="step-1", turn=1)
        assert result.transcript_ref.key == (
            f"runs/{harness.run_id}/transcripts/step-1/turn-1.json"
        )

    async def test_accounting_fields_are_sane(self, harness: ExecutorHarness) -> None:
        result = await harness.execute()
        assert result.tokens.input_tokens > 0
        assert result.tokens.output_tokens > 0
        assert result.cost_usd >= 0
        assert result.model_used
        assert result.outcome in ("continue", "step_done")

    async def test_step_done_produces_output_artifacts(self, harness: ExecutorHarness) -> None:
        result = await harness.execute()
        assert result.outcome == "step_done"
        assert result.step_outputs
        output: dict[str, Any] = await harness.store.get_json(result.step_outputs[0])
        assert output["step_id"] == "step-1"

    async def test_steers_drain_into_the_transcript(self, harness: ExecutorHarness) -> None:
        steers = [
            SteerMessage(
                id="steer-42",
                author="human",
                author_id="ops@example.test",
                mode="note",
                body="check the runway figure",
            )
        ]
        result = await harness.execute(steers=steers)
        transcript: dict[str, Any] = await harness.store.get_json(result.transcript_ref)
        assert transcript["steers_drained"] == ["steer-42"]
