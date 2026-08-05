"""Steer workflow tests: notes drain into the next turn's context with their
audit event; redirects force an assessment whose outcome is either a
validated (and possibly approved) revision linked to the steer, an explicit
"plan already covers it" record, or a recorded validation rejection."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from _support.common import TEST_TASK_QUEUE, fixture_run_state, start_time_skipping_env
from _support.fakes import FakeRuntime, ScriptedTurn
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from convoy_core import PlanPatchOp, PlanStep, RunResult, RunState, SteerMessage
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


def _steer(
    steer_id: str, mode: str, body: str, author_id: str = "ops@example.test"
) -> SteerMessage:
    return SteerMessage.model_validate(
        {"id": steer_id, "author": "human", "author_id": author_id, "mode": mode, "body": body}
    )


async def _steer_mid_first_turn(fake: FakeRuntime, handle: Handle, message: SteerMessage) -> None:
    """Deliver a steer while turn 1 is verifiably in flight, so it drains
    into turn 2 deterministically."""
    async with asyncio.timeout(15):
        await fake.first_turn_started.wait()
    await handle.signal(AgentRunWorkflow.steer, message)
    fake.release_first_turn.set()


async def test_note_steer_drains_into_next_turn_context() -> None:
    fake = FakeRuntime(gate_first_turn=True)
    state = fixture_run_state(run_id="run-steer-1")
    note = _steer("steer-note-1", "note", "check the runway figure", author_id="alice@example.test")
    async with _running(fake, state) as (_, handle):
        await _steer_mid_first_turn(fake, handle, note)
        result = await handle.result()

    assert result.status == "completed"
    received = fake.events_of("steer_received")[0]
    assert received.actor == "alice@example.test"
    assert received.actor_type == "human"
    assert received.payload["steer_id"] == "steer-note-1"
    assert received.payload["mode"] == "note"
    assert received.payload["body"] == "check the runway figure"

    # Turn 1 saw no steers; turn 2 carries the drained note.
    assert fake.turn_inputs[0].steers == []
    assert [s.id for s in fake.turn_inputs[1].steers] == ["steer-note-1"]
    # Drained means drained: no later turn sees it again.
    assert all(not turn.steers for turn in fake.turn_inputs[2:])
    types = fake.event_types
    second_step_started = types.index("step_started", types.index("step_started") + 1)
    assert types.index("steer_received") < second_step_started


async def test_redirect_with_no_proposal_records_covered_assessment() -> None:
    fake = FakeRuntime(gate_first_turn=True)
    state = fixture_run_state(run_id="run-steer-2")
    redirect = _steer("steer-redir-1", "redirect", "focus on enterprise accounts")
    async with _running(fake, state) as (_, handle):
        await _steer_mid_first_turn(fake, handle, redirect)
        result = await handle.result()

    assert result.status == "completed"
    # The redirect drained into step-2's turn, which proposed nothing, so the
    # workflow recorded the "plan already covers it" assessment.
    assessments = fake.events_of("revision_rejected")
    assert len(assessments) == 1
    payload = assessments[0].payload
    assert payload["kind"] == "steer_assessment"
    assert payload["reason"] == "plan_already_covers"
    assert payload["steer_ids"] == ["steer-redir-1"]
    assert payload["plan_version"] == 1
    assert payload["transcript_ref"]["key"].startswith(f"runs/{state.run_id}/transcripts/")
    assert assessments[0].actor == f"{state.run_id}-root"
    assert assessments[0].actor_type == "agent"
    # No revision was applied and no snapshot archived.
    assert fake.events_of("revision_applied") == []
    assert fake.snapshots == []


async def test_redirect_proposal_flows_through_validation_and_applies() -> None:
    ops = (
        PlanPatchOp(
            op="add_step",
            step=PlanStep(id="step-3", description="address the redirect", depends_on=["step-2"]),
            after="step-2",
            reason="redirect needs an extra step",
        ),
    )
    fake = FakeRuntime(
        gate_first_turn=True,
        turns=[
            ScriptedTurn(),  # step-1 completes while the steer arrives
            ScriptedTurn(outcome="propose_revision", ops=ops),
            ScriptedTurn(),
        ],
    )
    state = fixture_run_state(run_id="run-steer-3")  # approval off
    redirect = _steer("steer-redir-2", "redirect", "also cover churn analysis")
    async with _running(fake, state) as (_, handle):
        await _steer_mid_first_turn(fake, handle, redirect)
        result = await handle.result()

    assert result.status == "completed"
    applied = fake.events_of("revision_applied")[0]
    assert applied.payload["plan_version"] == 2
    assert applied.payload["reason"] == "steer"
    assert applied.payload["steer_ids"] == ["steer-redir-2"]
    assert applied.payload["requires_approval"] is False
    assert applied.payload["run_status"] == "running"
    assert applied.actor == f"{state.run_id}-root"
    assert applied.actor_type == "agent"
    assert applied.payload["plan"]["version"] == 2
    # Snapshot of v2 was archived through the claim-check activity.
    assert [plan.version for plan in fake.snapshots] == [2]
    # All three steps (including the added one) completed.
    assert result.land_report is not None
    assert result.land_report.steps_done == 3
    # No approval events under a policy with approval off.
    assert fake.events_of("revision_approved") == []


async def test_invalid_proposal_is_recorded_and_run_continues() -> None:
    # The proposal edits the step that is currently running — immutable
    # history, so validation must reject it.
    ops = (
        PlanPatchOp(
            op="edit_step",
            step_id="step-1",
            changes={"description": "rewrite the running step"},
            reason="illegal edit",
        ),
    )
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(outcome="propose_revision", ops=ops),
            ScriptedTurn(),
        ]
    )
    state = fixture_run_state(run_id="run-steer-4")
    async with _running(fake, state) as (_, handle):
        result = await handle.result()

    assert result.status == "completed"
    rejected = fake.events_of("revision_rejected")[0]
    assert rejected.payload["kind"] == "validation"
    assert rejected.payload["steer_ids"] == []
    assert any("cannot edit step 'step-1'" in reason for reason in rejected.payload["reasons"])
    # The plan never changed: no revision applied, no snapshot, version 1.
    assert fake.events_of("revision_applied") == []
    assert fake.snapshots == []
    plans = [e.payload["plan"] for e in fake.events if isinstance(e.payload.get("plan"), dict)]
    assert plans[-1]["version"] == 1
    # The run still completed both fixture steps.
    assert result.land_report is not None
    assert result.land_report.steps_done == 2
