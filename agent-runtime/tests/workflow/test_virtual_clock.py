"""Virtual clock workflow behavior in the time-skipping environment: the
sandbox-kind guard, forward-only advancement, dual event stamps, idle
auto-advance semantics around human gates, and the ratio mapping."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from _support.common import (
    TEST_TASK_QUEUE,
    fixture_carry,
    fixture_run_state,
    start_time_skipping_env,
)
from _support.fakes import FakeRuntime, ScriptedTurn
from temporalio.client import WorkflowHandle
from temporalio.worker import Worker

from convoy_core import ClockConfig, HumanGate, RunResult, RunState
from convoy_runtime.carry import RunCarry
from convoy_runtime.signals import ClockAdvance, GateResponse
from convoy_runtime.workflows.agent_run import AgentRunWorkflow

pytestmark = pytest.mark.anyio

Handle = WorkflowHandle[AgentRunWorkflow, RunResult]


@asynccontextmanager
async def _running(fake: FakeRuntime, state: RunState, carry: RunCarry) -> AsyncGenerator[Handle]:
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
            args=[state, "alice@example.test", carry],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )
        yield handle


async def _wait_until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(30):
        while not condition():  # noqa: ASYNC110
            await asyncio.sleep(0.05)


def _gate(timeout_seconds: float | None, on_timeout: str = "skip") -> HumanGate:
    return HumanGate(
        kind="approval",
        prompt="proceed?",
        timeout=timedelta(seconds=timeout_seconds) if timeout_seconds is not None else None,
        on_timeout=on_timeout,  # type: ignore[arg-type]
    )


async def test_manual_advance_resolves_gate_timeout_at_the_virtual_deadline() -> None:
    fake = FakeRuntime(plan_gates={"step-2": _gate(3600)})
    state = fixture_run_state(run_id="run-vc-manual")
    carry = fixture_carry(clock=ClockConfig(mode="virtual", advance="manual"))
    async with _running(fake, state, carry) as handle:
        await _wait_until(lambda: bool(fake.events_of("gate_opened")))
        epoch = fake.events[0].virtual_ts
        assert epoch is not None
        # A backward (stale) advance is a no-op; the real one resolves the
        # timer at exactly the deadline.
        await handle.signal(
            AgentRunWorkflow.advance_time,
            ClockAdvance(to=epoch - timedelta(hours=1), actor="op@convoy.test"),
        )
        await handle.signal(
            AgentRunWorkflow.advance_time,
            ClockAdvance(to=epoch + timedelta(seconds=3600), actor="op@convoy.test"),
        )
        result = await handle.result()

    assert result.status == "completed"
    timed_out = fake.events_of("gate_timed_out")[0]
    assert timed_out.virtual_ts is not None and epoch is not None
    assert (timed_out.virtual_ts - epoch).total_seconds() == 3600
    assert timed_out.payload["advanced_by"] == "op@convoy.test"
    # Dual stamps everywhere, virtual time never regressing, sandbox flagged.
    assert all(e.virtual_ts is not None and e.ts is not None for e in fake.events)
    assert all(e.sandbox for e in fake.events)
    assert "step_skipped" in fake.event_types


async def test_advance_is_dropped_on_real_clock_runs() -> None:
    fake = FakeRuntime(gate_first_turn=True)
    state = fixture_run_state(run_id="run-vc-guard")
    carry = fixture_carry(kind="production")  # real clock, production binding
    async with _running(fake, state, carry) as handle:
        await fake.first_turn_started.wait()
        epoch = fake.events[0].ts
        await handle.signal(
            AgentRunWorkflow.advance_time,
            ClockAdvance(to=epoch + timedelta(days=5), actor="op@convoy.test"),
        )
        fake.release_first_turn.set()
        result = await handle.result()

    assert result.status == "completed"
    # No virtual clock exists: nothing was advanced, nothing dual-stamped,
    # and nothing is flagged as a rehearsal trajectory.
    assert all(e.virtual_ts is None for e in fake.events)
    assert all(not e.sandbox for e in fake.events)


async def test_on_idle_auto_advances_gate_timeouts_but_not_human_checkpoints() -> None:
    # step-1's gate has a timeout (auto-resolvable); step-2's gate has none —
    # a rehearsal checkpoint that must park until a human answers.
    fake = FakeRuntime(
        plan_gates={"step-1": _gate(86_400), "step-2": _gate(None)},
        turns=[ScriptedTurn()],
    )
    state = fixture_run_state(run_id="run-vc-idle")
    carry = fixture_carry(clock=ClockConfig(mode="virtual", advance="on_idle"))
    async with _running(fake, state, carry) as handle:
        # The day-long gate timeout compresses to nothing: on_idle advances
        # to the deadline and the skip fires without any signal.
        await _wait_until(lambda: "step_skipped" in fake.event_types)
        await _wait_until(lambda: len(fake.events_of("gate_opened")) == 2)
        # The checkpoint gate does not auto-advance: the run stays blocked.
        await asyncio.sleep(1.0)
        assert "gate_answered" not in fake.event_types
        assert await handle.query(AgentRunWorkflow.get_status) == "blocked_on_human"
        await handle.signal(
            AgentRunWorkflow.human_response,
            GateResponse(step_id="step-2", response="go ahead", actor="human@convoy.test"),
        )
        result = await handle.result()

    assert result.status == "completed"
    timed_out = fake.events_of("gate_timed_out")[0]
    epoch = fake.events[0].virtual_ts
    assert timed_out.virtual_ts is not None and epoch is not None
    assert (timed_out.virtual_ts - epoch).total_seconds() == 86_400
    assert timed_out.payload["advanced_by"] == "clock:on_idle"


async def test_scheduled_simulated_response_answers_a_checkpoint_at_a_virtual_instant() -> None:
    fake = FakeRuntime(plan_gates={"step-2": _gate(None)})
    state = fixture_run_state(run_id="run-vc-sim")
    carry = fixture_carry(clock=ClockConfig(mode="virtual", advance="on_idle"))
    async with _running(fake, state, carry) as handle:
        await _wait_until(lambda: bool(fake.events_of("gate_opened")))
        epoch = fake.events[0].virtual_ts
        assert epoch is not None
        # The scenario scripts a simulated human answering two virtual days
        # later; idle advancement compresses the wait to nothing.
        await handle.signal(
            AgentRunWorkflow.human_response,
            GateResponse(
                step_id="step-2",
                response="simulated: looks good",
                actor="sim-reviewer@convoy.test",
                at_virtual=epoch + timedelta(days=2),
            ),
        )
        result = await handle.result()

    assert result.status == "completed"
    answered = fake.events_of("gate_answered")[0]
    assert answered.actor == "sim-reviewer@convoy.test"
    assert answered.virtual_ts is not None and epoch is not None
    assert (answered.virtual_ts - epoch).total_seconds() == 2 * 86_400
    assert answered.payload["simulated_at"] is not None


async def test_ratio_clock_scales_gate_timeouts_and_stamps_virtual_time() -> None:
    # One virtual hour at ratio 60 is one real minute; the time-skipping
    # environment makes the real wait instantaneous either way.
    fake = FakeRuntime(plan_gates={"step-2": _gate(3600)})
    state = fixture_run_state(run_id="run-vc-ratio")
    carry = fixture_carry(clock=ClockConfig(mode="virtual", advance="ratio", ratio=60.0))
    async with _running(fake, state, carry) as handle:
        result = await handle.result()

    assert result.status == "completed"
    assert "gate_timed_out" in fake.event_types
    timed_out = fake.events_of("gate_timed_out")[0]
    epoch = fake.events[0].virtual_ts
    assert timed_out.virtual_ts is not None and epoch is not None
    virtual_elapsed = (timed_out.virtual_ts - epoch).total_seconds()
    real_elapsed = (timed_out.ts - fake.events[0].ts).total_seconds()
    assert virtual_elapsed >= 3600
    # The mapping holds: virtual elapsed is the real elapsed scaled by 60.
    assert abs(virtual_elapsed - real_elapsed * 60.0) < 1.0
