"""The executor's platform-tuple gate: refused at preflight (before any bytes move); re-checked with a
fresh inventory read BEFORE the disruptive boundary of a deploy or recovery (incumbent and recovery
metadata untouched, grant TTL re-checked after the read); and required before every retained-runtime
start (recover(), recovery operations, boot, controlled restart)."""

from __future__ import annotations

import time

import pytest
from convoy_agent.executor import OpFailure
from lifecycle_stub import Stub, boot, crash, make_agent, run_op

T3652 = {
    "arch": "aarch64",
    "os": "linux",
    "backend": "cuda",
    "compute_capability": "8.7",
    "jetpack": "6.2.3",
    "l4t": "36.5.2",
    "cuda": "12.6",
}
BOARD = {"arch": "aarch64", "l4t_release": "36.4.7", "cuda_version": "12.6.11", "compute_capability": "8.7"}
PIN = {"arch": "aarch64", "l4t_release": "36.5.2", "cuda_version": "12.6.68", "compute_capability": "8.7"}


@pytest.fixture()
def stub():
    s = Stub()
    yield s
    s.stop()


def _gate(agent, inventory, reader=None):
    """Enable the physical tuple gate on a simulated flow (everything else stays simulated)."""
    agent.exec.enforce_platform = True
    agent.exec.inventory = inventory
    agent.exec.inventory_reader = reader


def _pin(manifest):
    manifest["spec"]["platform"] = {"profile_id": "jetson-orin-nano-8gb", "target": T3652}
    return manifest


def _deploy(stub, a, op_id, rid, gen):
    op = stub.deploy_op(op_id, rid, gen, expected_active=a.journal.get("active_release_id"))
    return op, run_op(a, op)


@pytest.mark.timeout(60)
def test_preflight_refuses_a_release_built_for_the_other_track_before_staging(stub, tmp_path):
    m = _pin(stub.add_release("rel_pin"))
    a = boot(make_agent(tmp_path, stub))
    try:
        a.exec.simulated = False
        a.exec.profile_policy = {"policy": "allowed"}
        _gate(a, BOARD)
        op = stub.deploy_op("op_1", "rel_pin", 1)
        with pytest.raises(OpFailure) as ei:
            a.exec.preflight(op, m)
        assert ei.value.code == "PREFLIGHT_COMPAT" and "36.5.2" in str(ei.value) and "36.4.7" in str(ei.value)
        assert ei.value.details["observed"]["l4t_release"] == "36.4.7"
        assert not list((a.data_dir / "cache" / "models").glob("*.gguf"))  # nothing was downloaded
        _gate(a, {"arch": "aarch64"})
        with pytest.raises(OpFailure) as ei2:
            a.exec.preflight(op, m)
        assert "unknown" in str(ei2.value)
        _gate(a, PIN)
        with pytest.raises(OpFailure) as ei3:
            a.exec.preflight(op, m)
        assert "CUDA runtime recipe" in str(
            ei3.value
        )  # the tuple gate passed; the simulated backend is the next refusal
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_deploy_fresh_mismatch_before_the_disruptive_boundary_leaves_incumbent_and_recovery_untouched(
    stub, tmp_path
):
    _pin(stub.add_release("rel_a"))
    _pin(stub.add_release("rel_b"))
    a = boot(make_agent(tmp_path, stub))
    try:
        _gate(a, PIN, reader=lambda: PIN)
        _, out = _deploy(stub, a, "op_a", "rel_a", 1)
        assert out["status"] == "succeeded" and a.sup.state() == "running"
        gen_before = a.sup.generation
        recovery_before = a.journal.get("recovery_release_id")
        # preflight saw a matching inventory; the fresh read just before cutover sees the other track
        a.exec.inventory_reader = lambda: BOARD
        op, out = _deploy(stub, a, "op_b", "rel_b", 2)
        f = out["failure"]
        assert out["status"] == "failed" and f["code"] == "PREFLIGHT_COMPAT" and f["stage"] == "cutover", out
        assert (
            f["details"]["disruption_started"] is False and "recovery" not in f["details"]
        )  # no rollback ran
        assert a.sup.state() == "running" and a.sup.generation == gen_before and a.gw.mode == "production"
        assert (
            a.journal.get("active_release_id") == "rel_a"
            and a.journal.get("recovery_release_id") == recovery_before
        )
        row = a.journal.operation("op_b")
        assert not (row.get("detail") or {}).get("disruption_started")
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_deploy_grant_ttl_consumed_by_the_fresh_read_expires_without_disruption(stub, tmp_path):
    _pin(stub.add_release("rel_a"))
    _pin(stub.add_release("rel_b"))
    a = boot(make_agent(tmp_path, stub))
    try:
        _gate(a, PIN, reader=lambda: PIN)
        _, out = _deploy(stub, a, "op_a", "rel_a", 1)
        assert out["status"] == "succeeded"
        gen_before = a.sup.generation
        stub.grant_ttl_s = 0.25

        def slow_read():
            time.sleep(0.4)  # nvidia-smi taking longer than the remaining TTL
            return PIN

        a.exec.inventory_reader = slow_read
        _, out = _deploy(stub, a, "op_b", "rel_b", 2)
        assert out["status"] == "failed" and out["failure"]["code"] == "GRANT_EXPIRED", out
        assert "nothing was changed" in out["failure"]["message"]
        assert a.sup.state() == "running" and a.sup.generation == gen_before and a.gw.mode == "production"
        assert a.journal.get("active_release_id") == "rel_a"
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_retained_recovery_release_is_not_started_on_a_mismatched_or_unknown_tuple(stub, tmp_path):
    _pin(stub.add_release("rel_a"))
    _pin(stub.add_release("rel_b"))
    a = boot(make_agent(tmp_path, stub))
    try:
        _gate(a, PIN, reader=lambda: PIN)
        assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
        # rel_b becomes active; rel_a is retained as the recovery release
        assert _deploy(stub, a, "op_b", "rel_b", 2)[1]["status"] == "succeeded"
        assert a.journal.get("recovery_release_id") == "rel_a"
        # a failure that needs recovery, with the fresh tuple now unknown: the retained release must not start
        a.exec.inventory_reader = lambda: {"arch": "aarch64"}
        op = stub.deploy_op("op_c", "rel_b", 3, expected_active="rel_b")
        a.journal.begin_operation(op)
        rec = a.exec.recover(op, OpFailure("HEALTH_FAILED", "simulated failure", "probation"))
        assert rec["recovered"] is False and rec["degraded"] is True
        assert rec["last"]["code"] == "PREFLIGHT_COMPAT" and "unknown" in rec["last"]["message"]
        assert a.sup.state() == "stopped" and a.journal.get("active_release_id") is None
        # recovery operation: refused before the gateway closes or the runtime is touched
        a.exec.inventory_reader = lambda: BOARD
        a.journal.set_many({"active_release_id": "rel_b", "recovery_release_id": "rel_a", "health": "ok"})
        a.gw.set_mode("production")
        op = stub.recover_op("op_r", "rel_a", 4)
        op["payload"]["expected_active_release_id"] = "rel_b"
        out = run_op(a, op)
        assert out["status"] == "failed" and out["failure"]["code"] == "PREFLIGHT_COMPAT", out
        assert out["failure"]["details"]["disruption_started"] is False and a.gw.mode == "production"
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_boot_and_controlled_restart_refuse_a_retained_release_on_a_mismatched_tuple(stub, tmp_path):
    _pin(stub.add_release("rel_a"))
    a = boot(make_agent(tmp_path, stub))
    _gate(a, PIN, reader=lambda: PIN)
    assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
    # controlled restart: fresh mismatch -> production stays closed, health failed, no new child
    a.exec.inventory_reader = lambda: BOARD
    gen_before = a.sup.generation
    a._restart_runtime_controlled()
    assert a.sup.state() == "stopped" and a.journal.get("health") == "failed" and a.gw.mode == "closed"
    assert a.sup.generation == gen_before
    crash(a)
    # boot: the retained active release is not started when the fresh tuple does not match
    b = make_agent(tmp_path, stub)
    _gate(b, PIN, reader=lambda: BOARD)
    b.acquire_lock()
    b.start_local()
    try:
        assert b.sup.state() != "running" and b.gw.mode == "closed"
        assert b.journal.get("health") == "failed"
    finally:
        b.shutdown()
    # and it does start when the tuple matches: an intact, independent fixture (its own directory,
    # the release deployed there, enough memory) MUST come back serving that release after a crash
    c = boot(make_agent(tmp_path, stub, name="dev-intact"))
    _gate(c, PIN, reader=lambda: PIN)
    assert _deploy(stub, c, "op_c", "rel_a", 1)[1]["status"] == "succeeded"
    crash(c)
    d = make_agent(tmp_path, stub, name="dev-intact")
    _gate(d, PIN, reader=lambda: PIN)
    d.acquire_lock()
    d.start_local()
    try:
        assert d.sup.state() == "running" and d.journal.get("active_release_id") == "rel_a"
        assert d.gw.mode == "production" and d.journal.get("health") == "ok"
        assert d.sup.release_id == "rel_a" and (d._restart_note or {}).get("action") is None
        assert d.journal.get("launch_admission")["release_id"] == "rel_a"  # the boot launch was gated
    finally:
        d.shutdown()
