"""Fan-out workflow tests: spawn/gather within the concurrency window,
reservation carve/refund math (including overshoot and early cheap landings),
deterministic spawn rejection (depth, layer, group size), every partial-
failure policy, the land cascade, pause semantics over in-flight children,
and the recorded parent-close cancellation contract that keeps children
from outliving the run."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from _support.common import TEST_TASK_QUEUE, fixture_run_state, start_time_skipping_env
from _support.fakes import FakeRuntime, ScriptedTurn
from temporalio.api.enums.v1 import ParentClosePolicy
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from convoy_core import RunPolicy, RunResult, RunState
from convoy_runtime.activities.plan import FanoutFixture
from convoy_runtime.signals import GateResponse
from convoy_runtime.workflows.agent_run import AgentRunWorkflow
from convoy_runtime.workflows.subagent import SubagentWorkflow

pytestmark = pytest.mark.anyio

Handle = WorkflowHandle[AgentRunWorkflow, RunResult]

STARTER = "starter@example.test"


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
            workflows=[AgentRunWorkflow, SubagentWorkflow],
            activities=fake.activities,
        ),
    ):
        handle = await env.client.start_workflow(
            AgentRunWorkflow.run,
            args=[state, STARTER],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )
        yield env, handle


async def _wait_until(check: Callable[[], bool], seconds: float = 15.0) -> None:
    async with asyncio.timeout(seconds):
        while not check():  # noqa: ASYNC110
            await asyncio.sleep(0.05)


async def _settled(check: Callable[[], bool], seconds: float = 0.4) -> bool:
    for _ in range(int(seconds / 0.05)):
        if not check():
            return False
        await asyncio.sleep(0.05)
    return check()


def _parent_budget_walk(fake: FakeRuntime, run_id: str, cap: Decimal) -> None:
    """The reservation invariant, asserted at every parent budget snapshot:
    committed (spent + reserved) never exceeds the cap — and when a child
    overshoot pushes it past, the run must have declared budget exhaustion
    (a violation is never silent)."""
    parent_events = [e for e in fake.events if e.run_id == run_id]
    declared = any(e.type == "budget_exhausted" for e in parent_events)
    snapshots = 0
    for event in parent_events:
        raw = event.payload.get("budget")
        if isinstance(raw, dict):
            budget = cast("dict[str, Any]", raw)
            snapshots += 1
            committed = Decimal(str(budget["spent_usd"])) + Decimal(str(budget["reserved_usd"]))
            assert committed <= cap or declared, (
                f"spent+reserved {committed} exceeds cap {cap} at {event.type} "
                f"without budget_exhausted"
            )
    assert snapshots, "no parent budget snapshots to check"


def _parent_types(fake: FakeRuntime, run_id: str) -> list[str]:
    return [e.type for e in fake.events if e.run_id == run_id]


def _final_parent_budget(fake: FakeRuntime, run_id: str) -> dict[str, str]:
    """The parent's own run_completed budget snapshot (children emit their
    own run_completed events without one)."""
    completed = [e for e in fake.events if e.run_id == run_id and e.type == "run_completed"]
    assert completed, f"no parent run_completed event for {run_id}"
    budget = completed[-1].payload["budget"]
    assert isinstance(budget, dict)
    return cast("dict[str, str]", budget)


# ------------------------------------------------------------ clean fan-out


async def test_clean_fanout_join_within_concurrency_window() -> None:
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=3, budget_slice=Decimal("1.00")),
        hold_child_turns=True,
    )
    state = fixture_run_state(
        run_id="run-fan-clean",
        max_children=3,
        policy=RunPolicy(require_plan_approval=False, max_parallel=2),
    )
    async with _running(fake, state) as (_, handle):
        # Concurrency stays within min(max_parallel, max_children) = 2: two
        # children hold in flight and the third never starts while they do.
        await _wait_until(lambda: fake.children_holding == 2)
        assert await _settled(lambda: fake.children_holding == 2)
        assert fake.child_turns_started == 2
        fake.release_children.set()
        result = await handle.result()

    assert result.status == "completed"
    assert fake.max_children_holding == 2
    report = result.land_report
    assert report is not None
    assert report.status == "completed"
    assert report.steps_done == 5  # step-1, three members, the join
    assert report.steps_failed == 0

    # Every member spawned and landed with linkage, and each child got its
    # own hard-capped virtual key at exactly its slice.
    spawned = [e for e in fake.events_of("child_spawned")]
    landed = [e for e in fake.events_of("child_landed")]
    assert {e.payload["step_id"] for e in spawned} == {"fan-1", "fan-2", "fan-3"}
    assert {e.payload["step_id"] for e in landed} == {"fan-1", "fan-2", "fan-3"}
    child_ids = {str(e.payload["child_run_id"]) for e in spawned}
    assert child_ids == {
        "run-fan-clean--fan-1-a1",
        "run-fan-clean--fan-2-a1",
        "run-fan-clean--fan-3-a1",
    }
    assert {e.payload["group_id"] for e in spawned} == {"fanout-1"}
    for child_id in child_ids:
        assert (child_id, Decimal("1.00")) in fake.provision_calls

    # SubagentResults — never transcripts — reached the parent: each landed
    # child was archived, and the join turn saw exactly the three compacted
    # summaries (headline + refs), with the answers' outputs as step outputs.
    assert sorted(r.step_id for r in fake.archived_results) == ["fan-1", "fan-2", "fan-3"]
    assert all(r.status == "done" for r in fake.archived_results)
    join_turn = fake.turn_inputs[-1]
    assert join_turn.step_id == "step-2"
    assert sorted(s.step_id for s in join_turn.step_summaries) == ["fan-1", "fan-2", "fan-3"]
    for summary in join_turn.step_summaries:
        assert summary.headline.startswith("Completed:")
        assert summary.summary_ref.key.endswith("/result.json")
        assert summary.transcript_ref.key  # full fidelity stays a ref

    # The children were briefed with their dependency's outputs, one layer
    # down, against their own run ids.
    assert len(fake.subagent_briefs) == 3
    for brief in fake.subagent_briefs:
        assert brief.parent_run_id == "run-fan-clean"
        assert brief.agent.layer == 1
        assert brief.agent.parent_id == "run-fan-clean-root"
        assert brief.budget_cap_usd == Decimal("1.00")
        assert [ref.key for ref in brief.input_refs] == ["runs/run-fan-clean/outputs/step-1.json"]

    _parent_budget_walk(fake, "run-fan-clean", Decimal("10"))
    # All reservations refunded: nothing left reserved at the end.
    assert Decimal(str(_final_parent_budget(fake, "run-fan-clean")["reserved_usd"])) == 0


async def test_serialized_fanout_reservation_carve_and_refund_sequence() -> None:
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=2, budget_slice=Decimal("1.50")),
        child_turns={
            "fan-1": [ScriptedTurn(cost_usd=Decimal("0.30"))],
            "fan-2": [ScriptedTurn(cost_usd=Decimal("0.20"))],
        },
    )
    state = fixture_run_state(
        run_id="run-fan-reserve",
        max_children=2,
        policy=RunPolicy(require_plan_approval=False, max_parallel=1),
    )
    async with _running(fake, state) as (_, handle):
        result = await handle.result()

    assert result.status == "completed"
    spawned = fake.events_of("child_spawned")
    landed = fake.events_of("child_landed")
    assert [e.payload["step_id"] for e in spawned] == ["fan-1", "fan-2"]
    assert [e.payload["step_id"] for e in landed] == ["fan-1", "fan-2"]

    # Carve on spawn: the slice moves into reserved before the child runs.
    def budget_of(event_payload: dict[str, object]) -> tuple[Decimal, Decimal]:
        raw = event_payload["budget"]
        assert isinstance(raw, dict)
        budget = cast("dict[str, Any]", raw)
        return Decimal(str(budget["spent_usd"])), Decimal(str(budget["reserved_usd"]))

    assert budget_of(spawned[0].payload) == (Decimal("0.0001"), Decimal("1.50"))
    # Refund on landing: actual cost moves to spent, the rest comes back —
    # the first child landing early and cheap frees its slice while the
    # second is still unspawned.
    assert budget_of(landed[0].payload) == (Decimal("0.3001"), Decimal("0"))
    assert landed[0].payload["refund_usd"] == "1.20"
    assert landed[0].payload["slice_reserved"] == "1.50"
    assert budget_of(spawned[1].payload) == (Decimal("0.3001"), Decimal("1.50"))
    assert budget_of(landed[1].payload) == (Decimal("0.5001"), Decimal("0"))
    assert landed[1].payload["refund_usd"] == "1.30"

    _parent_budget_walk(fake, "run-fan-reserve", Decimal("10"))
    final_budget = _final_parent_budget(fake, "run-fan-reserve")
    assert Decimal(str(final_budget["spent_usd"])) == Decimal("0.5002")
    assert Decimal(str(final_budget["reserved_usd"])) == 0


# ----------------------------------------------------------- budget guards


async def test_child_exceeding_slice_is_capped_by_its_own_enforcement() -> None:
    # The child wants three turns at 0.40 each against a 0.50 slice: its own
    # boundary stops it after the second turn (spend 0.80 >= cap), wrapping
    # up as landed_partial rather than running away.
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=1, budget_slice=Decimal("0.50")),
        child_turns={
            "fan-1": [
                ScriptedTurn(cost_usd=Decimal("0.40"), outcome="continue"),
                ScriptedTurn(cost_usd=Decimal("0.40"), outcome="continue"),
                ScriptedTurn(cost_usd=Decimal("0.40"), outcome="step_done"),
            ]
        },
    )
    state = fixture_run_state(run_id="run-fan-slice-cap", max_children=1)
    async with _running(fake, state) as (_, handle):
        result = await handle.result()

    assert result.status == "completed"
    child_events = [e for e in fake.events if e.run_id == "run-fan-slice-cap--fan-1-a1"]
    assert "budget_exhausted" in [e.type for e in child_events]
    landed = fake.events_of("child_landed")[0]
    assert landed.payload["status"] == "landed_partial"
    assert landed.payload["cost_usd"] == "0.80"
    assert landed.payload["refund_usd"] == "0"  # nothing left to refund

    # Mid-run landed_partial counts as a failed member; the default policy
    # releases it so the join still proceeds, with the gap flagged.
    types = _parent_types(fake, "run-fan-slice-cap")
    assert "step_failed" in types
    started_join = [
        e
        for e in fake.events_of("step_started")
        if e.run_id == "run-fan-slice-cap" and e.payload["step_id"] == "step-2"
    ]
    assert started_join[0].payload["joined_with_failures"] == ["fan-1"]
    _parent_budget_walk(fake, "run-fan-slice-cap", Decimal("10"))


async def test_child_overshoot_beyond_cap_triggers_parent_budget_policy() -> None:
    # A single expensive turn can overshoot the slice (spend is only known
    # after the turn, exactly as for the parent). The parent books the real
    # cost; committed money passes the cap, so the budget policy fires at
    # the next boundary — the invariant never fails silently.
    fake = FakeRuntime(
        turns=[ScriptedTurn(cost_usd=Decimal("0.40"))],
        plan_fanout=FanoutFixture(size=1, budget_slice=Decimal("0.50")),
        child_turns={"fan-1": [ScriptedTurn(cost_usd=Decimal("2.30"))]},
    )
    state = fixture_run_state(
        run_id="run-fan-overshoot",
        budget_cap_usd=Decimal("2.50"),
        max_children=1,
        policy=RunPolicy(require_plan_approval=False, on_budget_exhausted="land"),
    )
    async with _running(fake, state) as (_, handle):
        result = await handle.result()

    assert result.status == "completed"
    landed = fake.events_of("child_landed")[0]
    assert landed.payload["status"] == "done"
    assert landed.payload["cost_usd"] == "2.30"
    assert landed.payload["refund_usd"] == "0"
    types = _parent_types(fake, "run-fan-overshoot")
    assert types.index("child_landed") < types.index("budget_exhausted")
    assert "landing_started" in types
    report = result.land_report
    assert report is not None
    assert report.status == "landed_partial"  # the join never got to run
    assert report.cost_usd == Decimal("2.70")
    _parent_budget_walk(fake, "run-fan-overshoot", Decimal("2.50"))


async def test_dead_child_counts_its_whole_slice_as_spent() -> None:
    # The child workflow itself dies (its wrap-up fails), so no result ever
    # arrives: the parent books the full reservation as spent — budget truth
    # never understates — and the member fails into the group policy.
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=2, budget_slice=Decimal("1.00")),
        fail_wrap_for={"fan-1"},
    )
    state = fixture_run_state(
        run_id="run-fan-dead-child",
        max_children=2,
        policy=RunPolicy(require_plan_approval=False, max_parallel=1),
    )
    async with _running(fake, state) as (_, handle):
        result = await handle.result()

    assert result.status == "completed"
    landed = {str(e.payload["step_id"]): e for e in fake.events_of("child_landed")}
    dead = landed["fan-1"].payload
    assert dead["status"] == "failed"
    assert dead["cost_assumed"] is True
    assert dead["cost_usd"] == "1.00"
    assert Decimal(str(dead["refund_usd"])) == 0
    assert dead["error"] is not None
    alive = landed["fan-2"].payload
    assert alive["status"] == "done"
    assert alive["cost_assumed"] is False
    # Only the surviving child produced a summary for the join.
    join_turn = fake.turn_inputs[-1]
    assert [s.step_id for s in join_turn.step_summaries] == ["fan-2"]
    _parent_budget_walk(fake, "run-fan-dead-child", Decimal("10"))
    assert Decimal(str(_final_parent_budget(fake, "run-fan-dead-child")["reserved_usd"])) == 0


# ------------------------------------------------------ deterministic guards


async def test_spawn_beyond_max_depth_is_rejected_deterministically() -> None:
    fake = FakeRuntime(plan_fanout=FanoutFixture(size=2, budget_slice=Decimal("1.00")))
    state = fixture_run_state(
        run_id="run-fan-depth",
        max_children=2,
        policy=RunPolicy(require_plan_approval=False, max_depth=0),
    )
    async with _running(fake, state) as (_, handle):
        result = await handle.result()

    assert result.status == "failed"
    assert result.error is not None and "max_depth" in result.error
    assert "child_spawned" not in fake.event_types  # no reservation ever carved
    failed_steps = [e.payload["step_id"] for e in fake.events_of("step_failed")]
    assert failed_steps == ["fan-1", "fan-2"]
    assert fake.event_types[-1] == "run_failed"


async def test_child_layer_must_sit_one_below_parent() -> None:
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=1, budget_slice=Decimal("1.00"), child_layer=2)
    )
    state = fixture_run_state(
        run_id="run-fan-layer",
        max_children=1,
        policy=RunPolicy(require_plan_approval=False, max_depth=2),
    )
    async with _running(fake, state) as (_, handle):
        result = await handle.result()

    assert result.status == "failed"
    assert result.error is not None and "one below its parent" in result.error
    assert "child_spawned" not in fake.event_types


async def test_group_larger_than_max_children_is_rejected_at_spawn() -> None:
    fake = FakeRuntime(plan_fanout=FanoutFixture(size=3, budget_slice=Decimal("1.00")))
    state = fixture_run_state(run_id="run-fan-size", max_children=2)
    async with _running(fake, state) as (_, handle):
        result = await handle.result()

    assert result.status == "failed"
    assert result.error is not None and "max_children" in result.error
    assert "child_spawned" not in fake.event_types


# ------------------------------------------------- partial-failure policies


async def test_one_child_fails_join_with_partials() -> None:
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=2, budget_slice=Decimal("1.00")),
        child_turns={"fan-2": [ScriptedTurn(outcome="fail")]},
    )
    state = fixture_run_state(
        run_id="run-fan-partial",
        max_children=2,
        policy=RunPolicy(require_plan_approval=False, max_parallel=1),
    )
    async with _running(fake, state) as (_, handle):
        result = await handle.result()

    assert result.status == "completed"
    report = result.land_report
    assert report is not None
    assert report.steps_failed == 1
    assert report.status == "completed"  # members are optional; the join ran

    landed = {str(e.payload["step_id"]): e for e in fake.events_of("child_landed")}
    assert landed["fan-1"].payload["status"] == "done"
    assert landed["fan-2"].payload["status"] == "failed"
    # The failed child still refunded its unspent slice.
    assert Decimal(str(landed["fan-2"].payload["refund_usd"])) > 0

    # The gap is flagged in events (the join's start) and fed to the join
    # turn as context: the survivor's summary plus the failed child's own
    # failure summary (compaction still applies to failures).
    join_started = next(
        e
        for e in fake.events_of("step_started")
        if e.run_id == "run-fan-partial" and e.payload["step_id"] == "step-2"
    )
    assert join_started.payload["joined_with_failures"] == ["fan-2"]
    join_turn = fake.turn_inputs[-1]
    assert join_turn.step_id == "step-2"
    assert [s.step_id for s in join_turn.step_summaries] == ["fan-1", "fan-2"]
    headlines = {s.step_id: s.headline for s in join_turn.step_summaries}
    assert headlines["fan-1"].startswith("Completed:")
    assert headlines["fan-2"].startswith("Failed:")
    gap_notes = [s for s in join_turn.steers if s.author == "system"]
    assert len(gap_notes) == 1
    assert "fan-2" in gap_notes[0].body
    _parent_budget_walk(fake, "run-fan-partial", Decimal("10"))


async def test_block_on_human_opens_gate_on_the_join() -> None:
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=2, budget_slice=Decimal("1.00")),
        child_turns={"fan-2": [ScriptedTurn(outcome="fail")]},
    )
    state = fixture_run_state(
        run_id="run-fan-gate",
        max_children=2,
        policy=RunPolicy(
            require_plan_approval=False,
            max_parallel=1,
            on_group_partial_failure="block_on_human",
        ),
    )
    async with _running(fake, state) as (_, handle):
        await _wait_until(lambda: bool(fake.events_of("gate_opened")))
        opened = fake.events_of("gate_opened")[0]
        assert opened.payload["step_id"] == "step-2"
        assert opened.payload["kind"] == "approval"
        assert "fan-2" in opened.payload["prompt"]
        assert opened.actor == "system"
        assert await handle.query(AgentRunWorkflow.get_status) == "blocked_on_human"
        await handle.signal(
            AgentRunWorkflow.human_response,
            GateResponse(
                step_id="step-2", response="proceed with partials", actor="human@example.test"
            ),
        )
        result = await handle.result()

    assert result.status == "completed"
    types = _parent_types(fake, "run-fan-gate")
    assert types.index("gate_opened") < types.index("gate_answered")
    join_started = next(
        e
        for e in fake.events_of("step_started")
        if e.run_id == "run-fan-gate" and e.payload["step_id"] == "step-2"
    )
    assert join_started.payload["joined_with_failures"] == ["fan-2"]
    # The human's answer rode into the join turn as context.
    join_turn = fake.turn_inputs[-1]
    assert "proceed with partials" in [s.body for s in join_turn.steers]


async def test_fail_group_fails_the_join_and_the_run() -> None:
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=2, budget_slice=Decimal("1.00")),
        child_turns={"fan-2": [ScriptedTurn(outcome="fail")]},
    )
    state = fixture_run_state(
        run_id="run-fan-failgroup",
        max_children=2,
        policy=RunPolicy(
            require_plan_approval=False,
            max_parallel=1,
            on_group_partial_failure="fail_group",
        ),
    )
    async with _running(fake, state) as (_, handle):
        result = await handle.result()

    assert result.status == "failed"
    assert result.error is not None and "fanout-1" in result.error
    types = _parent_types(fake, "run-fan-failgroup")
    join_failures = [
        e
        for e in fake.events_of("step_failed")
        if e.run_id == "run-fan-failgroup" and e.payload["step_id"] == "step-2"
    ]
    assert len(join_failures) == 1
    assert join_failures[0].payload["failed_members"] == ["fan-2"]
    assert types[-1] == "run_failed"
    # Even a failing group refunds every reservation before the run ends.
    _parent_budget_walk(fake, "run-fan-failgroup", Decimal("10"))


# ---------------------------------------------------- land cascade & pause


async def test_land_cascade_wraps_children_and_their_results_still_join() -> None:
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=2, budget_slice=Decimal("1.00")),
        hold_child_turns=True,
        child_turns={
            "fan-1": [ScriptedTurn(outcome="continue"), ScriptedTurn()],
            "fan-2": [ScriptedTurn(outcome="continue"), ScriptedTurn()],
        },
    )
    state = fixture_run_state(run_id="run-fan-land", max_children=2)
    async with _running(fake, state) as (_, handle):
        await _wait_until(lambda: fake.children_holding == 2)
        await handle.signal(AgentRunWorkflow.land, "lander@example.test")
        # The land request never cancels the in-flight child turns; once
        # released they finish, see the forwarded land, and wrap up.
        assert await _settled(lambda: fake.children_holding == 2, seconds=0.3)
        fake.release_children.set()
        result = await handle.result()

    assert result.status == "completed"
    assert fake.cancelled_child_turns == []
    report = result.land_report
    assert report is not None
    assert report.status == "landed_partial"

    landed = fake.events_of("child_landed")
    assert {str(e.payload["step_id"]) for e in landed} == {"fan-1", "fan-2"}
    assert {str(e.payload["status"]) for e in landed} == {"landed_partial"}
    # Each child took exactly its held first turn, then wrapped instead of
    # continuing, and its partial result still joined the parent (summaries
    # recorded, reservations refunded).
    assert fake.child_turns_started == 2
    assert sorted(r.step_id for r in fake.archived_results) == ["fan-1", "fan-2"]
    assert all(r.status == "landed_partial" for r in fake.archived_results)
    types = _parent_types(fake, "run-fan-land")
    assert "step_failed" not in types  # a land cascade fails nothing
    assert types.index("landing_started") > types.index("child_landed")
    assert Decimal(str(_final_parent_budget(fake, "run-fan-land")["reserved_usd"])) == 0
    for event in landed:
        assert Decimal(str(event.payload["refund_usd"])) == Decimal("0.9999")
    # The children's own streams show the landing handshake.
    for child_run_id in ("run-fan-land--fan-1-a1", "run-fan-land--fan-2-a1"):
        child_types = [e.type for e in fake.events if e.run_id == child_run_id]
        assert "landing_started" in child_types
        assert child_types[-1] == "run_completed"


async def test_pause_never_cancels_in_flight_children() -> None:
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=3, budget_slice=Decimal("1.00")),
        hold_child_turns=True,
    )
    state = fixture_run_state(
        run_id="run-fan-pause",
        max_children=3,
        policy=RunPolicy(require_plan_approval=False, max_parallel=2),
    )
    async with _running(fake, state) as (_, handle):
        await _wait_until(lambda: fake.children_holding == 2)
        await handle.signal(AgentRunWorkflow.pause, "pauser@example.test")
        # The pause stops the third spawn but leaves both in-flight children
        # running untouched.
        assert await _settled(
            lambda: fake.children_holding == 2 and len(fake.events_of("child_spawned")) == 2
        )
        assert await handle.query(AgentRunWorkflow.get_status) == "paused"
        # Released children land and are absorbed while paused; the parent
        # then parks with nothing in flight.
        fake.release_children.set()
        await _wait_until(lambda: len(fake.events_of("child_landed")) == 2)
        await _wait_until(lambda: bool(fake.events_of("paused")))
        assert len(fake.events_of("child_spawned")) == 2
        await handle.signal(AgentRunWorkflow.resume, "resumer@example.test")
        result = await handle.result()

    assert result.status == "completed"
    assert fake.cancelled_child_turns == []
    assert len(fake.events_of("child_spawned")) == 3
    types = _parent_types(fake, "run-fan-pause")
    # Both in-flight children landed before the parent parked; the third
    # member spawned only after the resume.
    second_landed = [i for i, t in enumerate(types) if t == "child_landed"][1]
    assert second_landed < types.index("paused") < types.index("resumed")
    assert types.index("resumed") < [i for i, t in enumerate(types) if t == "child_spawned"][2]
    report = result.land_report
    assert report is not None
    assert report.status == "completed"
    assert report.steps_done == 5


async def test_every_child_is_started_with_request_cancel_close_policy() -> None:
    # Orphan prevention is a close-policy contract: every child workflow is
    # started with parent-close request-cancel, recorded in parent history.
    # (The time-skipping test server does not enforce the policy itself; the
    # end-to-end suite terminates a live run against the real dev server and
    # asserts the children actually close.)
    fake = FakeRuntime(plan_fanout=FanoutFixture(size=2, budget_slice=Decimal("1.00")))
    state = fixture_run_state(run_id="run-fan-closepolicy", max_children=2)
    async with _running(fake, state) as (_, handle):
        result = await handle.result()
        history = await handle.fetch_history()

    assert result.status == "completed"
    initiated = [
        event.start_child_workflow_execution_initiated_event_attributes
        for event in history.events
        if event.HasField("start_child_workflow_execution_initiated_event_attributes")
    ]
    assert len(initiated) == 2
    assert {attrs.workflow_id for attrs in initiated} == {
        "run-fan-closepolicy--fan-1-a1",
        "run-fan-closepolicy--fan-2-a1",
    }
    for attrs in initiated:
        assert attrs.parent_close_policy == ParentClosePolicy.PARENT_CLOSE_POLICY_REQUEST_CANCEL
        assert attrs.retry_policy.maximum_attempts == 1  # a child executes exactly once
        assert attrs.workflow_execution_timeout.ToTimedelta() > timedelta(0)
