"""Turn-flow mechanics in the time-skipping environment: per-run key
provisioning, fresh pinned-header assembly before every turn, fallback
recording, and token aggregation into the land report."""

from decimal import Decimal

import pytest
from _support.common import TEST_TASK_QUEUE, fixture_run_state, start_time_skipping_env
from _support.fakes import FakeRuntime, ScriptedTurn
from temporalio.worker import Worker

from convoy_runtime.workflows.agent_run import AgentRunWorkflow

pytestmark = pytest.mark.anyio


async def _run(fake: FakeRuntime, run_id: str, **state_kwargs: object):
    state = fixture_run_state(run_id=run_id, **state_kwargs)  # type: ignore[arg-type]
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
        return await env.client.execute_workflow(
            AgentRunWorkflow.run,
            args=[state, "alice@example.test"],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )


async def test_virtual_key_provisioned_once_at_run_start_with_full_cap() -> None:
    fake = FakeRuntime()
    result = await _run(fake, "run-key-1", budget_cap_usd=Decimal("7.50"))
    assert result.status == "completed"
    assert fake.provision_calls == [("run-key-1", Decimal("7.50"))]
    # Provisioning precedes every turn: the key exists before money can move.
    assert fake.turn_calls == 2


async def test_pinned_header_rebuilt_fresh_for_every_turn() -> None:
    fake = FakeRuntime()
    result = await _run(fake, "run-header-1")
    assert result.status == "completed"
    assert fake.assemble_calls == fake.turn_calls == 2
    refs = [turn.pinned_ref.key for turn in fake.turn_inputs]
    assert refs == [
        "runs/run-header-1/pinned/turn-1.json",
        "runs/run-header-1/pinned/turn-2.json",
    ]


async def test_turn_context_carries_agent_binding_and_turn_number() -> None:
    fake = FakeRuntime()
    await _run(fake, "run-ctx-1")
    assert [ctx.turn for ctx in fake.turn_contexts] == [1, 2]
    assert all(ctx.agent.id == "run-ctx-1-root" for ctx in fake.turn_contexts)
    assert all(ctx.binding_ref.key == "runs/run-ctx-1/binding.json" for ctx in fake.turn_contexts)


async def test_fallback_is_recorded_on_step_done_events() -> None:
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(model_used="mock-fallback"),  # fell back
            ScriptedTurn(model_used="scripted-echo-1"),  # served by the primary
        ]
    )
    result = await _run(fake, "run-fb-1")
    assert result.status == "completed"
    step_done = [e for e in fake.events if e.type == "step_done"]
    assert [e.payload["model_used"] for e in step_done] == [
        "mock-fallback",
        "scripted-echo-1",
    ]
    assert [e.payload["model_fallback"] for e in step_done] == [True, False]
    assert all(e.payload["model_requested"] == "scripted-echo-1" for e in step_done)


async def test_tokens_aggregate_into_the_land_report() -> None:
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(input_tokens=100, output_tokens=10),
            ScriptedTurn(input_tokens=25, output_tokens=5),
        ]
    )
    result = await _run(fake, "run-tokens-1")
    report = result.land_report
    assert report is not None
    assert report.tokens.input_tokens == 125
    assert report.tokens.output_tokens == 15


async def test_working_transcript_resets_between_steps() -> None:
    # step-1 takes two turns (continue then done); step-2 starts clean.
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(outcome="continue"),
            ScriptedTurn(outcome="step_done"),
            ScriptedTurn(outcome="step_done"),
        ]
    )
    result = await _run(fake, "run-wt-1")
    assert result.status == "completed"
    assert fake.turn_calls == 3
    refs = [
        turn.working_transcript_ref.key if turn.working_transcript_ref else None
        for turn in fake.turn_inputs
    ]
    assert refs == [
        None,  # first turn of step-1
        "runs/run-wt-1/transcripts/step-1/turn-1.json",  # resumes step-1
        None,  # step-2 starts a fresh working transcript
    ]


async def test_failed_turn_emits_step_failed_then_run_failed() -> None:
    fake = FakeRuntime(turns=[ScriptedTurn(outcome="fail")])
    result = await _run(fake, "run-fail-1")
    assert result.status == "failed"
    assert result.error == "step step-1 failed"
    types = fake.event_types
    assert types[-2:] == ["step_failed", "run_failed"]
    step_failed = next(e for e in fake.events if e.type == "step_failed")
    assert step_failed.payload["step_id"] == "step-1"
