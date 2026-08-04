"""Full-stack behaviors added with real turn execution: a run through the
Pydantic AI executor (LiteLLM -> mock-model) with inline tools, budget
threshold flows over SSE, per-step projections, SSE resume from a cursor, and
projection/SSE parity for every event type the runtime emits so far."""

import json
import time
import uuid
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pytest
from _support.e2e import PG_APP_DSN, auth_headers

pytestmark = pytest.mark.e2e


def _unique_run_id(label: str) -> str:
    return f"run-e2e-{label}-{uuid.uuid4().hex[:8]}"


def _collect_sse(
    api: httpx.Client,
    run_id: str,
    *,
    terminal: set[str],
    timeout: float = 120.0,
    after: int = 0,
    headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    request_headers = auth_headers() | (headers or {})
    with api.stream(
        "GET",
        f"/runs/{run_id}/events",
        params={"after": after},
        headers=request_headers,
        timeout=httpx.Timeout(timeout, read=timeout),
    ) as response:
        assert response.status_code == 200
        current_type: str | None = None
        deadline = time.monotonic() + timeout
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                raise TimeoutError(f"SSE did not reach {terminal} in {timeout}s")
            if line.startswith("event: "):
                current_type = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
                assert payload["type"] == current_type
                events.append(payload)
                if current_type in terminal:
                    return events
    raise AssertionError(f"SSE stream closed before a terminal event in {terminal}")


def _wait_status(api: httpx.Client, run_id: str, statuses: set[str], timeout: float = 90.0) -> str:
    deadline = time.monotonic() + timeout
    last = "<none>"
    while time.monotonic() < deadline:
        response = api.get(f"/runs/{run_id}", headers=auth_headers())
        assert response.status_code == 200
        last = response.json()["status"]
        if last in statuses:
            return last
        time.sleep(0.2)
    raise TimeoutError(f"run {run_id} never reached {statuses}; last status {last!r}")


# ------------------------------------------------------- real turn execution


def test_pydantic_ai_run_with_inline_tools_lands(api_ai: httpx.Client) -> None:
    run_id = _unique_run_id("ai-tools")
    created = api_ai.post(
        "/runs",
        json={
            "goal": (
                "Answer from the knowledge base "
                '[[call:kb_lookup {"key": "q3-revenue"}]] [[done:revenue found]]'
            ),
            "run_id": run_id,
            "tools": ["kb_lookup", "kb_search"],
            "model": "mock-fallback",
        },
        headers=auth_headers(),
    )
    assert created.status_code == 202

    events = _collect_sse(api_ai, run_id, terminal={"run_completed", "run_failed"})
    types = [e["type"] for e in events]
    assert types[-1] == "run_completed"
    assert types.count("step_started") == 2
    assert types.count("step_done") == 2

    step_done = [e for e in events if e["type"] == "step_done"]
    assert all(e["payload"]["model_used"] == "mock-fallback" for e in step_done)
    assert all(e["payload"]["model_fallback"] is False for e in step_done)
    # Proxy-priced spend accumulated through the workflow's accounting.
    assert Decimal(step_done[-1]["payload"]["budget"]["spent_usd"]) > 0

    run = api_ai.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert run["status"] == "completed"
    assert {s["status"] for s in run["steps"]} == {"done"}
    assert all(s["model_used"] == "mock-fallback" for s in run["steps"])
    assert Decimal(run["budget"]["spent_usd"]) > 0


def test_pydantic_ai_fallback_recorded_end_to_end(api_ai: httpx.Client) -> None:
    run_id = _unique_run_id("ai-fallback")
    created = api_ai.post(
        "/runs",
        json={"goal": "fallback exercise", "run_id": run_id, "model": "mock-primary"},
        headers=auth_headers(),
    )
    assert created.status_code == 202

    events = _collect_sse(api_ai, run_id, terminal={"run_completed", "run_failed"})
    assert events[-1]["type"] == "run_completed"
    step_done = [e for e in events if e["type"] == "step_done"]
    assert step_done
    for event in step_done:
        assert event["payload"]["model_requested"] == "mock-primary"
        assert event["payload"]["model_used"] == "mock-fallback"
        assert event["payload"]["model_fallback"] is True


def test_unapproved_model_is_rejected_at_creation(api_ai: httpx.Client) -> None:
    response = api_ai.post(
        "/runs",
        json={"goal": "nope", "model": "rogue-model"},
        headers=auth_headers(),
    )
    assert response.status_code == 422
    assert "approved" in response.json()["detail"]


def test_invalid_tool_requests_are_rejected_at_creation(api: httpx.Client) -> None:
    unknown = api.post(
        "/runs",
        json={"goal": "x", "tools": ["not_a_tool"]},
        headers=auth_headers(),
    )
    assert unknown.status_code == 422
    assert "unknown tool" in unknown.json()["detail"]

    # audit_write is offered by the environment but is inline+side-effecting,
    # which can never be granted.
    invalid = api.post(
        "/runs",
        json={"goal": "x", "tools": ["audit_write"]},
        headers=auth_headers(),
    )
    assert invalid.status_code == 422
    assert "side-effecting" in invalid.json()["detail"]


# ----------------------------------------------------------------- budget


def test_budget_warning_event_at_soft_threshold(api: httpx.Client) -> None:
    run_id = _unique_run_id("warn")
    created = api.post(
        "/runs",
        json={
            "goal": "cross the soft threshold",
            "run_id": run_id,
            # Scripted turns cost 0.0001 each: turn 1 lands at 83% of the cap,
            # so the warning fires before the final turn.
            "budget_usd": "0.00012",
        },
        headers=auth_headers(),
    )
    assert created.status_code == 202

    events = _collect_sse(api, run_id, terminal={"run_completed", "run_failed"})
    types = [e["type"] for e in events]
    assert types[-1] == "run_completed"
    assert types.count("budget_warning") == 1
    assert types.index("budget_warning") < types.index("landing_started")
    warning = next(e for e in events if e["type"] == "budget_warning")
    assert warning["payload"]["threshold"] == "0.8"

    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert Decimal(run["budget"]["spent_usd"]) == Decimal("0.0002")
    assert Decimal(run["budget"]["cap_usd"]) == Decimal("0.00012")


def test_budget_exhaustion_pauses_then_lands_with_partials(api: httpx.Client) -> None:
    run_id = _unique_run_id("exhaust")
    created = api.post(
        "/runs",
        json={
            "goal": "spend the whole cap",
            "run_id": run_id,
            # Turn 1 consumes exactly the cap; enforcement pauses the run at
            # the next boundary, before step 2 can start.
            "budget_usd": "0.0001",
        },
        headers=auth_headers(),
    )
    assert created.status_code == 202

    _wait_status(api, run_id, {"paused"})
    landed = api.post(f"/runs/{run_id}/land", headers=auth_headers())
    assert landed.status_code == 202
    _wait_status(api, run_id, {"completed"})

    events = _collect_sse(api, run_id, terminal={"run_completed"}, timeout=30)
    types = [e["type"] for e in events]
    assert types.index("budget_exhausted") < types.index("paused")
    assert types[-1] == "run_completed"
    assert types.count("step_started") == 1  # step 2 never scheduled

    exhausted = next(e for e in events if e["type"] == "budget_exhausted")
    assert exhausted["payload"]["action"] == "pause"
    # The system, not a human, paused this run.
    paused = next(e for e in events if e["type"] == "paused")
    assert paused["actor"] == "system"

    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert Decimal(run["budget"]["spent_usd"]) == Decimal("0.0001")
    assert run["land_report"]["status"] == "landed_partial"
    steps = {s["step_id"]: s["status"] for s in run["steps"]}
    assert steps["step-1"] == "done"
    assert steps["step-2"] != "done"


def test_budget_abort_policy_fails_the_run(api: httpx.Client) -> None:
    run_id = _unique_run_id("abort")
    created = api.post(
        "/runs",
        json={
            "goal": "abort past the cap",
            "run_id": run_id,
            "budget_usd": "0.0001",
            "policy": {"on_budget_exhausted": "abort"},
        },
        headers=auth_headers(),
    )
    assert created.status_code == 202

    events = _collect_sse(api, run_id, terminal={"run_completed", "run_failed"})
    types = [e["type"] for e in events]
    assert types[-1] == "run_failed"
    assert "budget_exhausted" in types
    failed = events[-1]
    assert failed["payload"]["reason"] == "budget_exhausted"
    assert _wait_status(api, run_id, {"failed"}) == "failed"


# ---------------------------------------------------- SSE + projections


def test_sse_resumes_from_last_event_id(api: httpx.Client) -> None:
    run_id = _unique_run_id("resume")
    created = api.post(
        "/runs",
        json={"goal": "sse resume", "run_id": run_id},
        headers=auth_headers(),
    )
    assert created.status_code == 202
    full = _collect_sse(api, run_id, terminal={"run_completed"})
    assert [e["seq"] for e in full] == list(range(1, len(full) + 1))

    # Reconnect as a browser would: Last-Event-ID continues after the cursor.
    resumed = _collect_sse(
        api, run_id, terminal={"run_completed"}, headers={"Last-Event-ID": "3"}, timeout=30
    )
    assert [e["seq"] for e in resumed] == [e["seq"] for e in full if e["seq"] > 3]
    assert [e["type"] for e in resumed] == [e["type"] for e in full if e["seq"] > 3]

    # The explicit ?after cursor behaves identically.
    after = _collect_sse(api, run_id, terminal={"run_completed"}, after=3, timeout=30)
    assert [e["seq"] for e in after] == [e["seq"] for e in resumed]


def _postgres_event_pairs(run_id: str) -> list[tuple[int, str]]:
    with psycopg.connect(PG_APP_DSN) as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'tenant-e2e', false)")
        rows = conn.execute(
            "SELECT seq, type FROM run_events WHERE run_id = %s ORDER BY seq",
            (run_id,),
        ).fetchall()
    return [(int(seq), t) for seq, t in rows]


def _assert_sse_matches_postgres(api: httpx.Client, run_id: str) -> list[str]:
    """The full SSE replay and the projection table must agree exactly, in
    seq order. Returns the event types for coverage accounting."""
    terminal = {"run_completed", "run_failed"}
    sse_events = _collect_sse(api, run_id, terminal=terminal, timeout=30)
    sse_pairs = [(e["seq"], e["type"]) for e in sse_events]
    seqs = [seq for seq, _ in sse_pairs]
    assert seqs == sorted(set(seqs)), "SSE emitted out of order or duplicated"
    assert _postgres_event_pairs(run_id) == sse_pairs
    return [t for _, t in sse_pairs]


def test_every_emitted_event_type_lands_in_postgres_and_streams_in_order(
    api: httpx.Client,
) -> None:
    """Runs that collectively exercise every event type the runtime can emit
    today; each run's SSE replay must match its Postgres event rows exactly."""
    seen: set[str] = set()

    # Pause/resume mid-run, then normal completion.
    pause_run_id = _unique_run_id("parity-pause")
    created = api.post(
        "/runs",
        json={"goal": "parity pause", "run_id": pause_run_id},
        headers=auth_headers(),
    )
    assert created.status_code == 202
    assert api.post(f"/runs/{pause_run_id}/pause", headers=auth_headers()).status_code == 202
    _wait_status(api, pause_run_id, {"paused"})
    assert api.post(f"/runs/{pause_run_id}/resume", headers=auth_headers()).status_code == 202
    _wait_status(api, pause_run_id, {"completed"})
    seen.update(_assert_sse_matches_postgres(api, pause_run_id))

    # Soft threshold warning, completes inside the cap boundaries.
    warn_run_id = _unique_run_id("parity-warn")
    api.post(
        "/runs",
        json={"goal": "parity warn", "run_id": warn_run_id, "budget_usd": "0.00012"},
        headers=auth_headers(),
    )
    _wait_status(api, warn_run_id, {"completed"})
    seen.update(_assert_sse_matches_postgres(api, warn_run_id))

    # Exhaustion under the default pause policy, landed by a human.
    exhaust_run_id = _unique_run_id("parity-exhaust")
    api.post(
        "/runs",
        json={"goal": "parity exhaust", "run_id": exhaust_run_id, "budget_usd": "0.0001"},
        headers=auth_headers(),
    )
    _wait_status(api, exhaust_run_id, {"paused"})
    api.post(f"/runs/{exhaust_run_id}/land", headers=auth_headers())
    _wait_status(api, exhaust_run_id, {"completed"})
    seen.update(_assert_sse_matches_postgres(api, exhaust_run_id))

    # Exhaustion under the abort policy: the run fails.
    abort_run_id = _unique_run_id("parity-abort")
    api.post(
        "/runs",
        json={
            "goal": "parity abort",
            "run_id": abort_run_id,
            "budget_usd": "0.0001",
            "policy": {"on_budget_exhausted": "abort"},
        },
        headers=auth_headers(),
    )
    _wait_status(api, abort_run_id, {"failed"})
    seen.update(_assert_sse_matches_postgres(api, abort_run_id))

    # The complete event surface the runtime emits today.
    assert seen == {
        "run_started",
        "plan_created",
        "step_started",
        "step_done",
        "paused",
        "resumed",
        "budget_warning",
        "budget_exhausted",
        "landing_started",
        "run_completed",
        "run_failed",
    }


def test_step_projections_track_per_step_status(api: httpx.Client) -> None:
    run_id = _unique_run_id("steps")
    created = api.post(
        "/runs",
        json={"goal": "step rollups", "run_id": run_id},
        headers=auth_headers(),
    )
    assert created.status_code == 202
    _collect_sse(api, run_id, terminal={"run_completed"})

    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    steps = {s["step_id"]: s for s in run["steps"]}
    assert set(steps) == {"step-1", "step-2"}
    for step in steps.values():
        assert step["status"] == "done"
        assert step["attempt"] == 1
        assert step["model_used"] == "scripted-echo-1"
        assert Decimal(step["cost_usd"]) == Decimal("0.0001")
        assert step["description"]
