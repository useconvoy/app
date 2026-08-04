"""Happy-path workflow test (TESTING §5-M0): fixture plan -> 2 scripted turns
-> land, with the full expected RunEvent sequence, in the time-skipping env."""

from decimal import Decimal

import pytest
from _support.common import TEST_TASK_QUEUE, fixture_run_state, start_time_skipping_env
from _support.fakes import FakeRuntime
from temporalio.worker import Worker

from convoy_runtime.workflows.agent_run import AgentRunWorkflow

pytestmark = pytest.mark.anyio

EXPECTED_EVENT_SEQUENCE = [
    "run_started",
    "plan_created",
    "step_started",
    "step_done",
    "step_started",
    "step_done",
    "landing_started",
    "run_completed",
]


async def test_happy_path_full_event_sequence() -> None:
    fake = FakeRuntime()
    state = fixture_run_state(run_id="run-happy-1")
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
        result = await env.client.execute_workflow(
            AgentRunWorkflow.run,
            args=[state, "alice@example.test"],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )

    assert result.status == "completed"
    report = result.land_report
    assert report is not None
    assert report.status == "completed"
    assert report.steps_done == 2
    assert report.steps_failed == 0
    assert report.goal == "test goal"
    assert len(report.deliverables) == 2
    assert report.cost_usd == Decimal("0.0002")  # two scripted turns

    # Full expected RunEvent sequence, in order (TESTING §5-M0).
    assert fake.event_types == EXPECTED_EVENT_SEQUENCE

    # Deterministic identity + per-run monotonic seq.
    assert [e.seq for e in fake.events] == list(range(1, len(EXPECTED_EVENT_SEQUENCE) + 1))
    assert all(e.id == f"{state.run_id}:{e.seq}" for e in fake.events)
    assert all(e.tenant_id == state.tenant_id for e in fake.events)

    # Actor identity flows into events: the API caller started the run.
    run_started = fake.events[0]
    assert run_started.actor == "alice@example.test"
    assert run_started.actor_type == "human"

    # Steps completed in plan order.
    step_events = [e for e in fake.events if e.type == "step_started"]
    assert [e.payload["step_id"] for e in step_events] == ["step-1", "step-2"]

    # run_completed carries the land report for the projection.
    assert fake.events[-1].payload["land_report"]["status"] == "completed"
