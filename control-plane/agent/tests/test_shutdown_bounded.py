"""Bounded shutdown (physical failure 2026-09-13: two ~45 s stops under a stale HTTPS reverse tunnel,
one ending in systemd's SIGKILL). The agent now stops under ONE monotonic budget: in-flight
control-plane I/O is aborted, no new attempt or dispatch starts after the stop, the cancelled operation
thread is joined within its share, the owned child is stopped and verified, gateway handlers still
running finish before the journal closes, the final usage record lands atomically, nothing is
uploaded (spool bytes and frontiers stay durable for the next start's replay), and ownership is
released only when nothing can write any more."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from convoy_agent.journal import LANES, Journal
from lifecycle_stub import Stub, boot, make_agent, wait_for

AGENT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def stub():
    s = Stub()
    yield s
    s.stop()


# ----------------------------------------------------------------------------- fixtures


class StalledListener:
    """TCP accepted, TLS handshake never answered: what a stale SSH reverse forward looks like to the
    agent (the tunnel end accepts the connection, the far side is gone)."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.accepted: list[float] = []
        self.conns: list[socket.socket] = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                c, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.accepted.append(time.monotonic())
            self.conns.append(c)  # held open, never read, never answered

    def close(self) -> None:
        self._stop.set()
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass
        self.sock.close()
        self.thread.join(timeout=2)


def _self_signed(tmp: Path) -> tuple[Path, Path] | None:
    """A throwaway certificate for 127.0.0.1 via the system openssl (None when unavailable)."""
    if shutil.which("openssl") is None:
        return None
    key, cert = tmp / "key.pem", tmp / "cert.pem"
    r = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
            "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        capture_output=True,
    )  # fmt: skip
    return (cert, key) if r.returncode == 0 else None


class TLSStallServer:
    """A real TLS server that completes the handshake, reads the request and then never answers: the
    post-handshake stall (the far side of the tunnel accepted the request and went silent)."""

    def __init__(self, cert: Path, key: Path, *, partial_body: bool = False) -> None:
        self.partial_body = partial_body  # answer 200 + Content-Length: 100 + one byte, then stall
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(str(cert), str(key))
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.handshakes: list[float] = []
        self.requests: list[bytes] = []
        self.conns: list[socket.socket] = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _serve(self, c: socket.socket) -> None:
        try:
            c.settimeout(10)
            tls = self.ctx.wrap_socket(c, server_side=True)
            self.handshakes.append(time.monotonic())
            self.conns.append(tls)
            self.requests.append(tls.recv(65536))
            if self.partial_body:
                tls.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{"
                )
            self._stop.wait(120)  # never (fully) answer
        except (OSError, ssl.SSLError):
            pass

    def _loop(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                c, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass
        self.sock.close()
        self.thread.join(timeout=2)


def enrolled_dir(tmp: Path, server: str, *, ca_file: str | None = None, name: str = "dev") -> Path:
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = {
        "device_id": "dev_1",
        "server": server,
        "name": name,
        "simulate": True,
        "seed": 1,
        "robot_sim": False,
    }
    if ca_file:
        cfg["ca_file"] = ca_file
    (d / "agent.json").write_text(json.dumps(cfg))
    fd = os.open(str(d / "credential"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("secret")
    return d


def run_agent(d: Path) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(AGENT_ROOT), "PYTHONUNBUFFERED": "1"}
    cmd = [sys.executable, "-m", "convoy_agent.cli", "--data-dir", str(d), "run", "--no-robot-sim"]
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def sigterm_and_wait(proc: subprocess.Popen, timeout: float = 40.0) -> tuple[int, float, str]:
    t0 = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise AssertionError(f"agent did not exit on SIGTERM within {timeout:.0f} s") from None
    took = time.monotonic() - t0
    out = proc.stdout.read().decode(errors="replace")
    summary = [ln for ln in out.splitlines() if "shutdown complete" in ln or "shutdown INCOMPLETE" in ln]
    print(
        f"[measured] SIGTERM -> exit {took:.3f} s, rc={rc}; {summary[-1][-400:] if summary else 'no summary line'}"
    )
    return rc, took, out


def _chat(port: int, body: dict, timeout: float = 15) -> tuple[int, object]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


def _start_sim_runtime(a) -> None:
    a.sup.start(
        release_id="r",
        spec={"config": {}, "model": {"total_bytes": 1}},
        model_path=Path("m"),
        template_path=None,
        binary=None,
        lib_dir=None,
        health_timeout_s=5,
    )
    a.gw.set_mode("production")


def _run_in_thread(a) -> threading.Thread:
    t = threading.Thread(target=a.run, name="agent-run", daemon=True)
    t.start()
    return t


# ----------------------------------------------------------------------------- real process, SIGTERM


@pytest.mark.timeout(120)
def test_sigterm_during_a_stalled_tls_handshake_exits_promptly_without_a_kill(tmp_path):
    """The dead-tunnel idle case: the report attempt is blocked in the TLS handshake on the main thread
    when SIGTERM arrives. Target: exit 0 in <= 10 s, no new connection attempt after the stop, and the
    data directory lock released for the next incarnation."""
    lst = StalledListener()
    try:
        d = enrolled_dir(tmp_path, f"https://127.0.0.1:{lst.port}")
        proc = run_agent(d)
        try:
            wait_for(lambda: len(lst.accepted) >= 1, 30)  # the first report attempt is in its handshake
            time.sleep(0.5)
            n_before = len(lst.accepted)
            rc, took, out = sigterm_and_wait(proc)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        assert rc == 0, out[-3000:]
        assert took <= 10.0, (took, out[-3000:])
        time.sleep(1.0)
        assert len(lst.accepted) == n_before, "a new control-plane attempt was made after the stop"
        assert "'aborted_requests': 1" in out, out[-3000:]  # the blocked handshake was woken by the stop
        assert "shutdown complete" in out, out[-3000:]
        # the lock is free and the journal is usable for the next incarnation
        probe = make_agent(tmp_path, _StubBase(f"https://127.0.0.1:{lst.port}"))  # type: ignore[arg-type]
        probe.acquire_lock()
        probe.journal.get("report_seq")
        probe.shutdown()
    finally:
        lst.close()


class _StubBase:
    """Minimal stand-in so make_agent() can reuse an existing enrolled directory (only .base is read)."""

    def __init__(self, base: str) -> None:
        self.base = base


@pytest.mark.timeout(120)
@pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl binary needed for a throwaway certificate"
)
def test_sigterm_during_a_stalled_response_after_the_tls_handshake(tmp_path):
    """Post-handshake stall: the request was sent over a verified TLS session and the response never
    comes. Certificate and hostname verification stay on (the throwaway CA is passed as --ca-file)."""
    pem = _self_signed(tmp_path)
    if pem is None:
        pytest.skip("openssl could not produce a certificate")
    cert, key = pem
    srv = TLSStallServer(cert, key)
    try:
        d = enrolled_dir(tmp_path, f"https://127.0.0.1:{srv.port}", ca_file=str(cert))
        proc = run_agent(d)
        try:
            wait_for(lambda: len(srv.requests) >= 1, 30)  # handshake done, request received, no answer
            time.sleep(0.5)
            n_before = len(srv.handshakes)
            rc, took, out = sigterm_and_wait(proc)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        assert rc == 0, out[-3000:]
        assert took <= 10.0, (took, out[-3000:])
        assert b"POST /api/agent/v1/report" in srv.requests[0]
        time.sleep(1.0)
        assert len(srv.handshakes) == n_before
        assert "shutdown complete" in out, out[-3000:]
    finally:
        srv.close()


# ----------------------------------------------------------------------------- stop-aware retries


class _WaitLog(threading.Event):
    def __init__(self) -> None:
        super().__init__()
        self.waits: list[tuple[str, float]] = []

    def wait(self, timeout=None):  # noqa: D401
        self.waits.append((threading.current_thread().name, timeout if timeout is not None else -1.0))
        return super().wait(timeout)


@pytest.mark.timeout(60)
def test_stop_during_retry_backoff_returns_promptly_and_makes_no_further_attempt(stub, tmp_path):
    stub.down = True  # every report answers 503: Transient -> retry backoff
    a = make_agent(tmp_path, stub)
    ev = _WaitLog()
    a.stop = ev
    a.client.stop = ev
    a.poll_s = 60
    t = _run_in_thread(a)
    try:
        # the first wait on the run thread is the client's backoff (the run loop's poll wait comes later)
        wait_for(lambda: any(n == "agent-run" and 0 < to < 30 for n, to in ev.waits), 20)
        attempts_at_stop = a.client.attempts
        t0 = time.monotonic()
        a.request_stop()
        t.join(timeout=15)
        took = time.monotonic() - t0
    finally:
        if t.is_alive():
            a.request_stop()
            t.join(timeout=15)
    assert not t.is_alive()
    assert took < 3.0, took
    assert a.client.attempts == attempts_at_stop, "a new attempt was started after the stop"
    s = a.last_shutdown
    assert s and s["complete"] and s["released"], s
    assert a.journal.closed and a.lock_fd is None


# ----------------------------------------------------------------------------- spool durability


def _seed_lanes(a, n: int = 2) -> dict[str, list[int]]:
    seqs: dict[str, list[int]] = {}
    for lane in LANES:
        seqs[lane] = [a.emit(lane, "note", {"ts": time.time(), "lane": lane, "i": i}) for i in range(n)]
    return seqs


def _pending_snapshot(j: Journal) -> dict[str, list[tuple[int, str, str]]]:
    return {
        lane: [
            (int(r["seq"]), str(r.get("kind")), json.dumps(r["body"], sort_keys=True))
            for r in j.pending(lane)[0]
        ]
        for lane in LANES
    }


@pytest.mark.timeout(90)
@pytest.mark.parametrize("lost_ack", [False, True])
def test_shutdown_uploads_nothing_and_the_next_start_replays_each_record_once(stub, tmp_path, lost_ack):
    a = make_agent(tmp_path, stub)
    a.poll_s = 60
    t = _run_in_thread(a)
    try:
        wait_for(lambda: stub.posts("/report"), 20)
        if lost_ack:
            stub.drop_spool_response = True  # the server commits the batch, the ACK never arrives
        else:
            stub.down = True  # every spool post is refused with 503
        seqs = _seed_lanes(a)
        a.flush_spool()  # one ordinary attempt while running: refused or unacknowledged, all stays pending
        # the tick thread's own flush is serialised on the same lock: hold it while counting and
        # stopping so the count covers exactly the shutdown, not a flush already in progress
        with a._flush_lock:
            before = _pending_snapshot(a.journal)
            # the seeded records (and the agent's own telemetry rows) are all still pending
            assert all(set(seqs[lane]) <= {q for q, _, _ in before[lane]} for lane in LANES), before
            frontier_before = {lane: a.journal.lane_status()[lane]["committed_seq"] for lane in LANES}
            calls_before = len(stub.spool_calls)
            a.request_stop()
        t.join(timeout=20)
        assert not t.is_alive()
    finally:
        if t.is_alive():
            t.join(timeout=20)
    s = a.last_shutdown
    assert s and s["complete"], s
    assert len(stub.spool_calls) == calls_before, "shutdown must not upload the spool"
    assert all(s["spool_pending"][lane] >= 2 for lane in LANES), s["spool_pending"]
    # bytes, sequences and frontiers survive the stop unchanged
    j = Journal(a.data_dir / "journal.db")
    try:
        after = _pending_snapshot(j)
        for lane in LANES:
            # every record that was pending is still there, same sequence, same kind, same bytes ...
            assert after[lane][: len(before[lane])] == before[lane], lane
            # ... and the only additions are the shutdown's own final usage record (local accounting)
            extra = after[lane][len(before[lane]) :]
            assert all(lane == "usage" and kind == "usage" for _, kind, _ in extra), (lane, extra)
        assert {lane: j.lane_status()[lane]["committed_seq"] for lane in LANES} == frontier_before
    finally:
        j.close()
    # next start: ordinary authenticated replay commits every record exactly once
    stub.down = False
    stub.drop_spool_response = False
    b = make_agent(tmp_path, stub)
    b.poll_s = 60
    t2 = _run_in_thread(b)
    try:
        wait_for(lambda: all(not b.journal.pending(lane)[0] for lane in LANES), 30)
    finally:
        b.request_stop()
        t2.join(timeout=20)
    for lane in LANES:
        got = [r for r in stub.spool if r["lane"] == lane and r.get("kind") == "note"]
        assert sorted(int(r["seq"]) for r in got) == sorted(seqs[lane]), (lane, got)
        assert len({int(r["seq"]) for r in got}) == len(got)  # no duplicate commit
        assert stub.cursor[lane] >= max(seqs[lane])


# ----------------------------------------------------------------------------- usage accounting


def _usage_records(j: Journal) -> list[dict]:
    return [r["body"] for r in j.pending("usage")[0] if r.get("kind") == "usage"]


@pytest.mark.timeout(90)
def test_final_usage_record_is_atomic_and_a_forced_stop_leaves_an_honest_unknown_interval(stub, tmp_path):
    a = boot(make_agent(tmp_path, stub))
    _start_sim_runtime(a)
    for _ in range(3):
        assert _chat(a.gw.port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})[0] == 200
    s = a.shutdown()
    assert s["complete"] and s["final_usage_record"] and s["runtime_stop"]["stopped"], s
    j = Journal(a.data_dir / "journal.db")
    try:
        recs = _usage_records(j)
        final = recs[-1]
        assert final["inference_requests"] == 3 and isinstance(final["inference_requests"], int)
        assert final["unknown_coverage_s"] == 0.0
        ckpt = j.get("usage_checkpoint")
        # record and consumed watermark landed together: the checkpoint already shows the emission
        assert ckpt["emitted"]["requests"] == ckpt["cum"]["requests"] == 3.0
        assert ckpt["incarnation"] == a.incarnation
    finally:
        j.close()
    # clean restart: nothing is recovered twice, the downtime is declared unknown, never measured zero
    b = boot(make_agent(tmp_path, stub))
    try:
        rec = b._usage_recovered
        assert rec is not None and rec["inference_requests"] == 0 and rec["recovered_after_restart"]
        assert (
            rec["unknown_coverage_s"] > 0
            and rec["unknown_interval"]["reason"] == "restart_before_durable_checkpoint"
        )
        _start_sim_runtime(b)
        for _ in range(2):
            assert (
                _chat(b.gw.port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})[0] == 200
            )
        wait_for(lambda: (b.journal.get("usage_checkpoint") or {}).get("requests") == 2, 10)
    finally:
        # forced termination (SIGKILL-like): no shutdown, no final record
        from lifecycle_stub import crash

        crash(b)
    c = boot(make_agent(tmp_path, stub))
    try:
        rec = c._usage_recovered
        assert rec is not None and rec["inference_requests"] == 2  # the per-request checkpoint's delta
        assert rec["unknown_coverage_s"] > 0.0  # from the last durable checkpoint to now: unknown, not 0
    finally:
        c.shutdown()


# ----------------------------------------------------------------------------- worker + writers


class _CloseObserver:
    """Records what was still alive when the journal was closed."""

    def __init__(self, a) -> None:
        self.a = a
        self.at_close: dict | None = None
        self._orig = a.journal.close

    def install(self) -> None:
        def close():
            w = self.a.worker
            self.at_close = {
                "worker_alive": bool(w and w.is_alive()),
                "gateway_inflight": self.a.gw.inflight,
                "runtime_state": self.a.sup.state(),
            }
            self._orig()

        self.a.journal.close = close


@pytest.mark.timeout(120)
def test_operation_thread_is_cancelled_within_its_share_and_ownership_released_after_it(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_plan("plan_a", "rel_a", sample_policy={"probation_min_s": 120, "probation_min_requests": 0})
    a = make_agent(tmp_path, stub)
    a.poll_s = 1
    obs = _CloseObserver(a)
    obs.install()
    t = _run_in_thread(a)
    try:
        wait_for(lambda: stub.posts("/report"), 20)
        stub.deploy_op("op_1", "rel_a", 1, plan_id="plan_a")
        wait_for(
            lambda: any(
                (b.get("progress") or {}).get("stage") == "probation" for _, b in stub.posts("/outcome")
            ),
            60,
        )
        assert a.worker is not None and a.worker.is_alive()
        t0 = time.monotonic()
        a.request_stop()
        t.join(timeout=40)
        took = time.monotonic() - t0
    finally:
        if t.is_alive():
            t.join(timeout=40)
    assert not t.is_alive()
    s = a.last_shutdown
    assert s and s["complete"] and s["released"], s
    assert took < a.shutdown_worker_s, took
    assert s["worker_s"] < a.shutdown_worker_s
    assert obs.at_close == {"worker_alive": False, "gateway_inflight": 0, "runtime_state": "stopped"}, (
        obs.at_close
    )
    j = Journal(a.data_dir / "journal.db")
    try:
        row = j.operation("op_1")
        assert row and row["terminal"] and row["outcome"]["failure"]["code"] == "INTERRUPTED"
        assert [o["id"] for o in j.unacked_terminal()] == ["op_1"]  # replayed by the next start, not now
    finally:
        j.close()
    assert not any(o.get("status") == "failed" for o in stub.outcomes.get("op_1", []))


@pytest.mark.timeout(60)
def test_inflight_gateway_request_completes_and_checkpoints_before_the_journal_closes(stub, tmp_path, caplog):
    a = boot(make_agent(tmp_path, stub, sim_faults={"latency_ms": 4000}))
    _start_sim_runtime(a)
    obs = _CloseObserver(a)
    obs.install()
    result: dict = {}

    def chat():
        result["status"], result["body"] = _chat(
            a.gw.port, {"messages": [{"role": "user", "content": "slow"}], "max_tokens": 4}, timeout=30
        )
        result["done_at"] = time.monotonic()

    th = threading.Thread(target=chat, daemon=True)
    th.start()
    wait_for(lambda: a.gw.slot_busy_since_wall is not None, 10)  # generation in progress
    ckpt_before = (a.journal.get("usage_checkpoint") or {}).get("requests")
    t0 = time.monotonic()
    s = a.shutdown()
    closed_at = time.monotonic()
    th.join(timeout=30)
    assert not th.is_alive()
    assert s["complete"] and s["runtime_stop"]["stopped"], s
    assert "status" in result and result["done_at"] <= closed_at + 0.05, result  # answered before the close
    assert obs.at_close["gateway_inflight"] == 0 and obs.at_close["worker_alive"] is False
    assert closed_at - t0 < a.shutdown_drain_s + 3.0
    # the request's per-request checkpoint landed while the journal was open, and nothing wrote after
    j = Journal(a.data_dir / "journal.db")
    try:
        assert (j.get("usage_checkpoint") or {}).get("requests") == (ckpt_before or 0) + 1
        assert _usage_records(j)[-1]["inference_requests"] == 1
    finally:
        j.close()
    assert not any("journal closed" in r.getMessage() for r in caplog.records)


@pytest.mark.timeout(60)
def test_budget_is_enforced_against_a_non_cooperative_worker_without_releasing_ownership(stub, tmp_path):
    a = boot(make_agent(tmp_path, stub))
    _start_sim_runtime(a)
    a.shutdown_budget_s, a.shutdown_worker_s, a.shutdown_drain_s, a.shutdown_reserve_s = 4.0, 2.0, 0.5, 0.5
    release = threading.Event()
    a.worker = threading.Thread(target=release.wait, args=(30,), name="op-stuck", daemon=True)
    a.worker.start()
    t0 = time.monotonic()
    s = a.shutdown()
    took = time.monotonic() - t0
    try:
        assert took <= a.shutdown_budget_s + 1.0, (took, s)
        assert s["complete"] is False and s["released"] is False
        assert any("operation thread" in x for x in s["limits"]), s
        assert s["runtime_stop"]["stopped"] is True  # the child is still stopped and verified
        assert a.journal.closed is False and a.lock_fd is not None  # left to process exit, never raced
        assert a.journal.get("health") is not None or True  # still readable by this process
    finally:
        release.set()
        a.worker.join(timeout=5)
        a.journal.close()
        if a.lock_fd is not None:
            os.close(a.lock_fd)
            a.lock_fd = None


# ----------------------------------------------------------------------------- deferred pre-grant work


@pytest.mark.timeout(120)
def test_stop_during_a_stalled_download_defers_the_operation_and_the_restart_resumes_it(stub, tmp_path):
    stub.add_release("rel_a")
    stub.hold_blob = threading.Event()  # the model download stalls after the request is sent
    a = make_agent(tmp_path, stub)
    a.poll_s = 1
    t = _run_in_thread(a)
    try:
        wait_for(lambda: stub.posts("/report"), 20)
        stub.deploy_op("op_1", "rel_a", 1)
        wait_for(lambda: any(m == "GET" and "/api/sim/blobs/" in p for m, p, _ in stub.log), 30)
        time.sleep(0.3)
        t0 = time.monotonic()
        a.request_stop()
        t.join(timeout=30)
        took = time.monotonic() - t0
    finally:
        if t.is_alive():
            t.join(timeout=30)
    assert not t.is_alive() and took < 10.0, took
    s = a.last_shutdown
    assert s and s["complete"], s
    j = Journal(a.data_dir / "journal.db")
    try:
        row = j.operation("op_1")
        assert row and not row["terminal"] and row["stage"] == "Staging", row
        assert (row.get("detail") or {}).get("deferred_by_shutdown", {}).get("stage") == "staging"
    finally:
        j.close()
    assert all(o.get("status") == "running" for o in stub.outcomes.get("op_1", [])), stub.outcomes.get("op_1")
    # the next start resumes the pre-grant row and the deploy completes once the source answers
    stub.hold_blob.set()
    stub.hold_blob = None
    b = make_agent(tmp_path, stub)
    b.poll_s = 1
    t2 = _run_in_thread(b)
    try:
        wait_for(lambda: any(o.get("status") == "succeeded" for o in stub.outcomes.get("op_1", [])), 60)
    finally:
        b.request_stop()
        t2.join(timeout=30)
    assert b.last_shutdown and b.last_shutdown["complete"]


# ----------------------------------------------------------------------------- review findings on 5b4b81c


@pytest.mark.timeout(120)
def test_sigterm_between_polls_is_seen_within_one_wait_slice_without_any_lock(stub, tmp_path):
    """The signal handler takes no lock (plain flag + lock-free socket abort); the loop waits in short
    slices and promotes the flag to the Events from thread context. Real process, server up, SIGTERM
    while the agent sleeps between polls: exit 0 well inside a couple of slices."""
    d = enrolled_dir(tmp_path, stub.base)
    proc = run_agent(d)
    try:
        wait_for(lambda: stub.posts("/report"), 30)
        time.sleep(0.3)  # now sleeping in the poll wait, no request in flight
        rc, took, out = sigterm_and_wait(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert rc == 0, out[-3000:]
    assert took <= 2.0, (took, out[-3000:])
    assert "shutdown complete" in out


def test_inflight_registry_is_lock_free_and_the_stop_registration_race_is_closed(stub, tmp_path):
    from convoy_agent.client import _Inflight

    reg = _Inflight()
    assert not hasattr(reg, "_lock")  # by construction: nothing a signal handler could self-deadlock on
    a_sock, b_sock = socket.socketpair()
    try:
        # abort() from another thread while this thread churns add/discard: never blocks, never raises
        stop = threading.Event()
        errors: list[BaseException] = []

        def churn():
            try:
                while not stop.is_set():
                    reg.add(b_sock)
                    reg.discard(b_sock)
            except BaseException as e:  # pragma: no cover
                errors.append(e)

        th = threading.Thread(target=churn, daemon=True)
        th.start()
        for _ in range(2000):
            reg.abort()
        stop.set()
        th.join(timeout=5)
        assert not th.is_alive() and not errors
    finally:
        a_sock.close()
        b_sock.close()
    # The stress loop may have already shut down its socket pair. Use a fresh connection to
    # prove the exact successful-abort count and peer EOF without depending on repeat-shutdown
    # behavior, which differs between Linux and macOS.
    a_sock, b_sock = socket.socketpair()
    try:
        reg.add(a_sock)
        assert reg.abort() == 1
        b_sock.settimeout(2)
        assert b_sock.recv(1) == b""
    finally:
        a_sock.close()
        b_sock.close()
    # stop-vs-registration race: a stop that lands before the socket exists is applied at registration,
    # so the request is abandoned before any I/O instead of running to its 20 s timeout
    a = make_agent(tmp_path, stub)
    n = len(stub.log)
    a.client.stop_flag = True  # the signal-handler half of a stop, no Event involved
    t0 = time.monotonic()
    try:
        from convoy_agent.client import Transient

        with pytest.raises(Transient):
            a.client._request("POST", "/api/agent/v1/report", {"seq": 1})
    finally:
        a.journal.close()
    assert time.monotonic() - t0 < 1.0
    assert len(stub.log) == n  # nothing reached the server


@pytest.mark.timeout(60)
def test_shutdown_is_incomplete_and_keeps_ownership_when_the_child_stop_is_not_verified(
    stub, tmp_path, monkeypatch
):
    a = boot(make_agent(tmp_path, stub))
    _start_sim_runtime(a)
    monkeypatch.setattr(
        a.sup, "stop", lambda *args, **kw: {"stopped": False, "seconds": 0.0, "method": "busy"}
    )
    s = a.shutdown()
    try:
        assert s["complete"] is False and s["released"] is False, s
        assert any("owned runtime not verified stopped" in x for x in s["limits"]), s
        assert a.journal.closed is False and a.lock_fd is not None  # ownership kept for process exit
    finally:
        monkeypatch.undo()
        a.sup.stop()
        a.journal.close()
        if a.lock_fd is not None:
            os.close(a.lock_fd)
            a.lock_fd = None


@pytest.mark.timeout(60)
def test_a_failed_final_usage_record_marks_the_shutdown_incomplete(stub, tmp_path, monkeypatch):
    a = boot(make_agent(tmp_path, stub))

    def boom(*a_, **k):
        raise RuntimeError("journal write failed")

    monkeypatch.setattr(a, "_record_usage", boom)
    s = a.shutdown()
    assert s["final_usage_record"] is False and s["complete"] is False, s
    assert "final usage record not written" in s["limits"]
    assert s["released"] is True  # nothing can write any more: ownership is still released


@pytest.mark.timeout(60)
def test_runtime_stop_keeps_lock_term_kill_and_reader_join_under_one_absolute_deadline(tmp_path):
    from convoy_agent.runtime import RuntimeSupervisor

    sup = RuntimeSupervisor(tmp_path / "runtime", simulate=False)
    # a child that ignores SIGTERM (only SIGKILL ends it); it reports once the handler is installed
    sup.proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(120)",
        ],
        start_new_session=True,
        stdout=subprocess.PIPE,
    )
    assert sup.proc.stdout is not None and sup.proc.stdout.readline().strip() == b"ready"
    try:
        t0 = time.monotonic()
        res = sup.stop(
            timeout_s=20.0, deadline=time.monotonic() + 2.0
        )  # would be 20 s TERM + 10 s KILL before
        took = time.monotonic() - t0
        assert res["stopped"] is True and res["method"] == "sigkill", res
        assert took <= 2.6, took
    finally:
        if sup.proc is not None and sup.proc.poll() is None:
            sup.proc.kill()
            sup.proc.wait(timeout=5)
    # the supervisor lock held by a start in progress: the stop reports busy at the deadline, touches nothing
    held = threading.Event()
    release = threading.Event()

    def hold():
        with sup._lock:
            held.set()
            release.wait(10)

    th = threading.Thread(target=hold, daemon=True)
    th.start()
    held.wait(5)
    t0 = time.monotonic()
    res = sup.stop(timeout_s=20.0, deadline=time.monotonic() + 0.5)
    assert res == {"stopped": False, "seconds": 0.0, "method": "busy"} and time.monotonic() - t0 < 1.0
    release.set()
    th.join(timeout=5)


@pytest.mark.timeout(60)
def test_a_writer_holding_the_accounting_lock_cannot_hold_shutdown_past_its_budget(stub, tmp_path):
    a = boot(make_agent(tmp_path, stub))
    a.shutdown_budget_s, a.shutdown_worker_s, a.shutdown_drain_s, a.shutdown_reserve_s = 4.0, 1.0, 0.5, 0.5
    release = threading.Event()
    holding = threading.Event()

    def hold_accounting():
        with a._usage_lock:
            holding.set()
            release.wait(30)

    a._ckpt_thread = threading.Thread(target=hold_accounting, name="usage-checkpoint", daemon=True)
    a._ckpt_thread.start()
    holding.wait(5)
    t0 = time.monotonic()
    s = a.shutdown()
    took = time.monotonic() - t0
    try:
        assert took <= a.shutdown_budget_s + 1.0, (took, s)
        assert s["final_usage_record"] is False and s["complete"] is False and s["released"] is False, s
        assert (
            "usage checkpointer still running" in s["limits"]
            and "final usage record not written" in s["limits"]
        )
        assert "spool_pending" not in s  # never read lane status under a lock a live writer may hold
        assert a.journal.closed is False
    finally:
        release.set()
        a._ckpt_thread.join(timeout=5)
        a.journal.close()
        if a.lock_fd is not None:
            os.close(a.lock_fd)
            a.lock_fd = None


# ----------------------------------------------------------------------------- 354 targeted review (Astra)


@pytest.mark.timeout(60)
def test_final_usage_record_is_bounded_by_the_journal_lock_not_only_the_accounting_lock(
    stub, tmp_path, caplog
):
    """Blocker (A): the accounting lock (`_usage_lock`) and the journal lock are SEPARATE. A spool or
    operation writer inside a journal transaction holds `journal._lock` while `_usage_lock` is free, so
    the outer accounting-lock deadline proved nothing and the final record entered an unbounded journal
    transaction. Now the remaining deadline bounds the journal acquisition too: the record is skipped
    (nothing written, the last durable checkpoint stands, the next start declares the gap as unknown
    coverage) and the call returns while the writer STILL holds the journal."""
    a = boot(make_agent(tmp_path, stub))
    try:
        a._record_usage(force=True)  # restart recovery resolved; the accounting path is ordinary from here
        assert a._usage_recovery_pending is False
        before_ckpt = a.journal.get("usage_checkpoint")
        before_n = len(_usage_records(a.journal))
        held = threading.Event()
        release = threading.Event()

        def hold_journal():  # another writer's open transaction
            with a.journal._lock:
                held.set()
                release.wait(30)

        th = threading.Thread(target=hold_journal, name="spool-writer", daemon=True)
        th.start()
        assert held.wait(5)
        assert a._usage_lock.acquire(blocking=False)  # the accounting lock is FREE ...
        a._usage_lock.release()
        result: dict = {}

        def call():
            t0 = time.monotonic()
            result["ok"] = a._final_usage_record(0.05)
            result["took"] = time.monotonic() - t0

        c = threading.Thread(target=call, daemon=True)
        c.start()
        c.join(2.0)
        # ... and the final record returned while the journal is STILL held (released only below)
        assert not c.is_alive(), "final accounting waited behind the journal transaction"
        assert not release.is_set() and th.is_alive()
        assert result["ok"] is False and result["took"] < 0.5, result
        assert "journal lock busy" in caplog.text and "a writer holds a journal transaction" in caplog.text
        release.set()
        th.join(5)
        # nothing was written: the durable checkpoint and the usage lane are exactly as before
        assert a.journal.get("usage_checkpoint") == before_ckpt
        assert len(_usage_records(a.journal)) == before_n
        assert a._usage_unreported_failures == 0  # a bounded skip is not a persistence failure
        # positive control: with the journal free the same call writes the record atomically
        assert a._final_usage_record(2.0) is True
        assert len(_usage_records(a.journal)) == before_n + 1
        assert a.journal.get("usage_checkpoint") != before_ckpt
    finally:
        a.shutdown()


@pytest.mark.timeout(60)
def test_unverified_child_exit_keeps_ownership_and_a_repeated_stop_retries_the_same_child(
    tmp_path, monkeypatch
):
    """Blocker (B): a stop that could not verify the child's exit used to drop `proc` and clear the
    record, so a second stop answered {stopped: true, method: none} for a child that was never reaped.
    The owned process, its record, its key file and its reader now stay exactly as they are until the
    exit is verified; a repeated stop retries the SAME child (escalating straight to SIGKILL again) and
    reports it, never "none". Deterministic stand-in for a child the kernel does not end for us
    (uninterruptible state): signals to its group are swallowed until the test lets them through."""
    from convoy_agent import runtime as rt

    sup = rt.RuntimeSupervisor(tmp_path / "runtime", simulate=False)
    sup.proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(120)",
        ],
        start_new_session=True,
        stdout=subprocess.PIPE,
    )
    assert sup.proc.stdout is not None and sup.proc.stdout.readline().strip() == b"ready"
    pid = sup.proc.pid
    sup._write_child_record(pid)
    sup.api_key_file = sup.workdir / "runtime.key"
    sup.api_key_file.write_text("k")
    try:
        monkeypatch.setattr(rt.os, "killpg", lambda *a, **k: None)  # nothing reaches the child
        r1 = sup.stop(timeout_s=1.0, deadline=time.monotonic() + 0.6)
        assert r1["stopped"] is False and r1["method"] == "sigkill" and r1["pid"] == pid, r1
        assert r1["exit_code"] is None
        assert sup.proc is not None and sup.proc.pid == pid and sup.proc.poll() is None
        assert sup._record_path().exists() and sup.api_key_file.exists()  # ownership state retained
        r2 = sup.stop(timeout_s=1.0, deadline=time.monotonic() + 0.6)
        assert r2["stopped"] is False and r2["method"] == "sigkill" and r2["pid"] == pid, (
            r2
        )  # same child, never "none"
        assert sup.proc is not None and sup.proc.pid == pid and sup.proc.poll() is None
        assert sup._record_path().exists() and sup.api_key_file.exists()
        monkeypatch.undo()  # the kernel delivers again: the retry ends and VERIFIES this same child
        r3 = sup.stop(timeout_s=1.0, deadline=time.monotonic() + 5.0)
        assert r3["stopped"] is True and r3["method"] == "sigkill" and r3["pid"] == pid, r3
        assert r3["exit_code"] == -signal.SIGKILL
        assert sup.proc is None and not sup._record_path().exists() and not sup.api_key_file.exists()
        assert sup.stop() == {"stopped": True, "seconds": 0.0, "method": "none"}  # only now is there no child
    finally:
        monkeypatch.undo()
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# ----------------------------------------------------------------------------- body-read tracking (5b4b81c blocker)


@pytest.mark.timeout(120)
@pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl binary needed for a throwaway certificate"
)
def test_sigterm_during_a_partial_json_body_over_verified_tls(tmp_path):
    """Headers arrived (200, Content-Length: 100), one body byte arrived, then nothing: the request is
    blocked in HTTPResponse.read through the socket's makefile transport. The transport stays tracked
    for the whole body lifetime, so the stop wakes it without the peer closing first."""
    pem = _self_signed(tmp_path)
    if pem is None:
        pytest.skip("openssl could not produce a certificate")
    cert, key = pem
    srv = TLSStallServer(cert, key, partial_body=True)
    try:
        d = enrolled_dir(tmp_path, f"https://127.0.0.1:{srv.port}", ca_file=str(cert))
        proc = run_agent(d)
        try:
            wait_for(lambda: len(srv.requests) >= 1, 30)
            time.sleep(0.7)  # headers + first byte delivered; the read of the remaining 99 bytes is blocked
            rc, took, out = sigterm_and_wait(proc)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        assert rc == 0, out[-3000:]
        assert took <= 10.0, (took, out[-3000:])
        assert "'aborted_requests': 1" in out, out[
            -3000:
        ]  # the body read was woken by the stop, not by the peer
        assert "response body incomplete" in out, out[-3000:]
    finally:
        srv.close()


def test_inflight_tracking_covers_the_body_read_in_process(tmp_path):
    """Astra's exact reproduction against the Client object: strict TLS, 200 + Content-Length: 100, one
    byte, then held open. inflight must be 1 while HTTPResponse.read is blocked and the abort must
    end the read without the fixture closing."""
    from convoy_agent.client import Client, Transient

    pem = _self_signed(tmp_path)
    if pem is None:
        pytest.skip("openssl could not produce a certificate")
    cert, key = pem
    srv = TLSStallServer(cert, key, partial_body=True)
    try:
        c = Client(f"https://127.0.0.1:{srv.port}", "cvd_x", ca_file=str(cert), timeout=20.0)
        c.stop = threading.Event()
        result: dict = {}

        def call():
            t0 = time.monotonic()
            try:
                c.post("/api/agent/v1/report", {"seq": 1}, retries=0)
            except Transient as e:
                result["error"] = str(e)
            result["took"] = time.monotonic() - t0

        th = threading.Thread(target=call, daemon=True)
        th.start()
        wait_for(lambda: len(srv.requests) >= 1, 10)
        time.sleep(0.5)  # blocked in the body read now
        assert th.is_alive()
        assert c.inflight == 1, "the transport must stay registered while the body is read"
        c.stop.set()
        assert c.abort_inflight() == 1
        th.join(timeout=5)
        assert not th.is_alive(), "the blocked body read was not woken"
        assert result["took"] < 2.0 and "incomplete" in result.get("error", ""), result
        assert c.inflight == 0  # unregistered once the transport is really gone
    finally:
        srv.close()


@pytest.mark.timeout(120)
def test_sigterm_during_a_stalled_download_body_with_advertised_length(stub, tmp_path):
    """The staging download received headers (full Content-Length) and one byte, then stalls: a real
    agent process on SIGTERM wakes the body read, keeps the partial file for a Range resume, defers the
    pre-grant operation and exits within the budget."""
    stub.add_release("rel_a")
    stub.blob_partial = threading.Event()
    d = enrolled_dir(tmp_path, stub.base)
    proc = run_agent(d)
    try:
        wait_for(lambda: stub.posts("/report"), 30)
        stub.deploy_op("op_1", "rel_a", 1)
        wait_for(lambda: any(m == "GET" and "/api/sim/blobs/" in p for m, p, _ in stub.log), 60)
        time.sleep(1.0)  # one byte written, body read blocked
        rc, took, out = sigterm_and_wait(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        stub.blob_partial.set()
    assert rc == 0, out[-3000:]
    assert took <= 10.0, (took, out[-3000:])
    j = Journal(d / "journal.db")
    try:
        row = j.operation("op_1")
        assert row and not row["terminal"] and row["stage"] == "Staging", row
        assert (row.get("detail") or {}).get("deferred_by_shutdown", {}).get("stage") == "staging"
    finally:
        j.close()
    parts = list((d / "cache" / "models").glob("*.part"))
    assert parts and parts[0].stat().st_size >= 1, parts  # the delivered byte is kept for a Range resume
    assert "deferred by agent shutdown" in out, out[-3000:]
