"""M0 e2e gate (TESTING §5-M0): POST /runs -> SSE events -> land, with
GET /runs/{id} served from Postgres projections only, against the compose
stack (Temporal dev, Postgres+RLS, MinIO, mock-model, stub-env)."""

import json
import time
import uuid
from typing import Any

import httpx
import pytest
from _support.e2e import auth_headers

pytestmark = pytest.mark.e2e


def _unique_run_id(label: str) -> str:
    """Unique per invocation so `make e2e` reruns cleanly against a reused stack."""
    return f"run-e2e-{label}-{uuid.uuid4().hex[:8]}"


def _collect_sse(
    api: httpx.Client,
    run_id: str,
    *,
    terminal: set[str],
    timeout: float = 120.0,
    after: int = 0,
) -> list[dict[str, Any]]:
    """Consume the SSE stream until a terminal event type arrives."""
    events: list[dict[str, Any]] = []
    with api.stream(
        "GET",
        f"/runs/{run_id}/events",
        params={"after": after},
        headers=auth_headers(),
        timeout=httpx.Timeout(timeout, read=timeout),
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        current_type: str | None = None
        deadline = time.monotonic() + timeout
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                raise TimeoutError(f"SSE stream did not reach {terminal} in {timeout}s")
            if line.startswith("event: "):
                current_type = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
                assert payload["type"] == current_type
                events.append(payload)
                if current_type in terminal:
                    return events
    raise AssertionError(f"SSE stream closed before a terminal event in {terminal}")


def _wait_status(api: httpx.Client, run_id: str, statuses: set[str], timeout: float = 60.0) -> str:
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


def test_auth_stub_rejects_bad_credentials(api: httpx.Client) -> None:
    assert api.post("/runs", json={"goal": "x"}).status_code == 401
    response = api.post(
        "/runs",
        json={"goal": "x"},
        headers={"Authorization": auth_headers()["Authorization"]},
    )
    assert response.status_code == 400  # actor/tenant headers required


def test_happy_path_run_lands_with_events(api: httpx.Client) -> None:
    requested_id = _unique_run_id("happy")
    body = {
        "goal": "prove the skeleton walks",
        "success_criteria": ["run lands", "events flow"],
        "run_id": requested_id,
    }
    created = api.post("/runs", json=body, headers=auth_headers())
    assert created.status_code == 202
    run_id = created.json()["run_id"]
    assert run_id == requested_id

    # Idempotent retry: same run_id, no duplicate run, same 202.
    retried = api.post("/runs", json=body, headers=auth_headers())
    assert retried.status_code == 202
    assert retried.json()["run_id"] == run_id

    events = _collect_sse(api, run_id, terminal={"run_completed", "run_failed"})
    types = [e["type"] for e in events]
    assert types == [
        "run_started",
        "plan_created",
        "step_started",
        "step_done",
        "step_started",
        "step_done",
        "landing_started",
        "run_completed",
    ]

    # Actor identity flows into RunEvents through the auth stub.
    assert events[0]["actor"] == "e2e@convoy.test"
    assert all(e["tenant_id"] == "tenant-e2e" for e in events)
    assert [e["seq"] for e in events] == list(range(1, 9))

    # Read path: projections only.
    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert run["status"] == "completed"
    assert run["goal"] == "prove the skeleton walks"
    assert run["plan"]["version"] == 1
    assert len(run["plan"]["steps"]) == 2
    report = run["land_report"]
    assert report["status"] == "completed"
    assert report["steps_done"] == 2
    assert report["report_ref"]["key"] == f"runs/{run_id}/reports/land.json"

    # RLS isolation: another tenant cannot see this run at all.
    other = api.get(f"/runs/{run_id}", headers=auth_headers(tenant="tenant-other"))
    assert other.status_code == 404


def test_pause_resume_endpoints(api: httpx.Client) -> None:
    created = api.post(
        "/runs",
        json={"goal": "pause and resume", "run_id": _unique_run_id("pause")},
        headers=auth_headers(),
    )
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    paused = api.post(f"/runs/{run_id}/pause", headers=auth_headers())
    assert paused.status_code == 202
    _wait_status(api, run_id, {"paused"})

    resumed = api.post(f"/runs/{run_id}/resume", headers=auth_headers())
    assert resumed.status_code == 202
    _wait_status(api, run_id, {"completed"})

    events = _collect_sse(api, run_id, terminal={"run_completed"}, timeout=30)
    types = [e["type"] for e in events]
    assert "paused" in types
    assert "resumed" in types
    assert types.index("paused") < types.index("resumed")
    pause_event = next(e for e in events if e["type"] == "paused")
    assert pause_event["actor"] == "e2e@convoy.test"


def test_land_signal_wraps_up_gracefully(api: httpx.Client) -> None:
    created = api.post(
        "/runs",
        json={"goal": "land early", "run_id": _unique_run_id("land")},
        headers=auth_headers(),
    )
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    # Pause first so the land request deterministically catches an active run.
    assert api.post(f"/runs/{run_id}/pause", headers=auth_headers()).status_code == 202
    _wait_status(api, run_id, {"paused"})

    landed = api.post(f"/runs/{run_id}/land", headers=auth_headers())
    assert landed.status_code == 202
    _wait_status(api, run_id, {"completed"})

    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    report = run["land_report"]
    assert report is not None
    # Landing early is a valid completion; partial results allowed (DESIGN §6.2).
    assert report["status"] in {"completed", "landed_partial"}

    events = _collect_sse(api, run_id, terminal={"run_completed"}, timeout=30)
    types = [e["type"] for e in events]
    assert "landing_started" in types
    assert types[-1] == "run_completed"

    # Signals against a finished run are rejected cleanly.
    conflict = api.post(f"/runs/{run_id}/land", headers=auth_headers())
    assert conflict.status_code in {202, 409}


def test_unknown_run_is_404(api: httpx.Client) -> None:
    assert api.get("/runs/run-nope", headers=auth_headers()).status_code == 404
    assert api.post("/runs/run-nope/pause", headers=auth_headers()).status_code == 404
