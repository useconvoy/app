"""run_turn activity — one turn through the owned TurnExecutor seam.

M0 wires `ScriptedTurnExecutor`. TODO(milestone-1): Pydantic AI executor with
inline tools + grant intersection; heartbeats per inline tool
({turn, tool_index}). TODO(milestone-4): promoted-call resume path.
"""

from temporalio import activity

from convoy_core import TurnInput, TurnResult
from convoy_runtime.activities import names
from convoy_runtime.providers.turn_executor import TurnExecutor


class TurnActivities:
    def __init__(self, executor: TurnExecutor) -> None:
        self._executor = executor

    @activity.defn(name=names.RUN_TURN)
    async def run_turn(self, turn: TurnInput) -> TurnResult:
        activity.heartbeat({"step_id": turn.step_id})
        return await self._executor.execute_turn(turn)
