"""Human gate workflow tests: a gated step blocks before its work with the
run deriving blocked_on_human; a response unblocks exactly the named step and
feeds the answer into the next turn's context; durable timeouts fire each
on_timeout action; landing during a gate wait never fires timer actions."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from _support.common import TEST_TASK_QUEUE, fixture_run_state, start_time_skipping_env
from _support.fakes import FakeRuntime, ScriptedTurn
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from convoy_core import HumanGate, RunResult, RunState
from convoy_runtime.signals import GateResponse
from convoy_runtime.workflows.agent_run import AgentRunWorkflow

pytestmark = pytest.mark.anyio

Handle = WorkflowHandle[AgentRunWorkflow, RunResult]


@asynccontextmanager
async def _running(
    fake: FakeRuntime, state: RunState
) -> AsyncGenerator[tuple[WorkflowEnvironment, Handle]]:
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
            args=[state, "starter@example.test"],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )
        yield env, handle


async def _wait_event(fake: FakeRuntime, event_type: str, occurrence: int = 1) -> None:
    async with asyncio.timeout(15):
        while fake.event_types.count(event_type) < occurrence:  # noqa: ASYNC110
            await asyncio.sleep(0.05)


async def _settled(check: Callable[[], bool], seconds: float = 0.4) -> bool:
    for _ in range(int(seconds / 0.05)):
        if not check():
            return False
        await asyncio.sleep(0.05)
    return check()


def _respond(step_id: str, response: str, actor: str = "responder@example.test") -> GateResponse:
    return GateResponse(step_id=step_id, response=response, actor=actor)


async def test_attached_gate_blocks_step_until_the_right_response() -> None:
    fake = FakeRuntime(
        plan_gates={"step-2": HumanGate(kind="approval", prompt="Proceed to the summary?")}
    )
    state = fixture_run_state(run_id="run-gate-1")
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "gate_opened")
        assert await handle.query(AgentRunWorkflow.get_status) == "blocked_on_human"
        opened = fake.events_of("gate_opened")[0]
        assert opened.payload["step_id"] == "step-2"
        assert opened.payload["kind"] == "approval"
        assert opened.payload["prompt"] == "Proceed to the summary?"
        assert opened.payload["run_status"] == "blocked_on_human"
        assert opened.actor == "system"

        # A response naming a step that is not blocked is dropped cleanly.
        await handle.signal(AgentRunWorkflow.human_response, _respond("step-1", "nope"))
        assert await _settled(lambda: not fake.events_of("gate_answered"))
        assert await handle.query(AgentRunWorkflow.get_status) == "blocked_on_human"

        # The response for the blocked step unblocks exactly that step.
        await handle.signal(AgentRunWorkflow.human_response, _respond("step-2", "yes, proceed"))
        result = await handle.result()

    assert result.status == "completed"
    answered = fake.events_of("gate_answered")[0]
    assert answered.payload["step_id"] == "step-2"
    assert answered.payload["response"] == "yes, proceed"
    assert answered.actor == "responder@example.test"
    assert answered.actor_type == "human"
    types = fake.event_types
    # The step is scheduled, then immediately blocks on its gate.
    assert types.index("gate_opened") == types.index("step_started", types.index("step_done")) + 1
    step2_started = [e for e in fake.events_of("step_started") if e.payload["step_id"] == "step-2"]
    assert len(step2_started) == 1  # unblocking never re-starts the step

    # The gate answer fed the gated step's turn as context.
    step2_turn = fake.turn_inputs[1]
    assert step2_turn.step_id == "step-2"
    assert [s.body for s in step2_turn.steers] == ["yes, proceed"]
    assert step2_turn.steers[0].author_id == "responder@example.test"
    assert step2_turn.steers[0].id.startswith("gate-answer-step-2")


async def test_needs_human_outcome_opens_agent_gate_and_resumes_step() -> None:
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(
                outcome="needs_human",
                gate=HumanGate(kind="input", prompt="What is the Q3 revenue figure?"),
            ),
            ScriptedTurn(),
            ScriptedTurn(),
        ]
    )
    state = fixture_run_state(run_id="run-gate-2")
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "gate_opened")
        opened = fake.events_of("gate_opened")[0]
        assert opened.payload["step_id"] == "step-1"
        assert opened.payload["kind"] == "input"
        assert opened.actor == f"{state.run_id}-root"
        assert opened.actor_type == "agent"
        assert await handle.query(AgentRunWorkflow.get_status) == "blocked_on_human"

        await handle.signal(AgentRunWorkflow.human_response, _respond("step-1", "$1.2M"))
        result = await handle.result()

    assert result.status == "completed"
    # The step resumed with its working transcript and the answer as context.
    resumed_turn = fake.turn_inputs[1]
    assert resumed_turn.step_id == "step-1"
    assert resumed_turn.working_transcript_ref is not None
    assert [s.body for s in resumed_turn.steers] == ["$1.2M"]
    assert fake.turn_calls == 3


async def test_gate_timeout_pause_keeps_gate_open_for_a_late_answer() -> None:
    fake = FakeRuntime(
        plan_gates={
            "step-2": HumanGate(
                kind="approval",
                prompt="Proceed?",
                timeout=timedelta(seconds=300),
                on_timeout="pause",
            )
        }
    )
    state = fixture_run_state(run_id="run-gate-3")
    async with _running(fake, state) as (env, handle):
        await _wait_event(fake, "gate_opened")
        await env.sleep(301)
        await _wait_event(fake, "gate_timed_out")
        await _wait_event(fake, "paused")
        timed_out = fake.events_of("gate_timed_out")[0]
        assert timed_out.payload["step_id"] == "step-2"
        assert timed_out.payload["on_timeout"] == "pause"
        assert timed_out.actor == "system"
        paused = fake.events_of("paused")[0]
        assert paused.actor == "system"
        assert paused.actor_type == "system"
        assert await handle.query(AgentRunWorkflow.get_status) == "paused"

        # The gate is still open: answer it, resume, and the run completes.
        await handle.signal(AgentRunWorkflow.human_response, _respond("step-2", "late yes"))
        await handle.signal(AgentRunWorkflow.resume, "operator@example.test")
        result = await handle.result()

    assert result.status == "completed"
    types = fake.event_types
    assert types.index("gate_timed_out") < types.index("paused")
    assert types.index("resumed") < types.index("gate_answered")
    assert result.land_report is not None and result.land_report.steps_done == 2


async def test_gate_timeout_skip_skips_the_step_and_continues() -> None:
    fake = FakeRuntime(
        plan_gates={
            "step-1": HumanGate(
                kind="action",
                prompt="Upload the ledger first",
                timeout=timedelta(seconds=60),
                on_timeout="skip",
            )
        }
    )
    state = fixture_run_state(run_id="run-gate-4")
    async with _running(fake, state) as (env, handle):
        await _wait_event(fake, "gate_opened")
        await env.sleep(61)
        result = await handle.result()

    assert result.status == "completed"
    assert result.land_report is not None
    assert result.land_report.status == "landed_partial"
    assert result.land_report.steps_done == 1
    assert result.land_report.steps_skipped == 1
    types = fake.event_types
    assert types.index("gate_timed_out") < types.index("step_skipped")
    skipped = fake.events_of("step_skipped")[0]
    assert skipped.payload["step_id"] == "step-1"
    assert fake.events_of("gate_timed_out")[0].payload["on_timeout"] == "skip"
    # Only step-2 ever ran a turn.
    assert fake.turn_calls == 1
    assert fake.turn_inputs[0].step_id == "step-2"


async def test_gate_timeout_fail_fails_the_step_and_run() -> None:
    fake = FakeRuntime(
        plan_gates={
            "step-2": HumanGate(
                kind="approval",
                prompt="Proceed?",
                timeout=timedelta(seconds=60),
                on_timeout="fail",
            )
        }
    )
    state = fixture_run_state(run_id="run-gate-5")
    async with _running(fake, state) as (env, handle):
        await _wait_event(fake, "gate_opened")
        await env.sleep(61)
        result = await handle.result()

    assert result.status == "failed"
    assert result.error is not None and "gate timed out" in result.error
    types = fake.event_types
    assert types[-3:] == ["gate_timed_out", "step_failed", "run_failed"]
    assert fake.events_of("step_failed")[0].payload["step_id"] == "step-2"


async def test_land_during_gate_wait_fires_no_timer_actions() -> None:
    fake = FakeRuntime(
        plan_gates={
            "step-2": HumanGate(
                kind="approval",
                prompt="Proceed?",
                timeout=timedelta(seconds=3600),
                on_timeout="fail",
            )
        }
    )
    state = fixture_run_state(run_id="run-gate-6")
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "gate_opened")
        await handle.signal(AgentRunWorkflow.land, "lander@example.test")
        result = await handle.result()

    # Landing wrapped up gracefully; the armed timeout never fired an action
    # even though awaiting the result skips far past the deadline.
    assert result.status == "completed"
    assert result.land_report is not None
    assert result.land_report.status == "landed_partial"
    assert "gate_timed_out" not in fake.event_types
    assert "step_failed" not in fake.event_types
    landing = fake.events_of("landing_started")[0]
    assert landing.actor == "lander@example.test"
