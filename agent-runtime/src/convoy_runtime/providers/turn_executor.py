"""TurnExecutor — the owned seam around one LLM turn.

The outer loop (plan/budget/pause/steer) stays in the workflow; the inner turn
runs behind this interface. `ScriptedTurnExecutor` is the default in every
deterministic lane; the Pydantic AI executor is selected by config and talks
to real (or mock) models through the LiteLLM proxy.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

import anyio
from pydantic import BaseModel

from convoy_core import AgentSpec, ArtifactRef, TokenCounts, TurnInput, TurnResult
from convoy_runtime.providers.artifact_store import ArtifactStore

SCRIPTED_MODEL = "scripted-echo-1"

# Progress callback for long turns; called per inline tool execution with
# {"turn": n, "tool_index": i} so the activity can heartbeat.
HeartbeatFn = Callable[[dict[str, int]], None]


class TurnContext(BaseModel):
    """Run-scoped context a turn needs beyond `TurnInput`: which agent is
    executing, the pinned environment binding, and the 1-based turn number."""

    agent: AgentSpec
    binding_ref: ArtifactRef
    turn: int


class TurnExecutor(Protocol):
    """Executes exactly one turn: model call + inline tools, claim-checked."""

    async def execute_turn(
        self,
        turn: TurnInput,
        ctx: TurnContext,
        heartbeat: HeartbeatFn | None = None,
    ) -> TurnResult: ...


class ScriptedTurnExecutor:
    """Deterministic echo executor: one scripted turn completes the step.

    Writes the turn transcript to the artifact store (full fidelity, never a
    blob through Temporal) and reports fixed token/cost numbers so budget and
    projection plumbing can be exercised without a model.
    """

    def __init__(
        self,
        store: ArtifactStore,
        model: str = SCRIPTED_MODEL,
        turn_delay_seconds: float = 0.0,
    ) -> None:
        self._store = store
        self._model = model
        # Test knob: lets e2e scenarios deterministically catch a run mid-turn.
        self._turn_delay_seconds = turn_delay_seconds

    async def execute_turn(
        self,
        turn: TurnInput,
        ctx: TurnContext,
        heartbeat: HeartbeatFn | None = None,
    ) -> TurnResult:
        if self._turn_delay_seconds > 0:
            await anyio.sleep(self._turn_delay_seconds)
        transcript = {
            "run_id": turn.run_id,
            "step_id": turn.step_id,
            "turn": ctx.turn,
            "now": turn.now.isoformat(),
            "pinned_ref_key": turn.pinned_ref.key,
            "steers_drained": [steer.id for steer in turn.steers],
            "turns": [
                {"role": "user", "content": f"execute step {turn.step_id}"},
                {"role": "assistant", "content": f"echo: step {turn.step_id} done"},
            ],
        }
        transcript_ref = await self._store.put_json(
            f"runs/{turn.run_id}/transcripts/{turn.step_id}/turn-{ctx.turn}.json", transcript
        )
        output_ref = await self._store.put_json(
            f"runs/{turn.run_id}/outputs/{turn.step_id}.json",
            {"step_id": turn.step_id, "result": f"echo output for {turn.step_id}"},
        )
        return TurnResult(
            transcript_ref=transcript_ref,
            tokens=TokenCounts(input_tokens=12, output_tokens=7),
            cost_usd=Decimal("0.0001"),
            model_used=self._model,
            outcome="step_done",
            step_outputs=[output_ref],
        )
