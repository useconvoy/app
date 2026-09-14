"""Negative regressions for the device-lifecycle review findings R33-R42, R28-fu, R29-fu, R72, R77 and
the evidence-before-success ordering. Real Journal/Executor/Agent/RuntimeSupervisor objects over
isolated tmp dirs; the server boundary is an in-process HTTP stub (lifecycle_stub.Stub)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from convoy_agent import gateway as gwmod
from convoy_agent import runtime as rtmod
from convoy_agent.executor import RECOVERY_ATTEMPTS, Executor, OpFailure
from convoy_agent.gateway import Gateway
from convoy_agent.hardware import SimulatedSensors
from convoy_agent.harness import Harness
from convoy_agent.journal import Journal
from convoy_agent.runtime import RuntimeSupervisor
from lifecycle_stub import Stub, boot, crash, make_agent, run_op, wait_for, write_fake_llama_server


@pytest.fixture()
def stub():
    s = Stub()
    yield s
    s.stop()


def _chat(port, body, headers=None, timeout=15):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


def _deploy(stub, agent, op_id, rid, gen, **kw):
    op = stub.deploy_op(op_id, rid, gen, expected_active=agent.journal.get("active_release_id"), **kw)
    out = run_op(agent, op)
    return op, out


def _spec_path(agent, rid) -> Path:
    return agent.data_dir / "cache" / "manifests" / f"{rid}.json"


def _inject_fault(agent, rid, faults):
    """Make a cached (already committed) release fail at its next runtime start."""
    p = _spec_path(agent, rid)
    m = json.loads(p.read_text())
    m["spec"]["config"]["sim"] = {**m["spec"]["config"].get("sim", {}), **faults}
    p.write_text(json.dumps(m))


# ----------------------------------------------------------------------------- R33
@pytest.mark.timeout(120)
def test_r33_success_outcome_is_persisted_whole_and_replayed_unchanged_after_restart(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_plan("plan_a", "rel_a")
    a1 = boot(make_agent(tmp_path, stub))
    try:
        op, out = _deploy(stub, a1, "op_1", "rel_a", 1, plan_id="plan_a")
        assert out["status"] == "succeeded", out
        row = a1.journal.operation("op_1")
        # the journal holds the COMPLETE wire outcome, not a {status} stub
        assert (
            row["terminal"]
            and row["outcome"] == out
            and row["outcome"]["evidence"]["eval"]["verdict"] == "passed"
        )
        assert row["outcome_acked"] == 0 and stub.outcomes.get("op_1") is None  # crash before publication
    finally:
        crash(a1)  # SIGKILL right after finish_operation, before the outcome was posted
    a2 = boot(make_agent(tmp_path, stub))
    try:
        assert a2.journal.get("active_release_id") == "rel_a" and a2.sup.state() == "running"
        assert a2._restart_note is None  # a terminal row is not "recovered"
        a2.tick()
        posted = stub.outcomes["op_1"][-1]
        assert posted["status"] == "succeeded" and posted == row["outcome"], "replayed unchanged"
        assert a2.journal.operation("op_1")["outcome_acked"] == 1
        assert a2.gw.mode == "production" and a2.sup.release_id == "rel_a"
        # evidence-before-success: the eval record reached the server before the success outcome
        i_eval = stub.first_index(
            lambda e: (
                e[1] == "/api/agent/v1/spool"
                and any(r.get("kind") == "eval_result" for r in (e[2] or {}).get("records", []))
            )
        )
        i_out = stub.first_index(
            lambda e: e[1] == "/api/agent/v1/operations/op_1/outcome" and e[2].get("status") == "succeeded"
        )
        assert i_eval is not None and i_out is not None and i_eval < i_out
        assert not any(o.get("_rejected") for o in stub.outcomes["op_1"])
    finally:
        a2.shutdown()


# ----------------------------------------------------------------------------- R34
@pytest.mark.timeout(120)
def test_r34_expired_grant_never_stops_the_healthy_runtime_for_deploy_and_recover(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    a = boot(make_agent(tmp_path, stub))
    try:
        _, out = _deploy(stub, a, "op_1", "rel_a", 1)
        assert out["status"] == "succeeded"
        gen_a, key_a = a.sup.generation, a.sup.api_key
        # tiny TTL, and the server is slow to answer the "cutover" progress POST that precedes the check
        stub.grant_ttl_s = 0.05
        stub.progress_delay["cutover"] = 0.4
        _, out = _deploy(stub, a, "op_2", "rel_b", 2)
        assert out["status"] == "failed" and out["failure"]["code"] == "GRANT_EXPIRED", out
        assert out["grant_id"] == "grant-op_2" and out["grant_consumed_seq"] == 1
        assert a.sup.generation == gen_a and a.sup.api_key == key_a and a.sup.release_id == "rel_a"
        assert a.sup.state() == "running" and a.gw.mode == "production"
        assert (
            a.journal.get("active_release_id") == "rel_a" and a.journal.operation("op_2")["stage"] == "Failed"
        )
        assert a.journal.operation("op_2")["outcome"] == out
        # recover flow: same bindings, same immediate check before the first disruptive effect
        stub.grant_ttl_s = 30.0
        stub.progress_delay.clear()
        _, out = _deploy(stub, a, "op_3", "rel_b", 3)
        assert out["status"] == "succeeded" and a.journal.get("recovery_release_id") == "rel_a"
        gen_b = a.sup.generation
        stub.grant_override = {"boot_id": "not-this-boot"}
        rop = stub.recover_op("op_4", "rel_a", 4)
        out = run_op(a, rop)
        assert out["status"] == "failed" and out["failure"]["code"] == "GRANT_BINDING", out
        assert a.sup.generation == gen_b and a.sup.release_id == "rel_b" and a.gw.mode == "production"
        stub.grant_override = {}
        stub.grant_ttl_s = 0.05
        stub.progress_delay["cutover"] = 0.4
        rop = stub.recover_op("op_5", "rel_a", 5)
        out = run_op(a, rop)
        assert out["status"] == "failed" and out["failure"]["code"] == "GRANT_EXPIRED", out
        assert a.sup.generation == gen_b and a.sup.release_id == "rel_b" and a.gw.mode == "production"
        assert (
            _chat(
                a.gw.port,
                {
                    "messages": [{"role": "user", "content": "What is the capital of France?"}],
                    "max_tokens": 4,
                },
            )[0]
            == 200
        )
    finally:
        a.shutdown()


# ----------------------------------------------------------------------------- R35
@pytest.mark.timeout(180)
def test_r35_server_loss_after_cutover_completes_from_staged_plan_and_publishes_later(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    stub.add_plan("plan_b", "rel_b")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"

        def lose_server(op_id, progress):
            if progress.get("stage") == "cutover":
                stub.down = True  # disconnect between candidate health and plan use

        stub.on_progress = lose_server
        op = stub.deploy_op("op_2", "rel_b", 2, plan_id="plan_b", expected_active="rel_a")
        a._run_operation(op)  # the worker path: execute + publish attempt
        row = a.journal.operation("op_2")
        assert row["stage"] == "Succeeded" and row["outcome"]["status"] == "succeeded", row
        assert row["outcome"]["evidence"]["eval"]["verdict"] == "passed" and row["outcome_acked"] == 0
        assert a.journal.get("active_release_id") == "rel_b" and a.sup.release_id == "rel_b"
        assert a.gw.mode == "production" and a.journal.current_operation() is None
        assert stub.outcomes.get("op_2") is None  # nothing was posted while the server was gone
        stub.on_progress = None
        stub.down = False
        a.tick()
        assert (
            stub.outcomes["op_2"][-1] == row["outcome"] and a.journal.operation("op_2")["outcome_acked"] == 1
        )
    finally:
        a.shutdown()


@pytest.mark.timeout(180)
@pytest.mark.parametrize(
    "exc,code", [(RuntimeError("boom"), "UNEXPECTED_ERROR"), (None, "SERVER_UNREACHABLE")]
)
def test_r35_unexpected_exception_after_cutover_rolls_back_with_terminal_row(
    stub, tmp_path, monkeypatch, exc, code
):
    from convoy_agent.client import Transient

    stub.add_release("rel_a")
    stub.add_release("rel_b")
    stub.add_plan("plan_b", "rel_b")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        gen_a = a.sup.generation

        def explode(self, *args, **kw):
            raise exc if exc is not None else Transient("connection lost")

        monkeypatch.setattr(Harness, "run", explode)
        op = stub.deploy_op("op_2", "rel_b", 2, plan_id="plan_b", expected_active="rel_a")
        a._run_operation(op)
        row = a.journal.operation("op_2")
        assert row["terminal"] and row["stage"] == "RolledBack", row
        assert (
            row["outcome"]["failure"]["code"] == code
            and row["outcome"]["failure"]["details"]["recovery"]["recovered"]
        )
        assert a.journal.get("active_release_id") == "rel_a" and a.sup.release_id == "rel_a"
        assert a.sup.generation > gen_a and a.gw.mode == "production" and not a.gw.needs_restart
        assert a.journal.get("failed_generation_latch") == 2 and a.journal.current_operation() is None
        assert stub.outcomes["op_2"][-1]["failure"]["code"] == code
    finally:
        a.shutdown()


# ----------------------------------------------------------------------------- R36
@pytest.mark.timeout(180)
def test_r36_active_release_failing_on_boot_falls_back_to_recovery_without_synthetic_rows(stub, tmp_path):
    stub.add_release("rel_r")
    stub.add_release("rel_a")
    a1 = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a1, "op_1", "rel_r", 1)[1]["status"] == "succeeded"
        assert _deploy(stub, a1, "op_2", "rel_a", 2)[1]["status"] == "succeeded"
        assert (
            a1.journal.get("active_release_id") == "rel_a"
            and a1.journal.get("recovery_release_id") == "rel_r"
        )
    finally:
        crash(a1)
    _inject_fault(a1, "rel_a", {"health_fail": True})  # committed active release cannot start on boot
    a2 = make_agent(tmp_path, stub)
    th = threading.Thread(target=a2.run, daemon=True)
    th.start()
    try:
        note = wait_for(
            lambda: (a2._restart_note or {}).get("action") == "recovered_on_start" and a2._restart_note, 60
        )
        assert a2.sup.state() == "running" and a2.journal.get("active_release_id") == "rel_r"
        assert note["action"] == "recovered_on_start" and note["result"]["recovered"] is True
        assert note["result"]["operation_id"].startswith("local-") and a2.gw.mode == "production"
        row = a2.journal.operation(note["result"]["operation_id"])
        assert row and row["terminal"] and row["type"] == "recover_local" and row["outcome_acked"] == 1
        assert a2.journal.current_operation() is None and a2.journal.get("health") == "ok"
        wait_for(lambda: stub.posts("/report"), 30)
        assert a2.observed()["operation_id"] is None  # local rows are never reported as server operations
    finally:
        a2.request_stop()
        th.join(timeout=30)
    assert not th.is_alive() and a2.lock_fd is None


@pytest.mark.timeout(60)
def test_r36_startup_failure_releases_lock_gateway_and_runtime(stub, tmp_path, monkeypatch):
    a = make_agent(tmp_path, stub)

    def explode():
        raise RuntimeError("journal exploded during startup recovery")

    monkeypatch.setattr(a.exec, "recover_after_restart", explode)
    with pytest.raises(RuntimeError):
        a.run()
    assert a.lock_fd is None and a.gw.server is None and a.sup.state() != "running"
    other = make_agent(tmp_path, stub)
    other.acquire_lock()  # the lock was released by the failed agent
    other.shutdown()


# ----------------------------------------------------------------------------- R37
@pytest.mark.timeout(120)
def test_r37_idle_timeout_latches_then_controlled_restart_reopens_production(stub, tmp_path):
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        gen = a.sup.generation
        a.gw.deadline_s = 1.0
        a.sup.sim.faults.update({"hang": True, "hang_s": 8})  # the live child hangs on its next request
        code, out = _chat(a.gw.port, {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 4})
        assert code == 504 and out["error"]["type"] == "timeout"
        assert a.gw.needs_restart is True  # idle could not be proven
        assert (
            _chat(a.gw.port, {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 4})[0] == 503
        )
        a.tick()  # no operation in flight: idle supervision must replace the owned child
        assert a.gw.needs_restart is False and a.gw.mode == "production" and a.sup.generation > gen
        assert a.sup.state() == "running" and a.journal.get("health") == "ok"
        code, out = _chat(
            a.gw.port,
            {"messages": [{"role": "user", "content": "What is the capital of France?"}], "max_tokens": 4},
        )
        assert code == 200 and out["choices"][0]["message"]["content"] == "Paris"
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_r37_probation_rollback_clears_needs_restart(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    stub.add_plan("plan_b", "rel_b", sample_policy={"probation_min_s": 20, "probation_min_requests": 0})
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"

        def flag(op_id, progress):
            if progress.get("stage") == "probation":
                a.gw.needs_restart = True  # a production request could not be proven idle

        stub.on_progress = flag
        op, out = _deploy(stub, a, "op_2", "rel_b", 2, plan_id="plan_b")
        assert out["status"] == "failed" and out["failure"]["code"] == "HEALTH_FAILED", out
        assert out["failure"]["details"]["recovery"]["recovered"] is True
        assert a.journal.get("active_release_id") == "rel_a" and a.sup.release_id == "rel_a"
        assert a.gw.needs_restart is False and a.gw.mode == "production"
        assert (
            _chat(
                a.gw.port,
                {
                    "messages": [{"role": "user", "content": "What is the capital of France?"}],
                    "max_tokens": 4,
                },
            )[0]
            == 200
        )
    finally:
        a.shutdown()


# ----------------------------------------------------------------------------- R38
@pytest.mark.timeout(120)
def test_r38_restart_restores_operation_bound_incumbent_not_global_pointer(stub, tmp_path):
    stub.add_release("rel_r")
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    a1 = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a1, "op_1", "rel_r", 1)[1]["status"] == "succeeded"
        assert _deploy(stub, a1, "op_2", "rel_a", 2)[1]["status"] == "succeeded"
        assert (
            a1.journal.get("active_release_id") == "rel_a"
            and a1.journal.get("recovery_release_id") == "rel_r"
        )
        # the fix commits intent + recovery identity together; prove atomicity (all or nothing)
        j = a1.journal
        j.begin_operation({"id": "op_atomic", "type": "deploy", "payload": {}, "generation": 9})
        j.set_stage("op_atomic", "WaitingGrant")
        real_set = j.set

        def failing_set(k, v, conn=None):
            raise RuntimeError("injected kv failure")

        j.set = failing_set
        with pytest.raises(RuntimeError):
            j.set_stage(
                "op_atomic",
                "Cutover",
                detail={"intent": {"recovery": "rel_a"}},
                kv_updates={"recovery_release_id": "rel_a"},
            )
        j.set = real_set
        assert j.operation("op_atomic")["stage"] == "WaitingGrant" and j.get("recovery_release_id") == "rel_r"
        j.finish_operation("op_atomic", "Dropped", {"status": "dropped"}, acked=True)
    finally:
        crash(a1)
    # pre-fix crash window: the Cutover row (intent.recovery = A) was committed, the global pointer was not
    j = Journal(tmp_path / "dev" / "journal.db")
    op = stub.deploy_op("op_3", "rel_b", 3, expected_active="rel_a")
    j.begin_operation(op)
    j.set_stage(
        "op_3",
        "Cutover",
        grant={"grant_id": "grant-op_3"},
        grant_consumed_seq=5,
        started_monotonic=0.0,
        detail={"intent": {"target": "rel_b", "recovery": "rel_a", "consumed_seq": 5}},
    )
    assert j.get("recovery_release_id") == "rel_r"  # older global pointer
    j.close()
    a2 = boot(make_agent(tmp_path, stub))
    try:
        note = a2._restart_note
        assert note["action"] == "recovered" and note["result"]["recovered"] is True
        assert note["result"]["active_release_id"] == "rel_a" and a2.sup.release_id == "rel_a"
        assert a2.journal.get("active_release_id") == "rel_a" and a2.gw.mode == "production"
        row = a2.journal.operation("op_3")
        assert row["stage"] == "RolledBack" and row["outcome"]["failure"]["code"] == "INTERRUPTED"
        assert row["outcome"]["grant_id"] == "grant-op_3" and row["outcome"]["grant_consumed_seq"] == 5
        assert row["outcome"]["result"]["active_release_id"] == "rel_a"
    finally:
        a2.shutdown()


# ----------------------------------------------------------------------------- R39
needs_proc = pytest.mark.skipif(
    not rtmod.PROC_IDENTITY_AVAILABLE,
    reason="child identity proof (pid + /proc start time + cmdline marker) is a Linux /proc contract; "
    "on other platforms the supervisor only discards ownership records and never signals",
)


@needs_proc
@pytest.mark.timeout(120)
def test_r39_orphaned_child_is_reaped_only_after_identity_proof(tmp_path):
    fake = write_fake_llama_server(tmp_path / "llama-server")
    work = tmp_path / "rt"
    spec = {"config": {}, "model": {"total_bytes": 1}}
    sup = RuntimeSupervisor(work, simulate=False)
    sup2 = sup3 = None
    victim = None
    pid = None
    try:
        ev = sup.start(
            release_id="r",
            spec=spec,
            model_path=Path("m.gguf"),
            template_path=None,
            binary=fake,
            lib_dir=None,
            health_timeout_s=20,
        )
        pid = sup.proc.pid
        rec = json.loads((work / "child.json").read_text())
        assert rec["pid"] == pid and rec["start_ticks"] == rtmod.proc_start_ticks(pid)
        assert rec["marker"] == str(sup.api_key_file) and rec["marker"] in rtmod.proc_cmdline(pid)
        assert ev["gpu_offloaded_layers"] == 29 and ev["backend"] == "CUDA0"
        # the agent dies without stopping its child (SIGKILL): only the record survives
        dead_proc = sup.proc
        sup.proc = None
        sup._reader = None
        sup2 = RuntimeSupervisor(work, simulate=False)
        assert sup2.orphan_note == {"action": "not_inspected"} and rtmod.proc_state(pid) not in (None, "Z")
        note = sup2.reap_orphan()  # explicit: only an exclusive owner inspects/signals
        assert note["action"] == "terminated" and note["pid"] == pid, note
        assert not (work / "child.json").exists()
        wait_for(lambda: rtmod.proc_state(pid) in (None, "Z"), 10)
        dead_proc.wait(timeout=5)
        # unrelated live processes are never signalled: same pid space, no marker / different start time
        victim = subprocess.Popen(["sleep", "60"])
        for bad in (
            {
                "pid": victim.pid,
                "start_ticks": rtmod.proc_start_ticks(victim.pid),
                "marker": str(work / "runtime.key"),
            },
            {"pid": victim.pid, "start_ticks": "1", "marker": "sleep"},
            {
                "pid": os.getpid(),
                "start_ticks": rtmod.proc_start_ticks(os.getpid()),
                "marker": str(work / "runtime.key"),
            },
        ):
            (work / "child.json").write_text(json.dumps(bad))
            note = RuntimeSupervisor(work, simulate=False).reap_orphan()
            assert note["action"] == "discarded", note
            assert victim.poll() is None and not (work / "child.json").exists()
        # a new launch over the same workdir reaps nothing and stop() clears its own record
        sup3 = RuntimeSupervisor(work, simulate=False)
        sup3.start(
            release_id="r",
            spec=spec,
            model_path=Path("m.gguf"),
            template_path=None,
            binary=fake,
            lib_dir=None,
            health_timeout_s=20,
        )
        assert (work / "child.json").exists()
        assert sup3.stop()["stopped"] is True and not (work / "child.json").exists()
    finally:
        for s_ in (sup, sup2, sup3):
            if s_ is not None:
                s_.stop()
        if victim is not None:
            victim.kill()
            victim.wait()
        if pid is not None and rtmod.proc_state(pid) not in (None, "Z"):
            os.kill(pid, 9)  # our own fake child, never an unrelated pid


@needs_proc
@pytest.mark.timeout(120)
def test_r39fu_live_owner_child_is_never_signalled_by_a_second_agent(stub, tmp_path):
    """Orphan inspection happens only behind the exclusive data-dir lock: constructing a second Agent
    (or supervisor) over a live owner's data dir must not touch the owner's child; the second agent
    fails its lock first."""
    fake = write_fake_llama_server(tmp_path / "llama-server")
    a1 = make_agent(tmp_path, stub)
    a1.acquire_lock()
    owner = RuntimeSupervisor(a1.data_dir / "runtime", simulate=False)  # the live owner's real child
    try:
        owner.start(
            release_id="r",
            spec={"config": {}, "model": {"total_bytes": 1}},
            model_path=Path("m.gguf"),
            template_path=None,
            binary=fake,
            lib_dir=None,
            health_timeout_s=20,
        )
        pid = owner.proc.pid
        record = json.loads((a1.data_dir / "runtime" / "child.json").read_text())
        a2 = make_agent(tmp_path, stub)  # construction must not inspect or signal
        assert a2.sup.orphan_note == {"action": "not_inspected"}
        with pytest.raises(SystemExit):
            a2.run()  # lock denied before any orphan inspection
        assert owner.proc.poll() is None and rtmod.proc_state(pid) not in (None, "Z")
        assert json.loads((a1.data_dir / "runtime" / "child.json").read_text()) == record
        assert a2.sup.orphan_note == {"action": "not_inspected"}
        a2.journal.close()
    finally:
        owner.stop()
        a1.shutdown()


# ----------------------------------------------------------------------------- R40
@pytest.mark.timeout(120)
def test_r40_pre_grant_row_is_resumed_and_the_redelivered_operation_executes(stub, tmp_path):
    stub.add_release("rel_a")
    j = Journal(tmp_path / "dev" / "journal.db")
    op = stub.deploy_op("op_x", "rel_a", 1)
    j.begin_operation(op)
    j.set_stage("op_x", "WaitingGrant", detail={"staged": {"note": "bytes were staged before the crash"}})
    j.close()
    a = boot(make_agent(tmp_path, stub))
    try:
        assert a._restart_note == {"operation_id": "op_x", "action": "resumed", "from": "WaitingGrant"}
        row = a.journal.operation("op_x")
        assert not row["terminal"] and row["stage"] == "Staging" and row["detail"]["resumed_after_restart"]
        a.tick()  # the server still delivers op_x: it must be admitted again, not suppressed
        assert a.worker is not None
        a.worker.join(timeout=60)
        row = a.journal.operation("op_x")
        assert row["terminal"] and row["stage"] == "Succeeded" and row["outcome_acked"] == 1, row
        assert (
            stub.outcomes["op_x"][-1]["status"] == "succeeded"
            and a.journal.get("active_release_id") == "rel_a"
        )
    finally:
        a.shutdown()


@pytest.mark.timeout(60)
def test_r40_pre_grant_row_not_redelivered_is_closed_only_when_dispatch_is_authoritative(stub, tmp_path):
    stub.add_release("rel_a")
    j = Journal(tmp_path / "dev" / "journal.db")
    j.begin_operation(
        {"id": "op_y", "type": "deploy", "payload": {"target_release_id": "rel_a"}, "generation": 1}
    )
    j.close()
    a = boot(make_agent(tmp_path, stub))
    try:
        stub.dispatch_paused = True
        a.tick()
        assert not a.journal.operation("op_y")["terminal"]  # paused dispatch says nothing about ownership
        stub.dispatch_paused = False
        a.tick()
        row = a.journal.operation("op_y")
        assert row["terminal"] and row["stage"] == "Dropped" and row["outcome_acked"] == 1
        assert stub.outcomes.get("op_y") is None  # nothing is owed to a server that does not deliver it
    finally:
        a.shutdown()


# ----------------------------------------------------------------------------- R41
@pytest.mark.timeout(60)
def test_r41_probation_counts_only_successful_production_completions(tmp_path):
    sens = SimulatedSensors(str(tmp_path))
    sup = RuntimeSupervisor(tmp_path / "rt", simulate=True, sensors=sens)
    sup.start(
        release_id="r",
        spec={"config": {}, "model": {"total_bytes": 1}},
        model_path=Path("m"),
        template_path=None,
        binary=None,
        lib_dir=None,
        health_timeout_s=5,
    )
    gw = Gateway(sup, deadline_s=5)
    port = gw.start()
    from convoy_agent.client import Client

    ex = Executor(
        journal=Journal(tmp_path / "j.db"),
        client=Client("http://127.0.0.1:1"),
        supervisor=sup,
        gateway=gw,
        sensors=sens,
        data_dir=tmp_path,
        device_id="d",
        simulated=True,
        server_base="http://127.0.0.1:1",
    )
    ex.j.begin_operation({"id": "op", "type": "deploy", "payload": {}, "generation": 1})
    result = {}
    th = threading.Thread(
        target=lambda: result.update(
            ex.probation({"id": "op", "generation": 1}, {"probation_min_s": 0.1, "probation_min_requests": 1})
        )
    )
    th.start()
    try:
        wait_for(lambda: gw.mode == "production", 5)
        assert (
            _chat(port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 0})[0] == 400
        )  # invalid request
        assert _chat(port, {"messages": [{"role": "user", "content": "hi"}], "tools": []})[0] == 400
        th.join(timeout=1.0)
        assert th.is_alive(), "rejected requests satisfied min_requests=1"
        assert gw.stats["requests"] >= 2 and gw.stats["served"] == 0
        assert _chat(port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})[0] == 200
        th.join(timeout=5)
        assert not th.is_alive() and result["requests_served"] == 1 and result["requests_rejected"] == 2
        assert result["requests_failed"] == 0 and result["population"] == "production_completions"
    finally:
        gw.stop()
        sup.stop()


# ----------------------------------------------------------------------------- R42
@pytest.mark.timeout(180)
def test_r42_recovery_attempt_bound_is_durable_across_restarts(stub, tmp_path, monkeypatch):
    stub.add_release("rel_r")
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    a1 = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a1, "op_1", "rel_r", 1)[1]["status"] == "succeeded"
        assert _deploy(stub, a1, "op_2", "rel_a", 2)[1]["status"] == "succeeded"
    finally:
        crash(a1)
    _inject_fault(a1, "rel_r", {"health_fail": True})  # the retained recovery release cannot start
    j = Journal(tmp_path / "dev" / "journal.db")
    op = stub.deploy_op("op_3", "rel_b", 3, expected_active="rel_a")
    j.begin_operation(op)
    j.set_stage(
        "op_3",
        "Cutover",
        grant={"grant_id": "g"},
        grant_consumed_seq=7,
        started_monotonic=0.0,
        detail={"intent": {"target": "rel_b", "recovery": "rel_r"}},
        kv_updates={"recovery_release_id": "rel_r"},
    )
    j.set_stage(
        "op_3", "Recovering", detail={"recovery_attempt": 1}, bump_attempts=True
    )  # crash after attempt 1
    j.set_many({"active_release_id": None, "health": "failed"})
    assert j.operation("op_3")["attempts"] == 1
    j.close()
    starts = []
    a2 = make_agent(tmp_path, stub)
    real_start = a2.sup.start
    monkeypatch.setattr(a2.sup, "start", lambda **kw: starts.append(kw["release_id"]) or real_start(**kw))
    boot(a2)
    try:
        note = a2._restart_note
        assert (
            note["action"] == "recovered"
            and note["result"]["recovered"] is False
            and note["result"]["degraded"] is True
        )
        row = a2.journal.operation("op_3")
        assert (
            row["stage"] == "Degraded" and row["attempts"] == RECOVERY_ATTEMPTS == 2 and starts == ["rel_r"]
        )
        assert note["result"]["attempts"] == 2 and a2.journal.get("active_release_id") is None
        assert a2.gw.mode == "closed" and a2.sup.state() != "running"
    finally:
        crash(a2)
    starts2 = []
    a3 = make_agent(tmp_path, stub)
    real_start3 = a3.sup.start
    monkeypatch.setattr(a3.sup, "start", lambda **kw: starts2.append(kw["release_id"]) or real_start3(**kw))
    boot(a3)
    try:
        assert (
            starts2 == [] and a3._restart_note is None and a3.journal.operation("op_3")["stage"] == "Degraded"
        )
        assert a3.journal.operation("op_3")["attempts"] == 2 and a3.gw.mode == "closed"
        a3.tick()
        assert stub.outcomes["op_3"][-1]["failure"]["code"] == "INTERRUPTED"
        assert stub.outcomes["op_3"][-1]["failure"]["details"]["recovery"]["attempts"] == 2
    finally:
        a3.shutdown()


# ----------------------------------------------------------------------------- R28-fu + eval record contract
def _sim_stack(tmp_path):
    sens = SimulatedSensors(str(tmp_path))
    sup = RuntimeSupervisor(tmp_path / "rt", simulate=True, sensors=sens)
    sup.start(
        release_id="rel_t",
        spec={"config": {}, "model": {"total_bytes": 1}},
        model_path=Path("m"),
        template_path=None,
        binary=None,
        lib_dir=None,
        health_timeout_s=5,
    )
    gw = Gateway(sup, deadline_s=5)
    gw.start()
    return sens, sup, gw


@pytest.mark.timeout(60)
def test_r28fu_harness_ttft_is_the_measured_gateway_value_never_prompt_ms(tmp_path, monkeypatch):
    from convoy_agent.evaluator import summarize

    sens, sup, gw = _sim_stack(tmp_path)
    stub = Stub()
    try:
        plan = stub.add_plan("p", "rel_t")
        real = gwmod.http_json

        def no_stream(base, key, req, timeout):
            code, out = real(
                f"{base}/completion", key, {k: v for k, v in req.items() if k != "stream"}, timeout=timeout
            )
            return code, out, None

        h = Harness(gw, sens, device_id="d", simulated=True)
        gw.set_mode("eval")
        monkeypatch.setattr(gwmod, "_completion_with_ttft", no_stream)
        res = h.run(
            plan,
            release_id="rel_t",
            release_digest="rd",
            runtime_evidence=sup.evidence,
            operation_id="op",
            generation=4,
        )
        assert res["device_verdict"] == "passed" and len(res["timings"]) == len(res["cases"]) == 2
        assert (
            all(t["ttft_ms"] is None for t in res["timings"])
            and res["summary"]["latency"]["ttft_p50_ms"] is None
        )
        assert all(
            set(t) == {"case_id", "status", "latency_ms", "ttft_ms", "queue_ms", "tok_s"}
            for t in res["timings"]
        )
        assert [t["case_id"] for t in res["timings"]] == [c["id"] for c in res["cases"]]
        assert res["generation"] == 4 and res["coverage"]["generation"] == 4
        assert res["sensor_samples"] and all(
            set(s)
            == {
                "ts",
                "mem_used_mb",
                "mem_available_mb",
                "temp_max_c",
                "power_w",
                "gpu_pct",
                "clock_confidence",
            }
            for s in res["sensor_samples"]
        )
        recomputed = summarize(
            res["cases"],
            res["timings"],
            res["sensor_samples"],
            runtime_props=res["runtime_props"],
            expected_cases=res["coverage"]["expected"],
        )
        assert recomputed == res["summary"]  # the transmitted lists are exactly the summary inputs

        def fixed_ttft(base, key, req, timeout):
            code, out = real(
                f"{base}/completion", key, {k: v for k, v in req.items() if k != "stream"}, timeout=timeout
            )
            return code, out, 7.25

        monkeypatch.setattr(gwmod, "_completion_with_ttft", fixed_ttft)
        res = h.run(
            plan,
            release_id="rel_t",
            release_digest="rd",
            runtime_evidence=sup.evidence,
            operation_id="op",
            generation=4,
        )
        assert all(t["ttft_ms"] == 7.25 for t in res["timings"])  # never prompt_ms (12.0 in the simulator)
    finally:
        stub.stop()
        gw.stop()
        sup.stop()


@pytest.mark.timeout(60)
def test_r28fu_sensor_samples_are_capped_at_source_and_summary_matches(tmp_path, monkeypatch):
    from convoy_agent import harness as hm
    from convoy_agent.evaluator import summarize

    sens, sup, gw = _sim_stack(tmp_path)
    stub = Stub()
    try:
        monkeypatch.setattr(hm, "SENSOR_SAMPLE_CAP", 4)
        plan = stub.add_plan("p", "rel_t")
        plan["workload"]["warmup"] = 0
        h = Harness(gw, sens, device_id="d", simulated=True)
        gw.set_mode("eval")
        real_sample = sens.sample
        sens.sample = lambda state=None: (time.sleep(0.0), real_sample(state))[1]
        res = h.run(
            plan,
            release_id="rel_t",
            release_digest="rd",
            runtime_evidence=sup.evidence,
            operation_id="op",
            generation=1,
        )
        assert len(res["sensor_samples"]) <= 4 and res["provenance"]["sensor_samples"] == len(
            res["sensor_samples"]
        )
        assert (
            summarize(
                res["cases"],
                res["timings"],
                res["sensor_samples"],
                runtime_props=res["runtime_props"],
                expected_cases=2,
            )
            == res["summary"]
        )
    finally:
        stub.stop()
        gw.stop()
        sup.stop()


@pytest.mark.timeout(60)
def test_gateway_timeout_span_records_streamed_tokens_as_generation_evidence(tmp_path, monkeypatch):
    sens, sup, gw = _sim_stack(tmp_path)
    spans = []
    gw.on_span = spans.append
    gw.set_mode("production")
    try:
        monkeypatch.setattr(
            gwmod, "_completion_with_ttft", lambda *a, **k: (_ for _ in ()).throw(gwmod.StreamDeadline(3))
        )
        code, _ = _chat(gw.port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})
        assert code == 504
        t = [s for s in spans if s["status"] == "timeout"][-1]
        assert t["attrs"]["tokens_streamed"] == 3 and t["attrs"]["reason"] == "StreamDeadline"
    finally:
        gw.stop()
        sup.stop()


# ----------------------------------------------------------------------------- R29-fu
@pytest.mark.timeout(120)
def test_r29fu_chatty_runtime_log_stays_bounded_while_startup_evidence_is_retained(tmp_path):
    fake = write_fake_llama_server(tmp_path / "llama-server")
    work = tmp_path / "rt"
    work.mkdir()
    (work / "CHATTY").write_text("1")
    sup = RuntimeSupervisor(work, simulate=False)
    try:
        ev = sup.start(
            release_id="r",
            spec={"config": {}, "model": {"total_bytes": 1}},
            model_path=Path("m.gguf"),
            template_path=None,
            binary=fake,
            lib_dir=None,
            health_timeout_s=20,
        )
        assert ev["gpu_offloaded_layers"] == 29 and ev["backend"] == "CUDA0" and ev["cuda_devices"] == 1
        wait_for(lambda: sup.log_stats()["total_bytes"] > 4 * 1024 * 1024, 60)
        st = sup.log_stats()
        on_disk = sum(p.stat().st_size for p in work.glob("runtime.*.log"))
        assert st["rotations"] >= 1 and on_disk <= 1024 * 1024 and st["retained_bytes"] <= 320 * 1024, (
            st,
            on_disk,
        )
        lines = sup.log_lines()
        assert any("offloaded 29/29" in x for x in lines) and "chatter line" in lines[-1]
        assert sup.collect_evidence()["gpu_offloaded_layers"] == 29  # startup proof survives the chatter
        tail = sup.log_tail(5)
        assert len(tail) == 5 and all("chatter line" in x for x in tail)
        wait_for(lambda: sup.log_stats()["total_bytes"] > st["total_bytes"] + 1024 * 1024, 60)
        assert sum(p.stat().st_size for p in work.glob("runtime.*.log")) <= 1024 * 1024  # still bounded
    finally:
        assert sup.stop()["stopped"] is True
    assert not (work / "child.json").exists()


# ----------------------------------------------------------------------------- R72
@pytest.mark.timeout(180)
def test_r72_sigterm_stops_the_service_cleanly_and_restart_resumes(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_plan("plan_a", "rel_a", sample_policy={"probation_min_s": 60, "probation_min_requests": 0})
    a = make_agent(tmp_path, stub)  # writes the enrolled data dir
    a.journal.close()
    d = a.data_dir
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTHONUNBUFFERED": "1"}
    cmd = [sys.executable, "-m", "convoy_agent.cli", "--data-dir", str(d), "run", "--no-robot-sim"]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        wait_for(lambda: stub.posts("/report"), 30)
        stub.deploy_op("op_1", "rel_a", 1, plan_id="plan_a")
        wait_for(
            lambda: any(
                (b.get("progress") or {}).get("stage") == "probation" for _, b in stub.posts("/outcome")
            ),
            90,
        )
        proc.send_signal(__import__("signal").SIGTERM)
        rc = proc.wait(timeout=45)  # inside the unit's TimeoutStopSec
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        out = proc.stdout.read().decode(errors="replace")
    assert rc == 0, out
    j = Journal(d / "journal.db")
    row = j.operation("op_1")
    assert row and row["terminal"] and row["outcome"]["failure"]["code"] == "INTERRUPTED", row
    persisted_outcome = row["outcome"]
    assert j.current_operation() is None
    j.close()
    # bounded shutdown posts nothing after the stop request: the INTERRUPTED outcome is durable locally
    # (unacked terminal row) and is delivered UNCHANGED by the next start's replay (R33)
    assert "op_1" not in stub.outcomes or stub.outcomes["op_1"][-1].get("status") == "running"
    lock_probe = make_agent(tmp_path, stub)
    lock_probe.acquire_lock()  # released by the terminated process
    lock_probe.shutdown()
    n_reports = len(stub.posts("/report"))
    proc2 = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        wait_for(lambda: len(stub.posts("/report")) > n_reports, 30)
        wait_for(
            lambda: any(
                o.get("status") != "running" and (o.get("failure") or {}).get("code") == "INTERRUPTED"
                for o in stub.outcomes.get("op_1", [])
            ),
            30,
        )
        delivered = [o for o in stub.outcomes["op_1"] if o.get("status") != "running"]
        assert delivered == [persisted_outcome], (delivered, persisted_outcome)  # replayed UNCHANGED (R33)
        proc2.send_signal(__import__("signal").SIGINT)
        assert proc2.wait(timeout=45) == 0
    finally:
        if proc2.poll() is None:
            proc2.kill()
            proc2.wait(timeout=10)
        out2 = proc2.stdout.read().decode(errors="replace")
    assert "another convoy-agent holds the lock" not in out2


# ----------------------------------------------------------------------------- R77 standalone eval
def _eval_op(stub, op_id, rid, gen, plan_id, *, baseline=False):
    m = stub.manifests[rid]
    plan = stub.plans[plan_id]
    op = {
        "id": op_id, "type": "eval", "generation": gen, "status": "delivered", "cancel_requested": False,
        "payload": {"release_id": rid, "release_digest": m["digest"], "plan_id": plan_id, "plan_digest": plan["digest"], "baseline": baseline, "simulated": True, "target_release_id": None, "expected_active_release_id": rid},
        "expected_active_release_id": rid,
    }  # fmt: skip
    stub.ops.append(op)
    return op


@pytest.mark.timeout(180)
def test_r77_standalone_eval_takes_a_grant_and_completes_over_http(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_plan("plan_a", "rel_a")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        a.tick()
        op = _eval_op(stub, "op_2", "rel_a", 2, "plan_a", baseline=True)
        a.tick()
        a.worker.join(timeout=60)
        g = stub.grants["op_2"]
        row = a.journal.operation("op_2")
        assert (
            row["stage"] == "Succeeded"
            and row["grant"]["grant_id"] == g["grant_id"]
            and row["grant_consumed_seq"] >= 1
        )
        posted = stub.outcomes["op_2"][-1]
        assert (
            posted == row["outcome"]
            and posted["status"] == "succeeded"
            and posted["grant_id"] == g["grant_id"]
        )
        assert posted["result"]["active_release_id"] == "rel_a" and posted["result"]["eval_result_id"]
        assert (
            posted["evidence"]["eval"]["verdict"] == "passed"
            and posted["evidence"]["eval"]["plan_digest"] == op["payload"]["plan_digest"]
        )
        rec = [
            r for r in stub.spool if r.get("kind") == "eval_result" and r["body"]["operation_id"] == "op_2"
        ][-1]
        assert (
            rec["body"]["id"] == posted["result"]["eval_result_id"]
            and rec["body"]["generation"] == 2
            and rec["body"]["coverage"]["generation"] == 2
        )
        assert rec["body"]["stage"] == "baseline"
        grant_i = stub.first_index(lambda e: e[1] == "/api/agent/v1/operations/op_2/grant")
        eval_i = stub.first_index(
            lambda e: (
                e[1] == "/api/agent/v1/spool"
                and any(
                    r.get("kind") == "eval_result" and r["body"]["operation_id"] == "op_2"
                    for r in (e[2] or {}).get("records", [])
                )
            )
        )
        out_i = stub.first_index(
            lambda e: e[1] == "/api/agent/v1/operations/op_2/outcome" and e[2].get("status") == "succeeded"
        )
        assert grant_i < eval_i < out_i and not any(o.get("_rejected") for o in stub.outcomes["op_2"])
        assert row["outcome_acked"] == 1 and a.gw.mode == "production" and a.sup.release_id == "rel_a"
    finally:
        a.shutdown()


@pytest.mark.timeout(180)
def test_r77_eval_grant_negatives_expired_cancelled_and_paused(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_plan("plan_a", "rel_a")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        gen = a.sup.generation
        # expired: issued already stale -> production is never closed, failure cites the consumed grant
        stub.grant_ttl_s = -1.0
        out = run_op(a, _eval_op(stub, "op_e", "rel_a", 2, "plan_a"))
        assert (
            out["status"] == "failed"
            and out["failure"]["code"] == "GRANT_EXPIRED"
            and out["grant_id"] == "grant-op_e"
        )
        assert (
            a.gw.mode == "production"
            and a.sup.generation == gen
            and a.journal.operation("op_e")["stage"] == "Failed"
        )
        assert not any(
            r.get("kind") == "eval_result" and r["body"]["operation_id"] == "op_e" for r in stub.spool
        )
        stub.grant_ttl_s = 30.0
        # cancelled before the grant: terminal locally with the server's reason, outcome posted honestly
        stub.grant_error = (409, "cancellation requested")
        out = run_op(a, _eval_op(stub, "op_c", "rel_a", 3, "plan_a"))
        assert out["status"] == "failed" and out["failure"]["code"] == "GRANT_CANCELLED"
        assert a.journal.operation("op_c")["stage"] == "Cancelled" and a.gw.mode == "production"
        a._post_outcome("op_c", out)
        assert stub.outcomes["op_c"][-1]["failure"]["code"] == "GRANT_CANCELLED"
        # paused / outside the window: NOT terminal, NOT acknowledged; retried on a later delivery
        stub.grant_error = (423, "dispatch paused")
        op_p = _eval_op(stub, "op_p", "rel_a", 4, "plan_a")
        a._run_operation(op_p)
        row = a.journal.operation("op_p")
        assert (
            not row["terminal"]
            and row["stage"] == "WaitingGrant"
            and row["detail"]["grant_deferred"]["status"] == 423
        )
        assert stub.outcomes.get("op_p") is None and a.gw.mode == "production"
        stub.grant_error = None
        a.tick()  # the server delivers op_p again: the pre-grant row is admitted and completes
        a.worker.join(timeout=60)
        row = a.journal.operation("op_p")
        assert (
            row["terminal"]
            and row["stage"] == "Succeeded"
            and stub.outcomes["op_p"][-1]["status"] == "succeeded"
        )
        assert row["outcome_acked"] == 1 and stub.grants["op_p"]["grant_id"] == row["outcome"]["grant_id"]
    finally:
        a.shutdown()


# ----------------------------------------------------------------------------- misc boundaries
@pytest.mark.timeout(60)
def test_executor_boundary_never_leaves_a_non_terminal_row(stub, tmp_path, monkeypatch):
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    try:
        monkeypatch.setattr(
            a.exec, "preflight", lambda *args, **kw: (_ for _ in ()).throw(ValueError("bad sensor"))
        )
        op = stub.deploy_op("op_1", "rel_a", 1)
        a._run_operation(op)
        row = a.journal.operation("op_1")
        assert row["terminal"] and row["outcome"]["failure"]["code"] == "UNEXPECTED_ERROR"
        assert stub.outcomes["op_1"][-1] == row["outcome"] and row["outcome_acked"] == 1
        assert a.sup.state() == "stopped" and a.gw.mode == "closed"  # nothing was running before
    finally:
        a.shutdown()


def test_opfailure_shape():
    f = OpFailure("X", "msg", "stage", {"k": 1})
    assert f.code == "X" and f.stage == "stage" and f.details == {"k": 1} and str(f) == "msg"


# ----------------------------------------------------------------------------- R82 stream completeness
class _Peer:
    """Controlled ordinary HTTP peer standing in for the native runtime at the gateway boundary. It is
    NOT a native crash reproduction: it answers /apply-template, /tokenize, /slots and streams a
    /completion SSE body with a valid Content-Length that either ends after a partial event (premature
    EOF) or carries a proper terminal `stop: true` event."""

    def __init__(self, mode: str):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        peer = self
        self.mode = mode
        self.calls: list[str] = []

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                peer.calls.append(self.path)
                if self.path == "/slots":
                    return self._json([{"id": 0, "is_processing": False}])
                if self.path == "/health":
                    return self._json({"status": "ok"})
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                peer.calls.append(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                if self.path == "/apply-template":
                    return self._json({"prompt": "<|im_start|>user\nhi<|im_end|>\n<|im_start|>assistant\n"})
                if self.path == "/tokenize":
                    return self._json({"tokens": [151644, 1001, 1002]})
                if self.path == "/completion":
                    events = ['data: {"content":"partial","stop":false}\n\n']
                    if peer.mode == "complete":
                        events.append(
                            'data: {"content":"","stop":true,"tokens_evaluated":3,"tokens_predicted":1,"stop_type":"eos","timings":{"prompt_ms":1.0}}\n\n'
                        )
                    body = "".join(events).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                    return
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _gateway_over_peer(tmp_path, peer):
    sens, sup, gw = _sim_stack(tmp_path)
    sup.port = peer.port  # the gateway now talks to the controlled peer instead of the simulator
    spans = []
    gw.on_span = spans.append
    gw.set_mode("production")
    return sup, gw, spans


@pytest.mark.timeout(60)
def test_r82_premature_stream_eof_is_a_structured_failure_not_a_served_completion(tmp_path):
    peer = _Peer("premature")
    sup, gw, spans = _gateway_over_peer(tmp_path, peer)
    try:
        code, out = _chat(gw.port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})
        assert (
            code == 502
            and out["error"]["type"] == "runtime_error"
            and "RUNTIME_STREAM_INCOMPLETE" in out["error"]["message"]
        )
        assert gw.stats["served"] == 0 and gw.stats["failed"] == 1 and gw.stats["served_tokens_out"] == 0
        f = [s for s in spans if s["status"] == "failed"][-1]
        assert f["attrs"]["reason"] == "RUNTIME_STREAM_INCOMPLETE" and f["attrs"]["tokens_streamed"] == 1
        assert "/slots" in peer.calls, "admission ownership is retained until the runtime is confirmed idle"
        assert gw.needs_restart is False  # the peer proved idle; nothing to replace
    finally:
        gw.stop()
        peer.stop()
        sup.port = sup.sim.port
        sup.stop()


@pytest.mark.timeout(60)
def test_r82_stream_with_terminal_event_is_served(tmp_path):
    peer = _Peer("complete")
    sup, gw, spans = _gateway_over_peer(tmp_path, peer)
    try:
        code, out = _chat(gw.port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})
        assert code == 200 and out["choices"][0]["message"]["content"] == "partial"
        assert out["choices"][0]["finish_reason"] == "stop" and out["convoy"]["ttft_ms"] is not None
        assert gw.stats["served"] == 1 and gw.stats["failed"] == 0 and spans[-1]["status"] == "ok"
        assert "/slots" not in peer.calls  # a completed stream needs no idle proof
    finally:
        gw.stop()
        peer.stop()
        sup.port = sup.sim.port
        sup.stop()


# ----------------------------------------------------------------------------- review follow-ups
@pytest.mark.timeout(180)
def test_r35fu_terminal_commit_failure_degrades_safely_and_is_never_acked(stub, tmp_path):
    """The post-probation success commit itself fails (StorageError): admission closes, the owned
    runtime stops, the row stays honestly non-terminal, nothing is posted or ACKed, the agent asks for
    a restart, and restart recovery settles the row from the durable journal."""
    from convoy_agent.journal import StorageError

    stub.add_release("rel_a")
    stub.add_release("rel_b")
    stub.add_plan("plan_b", "rel_b")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        real_finish = a.journal.finish_operation

        def failing_finish(op_id, stage, outcome, kv_updates=None, **kw):
            if stage == "Succeeded":
                raise StorageError("injected: COMMIT failed")
            return real_finish(op_id, stage, outcome, kv_updates, **kw)

        a.journal.finish_operation = failing_finish
        op = stub.deploy_op("op_2", "rel_b", 2, plan_id="plan_b", expected_active="rel_a")
        a._run_operation(op)
        row = a.journal.operation("op_2")
        assert row["terminal"] == 0 and row["stage"] == "Probation" and row["outcome"] is None  # honest
        assert a.gw.mode == "closed" and a.sup.state() != "running" and a.journal.get("health") == "failed"
        assert stub.outcomes.get("op_2") is None and row["outcome_acked"] == 0  # never an invented terminal
        assert a.stop.is_set()  # explicit degraded handling: stop for restart recovery
        a.journal.finish_operation = real_finish
    finally:
        crash(a)
    a2 = boot(make_agent(tmp_path, stub))
    try:
        note = a2._restart_note
        assert note["action"] == "recovered" and note["result"]["recovered"] is True
        row = a2.journal.operation("op_2")
        assert (
            row["terminal"]
            and row["stage"] == "RolledBack"
            and row["outcome"]["failure"]["code"] == "INTERRUPTED"
        )
        assert a2.journal.get("active_release_id") == "rel_a" and a2.gw.mode == "production"
    finally:
        a2.shutdown()


@pytest.mark.timeout(120)
def test_r33fu_post_commit_cleanup_failure_keeps_the_durable_success(stub, tmp_path, monkeypatch):
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    try:
        monkeypatch.setattr(a.exec, "_gc_pins", lambda **kw: (_ for _ in ()).throw(OSError("disk went away")))
        op, out = _deploy(stub, a, "op_1", "rel_a", 1)
        row = a.journal.operation("op_1")
        assert out["status"] == "succeeded" and row["stage"] == "Succeeded" and row["outcome"] == out
        assert a.journal.get("active_release_id") == "rel_a" and a.gw.mode == "production"
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_r34fu_expired_grant_preserves_the_previously_retained_recovery_release(stub, tmp_path):
    stub.add_release("rel_r")
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_r", 1)[1]["status"] == "succeeded"
        assert _deploy(stub, a, "op_2", "rel_a", 2)[1]["status"] == "succeeded"
        assert (
            a.journal.get("active_release_id") == "rel_a" and a.journal.get("recovery_release_id") == "rel_r"
        )
        stub.grant_ttl_s = -1.0
        _, out = _deploy(stub, a, "op_3", "rel_b", 3)
        assert out["status"] == "failed" and out["failure"]["code"] == "GRANT_EXPIRED"
        assert (
            a.journal.get("active_release_id") == "rel_a" and a.journal.get("recovery_release_id") == "rel_r"
        )
        assert a.journal.operation("op_3")["detail"]["intent"]["recovery"] == "rel_a"  # op-bound intent kept
        assert "disruption_started" not in a.journal.operation("op_3")["detail"]
        stub.grant_ttl_s = 30.0
        out = run_op(a, stub.recover_op("op_4", "rel_r", 4))  # explicit recovery to the retained R works
        assert out["status"] == "succeeded" and a.journal.get("active_release_id") == "rel_r"
        assert a.journal.get("recovery_release_id") == "rel_a"
    finally:
        a.shutdown()


def _partial_peer(mode: str):
    """Controlled peer that streams ONE event and then either truncates (declares a longer body and
    closes: clean EOF at the gateway) or stalls (keeps the socket open without further bytes: a
    transport read timeout at the gateway). Neither is a native crash reproduction."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    calls: list[str] = []

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _json(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            calls.append(self.path)
            self._json([{"id": 0, "is_processing": False}])

        def do_POST(self):
            calls.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/apply-template":
                return self._json({"prompt": "p"})
            if self.path == "/tokenize":
                return self._json({"tokens": [1, 2, 3]})
            if mode == "reset":  # admission worked; the completion connection dies before any byte
                self.close_connection = True
                self.connection.close()
                return
            first = b'data: {"content":"partial","stop":false}\n\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(first) + 4096))  # promises more than it sends
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            if mode == "stall":
                time.sleep(4.0)  # longer than the gateway deadline used by the test
            if mode == "late":  # a valid terminal event that lands AFTER the gateway's absolute deadline
                time.sleep(1.4)
                self.wfile.write(
                    b'data: {"content":"","stop":true,"tokens_predicted":1,"stop_type":"eos"}\n\n'
                )
                self.wfile.flush()
            self.close_connection = True
            self.connection.close()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, calls


@pytest.mark.timeout(60)
def test_r82fu_transport_failures_preserve_observed_streamed_progress(tmp_path):
    sens, sup, gw = _sim_stack(tmp_path)
    spans = []
    gw.on_span = spans.append
    gw.set_mode("production")
    trunc, calls_t = _partial_peer("truncate")
    stall, calls_s = _partial_peer("stall")
    reset, calls_r = _partial_peer("reset")
    sim_port = sup.port
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}
    try:
        # (a) body shorter than declared, then close: a clean EOF without a terminal event
        sup.port = trunc.server_address[1]
        code, out = _chat(gw.port, body)
        assert (
            code == 502 and "RUNTIME_STREAM_INCOMPLETE" in out["error"]["message"] and gw.stats["served"] == 0
        )
        f = [s for s in spans if s["status"] == "failed"][-1]
        assert f["attrs"]["tokens_streamed"] == 1 and "/slots" in calls_t
        # (b) transport read timeout mid-stream: the observed progress (1 token) survives the exception
        sup.port = stall.server_address[1]
        gw.deadline_s = 1.5
        code, out = _chat(gw.port, body)
        gw.deadline_s = 5
        assert code == 504 and out["error"]["type"] == "timeout" and gw.stats["served"] == 0
        t = [s for s in spans if s["status"] == "timeout"][-1]
        assert t["attrs"]["tokens_streamed"] == 1 and t["attrs"]["reason"] == "TimeoutError", t["attrs"]
        assert "/slots" in calls_s  # ownership retained until idle is proven
        # (c) the completion connection dies before any token: observed progress is exactly zero
        sup.port = reset.server_address[1]
        code, out = _chat(gw.port, body)
        assert code == 504 and gw.stats["served"] == 0
        t = [s for s in spans if s["status"] == "timeout"][-1]
        assert t["attrs"]["tokens_streamed"] == 0 and t["attrs"]["reason"] == "RemoteDisconnected", t["attrs"]
        assert "/slots" in calls_r
    finally:
        sup.port = sim_port
        gw.stop()
        for srv in (trunc, stall, reset):
            srv.shutdown()
            srv.server_close()
        sup.stop()


# ----------------------------------------------------------------------------- TLS trust (B)
@pytest.mark.timeout(60)
def test_tls_private_ca_is_additive_and_scoped_to_the_control_plane(tmp_path):
    import ssl
    import urllib.error
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from convoy_agent.client import Client

    key, cert = tmp_path / "ca.key", tmp_path / "ca.pem"
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
         "-days", "1", "-subj", "/CN=convoy-private-ca", "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        capture_output=True, text=True,
    )  # fmt: skip
    if r.returncode != 0:
        pytest.skip(f"openssl unavailable for the TLS regression: {r.stderr[:200]}")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(str(cert), str(key))
    srv.socket = sctx.wrap_socket(srv.socket, server_side=True)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"https://127.0.0.1:{port}"
        c = Client(base, "cvd_dev_secret", ca_file=str(cert))
        # additive trust: the private CA is loaded ON TOP of the system roots, never instead of them
        system = ssl.create_default_context().cert_store_stats()["x509_ca"]
        assert c.ctx.cert_store_stats()["x509_ca"] == system + 1
        assert any(
            any("convoy-private-ca" in str(v) for v in x.get("subject", ())) for x in c.ctx.get_ca_certs()
        )
        assert c.ctx.check_hostname and c.ctx.verify_mode == ssl.CERT_REQUIRED
        # the public context carries only the system roots (no private CA) and full validation
        assert c.public_ctx.cert_store_stats()["x509_ca"] == system and c.public_ctx.check_hostname
        # control-plane origin validates through the private CA (API call and artifact stream alike)
        assert c.get("/api/agent/v1/releases/x") == {"ok": True}
        assert c.open_stream(f"{base}/api/sim/blobs/x").status == 200
        assert c.context_for(f"{base}/api/x") is c.ctx
        # any other origin (a public artifact host) never uses the private trust: same server reached
        # under a different origin must fail public validation, not silently pass through the private CA
        other = f"https://localhost:{port}/resolve/model.gguf"
        assert c.context_for(other) is c.public_ctx
        with pytest.raises(urllib.error.URLError) as e:
            c.open_stream(other)
        assert "CERTIFICATE_VERIFY_FAILED" in str(e.value)
        # without --ca-file the control plane itself is rejected (no silent trust anywhere)
        from convoy_agent.client import Transient

        with pytest.raises(Transient) as e2:
            Client(base, "cvd_dev_secret").get("/api/agent/v1/releases/x", retries=0)
        assert "CERTIFICATE_VERIFY_FAILED" in str(e2.value)
    finally:
        srv.shutdown()
        srv.server_close()


# ----------------------------------------------------------------------------- restore gap (R48 follow-up)
def _lane(a, lane):
    return a.journal.lane_status()[lane]


@pytest.mark.timeout(120)
def test_restore_gap_declares_exact_loss_once_and_resumes_exactly_once(stub, tmp_path, caplog):
    a = boot(make_agent(tmp_path, stub))
    try:
        for i in range(3):
            a.emit("critical", "failure", {"ts": i, "code": "X", "n": i})
            a.emit("usage", "usage", {"ts": i, "inference_requests": i})
        a.tick()  # telemetry from the report + flush: the old server acknowledges everything
        a.tick()
        before = {ln: _lane(a, ln)["committed_seq"] for ln in ("critical", "usage", "telemetry")}
        assert before["critical"] == 3 and before["usage"] == 3 and before["telemetry"] >= 2
        assert stub.cursor == before
        report_seq_before = a.journal.get("report_seq")
        # --- the server is restored to an OLDER snapshot: its cursors fall behind the durable local ACKs
        stub.cursor = {"critical": 1, "usage": 0, "telemetry": before["telemetry"] - 2}
        stub.spool.clear()
        stub.spool_calls.clear()
        a.emit("critical", "failure", {"ts": 9, "code": "NEW"})  # seq 4: retained locally, never acked
        a.emit("usage", "usage", {"ts": 9, "inference_requests": 9})  # seq 4
        retained = {ln: a.journal.pending(ln)[0] for ln in ("critical", "usage", "telemetry")}
        stub.quarantine = True
        with caplog.at_level("WARNING"):
            a.tick()
        assert any("until quarantine is lifted" in r.getMessage() for r in caplog.records)
        assert not any("until rebinding" in r.getMessage() for r in caplog.records)
        assert any(
            "pre_restore_history_unrecoverable" in r.getMessage() and "context=" in r.getMessage()
            for r in caplog.records
        )
        stub.quarantine = False
        # exactly one declaration per lane over EXACTLY the unrecoverable span, then the retained records
        assert [(x["lane"], x["from_seq"], x["to_seq"], x["reason"]) for x in stub.losses] == [
            ("critical", 2, 3, "pre_restore_history_unrecoverable"),
            ("usage", 1, 3, "pre_restore_history_unrecoverable"),
            ("telemetry", before["telemetry"] - 1, before["telemetry"], "pre_restore_history_unrecoverable"),
        ]
        for ln in ("critical", "usage"):
            got = [r for r in stub.spool if r["lane"] == ln]
            assert [(r["seq"], r["body"]) for r in got] == [
                (r["seq"], r["body"]) for r in retained[ln]
            ]  # same bytes, same seqs
            assert [r["seq"] for r in got][0] == before[ln] + 1
        assert stub.cursor["critical"] == _lane(a, "critical")["committed_seq"] == 4
        assert stub.cursor["usage"] == _lane(a, "usage")["committed_seq"] == 4
        assert a.journal.get("report_seq") > report_seq_before  # sequences/identity never reset
        assert all(x["rowid"] is None for x in []) and all(
            not a.journal.pending(ln)[1] for ln in ("critical", "usage", "telemetry")
        )
        # repeated reconnects: nothing new is declared and nothing is double counted
        n_losses, n_calls = len(stub.losses), len(stub.spool_calls)
        a.flush_spool(probe=True)
        a.tick()
        assert len(stub.losses) == n_losses
        assert all(not c["loss_ranges"] for c in stub.spool_calls[n_calls:])
        assert len([r for r in stub.spool if r["lane"] == "usage"]) == 1  # usage counted exactly once
    finally:
        a.shutdown()


def test_journal_partial_ack_retains_unacknowledged_loss_rows(tmp_path):
    j = Journal(tmp_path / "j.db")
    for i in range(6):
        j.append("critical", "failure", {"i": i})
    j.commit_lane("critical", 6, [])
    rid = j.declare_loss("critical", 3, 8, "pre_restore_history_unrecoverable")
    assert j.declare_loss("critical", 3, 8, "pre_restore_history_unrecoverable") == rid  # idempotent
    j.commit_lane("critical", 5, [rid])  # partial ACK: the server frontier does not cover the span
    assert [x["rowid"] for x in j.pending("critical")[1]] == [rid]
    assert j.lane_status()["critical"]["committed_seq"] == 6  # never moves backwards
    j.commit_lane("critical", 8, [rid])
    assert (
        j.pending("critical")[1] == [] and j.loss_covers("critical", 4) == "pre_restore_history_unrecoverable"
    )
    assert j.loss_covers("critical", 9) is None
    rid2 = j.declare_loss("critical", 9, 12, "pre_restore_history_unrecoverable")
    assert rid2 != rid and [x["from_seq"] for x in j.pending("critical")[1]] == [9]


@pytest.mark.timeout(240)
def test_success_after_restore_requires_current_server_acceptance_or_reports_evidence_lost(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    stub.add_plan("plan_a", "rel_a")
    stub.add_plan("plan_b", "rel_b")
    a = boot(make_agent(tmp_path, stub))
    try:
        # (1) eval record still RETAINED locally when the server is restored: it is resent after the
        #     declared loss and the success settles exactly once
        for i in range(2):  # acknowledged critical history that the restored server will have lost
            a.emit("critical", "failure", {"ts": i, "code": "OLD", "i": i})
        a.tick()
        acked = _lane(a, "critical")["committed_seq"]
        assert acked == 2 and stub.cursor["critical"] == 2
        stub.on_progress = lambda op_id, pr: setattr(stub, "down", pr.get("stage") == "probation") or None
        op1 = stub.deploy_op("op_1", "rel_a", 1, plan_id="plan_a")
        a._run_operation(op1)  # success committed; nothing published (server unreachable)
        stub.on_progress = None
        row = a.journal.operation("op_1")
        seq1 = row["detail"]["eval_spool_seq"]
        assert row["stage"] == "Succeeded" and row["outcome_acked"] == 0 and stub.outcomes.get("op_1") is None
        stub.down = False
        stub.cursor["critical"] = 0  # restored snapshot predates the acknowledged pre-eval history
        stub.spool.clear()  # the restored server holds none of the old records
        a.tick()
        assert stub.outcomes["op_1"][-1] == a.journal.operation("op_1")["outcome"]
        assert stub.outcomes["op_1"][-1]["status"] == "succeeded" and not any(
            o.get("_rejected") for o in stub.outcomes["op_1"]
        )
        assert row["outcome"]["evidence"]["eval"]["eval_result_id"] in stub.accepted_eval_ids
        assert [(x["from_seq"], x["to_seq"]) for x in stub.losses if x["lane"] == "critical"] == [(1, acked)]
        assert acked < seq1 and [r["seq"] for r in stub.spool if r["lane"] == "critical"][0] == acked + 1
        # (2) eval record ACKed by the OLD server and deleted locally, outcome not yet posted, then the
        #     server is restored to before that record: success is impossible -> EVIDENCE_LOST_ON_RESTORE
        stub.reject_outcomes = True
        op2 = stub.deploy_op("op_2", "rel_b", 2, plan_id="plan_b", expected_active="rel_a")
        a._run_operation(op2)  # evidence flushed + ACKed (old server), outcome post 503 -> deferred
        row2 = a.journal.operation("op_2")
        seq2 = row2["detail"]["eval_spool_seq"]
        assert row2["stage"] == "Succeeded" and row2["outcome_acked"] == 0
        assert _lane(a, "critical")["committed_seq"] >= seq2 and a.journal.pending("critical")[0] == []
        assert row2["outcome"]["evidence"]["eval"]["eval_result_id"] in stub.accepted_eval_ids
        stub.reject_outcomes = False
        stub.cursor["critical"] = seq2 - 1  # restore: the eval record is gone on both sides
        stub.accepted_eval_ids.discard(row2["outcome"]["evidence"]["eval"]["eval_result_id"])
        a2_note = a._server_committed.pop("critical", None)  # a fresh session knows nothing yet
        a.tick()
        posted = stub.outcomes["op_2"][-1]
        assert posted["status"] == "failed" and posted["failure"]["code"] == "EVIDENCE_LOST_ON_RESTORE", (
            posted
        )
        assert posted["result"]["active_release_id"] == "rel_b" and posted["grant_id"] == "grant-op_2"
        assert posted["failure"]["details"]["critical_seq"] == seq2
        assert not any(o.get("_rejected") for o in stub.outcomes["op_2"])  # no success was ever attempted
        assert (
            a.journal.operation("op_2")["outcome"] == posted
            and a.journal.operation("op_2")["outcome_acked"] == 1
        )
        assert a.journal.loss_covers("critical", seq2) == "pre_restore_history_unrecoverable"
        assert a2_note is not None
    finally:
        a.shutdown()


# ----------------------------------------------------------------------------- delta-review residuals
@pytest.mark.timeout(120)
def test_r34fu_ttl_is_rechecked_after_blocking_preparation_before_disruption(stub, tmp_path):
    """A journal transaction that blocks past the grant TTL after the first check must not lead to a
    cutover: the final check sits immediately before the first disruptive effect, nothing is stopped,
    the prior retained recovery pointer is preserved and the durable operation intent stays."""
    stub.add_release("rel_r")
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_r", 1)[1]["status"] == "succeeded"
        assert _deploy(stub, a, "op_2", "rel_a", 2)[1]["status"] == "succeeded"
        assert a.journal.get("recovery_release_id") == "rel_r"
        gen, key = a.sup.generation, a.sup.api_key
        real_set_stage = a.journal.set_stage

        def slow_set_stage(op_id, stage, **kw):
            if (kw.get("detail") or {}).get("disruption_started") is True:
                time.sleep(0.6)  # the transaction blocks well past the 0.2 s TTL
            return real_set_stage(op_id, stage, **kw)

        a.journal.set_stage = slow_set_stage
        stub.grant_ttl_s = 0.2
        _, out = _deploy(stub, a, "op_3", "rel_b", 3)
        assert out["status"] == "failed" and out["failure"]["code"] == "GRANT_EXPIRED", out
        assert out["grant_id"] == "grant-op_3" and out["failure"]["details"]["elapsed_s"] > 0.2
        assert a.sup.generation == gen and a.sup.api_key == key and a.sup.release_id == "rel_a"
        assert a.gw.mode == "production" and a.journal.get("active_release_id") == "rel_a"
        assert a.journal.get("recovery_release_id") == "rel_r"  # prior retained pointer preserved
        row = a.journal.operation("op_3")
        assert row["stage"] == "Failed" and row["detail"]["intent"]["recovery"] == "rel_a"  # intent kept
        assert row["detail"]["disruption_started"] is False and row["detail"]["prior_recovery"] == "rel_r"
        assert row["outcome"] == out
        a.journal.set_stage = real_set_stage
        stub.grant_ttl_s = 30.0
        assert run_op(a, stub.recover_op("op_4", "rel_r", 4))["status"] == "succeeded"
    finally:
        a.shutdown()


@pytest.mark.timeout(60)
def test_r82fu_terminal_event_after_the_absolute_deadline_is_not_a_success(tmp_path):
    sens, sup, gw = _sim_stack(tmp_path)
    spans = []
    gw.on_span = spans.append
    gw.set_mode("production")
    late, calls = _partial_peer("late")
    sim_port = sup.port
    try:
        sup.port = late.server_address[1]
        gw.deadline_s = 1.0
        t0 = time.monotonic()
        code, out = _chat(gw.port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})
        elapsed = time.monotonic() - t0
        assert code == 504 and out["error"]["type"] == "timeout" and gw.stats["served"] == 0, (code, out)
        assert elapsed < 1.4, "each blocking read must be bounded by the remaining budget"
        t = [s for s in spans if s["status"] == "timeout"][-1]
        assert t["attrs"]["tokens_streamed"] == 1 and t["attrs"]["reason"] in (
            "StreamDeadline",
            "TimeoutError",
        )
        assert "/slots" in calls
    finally:
        sup.port = sim_port
        gw.deadline_s = 5
        gw.stop()
        late.shutdown()
        late.server_close()
        sup.stop()


# ----------------------------------------------------------------------------- usage accounting
class _FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _usage_records(stub, **match):
    return [
        r["body"]
        for r in stub.spool
        if r["lane"] == "usage" and all(r["body"].get(k) == v for k, v in match.items())
    ]


@pytest.mark.timeout(120)
def test_usage_restart_before_minute_flush_emits_known_delta_and_unknown_interval(stub, tmp_path):
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    body = {"messages": [{"role": "user", "content": "What is the capital of France?"}], "max_tokens": 4}
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        a.tick()  # drains the deploy-time usage so the lane below only carries this scenario
        base_requests = a.gw.stats["requests"]
        for _ in range(7):
            assert _chat(a.gw.port, body)[0] == 200
        ckpt = a._usage_checkpoint()  # the <=10 s cadence (or 25-request) durable checkpoint
        assert ckpt["cum"]["requests"] - base_requests == 7 and ckpt["cum"]["tokens_in"] > 0
        for _ in range(2):
            assert _chat(a.gw.port, body)[0] == 200  # per-request checkpoints: these are durable too
        assert a.gw.stats["requests"] - base_requests == 9
    finally:
        crash(a)  # unexpected process loss before the minute flush
    a2 = boot(make_agent(tmp_path, stub))
    try:
        rec = a2._usage_recovered
        assert rec and rec["recovered_after_restart"] is True and rec["previous_boot_id"] == a.boot_id
        assert (
            rec["inference_requests"] == 9 and rec["tokens_in"] > 0 and rec["tokens_out"] > 0
        )  # known delta: every COMPLETED request was checkpointed; only in-flight time can be unknown
        assert rec["unknown_coverage_s"] > 0 and rec["unknown_interval"]["from_ts"] >= ckpt["wall"]
        assert rec["schema"] == 2 and 0 < rec["inference_minutes"] * 60 < 5 and rec["runtime_up_minutes"] > 0
        ck = a2.journal.get("usage_checkpoint")  # consumed exactly once: replaced by this process's baseline
        assert (
            ck["boot_id"] == a2.boot_id and ck["cum"]["requests"] == 0.0 and ck["emitted"]["requests"] == 0.0
        )
        a2.tick()
        got = _usage_records(stub, recovered_after_restart=True)
        assert len(got) == 1 and got[0]["inference_requests"] == 9
        assert not any(
            r.get("inference_requests") == 0
            and r.get("unknown_coverage_s", 0) == 0
            and r.get("interval_s", 1) == 0
            for r in _usage_records(stub)
        )
        a2.tick()
        assert len(_usage_records(stub, recovered_after_restart=True)) == 1  # never replayed twice
        a3_ckpt = a2.journal.get("usage_checkpoint")
        assert a3_ckpt and a3_ckpt["boot_id"] == a2.boot_id  # the new process checkpoints its own life
    finally:
        a2.shutdown()


@pytest.mark.timeout(120)
def test_usage_offline_records_accumulate_durably_and_drain_exactly_once(stub, tmp_path):
    a = boot(make_agent(tmp_path, stub))
    clock = _FakeClock()
    a._clock = clock
    a._usage_started = a._usage_sampled_at = a._usage_rt_mark = a._usage_emit_t = a._usage_ckpt_t = clock.t
    try:
        a.tick()  # connected once
        assert a._report_ok_t is not None
        stub.down = True
        emitted_before = len(a.journal.pending("usage")[0])
        for _ in range(3):
            clock.t += 61
            a.tick()  # report fails; usage is still recorded before the network step
        pending = a.journal.pending("usage")[0]
        assert len(pending) == emitted_before + 3
        newest = pending[-1]["body"]
        assert newest["interval_s"] == 61 and newest["agent_up_minutes"] == round(61 / 60, 4)
        assert (
            newest["contact_minutes"] == 0.0 and "online_minutes" not in newest
        )  # offline is not fabricated
        stub.down = False
        clock.t += 5
        a.tick()
        seqs = [r["seq"] for r in stub.spool if r["lane"] == "usage"]
        assert (
            len(seqs) == len(set(seqs)) == emitted_before + 3 + 1
            or len(seqs) == len(set(seqs)) >= emitted_before + 3
        )
        assert a.journal.pending("usage")[0] == []
        n = len([r for r in stub.spool if r["lane"] == "usage"])
        a.flush_spool()
        a.flush_spool()
        assert len([r for r in stub.spool if r["lane"] == "usage"]) == n  # exactly once
        # a fresh connection credits at most the bounded polling interval, never the outage
        clock.t += 61
        a.tick()
        last = a.journal.pending("usage")[0] or [{"body": _usage_records(stub)[-1]}]
        assert last[-1]["body"]["contact_minutes"] * 60 <= 2 * a.poll_s + 5 + 0.01  # 4-decimal rounding
    finally:
        a.shutdown()


@pytest.mark.timeout(60)
def test_usage_runtime_up_is_measured_from_start_stop_instants_and_active_from_inference(stub, tmp_path):
    a = boot(make_agent(tmp_path, stub))
    clock = _FakeClock()
    a._clock = clock
    a.sup.clock = clock
    a._usage_started = a._usage_sampled_at = a._usage_rt_mark = a._usage_emit_t = a._usage_ckpt_t = clock.t
    spec = {"config": {}, "model": {"total_bytes": 1}}
    try:
        clock.t += 59  # t=59: runtime starts
        a.sup.start(
            release_id="r",
            spec=spec,
            model_path=Path("m"),
            template_path=None,
            binary=None,
            lib_dir=None,
            health_timeout_s=5,
        )
        clock.t += 1  # t=60
        cum = a._usage_sample()
        assert abs(cum["runtime_up_s"] - 1.0) < 1e-6 and abs(cum["agent_up_s"] - 60.0) < 1e-6
        a.gw.set_mode("production")
        for _ in range(3):
            assert (
                _chat(a.gw.port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})[0] == 200
            )
        clock.t += 59  # t=119: runtime stops
        a.sup.stop()
        clock.t += 1  # t=120
        cum = a._usage_sample()
        assert abs(cum["runtime_up_s"] - 60.0) < 1e-6  # 1 s + 59 s, not two whole minutes
        assert 0 < cum["inference_s"] < 5  # active = slot-busy time of 3 short requests, not residency
        a._record_usage(force=True)
        rec = a.journal.pending("usage")[0][-1]["body"]
        assert rec["runtime_up_minutes"] == 1.0 and rec["agent_up_minutes"] == 2.0
        assert 0 < rec["inference_minutes"] < rec["runtime_up_minutes"] and rec["inference_requests"] == 3
        assert rec["contact_minutes"] == 0.0 and rec["unknown_coverage_s"] == 0.0
        # exactly-once replay by sequence: a duplicate submission of the same records is ignored upstream
        a.flush_spool()
        before = [r["seq"] for r in stub.spool if r["lane"] == "usage"]
        stub.cursor["usage"] = max(before) if before else 0
        a.client.post(
            "/api/agent/v1/spool",
            {
                "lane": "usage",
                "records": [{"seq": before[-1], "kind": "usage", "body": rec}],
                "loss_ranges": [],
            },
        )
        assert [r["seq"] for r in stub.spool if r["lane"] == "usage"] == before
    finally:
        a.shutdown()


# ----------------------------------------------------------------------------- accounting follow-ups (a)-(f)
@pytest.mark.timeout(120)
def test_usage_restart_on_the_same_kernel_boot_still_recovers_and_never_double_counts(stub, tmp_path):
    """(a) recovery keys on the process incarnation, not the kernel boot id; (b) emission and the consumed
    watermark are one transaction, so a restart right after an emission recovers nothing twice;
    (c) every completed request is durably checkpointed without waiting for a cadence."""
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        a.tick()
        base = a.gw.stats["requests"]
        for _ in range(3):
            assert _chat(a.gw.port, body)[0] == 200
        ck = a.journal.get("usage_checkpoint")  # written by the per-request completion callback
        assert ck["cum"]["requests"] - base == 3 and ck["incarnation"] == a.incarnation
        a._record_usage(force=True)  # record + watermark atomically
        ck = a.journal.get("usage_checkpoint")
        assert ck["emitted"]["requests"] == ck["cum"]["requests"]
        n_before = len(a.journal.pending("usage", limit=500)[0])
        boot_id = a.boot_id
    finally:
        crash(a)
    a2 = make_agent(tmp_path, stub)
    a2.boot_id = boot_id  # same kernel boot: a plain process restart
    boot(a2)
    try:
        rec = a2._usage_recovered
        assert rec is not None and rec["previous_incarnation"] == ck["incarnation"]
        assert (
            rec["inference_requests"] == 0 and rec["tokens_in"] == 0
        )  # everything emitted was already emitted
        assert rec["unknown_coverage_s"] >= 0
        recs = a2.journal.pending("usage", limit=500)[0]
        assert len(recs) == n_before + 1 and sum(r["body"]["inference_requests"] for r in recs) == 3
    finally:
        a2.shutdown()


@pytest.mark.timeout(120)
def test_usage_inflight_request_at_crash_is_unknown_from_its_start(stub, tmp_path):
    """(c) a checkpoint taken while a request holds the slot records the open interval; after a crash
    the unknown coverage starts at that request's start, not at the checkpoint."""
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        a.tick()
        t_req = time.time() - 30.0
        a.gw.slot_busy_since_wall = t_req  # a request is in flight (its slot interval not accounted yet)
        ck = a._usage_checkpoint()
        assert ck["open_interval_from_wall"] == t_req
    finally:
        a.gw.slot_busy_since_wall = None
        crash(a)
    a2 = boot(make_agent(tmp_path, stub))
    try:
        rec = a2._usage_recovered
        assert rec and rec["unknown_interval"]["from_ts"] == t_req and rec["unknown_coverage_s"] >= 30.0
    finally:
        a2.shutdown()


@pytest.mark.timeout(60)
def test_usage_contact_gap_is_not_connected_time(stub, tmp_path):
    """(e) contacts after a gap longer than the cadence are reconnects: nothing is credited."""
    a = boot(make_agent(tmp_path, stub))
    clock = _FakeClock()
    a._clock = clock
    try:
        a._report_ok_t = None
        a._mark_connected()  # first contact
        clock.t += 15
        a._mark_connected()  # contiguous
        clock.t += 600
        a._mark_connected()  # after an outage
        cum = a._usage_cum
        assert abs(cum["connected_s"] - 15.0) < 1e-6 and cum["contacts"] == 3 and cum["reconnects"] == 1
        a._usage_emit_t = clock.t - 61
        a._record_usage(force=True)
        rec = a.journal.pending("usage")[0][-1]["body"]
        assert rec["contact_minutes"] == 0.25 and rec["contacts"] == 3 and rec["reconnects"] == 1
        assert "online_minutes" not in rec and "active_minutes" not in rec and rec["schema"] == 2
    finally:
        a.shutdown()


@pytest.mark.timeout(60)
def test_usage_runtime_transitions_between_samples_are_all_counted(stub, tmp_path):
    """(f) start 0-4 and 6-10 between two samples at 0 and 12 credit 8 s, not the latest 4 s."""
    a = boot(make_agent(tmp_path, stub))
    clock = _FakeClock()
    a._clock = clock
    a.sup.clock = clock
    a._usage_started = a._usage_sampled_at = a._usage_rt_mark = a._usage_emit_t = a._usage_ckpt_t = clock.t
    spec = {"config": {}, "model": {"total_bytes": 1}}
    try:

        def start():
            a.sup.start(
                release_id="r",
                spec=spec,
                model_path=Path("m"),
                template_path=None,
                binary=None,
                lib_dir=None,
                health_timeout_s=5,
            )

        start()
        clock.t += 4
        a.sup.stop()
        clock.t += 2
        start()
        clock.t += 4
        a.sup.stop()
        clock.t += 2
        cum = a._usage_sample()
        assert abs(cum["runtime_up_s"] - 8.0) < 1e-6, cum["runtime_up_s"]
        cum = a._usage_sample()
        assert abs(cum["runtime_up_s"] - 8.0) < 1e-6  # nothing double counted on a later sample
    finally:
        a.shutdown()


# ----------------------------------------------------------------------------- producer boundaries (6efd514 review)
@pytest.mark.timeout(120)
def test_usage_recovery_is_one_atomic_transition_and_survives_a_failed_transaction(stub, tmp_path):
    """(1) The recovery record and the new process's baseline checkpoint are ONE journal transaction:
    no path emits then clears (a crash between the two would recover the same delta twice). A failed
    transaction leaves the previous checkpoint untouched and is retried before any later checkpoint
    could overwrite it; the delta is emitted exactly once across three incarnations."""
    from convoy_agent.journal import StorageError

    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        a.tick()
        base = a.gw.stats["requests"]
        for _ in range(7):
            assert _chat(a.gw.port, body)[0] == 200
        assert a.journal.get("usage_checkpoint")["cum"]["requests"] - base == 7
        n_before = len(a.journal.pending("usage", limit=500)[0])
    finally:
        crash(a)

    a2 = make_agent(tmp_path, stub)
    # no separate emit/set of the usage lane/checkpoint may take part in recovery: only the single
    # append_and_set transaction
    orig_emit, orig_set = a2.emit, a2.journal.set

    def guarded_emit(lane, kind, body):
        assert lane != "usage", "recovery must not emit separately"
        return orig_emit(lane, kind, body)

    def guarded_set(key, value, conn=None):
        assert key != "usage_checkpoint", "recovery must not set the checkpoint separately"
        return orig_set(key, value, conn)

    a2.emit, a2.journal.set = guarded_emit, guarded_set
    orig_aas = a2.journal.append_and_set
    faults = {"n": 1}

    def failing_aas(*x, **k):
        if faults["n"]:
            faults["n"] -= 1
            raise StorageError("commit failed (disk)")  # the transaction is rolled back: nothing landed
        return orig_aas(*x, **k)

    a2.journal.append_and_set = failing_aas
    boot(a2)  # boot survives; the previous checkpoint is still there, untouched
    try:
        assert a2._usage_recovered is None and a2._usage_recovery_pending
        ck = a2.journal.get("usage_checkpoint")
        assert ck["incarnation"] == a.incarnation and ck["cum"]["requests"] - base == 7
        assert len(a2.journal.pending("usage", limit=500)[0]) == n_before
        # a checkpoint attempt while pending retries the recovery first and never overwrites it
        a2.journal.append_and_set = lambda *x, **k: (_ for _ in ()).throw(StorageError("still failing"))
        with pytest.raises(StorageError):
            a2._usage_checkpoint()
        assert a2.journal.get("usage_checkpoint")["incarnation"] == a.incarnation
        a2.journal.append_and_set = orig_aas
        a2._usage_checkpoint()  # storage back: recovery lands atomically with a2's baseline
        recs = a2.journal.pending("usage", limit=500)[0]
        assert len(recs) == n_before + 1 and recs[-1]["body"]["inference_requests"] == 7
        assert recs[-1]["body"]["unknown_interval"]["reason"] == "restart_before_durable_checkpoint"
        ck = a2.journal.get("usage_checkpoint")
        assert ck["incarnation"] == a2.incarnation and not a2._usage_recovery_pending
        a2.journal.set = orig_set
    finally:
        crash(a2)

    a3 = boot(make_agent(tmp_path, stub))
    try:
        recs = a3.journal.pending("usage", limit=500)[0]
        assert sum(r["body"]["inference_requests"] for r in recs) == 7  # never 14
        assert a3._usage_recovered["inference_requests"] == 0
    finally:
        a3.shutdown()


@pytest.mark.timeout(120)
def test_usage_concurrent_snapshots_never_regress_the_durable_checkpoint(stub, tmp_path):
    """(2) Sampling, the checkpoint decision and the write are serialised under one lock: a newer
    completion that samples while an older snapshot is being written waits, so the durable checkpoint
    sequence is monotonic (before the fix the older snapshot landed last and a restart recovered it)."""
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        a.tick()
        for _ in range(7):
            assert _chat(a.gw.port, body)[0] == 200
        base = a.gw.stats["requests"] - 7
        written: list[int] = []
        orig_set = a.journal.set

        def recording_set(key, value, conn=None):
            if key == "usage_checkpoint":
                written.append(int(value["cum"]["requests"]) - base)
            return orig_set(key, value, conn)

        a.journal.set = recording_set
        orig_sample = a._usage_sample
        state: dict = {"armed": True}

        def sample_then_race():
            cum = orig_sample()
            if state["armed"]:
                state["armed"] = False
                a.gw.stats["requests"] += 1  # an 8th request completes on another thread right now...
                t = threading.Thread(target=a._usage_checkpoint, daemon=True)
                t.start()
                t.join(0.5)
                state["newer_waited"] = t.is_alive()  # ...and its checkpoint must wait for this one
                state["t"] = t
            return cum

        a._usage_sample = sample_then_race
        a._usage_checkpoint()  # the older snapshot (7)
        state["t"].join(10)
        assert state["newer_waited"] is True
        assert written == [7, 8], written
        assert a.journal.get("usage_checkpoint")["cum"]["requests"] - base == 8
        a._usage_sample = orig_sample
        a.journal.set = orig_set
    finally:
        crash(a)
    a2 = boot(make_agent(tmp_path, stub))
    try:
        assert a2._usage_recovered["inference_requests"] == 8
    finally:
        a2.shutdown()


@pytest.mark.timeout(120)
def test_every_answered_request_reaches_the_durable_checkpoint(stub, tmp_path):
    """(3) Validation, admission, overload and HTTP-layer rejections are counted as requests by usage,
    so each of them fires the completion callback exactly once and lands in the durable checkpoint."""
    import http.client

    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        a.tick()

        def durable():
            return int(a.journal.get("usage_checkpoint")["cum"]["requests"])

        start = a.gw.stats["requests"]
        assert durable() == start
        assert a.gw.handle({"messages": []}, None, "t1")[0] == 400
        assert a.gw.stats["requests"] == start + 1 and durable() == start + 1
        assert a.gw.handle("not an object", None, "t2")[0] == 400  # type: ignore[arg-type]
        assert durable() == start + 2
        assert (
            a.gw.handle({"messages": [{"role": "user", "content": "x"}], "max_tokens": 0}, None, "t3")[0]
            == 400
        )
        assert durable() == start + 3
        mode = a.gw.mode
        a.gw.mode = "closed"
        try:
            assert (
                a.gw.handle({"messages": [{"role": "user", "content": "x"}], "max_tokens": 4}, None, "t4")[0]
                == 503
            )
        finally:
            a.gw.mode = mode
        assert durable() == start + 4
        c = http.client.HTTPConnection("127.0.0.1", a.gw.port, timeout=10)
        c.request(
            "POST", "/v1/chat/completions", body=b"{not json", headers={"Content-Type": "application/json"}
        )
        assert c.getresponse().status == 400  # HTTP-layer rejection (gateway._reject)
        c.close()
        assert a.gw.stats["requests"] == start + 5 and durable() == start + 5
        assert _chat(a.gw.port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})[0] == 200
        assert durable() == start + 6 and a.gw.stats["requests"] == start + 6
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_usage_checkpoint_persist_failure_is_accounted_not_swallowed(stub, tmp_path, caplog):
    """(4) When the per-request checkpoint cannot be persisted the gateway keeps answering, but the
    lapse is counted, logged and carried by the next durable checkpoint and usage record; after a crash
    the requests answered in the lapse fall inside the declared unknown interval, never into the known
    delta. A broken journal stops the agent for restart recovery."""
    import logging

    from convoy_agent.journal import StorageError

    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        a.tick()
        assert _chat(a.gw.port, body)[0] == 200
        durable = a.journal.get("usage_checkpoint")
        base = int(durable["cum"]["requests"])
        orig_set = a.journal.set

        def failing_set(key, value, conn=None):
            if key == "usage_checkpoint":
                raise StorageError("disk full")
            return orig_set(key, value, conn)

        a.journal.set = failing_set
        with caplog.at_level(logging.ERROR, logger="convoy.agent"):
            assert _chat(a.gw.port, body)[0] == 200  # still served
            assert _chat(a.gw.port, body)[0] == 200
        assert a._usage_ckpt_failures == 2 and a._usage_ckpt_failed_since_wall is not None
        assert "not durable" in caplog.text and "not guaranteed durable" in caplog.text
        assert a.journal.get("usage_checkpoint") == durable  # the last durable checkpoint is untouched
        # storage back: the next durable checkpoint carries the lapse and resets the streak
        a.journal.set = orig_set
        ck = a._usage_checkpoint()
        assert ck["checkpoint_failures"] == 2 and ck["undurable_since_wall"] is not None
        assert ck["cum"]["requests"] - base == 2 and a._usage_ckpt_failures == 0
        a._record_usage(force=True)
        assert a.journal.pending("usage", limit=500)[0][-1]["body"]["inference_requests"] >= 2
        # lapse again, then crash: the unpersisted request is inside the unknown interval, not the delta
        a.journal.set = failing_set
        assert _chat(a.gw.port, body)[0] == 200
        wall_last_durable = a.journal.get("usage_checkpoint")["wall"]
        a.journal.set = orig_set
    finally:
        crash(a)
    a2 = boot(make_agent(tmp_path, stub))
    try:
        rec = a2._usage_recovered
        assert rec["inference_requests"] == 0  # the lapsed request is NOT claimed as known
        assert rec["unknown_interval"]["from_ts"] <= wall_last_durable and rec["unknown_coverage_s"] > 0
        # a broken journal stops the agent for restart recovery instead of serving without durability
        a2.journal.broken = "rollback failed: simulated"
        assert _chat(a2.gw.port, body)[0] == 200
        assert a2.stop.is_set()
    finally:
        a2.journal.broken = None
        a2.shutdown()


@pytest.mark.timeout(120)
def test_usage_recovery_stays_unresolved_across_a_transient_first_read(stub, tmp_path):
    """(8b59f5c P1) Recovery is UNRESOLVED from construction until a successful decision or the durable
    transition. A transient failure of the very first checkpoint read at boot must not let an ordinary
    checkpoint overwrite the previous incarnation's 7 un-emitted requests: the next checkpoint performs
    the recovery first and the 7 are emitted exactly once."""
    from convoy_agent.journal import StorageError

    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        a.tick()
        base = a.gw.stats["requests"]
        for _ in range(7):
            assert _chat(a.gw.port, body)[0] == 200
        assert a.journal.get("usage_checkpoint")["cum"]["requests"] - base == 7
        n_before = len(a.journal.pending("usage", limit=500)[0])
    finally:
        crash(a)
    a2 = make_agent(tmp_path, stub)
    assert a2._usage_recovery_pending  # unresolved before anything is read
    orig_get = a2.journal.get
    faults = {"n": 1}

    def flaky_get(key, default=None):
        if key == "usage_checkpoint" and faults["n"]:
            faults["n"] -= 1
            raise StorageError("transient read failure")
        return orig_get(key, default)

    a2.journal.get = flaky_get
    boot(a2)  # start_local survives the read failure and writes NO baseline
    try:
        assert a2._usage_recovered is None and a2._usage_recovery_pending
        assert orig_get("usage_checkpoint")["incarnation"] == a.incarnation  # untouched
        a2._usage_checkpoint()  # an ORDINARY checkpoint: recovery runs first, then the baseline
        recs = a2.journal.pending("usage", limit=500)[0]
        assert len(recs) == n_before + 1 and recs[-1]["body"]["inference_requests"] == 7
        assert orig_get("usage_checkpoint")["incarnation"] == a2.incarnation
        assert not a2._usage_recovery_pending
        a2._usage_checkpoint()  # no second recovery
        assert len(a2.journal.pending("usage", limit=500)[0]) == n_before + 1
    finally:
        crash(a2)
    a3 = boot(make_agent(tmp_path, stub))
    try:
        assert sum(r["body"]["inference_requests"] for r in a3.journal.pending("usage", limit=500)[0]) == 7
    finally:
        a3.shutdown()


@pytest.mark.timeout(120)
def test_usage_checkpoint_failure_evidence_survives_until_emitted(stub, tmp_path):
    """(8b59f5c P2) Persist-failure evidence is diagnostic, not a counter loss: it survives later
    successful checkpoints (which only end the retry streak) and is consumed solely by the atomic
    emission that reports it, or by the recovery record after a crash."""
    from convoy_agent.journal import StorageError

    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}
    try:
        assert _deploy(stub, a, "op_1", "rel_a", 1)[1]["status"] == "succeeded"
        a.tick()
        a._record_usage(force=True)  # clean slate: nothing unreported
        orig_set = a.journal.set

        def failing_set(key, value, conn=None):
            if key == "usage_checkpoint":
                raise StorageError("disk full")
            return orig_set(key, value, conn)

        a.journal.set = failing_set
        assert _chat(a.gw.port, body)[0] == 200 and _chat(a.gw.port, body)[0] == 200
        a.journal.set = orig_set
        ck = a._usage_checkpoint()
        assert ck["checkpoint_failures"] == 2 and ck["unreported_checkpoint_failures"] == 2
        anchor = ck["unreported_since_wall"]
        assert anchor is not None and ck["undurable_since_wall"] == anchor
        ck = a._usage_checkpoint()  # an intermediate durable checkpoint ends the streak, keeps the evidence
        assert ck["checkpoint_failures"] == 0 and ck["undurable_since_wall"] is None
        assert ck["unreported_checkpoint_failures"] == 2 and ck["unreported_since_wall"] == anchor
        a._record_usage(force=True)
        rec = a.journal.pending("usage", limit=500)[0][-1]["body"]
        assert rec["checkpoint_failures"] == 2 and rec["checkpoint_failures_since_wall"] == anchor
        assert rec["inference_requests"] == 2  # the requests themselves were never lost
        assert a.journal.get("usage_checkpoint")["unreported_checkpoint_failures"] == 0
        a._record_usage(force=True)
        assert "checkpoint_failures" not in a.journal.pending("usage", limit=500)[0][-1]["body"]
        # crash variant: evidence still unreported at the crash reaches the recovery record
        a.journal.set = failing_set
        assert _chat(a.gw.port, body)[0] == 200
        a.journal.set = orig_set
        a._usage_checkpoint()
        assert a.journal.get("usage_checkpoint")["unreported_checkpoint_failures"] == 1
    finally:
        crash(a)
    a2 = boot(make_agent(tmp_path, stub))
    try:
        rec = a2._usage_recovered
        assert rec["previous_checkpoint_failures"] == 1 and rec["previous_undurable_since_wall"] is not None
        assert a2.journal.get("usage_checkpoint")["unreported_checkpoint_failures"] == 0
    finally:
        a2.shutdown()


@pytest.mark.timeout(120)
def test_legacy_checkpoint_recovery_keeps_provenance(stub, tmp_path):
    """Upgrade compatibility: a pre-rework usage checkpoint (no `schema`, no `incarnation`; durations
    sampled with the old semantics) is recovered exactly once with its integer deltas, its residual
    durations declared as MIXED with legacy provenance (never promoted to measured populations), and
    explicit unknown coverage from its wall time; the replacement checkpoint carries the version."""
    stub.add_release("rel_a")
    a = make_agent(tmp_path, stub)
    old = {  # the reviewer's probe: the exact 589b737 checkpoint shape
        "boot_id": "boot-legacy",
        "wall": time.time() - 40.0,
        "monotonic": 12345.0,
        "cum": {
            "requests": 12,
            "tokens_in": 120,
            "tokens_out": 24,
            "inference_s": 60.0,
            "runtime_up_s": 120.0,
            "agent_up_s": 180.0,
            "connected_s": 35.0,
        },
        "emitted": {"requests": 5, "tokens_in": 50, "tokens_out": 10},
        "requests": 12,
    }
    a.journal.set("usage_checkpoint", old)
    boot(a)
    try:
        rec = a._usage_recovered
        assert rec["schema"] == 2 and rec["legacy_checkpoint"] is True and rec["recovered_after_restart"]
        assert (rec["inference_requests"], rec["tokens_in"], rec["tokens_out"]) == (7, 70, 14)
        assert rec["mixed_active_minutes"] == 1.0 and rec["mixed_online_minutes"] == 0.5833
        for measured in ("inference_minutes", "contact_minutes", "runtime_up_minutes", "agent_up_minutes"):
            assert measured not in rec
        assert rec["unknown_coverage_s"] >= 40.0 and rec["unknown_interval"]["from_ts"] == old["wall"]
        assert rec["previous_boot_id"] == "boot-legacy" and rec["previous_incarnation"] is None
        ck = a.journal.get("usage_checkpoint")
        assert ck["schema"] == 2 and ck["incarnation"] == a.incarnation
        journaled = [
            r["body"] for r in a.journal.pending("usage", limit=500)[0] if r["body"].get("legacy_checkpoint")
        ]
        assert len(journaled) == 1 and journaled[0]["inference_requests"] == 7
    finally:
        crash(a)
    a2 = boot(make_agent(tmp_path, stub))
    try:
        assert (
            a2._usage_recovered["inference_requests"] == 0 and "legacy_checkpoint" not in a2._usage_recovered
        )
        recs = a2.journal.pending("usage", limit=500)[0]
        assert sum(r["body"]["inference_requests"] for r in recs) == 7  # exactly once
    finally:
        a2.shutdown()
