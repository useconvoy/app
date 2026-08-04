"""run_turn activity — one turn through the owned TurnExecutor seam.

The workflow passes the core `TurnInput` plus a runtime `TurnContext` (agent
spec, pinned binding ref, turn number). The activity heartbeats at turn start
and again per inline tool ({turn, tool_index}) via the executor's callback,
so a stalled tool loop is detected and retried. Inline tools are read-only
idempotent by contract, which is what makes that retry safe.

TODO: promoted-call resume path — long/side-effecting tools return to the
workflow and run as their own activities with idempotency keys.
"""

from temporalio import activity

from convoy_core import TurnInput, TurnResult
from convoy_runtime.activities import names
from convoy_runtime.providers.turn_executor import TurnContext, TurnExecutor


class TurnActivities:
    def __init__(self, executor: TurnExecutor) -> None:
        self._executor = executor

    @activity.defn(name=names.RUN_TURN)
    async def run_turn(self, turn: TurnInput, ctx: TurnContext) -> TurnResult:
        activity.heartbeat({"step_id": turn.step_id, "turn": ctx.turn})

        def beat(details: dict[str, int]) -> None:
            activity.heartbeat(details)

        return await self._executor.execute_turn(turn, ctx, heartbeat=beat)
