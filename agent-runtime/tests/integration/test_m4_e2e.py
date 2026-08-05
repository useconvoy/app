"""Durability flows against the compose stack: the virtual-clock advance
endpoint with its sandbox-kind guard, a gate timeout resolved at a virtual
deadline with dual timestamps over SSE, promoted tool calls journaled by the
stub environment, sandbox jobs chaining workspace snapshots, and multi-turn
steps with compaction events on the wire."""

import io
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import anyio
import httpx
import pytest
from _support.e2e import auth_headers, collect_sse, wait_status

from convoy_core import ArtifactRef
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.promoted import promoted_call_key

pytestmark = pytest.mark.e2e

STUB_ENV_URL = "http://localhost:8902"


def _unique_run_id(label: str) -> str:
    return f"run-e2e-{label}-{uuid.uuid4().hex[:8]}"


def _create_run(api: httpx.Client, body: dict[str, Any]) -> str:
    created = api.post("/runs", json=body, headers=auth_headers())
    assert created.status_code == 202, created.text
    return str(created.json()["run_id"])


def _fetch_artifact(ref_payload: dict[str, Any]) -> bytes:
    store = ArtifactStore(
        bucket="convoy-artifacts",
        endpoint_url="http://localhost:9000",
        access_key="convoy",
        secret_key="convoy-secret-key",
    )

    async def _get() -> bytes:
        return await store.get_bytes(ArtifactRef.model_validate(ref_payload))

    return anyio.run(_get)


def test_clock_advance_guards_reject_non_virtual_runs(api: httpx.Client) -> None:
    target = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    # A production-kind binding refuses advances outright.
    prod_run = _create_run(
        api,
        {
            "goal": "production run refuses clock advances",
            "run_id": _unique_run_id("clock-prod"),
            "environment_id": "prod-local",
            "policy": {"require_plan_approval": False},
        },
    )
    response = api.post(
        f"/runs/{prod_run}/clock/advance", json={"to": target}, headers=auth_headers()
    )
    assert response.status_code == 409
    assert "sandbox-only" in response.json()["detail"]

    # A sandbox binding on a real clock refuses too: virtual mode required.
    real_run = _create_run(
        api,
        {"goal": "real-clock sandbox refuses advances", "run_id": _unique_run_id("clock-real")},
    )
    response = api.post(
        f"/runs/{real_run}/clock/advance", json={"to": target}, headers=auth_headers()
    )
    assert response.status_code == 409
    assert "virtual mode" in response.json()["detail"]

    # A malformed target is a validation error, not a signal.
    response = api.post(
        f"/runs/{real_run}/clock/advance", json={"to": "not-a-time"}, headers=auth_headers()
    )
    assert response.status_code == 422


def test_manual_advance_resolves_gate_timeout_with_dual_stamps(api: httpx.Client) -> None:
    run_id = _create_run(
        api,
        {
            "goal": "rehearse the review deadline",
            "run_id": _unique_run_id("clock-virtual"),
            "environment_id": "stub-virtual",
            "fixture_gates": {
                "step-2": {
                    "kind": "approval",
                    "prompt": "Approve the summary?",
                    "timeout": "PT1H",
                    "on_timeout": "skip",
                }
            },
        },
    )
    head = collect_sse(api, run_id, terminal={"gate_opened"})
    gate_opened = head[-1]
    deadline = gate_opened["payload"]["deadline"]
    assert deadline is not None

    # Fast-forward exactly to the gate's virtual deadline.
    response = api.post(
        f"/runs/{run_id}/clock/advance", json={"to": deadline}, headers=auth_headers()
    )
    assert response.status_code == 202
    assert response.json()["signal"] == "advance_time"

    wait_status(api, run_id, {"completed"})
    events = collect_sse(api, run_id, terminal={"run_completed"})
    types = [e["type"] for e in events]
    assert "gate_timed_out" in types
    assert "step_skipped" in types

    # Dual stamps on every event; the timeout resolved at the virtual
    # deadline, an hour past the epoch while barely any real time passed.
    assert all(e["virtual_ts"] is not None and e["ts"] is not None for e in events)
    assert all(e["sandbox"] is True for e in events)
    epoch = datetime.fromisoformat(events[0]["virtual_ts"])
    timed_out = next(e for e in events if e["type"] == "gate_timed_out")
    stamp = datetime.fromisoformat(timed_out["virtual_ts"])
    assert (stamp - epoch).total_seconds() == 3600
    assert stamp == datetime.fromisoformat(deadline)
    assert timed_out["payload"]["advanced_by"] == "e2e@convoy.test"
    real_elapsed = datetime.fromisoformat(timed_out["ts"]) - datetime.fromisoformat(events[0]["ts"])
    assert real_elapsed < timedelta(minutes=5)


def test_promoted_tool_call_is_journaled_once_per_key(api: httpx.Client) -> None:
    run_id = _create_run(
        api,
        {
            "goal": "clean up the stale records",
            "run_id": _unique_run_id("promote"),
            "tools": ["kb_delete"],
        },
    )
    wait_status(api, run_id, {"completed"}, timeout=120.0)

    # Each step's first turn promoted one call; the environment journal shows
    # exactly one execution per idempotency key.
    expected_keys = {
        promoted_call_key(run_id, "step-1", 1, 0),
        promoted_call_key(run_id, "step-2", 2, 0),
    }
    journal = httpx.get(f"{STUB_ENV_URL}/journal", timeout=10.0).json()
    entries = {e["idempotency_key"]: e for e in journal["entries"]}
    for key in expected_keys:
        assert key in entries, f"promoted key {key} never reached the environment"
        assert entries[key]["executions"] == 1
        assert entries[key]["tool_id"] == "kb_delete"


def test_sandbox_jobs_chain_snapshots_across_steps(api: httpx.Client) -> None:
    run_id = _create_run(
        api,
        {
            "goal": "build the workspace state",
            "run_id": _unique_run_id("sandbox"),
            "tools": ["sandbox_exec"],
        },
    )
    wait_status(api, run_id, {"completed"}, timeout=120.0)
    events = collect_sse(api, run_id, terminal={"run_completed"})
    step_done = [e for e in events if e["type"] == "step_done"]
    assert len(step_done) == 2
    snapshots = [e["payload"].get("sandbox_snapshot_ref") for e in step_done]
    assert all(s is not None for s in snapshots)
    assert snapshots[0]["key"] != snapshots[1]["key"]

    # The final snapshot is the workspace truth: both jobs' appends are in
    # it (the second job saw the first job's state), and the job journal
    # rides inside it.
    tar_bytes = _fetch_artifact(snapshots[1])
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
        names = tar.getnames()
        log_member = tar.extractfile("data.log")
        assert log_member is not None
        log = log_member.read().decode()
    assert log == "step-1 turn 1\nstep-2 turn 2\n"
    assert any(name.startswith(".convoy/journal/") for name in names)


def test_multi_turn_steps_accumulate_cost_and_compact_once_per_step(api: httpx.Client) -> None:
    run_id = _create_run(
        api,
        {
            "goal": "[turns:3] rehearse the multi-turn week",
            "run_id": _unique_run_id("multiturn"),
        },
    )
    wait_status(api, run_id, {"completed"}, timeout=120.0)
    events = collect_sse(api, run_id, terminal={"run_completed"})
    types = [e["type"] for e in events]
    # Three turns per step billed, one distillation per completed step.
    assert types.count("step_done") == 2
    assert types.count("compaction_applied") == 2
    compactions = [e for e in events if e["type"] == "compaction_applied"]
    for event in compactions:
        assert event["payload"]["boundary"] == "step_end"
        assert event["payload"]["summary_ref"] is not None
        assert event["payload"]["archived_ref"] is not None

    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert Decimal(str(run["budget"]["spent_usd"])) == Decimal("0.0006")
