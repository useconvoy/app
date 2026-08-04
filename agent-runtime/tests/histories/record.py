"""Re-record checked-in replay histories.

Run via `make record-history`. Re-record only with a rationale in the commit
message; prefer `workflow.patched` versioning for live-run compatibility.

Two representative histories are recorded: the linear happy path, and a
budget-exhausted run under the land policy (warning + exhaustion + landing),
so replay coverage includes the budget event surface.
"""

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

from temporalio.worker import Worker

TESTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS_DIR))

from _support.common import (  # noqa: E402
    TEST_TASK_QUEUE,
    fixture_run_state,
    start_time_skipping_env,
)
from _support.fakes import FakeRuntime, ScriptedTurn  # noqa: E402

from convoy_core import RunPolicy, RunState  # noqa: E402
from convoy_runtime.workflows.agent_run import AgentRunWorkflow  # noqa: E402

HISTORIES_DIR = Path(__file__).resolve().parent


async def _record(name: str, fake: FakeRuntime, state: RunState) -> Path:
    env = await start_time_skipping_env()
    async with (
        env,
        Worker(
            env.client,
            task_queue=TEST_TASK_QUEUE,
            workflows=[AgentRunWorkflow],
            activities=fake.activities,
        ),
    ):
        handle = await env.client.start_workflow(
            AgentRunWorkflow.run,
            args=[state, "recorder@convoy.test"],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )
        await handle.result()
        history = await handle.fetch_history()

    out = HISTORIES_DIR / f"{name}.json"
    out.write_text(json.dumps(history.to_json_dict(), indent=2, sort_keys=True) + "\n")
    return out


async def record_all() -> list[Path]:
    return [
        await _record(
            "happy_path", FakeRuntime(), fixture_run_state(run_id="run-history-happy-path")
        ),
        await _record(
            "budget_exhausted_land",
            FakeRuntime(
                turns=[
                    ScriptedTurn(cost_usd=Decimal("0.85")),
                    ScriptedTurn(cost_usd=Decimal("0.20"), outcome="continue"),
                ]
            ),
            fixture_run_state(
                run_id="run-history-budget-land",
                budget_cap_usd=Decimal("1.00"),
                policy=RunPolicy(require_plan_approval=False, on_budget_exhausted="land"),
            ),
        ),
    ]


def main() -> None:
    for path in asyncio.run(record_all()):
        print(f"recorded {path}")


if __name__ == "__main__":
    main()
