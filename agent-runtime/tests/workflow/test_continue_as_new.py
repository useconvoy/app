"""continue_as_new durability in the time-skipping environment.

The centerpiece is a 300-turn scripted rehearsal compressed via idle virtual
advancement: two human checkpoints answered by scheduled simulated reviewers
days apart in virtual time, 150 turns of work per step, a small turn limit
forcing hops every 40 turns, and mid-step folds throughout. The assertions
are the lossless-carry contract: the event sequence stays gapless and
duplicate-free across hops (the outbox key survives), budget and token
totals are exact, summaries and folds all land, virtual time only moves
forward, and the working transcript threads through every mid-step hop."""

import asyncio
import itertools
from datetime import timedelta
from decimal import Decimal

import pytest
from _support.common import (
    TEST_TASK_QUEUE,
    fixture_carry,
    fixture_run_state,
    start_time_skipping_env,
)
from _support.fakes import FakeRuntime, ScriptedTurn
from temporalio.worker import Worker

from convoy_core import ClockConfig, HumanGate, SteerMessage
from convoy_runtime.signals import GateResponse
from convoy_runtime.workflows.agent_run import AgentRunWorkflow

pytestmark = pytest.mark.anyio

DAY = 86_400


def _checkpoint() -> HumanGate:
    return HumanGate(kind="approval", prompt="rehearsal checkpoint", timeout=None)


def _three_hundred_turns() -> list[ScriptedTurn]:
    """Two 150-turn steps: continue x149 then step_done, twice."""
    step = [ScriptedTurn(outcome="continue")] * 149 + [ScriptedTurn(outcome="step_done")]
    return [*step, *step]


async def test_300_turn_rehearsal_hops_losslessly_under_idle_advancement() -> None:
    fake = FakeRuntime(
        turns=_three_hundred_turns(),
        plan_gates={"step-1": _checkpoint(), "step-2": _checkpoint()},
    )
    state = fixture_run_state(run_id="run-can-300")
    carry = fixture_carry(
        clock=ClockConfig(mode="virtual", advance="on_idle"),
        turn_limit=40,
        midstep_compaction_tokens=500,  # 19 tokens/turn -> a fold every 27 turns
    )
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
        async with asyncio.timeout(30):
            while not fake.events:  # noqa: ASYNC110
                await asyncio.sleep(0.05)
        epoch = fake.events[0].virtual_ts
        assert epoch is not None
        # Simulated reviewers answer the checkpoints on day 1 and day 3.
        await handle.signal(
            AgentRunWorkflow.human_response,
            GateResponse(
                step_id="step-1",
                response="day-1 review: proceed",
                actor="sim-day1@convoy.test",
                at_virtual=epoch + timedelta(days=1),
            ),
        )
        await handle.signal(
            AgentRunWorkflow.human_response,
            GateResponse(
                step_id="step-2",
                response="day-3 review: proceed",
                actor="sim-day3@convoy.test",
                at_virtual=epoch + timedelta(days=3),
            ),
        )
        result = await handle.result()
        durability = await env.client.get_workflow_handle(state.run_id).query(
            AgentRunWorkflow.get_durability
        )

    assert result.status == "completed"
    report = result.land_report
    assert report is not None
    assert report.status == "completed"

    # 300 logical turns executed exactly once each; the turn limit forced a
    # hop every 40 turns across the run's life.
    assert fake.turn_calls == 300
    assert durability["hops"] == 7
    assert durability["turn_count"] == 300
    assert durability["clock_kind"] == "virtual"

    # Lossless accounting across hops: exact spend and token totals.
    assert report.cost_usd == Decimal("0.03")
    assert report.tokens.input_tokens == 300 * 12
    assert report.tokens.output_tokens == 300 * 7

    # The event stream is gapless and duplicate-free across hops — the
    # outbox key (run_id, seq) held for the run's whole life.
    seqs = [e.seq for e in fake.events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert len({e.id for e in fake.events}) == len(fake.events)
    assert fake.event_types[-1] == "run_completed"

    # Compaction at both boundaries: each step distilled at its end, and the
    # long transcripts folded mid-step (5 folds per 150-turn step).
    compactions = fake.events_of("compaction_applied")
    assert len([e for e in compactions if e.payload["boundary"] == "step_end"]) == 2
    assert len([e for e in compactions if e.payload["boundary"] == "mid_step"]) == 10
    assert all(e.payload["archived_ref"] is not None for e in compactions)

    # Both checkpoints were answered by their simulated reviewers at their
    # scheduled virtual instants, days apart.
    answered = fake.events_of("gate_answered")
    assert [e.actor for e in answered] == ["sim-day1@convoy.test", "sim-day3@convoy.test"]
    assert epoch is not None
    stamps = [e.virtual_ts for e in answered]
    assert stamps[0] is not None and (stamps[0] - epoch).total_seconds() == DAY
    assert stamps[1] is not None and (stamps[1] - epoch).total_seconds() == 3 * DAY

    # Virtual time is dual-stamped everywhere and never regresses.
    virtuals = [e.virtual_ts for e in fake.events]
    assert all(v is not None for v in virtuals)
    assert all(
        a <= b  # type: ignore[operator]
        for a, b in itertools.pairwise(virtuals)
    )
    last = virtuals[-1]
    assert last is not None and (last - epoch).total_seconds() >= 3 * DAY

    # The working transcript threads through every hop: only each step's
    # first turn starts without a head (folds swap the head, never drop it).
    first_turns = {1, 151}
    for turn_input, ctx in zip(fake.turn_inputs, fake.turn_contexts, strict=True):
        if ctx.turn in first_turns:
            assert turn_input.working_transcript_ref is None
        else:
            assert turn_input.working_transcript_ref is not None, f"turn {ctx.turn} lost its head"

    # Step summaries survive the hops: the final turn sees step-1's summary.
    assert [s.step_id for s in fake.turn_inputs[-1].step_summaries] == ["step-1"]


async def test_open_gate_deadline_survives_a_hop() -> None:
    # Turn 2 requests a gate right as the segment hits its limit, so the hop
    # happens with the gate open and its timer armed; the next execution
    # re-arms the identical absolute deadline and the timeout still fires.
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(outcome="continue"),
            ScriptedTurn(
                outcome="needs_human",
                gate=HumanGate(
                    kind="input",
                    prompt="need a decision",
                    timeout=timedelta(seconds=3600),
                    on_timeout="skip",
                ),
            ),
            ScriptedTurn(outcome="step_done"),
        ],
    )
    state = fixture_run_state(run_id="run-can-gate")
    carry = fixture_carry(turn_limit=2)
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
        async with asyncio.timeout(30):
            while "gate_opened" not in fake.event_types:  # noqa: ASYNC110
                await asyncio.sleep(0.05)
        await env.sleep(3700)
        result = await handle.result()
        durability = await env.client.get_workflow_handle(state.run_id).query(
            AgentRunWorkflow.get_durability
        )

    assert result.status == "completed"
    assert durability["hops"] >= 1
    opened = fake.events_of("gate_opened")[0]
    timed_out = fake.events_of("gate_timed_out")[0]
    # The re-armed timer kept the original absolute deadline.
    assert timed_out.payload["deadline"] == opened.payload["deadline"]
    assert "step_skipped" in fake.event_types
    seqs = [e.seq for e in fake.events]
    assert seqs == list(range(1, len(seqs) + 1))


async def test_duplicate_steer_delivery_is_idempotent() -> None:
    # Temporal re-delivers signals buffered during a hop; the same steer id
    # arriving twice must drain exactly once.
    fake = FakeRuntime(gate_first_turn=True)
    state = fixture_run_state(run_id="run-can-steer")
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
        await fake.first_turn_started.wait()
        steer = SteerMessage(
            id="steer-dup-1",
            author="human",
            author_id="alice@example.test",
            mode="note",
            body="watch the burn rate",
        )
        await handle.signal(AgentRunWorkflow.steer, steer)
        await handle.signal(AgentRunWorkflow.steer, steer)
        fake.release_first_turn.set()
        result = await handle.result()

    assert result.status == "completed"
    assert len(fake.events_of("steer_received")) == 1
    delivered = [s.id for turn in fake.turn_inputs for s in turn.steers]
    assert delivered == ["steer-dup-1"]
