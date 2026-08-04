"""Pause/resume workflow test: pause mid-run never cancels the in-flight
activity; resume continues to completion."""

import asyncio
from collections.abc import Callable

import pytest
from _support.common import TEST_TASK_QUEUE, fixture_run_state, start_time_skipping_env
from _support.fakes import FakeRuntime
from temporalio.worker import Worker

from convoy_runtime.workflows.agent_run import AgentRunWorkflow

pytestmark = pytest.mark.anyio


async def _eventually(check: Callable[[], bool], attempts: int = 300) -> None:
    for _ in range(attempts):
        if check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


async def test_pause_mid_run_then_resume() -> None:
    fake = FakeRuntime(gate_first_turn=True)
    state = fixture_run_state(run_id="run-pause-1")
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
            args=[state, "alice@example.test"],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )

        # Wait until the first run_turn activity is genuinely in flight.
        async with asyncio.timeout(15):
            await fake.first_turn_started.wait()

        # Pause while the activity is running.
        await handle.signal(AgentRunWorkflow.pause, "bob@example.test")
        # The paused flag is queryable immediately (precedence: paused).
        assert await handle.query(AgentRunWorkflow.get_status) == "paused"

        # Release the activity: it must finish normally, never be cancelled.
        fake.release_first_turn.set()

        await _eventually(lambda: "paused" in fake.event_types)
        assert not fake.turn_cancelled

        # The in-flight step completed BEFORE the pause took effect at the
        # loop boundary (edge-triggered pause).
        types = fake.event_types
        assert types.index("step_done") < types.index("paused")

        # Queries serve internal live state while paused.
        plan = await handle.query(AgentRunWorkflow.get_plan)
        assert plan is not None
        assert plan.version == 1

        # Only step-1 ran; step-2 must not start while paused.
        assert types.count("step_started") == 1

        # Resume clears the flag and the run completes.
        await handle.signal(AgentRunWorkflow.resume, "bob@example.test")
        result = await handle.result()

    assert result.status == "completed"
    assert result.land_report is not None
    assert result.land_report.steps_done == 2

    types = fake.event_types
    assert types.index("paused") < types.index("resumed")
    assert types[-1] == "run_completed"

    paused = next(e for e in fake.events if e.type == "paused")
    resumed = next(e for e in fake.events if e.type == "resumed")
    assert paused.actor == "bob@example.test"
    assert resumed.actor == "bob@example.test"
    assert paused.payload["run_status"] == "paused"
    assert resumed.payload["run_status"] == "running"
