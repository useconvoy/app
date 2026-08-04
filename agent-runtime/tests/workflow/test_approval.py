"""Plan approval workflow tests: the initial plan blocks until approved;
wrong-version decisions are dropped; mid-run revisions re-enter the approval
wait per the policy scope; rejection records its reason and pauses; landing
wraps up an approval wait gracefully."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from _support.common import TEST_TASK_QUEUE, fixture_run_state, start_time_skipping_env
from _support.fakes import FakeRuntime, ScriptedTurn
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from convoy_core import PlanPatchOp, PlanStep, RunPolicy, RunResult, RunState
from convoy_runtime.signals import PlanApprovalDecision
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
    """True when the check holds continuously for a short real-time window —
    how the tests assert that something did NOT happen."""
    for _ in range(int(seconds / 0.05)):
        if not check():
            return False
        await asyncio.sleep(0.05)
    return check()


def _approval(
    version: int, *, approve: bool, actor: str, reason: str | None = None
) -> PlanApprovalDecision:
    return PlanApprovalDecision(plan_version=version, approve=approve, reason=reason, actor=actor)


def _approval_policy(**overrides: object) -> RunPolicy:
    return RunPolicy.model_validate({"require_plan_approval": True, **overrides})


def _add_step_ops() -> tuple[PlanPatchOp, ...]:
    return (
        PlanPatchOp(
            op="add_step",
            step=PlanStep(id="step-3", description="follow-up work", depends_on=["step-2"]),
            after="step-2",
            reason="more work discovered",
        ),
    )


def _minor_ops() -> tuple[PlanPatchOp, ...]:
    return (
        PlanPatchOp(
            op="set_budget_slice",
            step_id="step-2",
            changes={"budget_slice": "1.5"},
            reason="reserve for the summary",
        ),
    )


async def test_initial_approval_blocks_until_signal() -> None:
    fake = FakeRuntime()
    state = fixture_run_state(run_id="run-approve-1", policy=_approval_policy())
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "plan_created")
        assert await handle.query(AgentRunWorkflow.get_status) == "awaiting_approval"
        # No turn may run while approval is pending.
        assert await _settled(lambda: fake.turn_calls == 0)
        assert "step_started" not in fake.event_types

        await handle.signal(
            AgentRunWorkflow.approve_plan,
            _approval(1, approve=True, actor="approver@example.test"),
        )
        await _wait_event(fake, "revision_approved")
        plan = await handle.query(AgentRunWorkflow.get_plan)
        assert plan is not None
        assert plan.revisions[0].approved_by == "approver@example.test"
        result = await handle.result()

    assert result.status == "completed"
    approved = fake.events_of("revision_approved")[0]
    assert approved.actor == "approver@example.test"
    assert approved.actor_type == "human"
    assert approved.payload["plan_version"] == 1
    plan_created = fake.events_of("plan_created")[0]
    assert plan_created.payload["run_status"] == "awaiting_approval"
    types = fake.event_types
    assert types.index("revision_approved") < types.index("step_started")


async def test_wrong_plan_version_approval_is_dropped() -> None:
    fake = FakeRuntime()
    state = fixture_run_state(run_id="run-approve-2", policy=_approval_policy())
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "plan_created")
        await handle.signal(
            AgentRunWorkflow.approve_plan,
            _approval(99, approve=True, actor="approver@example.test"),
        )
        # The mismatched approval must not unblock the run.
        assert await _settled(lambda: fake.turn_calls == 0)
        assert await handle.query(AgentRunWorkflow.get_status) == "awaiting_approval"
        assert fake.events_of("revision_approved") == []

        await handle.signal(
            AgentRunWorkflow.approve_plan,
            _approval(1, approve=True, actor="approver@example.test"),
        )
        result = await handle.result()
    assert result.status == "completed"
    assert len(fake.events_of("revision_approved")) == 1


async def test_rejection_records_reason_and_pauses() -> None:
    fake = FakeRuntime()
    state = fixture_run_state(run_id="run-reject-1", policy=_approval_policy())
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "plan_created")
        await handle.signal(
            AgentRunWorkflow.approve_plan,
            _approval(1, approve=False, actor="rejector@example.test", reason="wrong direction"),
        )
        await _wait_event(fake, "paused")
        assert await handle.query(AgentRunWorkflow.get_status) == "paused"
        rejected = fake.events_of("revision_rejected")[0]
        assert rejected.actor == "rejector@example.test"
        assert rejected.payload["reason"] == "wrong direction"
        assert rejected.payload["kind"] == "human_rejection"
        assert rejected.payload["plan_version"] == 1
        paused = fake.events_of("paused")[0]
        assert paused.actor == "rejector@example.test"

        # Resume alone does not execute an unapproved plan: the run re-enters
        # the approval wait.
        await handle.signal(AgentRunWorkflow.resume, "operator@example.test")
        await _wait_event(fake, "resumed")
        assert fake.events_of("resumed")[0].payload["run_status"] == "awaiting_approval"
        assert await _settled(lambda: fake.turn_calls == 0)
        assert await handle.query(AgentRunWorkflow.get_status) == "awaiting_approval"

        # A follow-up approval finally releases execution.
        await handle.signal(
            AgentRunWorkflow.approve_plan,
            _approval(1, approve=True, actor="approver@example.test"),
        )
        result = await handle.result()
    assert result.status == "completed"


async def test_major_revision_reenters_awaiting_under_major_scope() -> None:
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(outcome="propose_revision", ops=_add_step_ops()),
            ScriptedTurn(),
        ]
    )
    state = fixture_run_state(
        run_id="run-approve-3", policy=_approval_policy(approval_scope="major_revisions")
    )
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "plan_created")
        await handle.signal(
            AgentRunWorkflow.approve_plan, _approval(1, approve=True, actor="a@example.test")
        )
        await _wait_event(fake, "revision_applied")
        applied = fake.events_of("revision_applied")[0]
        assert applied.payload["requires_approval"] is True
        assert applied.payload["run_status"] == "awaiting_approval"
        assert applied.payload["plan_version"] == 2
        assert applied.actor_type == "agent"
        assert await handle.query(AgentRunWorkflow.get_status) == "awaiting_approval"
        # Execution stays blocked until the revision is approved.
        turns_at_block = fake.turn_calls
        assert await _settled(lambda: fake.turn_calls == turns_at_block)

        await handle.signal(
            AgentRunWorkflow.approve_plan, _approval(2, approve=True, actor="b@example.test")
        )
        result = await handle.result()
    assert result.status == "completed"
    assert result.land_report is not None
    assert result.land_report.steps_done == 3
    # The archived snapshot carries the bumped version.
    assert [plan.version for plan in fake.snapshots] == [2]
    approvals = fake.events_of("revision_approved")
    assert [e.payload["plan_version"] for e in approvals] == [1, 2]
    assert approvals[1].actor == "b@example.test"


async def test_minor_revision_skips_approval_under_major_scope() -> None:
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(outcome="propose_revision", ops=_minor_ops()),
            ScriptedTurn(),
        ]
    )
    state = fixture_run_state(
        run_id="run-approve-4", policy=_approval_policy(approval_scope="major_revisions")
    )
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "plan_created")
        await handle.signal(
            AgentRunWorkflow.approve_plan, _approval(1, approve=True, actor="a@example.test")
        )
        result = await handle.result()
    assert result.status == "completed"
    applied = fake.events_of("revision_applied")[0]
    assert applied.payload["requires_approval"] is False
    assert applied.payload["run_status"] == "running"
    # Only the initial plan needed a human decision.
    assert len(fake.events_of("revision_approved")) == 1


async def test_all_revisions_scope_gates_minor_ops() -> None:
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(outcome="propose_revision", ops=_minor_ops()),
            ScriptedTurn(),
        ]
    )
    state = fixture_run_state(
        run_id="run-approve-5", policy=_approval_policy(approval_scope="all_revisions")
    )
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "plan_created")
        await handle.signal(
            AgentRunWorkflow.approve_plan, _approval(1, approve=True, actor="a@example.test")
        )
        await _wait_event(fake, "revision_applied")
        assert fake.events_of("revision_applied")[0].payload["requires_approval"] is True
        await handle.signal(
            AgentRunWorkflow.approve_plan, _approval(2, approve=True, actor="a@example.test")
        )
        result = await handle.result()
    assert result.status == "completed"
    assert len(fake.events_of("revision_approved")) == 2


async def test_initial_scope_never_reapproves_revisions() -> None:
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(outcome="propose_revision", ops=_add_step_ops()),
            ScriptedTurn(),
        ]
    )
    state = fixture_run_state(
        run_id="run-approve-6", policy=_approval_policy(approval_scope="initial")
    )
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "plan_created")
        # The very first plan still needs its approval.
        assert await handle.query(AgentRunWorkflow.get_status) == "awaiting_approval"
        await handle.signal(
            AgentRunWorkflow.approve_plan, _approval(1, approve=True, actor="a@example.test")
        )
        result = await handle.result()
    assert result.status == "completed"
    applied = fake.events_of("revision_applied")[0]
    assert applied.payload["requires_approval"] is False
    assert len(fake.events_of("revision_approved")) == 1
    assert result.land_report is not None and result.land_report.steps_done == 3


async def test_land_during_awaiting_approval_wraps_up() -> None:
    fake = FakeRuntime()
    state = fixture_run_state(
        run_id="run-approve-7",
        policy=_approval_policy(),
        budget_cap_usd=Decimal("5"),
    )
    async with _running(fake, state) as (_, handle):
        await _wait_event(fake, "plan_created")
        assert await handle.query(AgentRunWorkflow.get_status) == "awaiting_approval"
        await handle.signal(AgentRunWorkflow.land, "lander@example.test")
        result = await handle.result()
    assert result.status == "completed"
    assert result.land_report is not None
    assert result.land_report.status == "landed_partial"
    assert result.land_report.steps_done == 0
    landing = fake.events_of("landing_started")[0]
    assert landing.actor == "lander@example.test"
    assert fake.turn_calls == 0
    assert fake.events_of("revision_approved") == []
