"""Fan-out flows against the compose stack: a group of subagent children runs
through real child workflows, their linkage and budget movements stream over
SSE, each child is visible as its own projection run row linked to the
parent, and terminating a parent with a child in flight leaves no orphan
(parent close policy enforced by the real Temporal server)."""

import time
import uuid
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest
from _support.e2e import auth_headers, collect_sse, wait_status

pytestmark = pytest.mark.e2e


def _unique_run_id(label: str) -> str:
    return f"run-e2e-{label}-{uuid.uuid4().hex[:8]}"


def _create_fanout_run(
    api: httpx.Client,
    label: str,
    *,
    size: int = 2,
    budget_slice: str = "1.00",
    max_parallel: int = 2,
) -> str:
    body: dict[str, Any] = {
        "goal": "fan out the research",
        "run_id": _unique_run_id(label),
        "max_children": size,
        "fixture_fanout": {"size": size, "budget_slice": budget_slice},
        "policy": {"max_parallel": max_parallel},
    }
    created = api.post("/runs", json=body, headers=auth_headers())
    assert created.status_code == 202
    return cast(str, created.json()["run_id"])


def test_fanout_group_lands_with_children_visible(api: httpx.Client) -> None:
    run_id = _create_fanout_run(api, "fanout")
    wait_status(api, run_id, {"completed"}, timeout=120.0)

    events = collect_sse(api, run_id, terminal={"run_completed"})
    types = [e["type"] for e in events]
    assert types.count("child_spawned") == 2
    assert types.count("child_landed") == 2

    spawned = [e for e in events if e["type"] == "child_spawned"]
    landed = [e for e in events if e["type"] == "child_landed"]
    child_ids = {str(e["payload"]["child_run_id"]) for e in spawned}
    assert child_ids == {f"{run_id}--fan-1-a1", f"{run_id}--fan-2-a1"}
    for event in spawned:
        assert event["payload"]["group_id"] == "fanout-1"
        assert Decimal(event["payload"]["budget_reserved"]) == Decimal("1.00")
        assert event["actor_type"] == "agent"
    for event in landed:
        assert event["payload"]["status"] == "done"
        assert event["payload"]["summary_ref"] is not None
        # The scripted child spent one cheap turn; the rest refunded.
        assert Decimal(event["payload"]["cost_usd"]) == Decimal("0.0001")
        assert Decimal(event["payload"]["refund_usd"]) == Decimal("0.9999")

    # The parent's console view: all reservations refunded, children's costs
    # in the spend, member steps folded as done.
    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert run["parent_run_id"] is None
    assert Decimal(run["budget"]["reserved_usd"]) == 0
    assert Decimal(run["budget"]["spent_usd"]) == Decimal("0.0004")  # 2 parent + 2 child turns
    steps = {s["step_id"]: s for s in run["steps"]}
    assert steps["fan-1"]["status"] == "done"
    assert steps["fan-2"]["status"] == "done"
    report = run["land_report"]
    assert report["status"] == "completed"
    assert report["steps_done"] == 4
    assert len(report["deliverables"]) == 4

    # Each child is a run row of its own, linked to the parent, with its
    # slice as its cap — served from projections, never Temporal.
    for child_id in child_ids:
        child = api.get(f"/runs/{child_id}", headers=auth_headers())
        assert child.status_code == 200
        child_run = child.json()
        assert child_run["parent_run_id"] == run_id
        assert child_run["status"] == "completed"
        assert Decimal(child_run["budget"]["cap_usd"]) == Decimal("1.00")
        assert child_run["goal"].startswith("Fan-out part")
        # The child's own event stream is replayable over the same SSE path.
        child_events = collect_sse(api, child_id, terminal={"run_completed"})
        child_types = [e["type"] for e in child_events]
        assert child_types == ["run_started", "step_started", "step_done", "run_completed"]
        assert child_events[0]["payload"]["parent_run_id"] == run_id


def test_terminating_parent_leaves_no_orphan_children(api: httpx.Client) -> None:
    import asyncio

    from temporalio.client import Client, WorkflowExecutionStatus

    # Serialized children (max_parallel=1) with the stack's slowed scripted
    # turns: the first child is mid-turn when the parent is terminated via
    # the break-glass path outside the product API.
    run_id = _create_fanout_run(api, "orphan", size=5, max_parallel=1)
    first_child = f"{run_id}--fan-1-a1"
    collect_sse(api, run_id, terminal={"child_spawned"}, timeout=90.0)

    async def _terminate_and_watch() -> WorkflowExecutionStatus:
        client = await Client.connect("localhost:7233", namespace="default")
        await client.get_workflow_handle(run_id).terminate("e2e break-glass test")
        deadline = time.monotonic() + 30.0
        status = WorkflowExecutionStatus.RUNNING
        while time.monotonic() < deadline:
            description = await client.get_workflow_handle(first_child).describe()
            status = description.status
            assert status is not None
            if status != WorkflowExecutionStatus.RUNNING:
                return status
            await asyncio.sleep(0.25)
        return status

    final_status = asyncio.run(_terminate_and_watch())
    # The close policy request-cancelled the in-flight child: it must not
    # outlive the parent (cancelled, or completed if it raced the cancel).
    assert final_status in (
        WorkflowExecutionStatus.CANCELED,
        WorkflowExecutionStatus.TERMINATED,
        WorkflowExecutionStatus.COMPLETED,
    )
