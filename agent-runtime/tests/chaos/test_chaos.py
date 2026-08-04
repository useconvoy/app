"""Chaos & durability suite against the compose stack.

Every case asserts the two invariants that define crash-safety here: no lost
progress and no doubled side effects, verified through the RunEvent log
(projections; gapless per-run sequences) and the side-effect journals (the
stub environment's for data-plane tools, the workspace journal inside
snapshots for sandbox jobs). Kills are SIGKILL — no goodbye, exactly like a
crashed host — and recovery rides Temporal retries plus the idempotency keys
promoted calls carry.
"""

import io
import shutil
import tarfile
import time
import uuid
from decimal import Decimal
from typing import Any

import anyio
import httpx
import pytest
from _support.e2e import auth_headers, collect_sse, wait_status

from chaos.conftest import ChaosStack
from convoy_core import ArtifactRef
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.promoted import promoted_call_key

pytestmark = pytest.mark.chaos

STUB_ENV_URL = "http://localhost:8902"
# A killed worker's in-flight activity is retried once its heartbeat window
# lapses; completion waits must absorb that.
RECOVERY_TIMEOUT = 300.0


def _unique_run_id(label: str) -> str:
    return f"run-chaos-{label}-{uuid.uuid4().hex[:8]}"


def _create_run(api: httpx.Client, body: dict[str, Any]) -> str:
    created = api.post("/runs", json=body, headers=auth_headers())
    assert created.status_code == 202, created.text
    return str(created.json()["run_id"])


def _ensure_worker(stack: ChaosStack) -> None:
    if stack.worker is None or stack.worker.poll() is not None:
        stack.start_worker()


def _journal() -> dict[str, dict[str, Any]]:
    entries = httpx.get(f"{STUB_ENV_URL}/journal", timeout=10.0).json()["entries"]
    return {e["idempotency_key"]: e for e in entries}


def _wait_for_journal_key(key: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if key in _journal():
            return
        time.sleep(0.1)
    raise TimeoutError(f"journal never saw key {key}")


def _assert_gapless(events: list[dict[str, Any]]) -> None:
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(seqs) + 1)), f"event sequence has gaps or dupes: {seqs}"


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


def test_kill_worker_mid_turn(chaos_stack: ChaosStack, api: httpx.Client) -> None:
    _ensure_worker(chaos_stack)
    run_id = _create_run(
        api, {"goal": "survive a mid-turn crash", "run_id": _unique_run_id("midturn")}
    )
    # Slowed scripted turns keep run_turn in flight while the worker dies.
    collect_sse(api, run_id, terminal={"step_started"}, timeout=120.0)
    time.sleep(0.5)
    chaos_stack.kill_worker()
    chaos_stack.start_worker()

    wait_status(api, run_id, {"completed"}, timeout=RECOVERY_TIMEOUT)
    events = collect_sse(api, run_id, terminal={"run_completed"}, timeout=60.0)
    # No lost progress, no duplicated events: the outbox stayed idempotent on
    # (run_id, seq) and the retried turn (inline tools re-run) finished the
    # step exactly once.
    _assert_gapless(events)
    assert [e["type"] for e in events] == [
        "run_started",
        "plan_created",
        "step_started",
        "step_done",
        "compaction_applied",
        "step_started",
        "step_done",
        "compaction_applied",
        "landing_started",
        "run_completed",
    ]
    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert Decimal(str(run["budget"]["spent_usd"])) == Decimal("0.0002")


def test_kill_worker_mid_promoted_tool(chaos_stack: ChaosStack, api: httpx.Client) -> None:
    _ensure_worker(chaos_stack)
    run_id = _create_run(
        api,
        {
            "goal": "survive a crash inside a promoted call",
            "run_id": _unique_run_id("midpromoted"),
            "tools": ["kb_delete"],
        },
    )
    key_1 = promoted_call_key(run_id, "step-1", 1, 0)
    # The promoted activity posts its side effect, then idles inside the
    # chaos-widened completion window — kill it exactly there.
    _wait_for_journal_key(key_1, timeout=120.0)
    chaos_stack.kill_worker()
    chaos_stack.start_worker()

    wait_status(api, run_id, {"completed"}, timeout=RECOVERY_TIMEOUT)
    journal = _journal()
    # The retry reached the environment with the same key: at least two
    # requests, exactly one execution — the side effect never doubled.
    assert journal[key_1]["executions"] == 1
    assert journal[key_1]["requests"] >= 2
    key_2 = promoted_call_key(run_id, "step-2", 2, 0)
    assert journal[key_2]["executions"] == 1
    events = collect_sse(api, run_id, terminal={"run_completed"}, timeout=60.0)
    _assert_gapless(events)
    assert [e["type"] for e in events].count("step_done") == 2


def test_restart_under_continue_as_new(chaos_stack: ChaosStack, api: httpx.Client) -> None:
    _ensure_worker(chaos_stack)
    # Eight turns against a turn limit of three force hops after turns 3 and
    # 6; the kill+restart lands around the first hop. The virtual-clock
    # binding makes clock-state carry observable: every event must stamp the
    # identical virtual epoch, because nothing ever advances it — a lost
    # carry would re-seed virtual_now and the stamps would jump.
    run_id = _create_run(
        api,
        {
            "goal": "[turns:4] survive restarts around the turn-limit hop",
            "run_id": _unique_run_id("can"),
            "environment_id": "stub-virtual",
        },
    )
    collect_sse(api, run_id, terminal={"step_started"}, timeout=120.0)
    time.sleep(5.0)  # roughly turn 3 at the chaos turn delay: the hop window
    chaos_stack.kill_worker()
    chaos_stack.start_worker()

    wait_status(api, run_id, {"completed"}, timeout=RECOVERY_TIMEOUT)
    events = collect_sse(api, run_id, terminal={"run_completed"}, timeout=60.0)
    _assert_gapless(events)
    types = [e["type"] for e in events]
    assert types.count("step_done") == 2
    assert types.count("compaction_applied") == 2

    # Lossless carry: all eight turns billed exactly once, plan version
    # unchanged, summaries distilled, and the virtual clock never re-seeded.
    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert Decimal(str(run["budget"]["spent_usd"])) == Decimal("0.0008")
    assert run["plan"]["version"] == 1
    compactions = [e for e in events if e["type"] == "compaction_applied"]
    assert all(e["payload"]["summary_ref"] is not None for e in compactions)
    virtual_stamps = {e["virtual_ts"] for e in events}
    assert None not in virtual_stamps
    assert len(virtual_stamps) == 1, f"virtual clock state was lost: {virtual_stamps}"
    assert run["land_report"]["status"] == "completed"


def test_sandbox_loss_rebuilds_from_snapshot(chaos_stack: ChaosStack, api: httpx.Client) -> None:
    _ensure_worker(chaos_stack)
    run_id = _create_run(
        api,
        {
            "goal": "survive losing the sandbox mid-run",
            "run_id": _unique_run_id("sandboxloss"),
            "tools": ["sandbox_exec"],
        },
    )
    # Step-1's job appended the first line and snapshotted. Destroy every
    # workspace before step-2's job runs: cache gone, snapshots stay truth.
    collect_sse(api, run_id, terminal={"step_done"}, timeout=120.0)
    for workspace in chaos_stack.sandbox_dir.glob("sbx-*"):
        shutil.rmtree(workspace, ignore_errors=True)

    wait_status(api, run_id, {"completed"}, timeout=RECOVERY_TIMEOUT)
    events = collect_sse(api, run_id, terminal={"run_completed"}, timeout=60.0)
    _assert_gapless(events)
    step_done = [e for e in events if e["type"] == "step_done"]
    assert len(step_done) == 2
    final_snapshot = step_done[1]["payload"]["sandbox_snapshot_ref"]
    assert final_snapshot is not None

    # The rebuilt sandbox started from the last snapshot and ran step-2's
    # job under its original idempotency key: both appends present exactly
    # once, and both keys journaled inside the workspace truth.
    tar_bytes = _fetch_artifact(final_snapshot)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
        log_member = tar.extractfile("data.log")
        assert log_member is not None
        log = log_member.read().decode()
        journal_names = [n for n in tar.getnames() if n.startswith(".convoy/journal/")]
    assert log == "step-1 turn 1\nstep-2 turn 2\n"
    keys = {
        promoted_call_key(run_id, "step-1", 1, 0),
        promoted_call_key(run_id, "step-2", 2, 0),
    }
    assert {n.rsplit("/", 1)[1].removesuffix(".json") for n in journal_names} == keys
