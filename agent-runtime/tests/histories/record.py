"""Re-record checked-in replay histories (TESTING.md section 4.2).

Run via `make record-history`. Re-record only with a rationale in the commit
message; prefer `workflow.patched` for live-run compatibility (CLAUDE.md rule 8).
"""

import asyncio
import json
import sys
from pathlib import Path

from temporalio.worker import Worker

TESTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS_DIR))

from _support.common import (  # noqa: E402
    TEST_TASK_QUEUE,
    fixture_run_state,
    start_time_skipping_env,
)
from _support.fakes import FakeRuntime  # noqa: E402

from convoy_runtime.workflows.agent_run import AgentRunWorkflow  # noqa: E402

HISTORIES_DIR = Path(__file__).resolve().parent


async def record_happy_path() -> Path:
    fake = FakeRuntime()
    state = fixture_run_state(run_id="run-history-happy-path")
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

    out = HISTORIES_DIR / "happy_path.json"
    out.write_text(json.dumps(history.to_json_dict(), indent=2, sort_keys=True) + "\n")
    return out


def main() -> None:
    path = asyncio.run(record_happy_path())
    print(f"recorded {path}")


if __name__ == "__main__":
    main()
