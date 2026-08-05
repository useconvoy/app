"""Promoted-tool workflow mechanics in the time-skipping environment: the
promote loop schedules the call as its own keyed activity, re-enters the turn
with the claim-checked result, absorbs partial costs, keys side effects so
retries never double-fire, and chains sandbox jobs through workspace
snapshots."""

from decimal import Decimal

import pytest
from _support.common import TEST_TASK_QUEUE, fixture_run_state, start_time_skipping_env
from _support.fakes import FakeRuntime, ScriptedTurn
from temporalio.worker import Worker

from convoy_core import RunResult
from convoy_runtime.providers.promoted import promoted_call_key
from convoy_runtime.workflows.agent_run import AgentRunWorkflow

pytestmark = pytest.mark.anyio


async def _run(fake: FakeRuntime, run_id: str) -> RunResult:
    state = fixture_run_state(run_id=run_id)
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
            id=run_id,
            task_queue=TEST_TASK_QUEUE,
        )


async def test_promote_runs_the_tool_as_its_own_activity_then_resumes_the_turn() -> None:
    fake = FakeRuntime(
        turns=[ScriptedTurn(promote_tool="kb_delete"), ScriptedTurn()],
    )
    result = await _run(fake, "run-promote-1")
    assert result.status == "completed"

    # One promoted call, keyed by hash(run_id, step_id, turn, call_index).
    key = promoted_call_key("run-promote-1", "step-1", 1, 0)
    assert [r.call.idempotency_key for r in fake.promoted_requests] == [key]
    assert fake.journal.executions(key) == 1

    # Three run_turn executions: the promoting turn, its re-entry with the
    # claim-checked result in context, and step-2's plain turn.
    assert fake.turn_calls == 3
    resume = fake.turn_contexts[1].resume
    assert resume is not None
    assert resume.tool_id == "kb_delete"
    assert resume.call_index == 0
    assert resume.idempotency_key == key
    assert resume.result_ref.key == f"runs/run-promote-1/promoted/{key}.json"
    assert fake.turn_contexts[0].resume is None
    assert fake.turn_contexts[2].resume is None

    # Same logical turn on both sides of the promotion; step-2 is turn 2.
    assert [ctx.turn for ctx in fake.turn_contexts] == [1, 1, 2]

    # The promoting partial result and the resumed result both billed.
    assert result.land_report is not None
    assert result.land_report.cost_usd == Decimal("0.0003")

    types = fake.event_types
    assert types.count("step_started") == 2
    assert types.count("step_done") == 2
    assert types.count("compaction_applied") == 2


async def test_promoted_activity_retry_reuses_the_key_and_never_doubles_the_effect() -> None:
    fake = FakeRuntime(
        turns=[ScriptedTurn(promote_tool="kb_delete"), ScriptedTurn()],
        fail_promoted_attempts=1,  # the side effect lands, then the activity dies
    )
    result = await _run(fake, "run-promote-retry")
    assert result.status == "completed"

    key = promoted_call_key("run-promote-retry", "step-1", 1, 0)
    # The retry arrived with the same key and hit the journal: two requests,
    # one execution — the side effect fired exactly once.
    assert fake.journal.requests(key) == 2
    assert fake.journal.executions(key) == 1
    assert len(fake.promoted_requests) == 2
    assert {r.call.idempotency_key for r in fake.promoted_requests} == {key}


async def test_sandbox_jobs_chain_through_workspace_snapshots() -> None:
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(promote_tool="sandbox_exec"),
            ScriptedTurn(promote_tool="sandbox_exec"),
        ],
    )
    result = await _run(fake, "run-sandbox-chain")
    assert result.status == "completed"

    key_1 = promoted_call_key("run-sandbox-chain", "step-1", 1, 0)
    key_2 = promoted_call_key("run-sandbox-chain", "step-2", 2, 0)
    assert [r.call.idempotency_key for r in fake.sandbox_requests] == [key_1, key_2]

    # The first job starts from nothing; the second starts from the first
    # job's snapshot — the workspace truth carried by the workflow.
    assert fake.sandbox_requests[0].snapshot_ref is None
    second_start = fake.sandbox_requests[1].snapshot_ref
    assert second_start is not None
    assert second_start.key == f"sandboxes/run-sandbox-chain/snapshot-{key_1}.tar"

    # The snapshot chain is observable on step completion events.
    step_done = fake.events_of("step_done")
    assert (
        step_done[0].payload["sandbox_snapshot_ref"]["key"]
        == f"sandboxes/run-sandbox-chain/snapshot-{key_1}.tar"
    )
    assert (
        step_done[1].payload["sandbox_snapshot_ref"]["key"]
        == f"sandboxes/run-sandbox-chain/snapshot-{key_2}.tar"
    )
    assert fake.journal.executions(key_1) == 1
    assert fake.journal.executions(key_2) == 1
