"""Negative regressions for agent-side review findings R24-R30, R32 and R20-fu."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from convoy_agent.download import DownloadError, download_verified, extract_archive
from convoy_agent.gateway import Gateway
from convoy_agent.hardware import SimulatedSensors, read_tegrastats_sample
from convoy_agent.journal import Journal, StorageError
from convoy_agent.runtime import RuntimeSupervisor


def _call(port, body, headers=None, timeout=15):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read()), r.headers.get("X-Convoy-Trace-Id")
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None), e.headers.get("X-Convoy-Trace-Id")


def _stack(tmp_path, faults=None, deadline=5, queue=4):
    sens = SimulatedSensors(str(tmp_path))
    sup = RuntimeSupervisor(tmp_path / "rt", simulate=True, sensors=sens, sim_faults=faults or {})
    sup.start(
        release_id="rel_t",
        spec={"config": {}, "model": {"total_bytes": 1}},
        model_path=Path("m"),
        template_path=None,
        binary=None,
        lib_dir=None,
        health_timeout_s=5,
    )
    spans = []
    gw = Gateway(sup, on_span=spans.append, deadline_s=deadline, queue_depth=queue)
    port = gw.start()
    gw.set_mode("production")
    return sup, gw, port, spans


def test_r24_queued_request_rejected_after_mode_change(tmp_path):
    sup, gw, port, spans = _stack(tmp_path, faults={"latency_ms": 600})
    try:
        results = {}

        def a():
            results["a"] = _call(port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})

        def b():
            results["b"] = _call(port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})

        ta = threading.Thread(target=a)
        ta.start()
        time.sleep(0.15)  # A owns the slot
        tb = threading.Thread(target=b)
        tb.start()
        time.sleep(0.15)  # B queued behind A
        gw.set_mode("closed")  # cutover begins while B is queued
        ta.join()
        tb.join()
        assert results["a"][0] == 200
        assert results["b"][0] == 503 and results["b"][1]["error"]["type"] == "unavailable"
        assert any(
            s["status"] == "rejected"
            and s["attrs"].get("reason") in ("unavailable", "admission_changed_while_queued")
            for s in spans
        )
    finally:
        gw.stop()
        sup.stop()


def test_r28_ttft_from_stream_and_spans_for_failures(tmp_path):
    sup, gw, port, spans = _stack(tmp_path)
    try:
        code, out, tid = _call(
            port,
            {
                "messages": [{"role": "user", "content": "What is the capital of France? One word."}],
                "max_tokens": 8,
            },
        )
        assert code == 200 and out["convoy"]["ttft_ms"] is not None and out["convoy"]["ttft_ms"] > 0
        ok = [s for s in spans if s["status"] == "ok"]
        assert ok and ok[0]["attrs"]["ttft_ms"] == out["convoy"]["ttft_ms"] and ok[0]["trace_id"] == tid
        # rejected / overloaded / timed out requests all carry correlated spans with reasons
        code, _, tid2 = _call(port, {"messages": [{"role": "user", "content": "hi"}], "tools": []})
        assert code == 400 and any(s["trace_id"] == tid2 and s["status"] == "rejected" for s in spans)
        code, _, tid3 = _call(port, {"messages": [{"role": "user", "content": "x " * 3000}], "max_tokens": 8})
        assert code == 400 and any(
            s["trace_id"] == tid3 and s["attrs"].get("reason") == "context_length_exceeded" for s in spans
        )
        gw.set_mode("closed")
        code, _, tid4 = _call(port, {"messages": [{"role": "user", "content": "hi"}]})
        assert code == 503 and any(s["trace_id"] == tid4 and s["status"] == "rejected" for s in spans)
        assert gw.stats["requests"] == 4 and gw.stats["served"] == 1 and gw.stats["rejected"] == 3
    finally:
        gw.stop()
        sup.stop()


def test_r28_ttft_unavailable_when_runtime_does_not_stream(tmp_path, monkeypatch):
    from convoy_agent import gateway as gwmod

    sup, gw, port, spans = _stack(tmp_path)
    try:
        real = gwmod.http_json

        def no_stream(base, key, req, timeout):
            code, out = real(
                f"{base}/completion", key, {k: v for k, v in req.items() if k != "stream"}, timeout=timeout
            )
            return code, out, None

        monkeypatch.setattr(gwmod, "_completion_with_ttft", no_stream)
        code, out, _ = _call(port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})
        assert (
            code == 200 and out["convoy"]["ttft_ms"] is None
        )  # unavailable, not approximated from prompt_ms
    finally:
        gw.stop()
        sup.stop()


def test_r32_bad_seed_and_internal_errors_are_structured(tmp_path, monkeypatch):
    sup, gw, port, spans = _stack(tmp_path)
    try:
        for bad in (
            {"seed": "bad"},
            {"seed": True},
            {"seed": -1},
            {"temperature": "hot"},
            {"max_tokens": "8"},
        ):
            code, out, tid = _call(port, {"messages": [{"role": "user", "content": "hi"}], **bad})
            assert code == 400 and out["error"]["type"] == "invalid_request_error" and tid, bad
        # an internal exception after admission becomes a JSON 500 with a trace, never a dropped connection
        from convoy_agent import gateway as gwmod

        def boom(*a, **k):
            raise RuntimeError("injected")

        monkeypatch.setattr(gwmod, "_completion_with_ttft", boom)
        code, out, tid = _call(port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})
        assert code == 504 or (code == 500 and out["error"]["type"] == "server_error")
        assert tid and any(s["trace_id"] == tid for s in spans)
        monkeypatch.undo()
        assert _call(port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})[0] in (
            200,
            503,
        )
    finally:
        gw.stop()
        sup.stop()


def test_r25_redirect_targets_never_receive_device_credential(tmp_path):
    seen = {}
    data = os.urandom(2048)
    sha = hashlib.sha256(data).hexdigest()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen[self.path] = self.headers.get("Authorization")
            if self.path == "/api/agent/v1/runtime-artifacts/x/archive":
                # control plane must not redirect authenticated artifact requests
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{port}/elsewhere.gguf")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from convoy_agent.client import Client

        base = f"http://127.0.0.1:{port}"
        c = Client(base, "cvd_dev_x_secret")
        with pytest.raises(DownloadError) as e:
            download_verified(
                c,
                url=f"{base}/api/agent/v1/runtime-artifacts/x/archive",
                dest=tmp_path / "a",
                expected_sha256=sha,
                expected_size=len(data),
                auth="device",
                server_base=base,
                attempts=1,
            )
        assert e.value.code == "URL_REJECTED" and "/elsewhere.gguf" not in seen
        # a different origin (even same host, other port) never gets the credential
        other = f"http://localhost:{port}"
        c2 = Client(base, "cvd_dev_x_secret")
        with pytest.raises(DownloadError):
            download_verified(
                c2,
                url=f"{other}/api/sim/blobs/{sha}",
                dest=tmp_path / "b",
                expected_sha256=sha,
                expected_size=len(data),
                auth="device",
                server_base=base,
                attempts=1,
            )
    finally:
        srv.shutdown()


def test_r26_extraction_rejects_alias_duplicate_and_unlisted_and_never_deletes_dest(tmp_path):
    a_bytes, b_bytes = b"#!/bin/sh\nexit 0\n", b"#!/bin/sh\nexit 1\n"

    def tar_of(entries):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name, d in entries:
                ti = tarfile.TarInfo(name)
                ti.size = len(d)
                tf.addfile(ti, io.BytesIO(d))
        p = tmp_path / f"{hashlib.sha256(buf.getvalue()).hexdigest()[:8]}.tar.gz"
        p.write_bytes(buf.getvalue())
        return p

    receipt = [
        {"path": "bin/llama-server", "sha256": hashlib.sha256(a_bytes).hexdigest(), "size": len(a_bytes)}
    ]
    with pytest.raises(DownloadError) as e:
        extract_archive(
            tar_of([("bin/llama-server", a_bytes), ("bin/./llama-server", b_bytes)]),
            tmp_path / "rt1",
            receipt,
        )
    assert e.value.code == "ARCHIVE_REJECTED" and not (tmp_path / "rt1").exists()
    with pytest.raises(DownloadError) as e:
        extract_archive(
            tar_of([("bin/llama-server", a_bytes), ("bin/extra.so", b_bytes)]), tmp_path / "rt2", receipt
        )
    assert e.value.code == "ARCHIVE_REJECTED" and "not listed" in str(e.value)
    good = tar_of([("./bin/llama-server", a_bytes)])
    out = extract_archive(good, tmp_path / "rt3", receipt)
    assert out[0]["path"] == "bin/llama-server"
    # existing digest dir is reconciled, never destroyed; a mismatching one is refused
    marker = tmp_path / "rt3" / "bin" / "llama-server"
    before = marker.stat().st_ino
    extract_archive(good, tmp_path / "rt3", receipt)
    assert marker.stat().st_ino == before
    marker.write_bytes(b_bytes)
    with pytest.raises(DownloadError) as e:
        extract_archive(good, tmp_path / "rt3", receipt)
    assert e.value.code == "DIGEST_MISMATCH" and marker.read_bytes() == b_bytes


def test_r27_journal_begin_and_commit_failures_release_the_lock(tmp_path, monkeypatch):
    j = Journal(tmp_path / "j.db")
    real_execute = j.conn.execute
    calls = {"n": 0}

    def flaky(sql, *a):
        if sql == "BEGIN IMMEDIATE" and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("injected BEGIN failure")
        return real_execute(sql, *a)

    monkeypatch.setattr(j.conn, "execute", flaky, raising=False) if hasattr(j.conn, "__dict__") else None

    # sqlite3.Connection.execute is read-only; wrap through a proxy instead
    class Proxy:
        def __init__(self, c):
            self._c = c

        def execute(self, sql, *a):
            return flaky(sql, *a)

        def __getattr__(self, k):
            return getattr(self._c, k)

    j.conn = Proxy(j.conn)
    with pytest.raises(StorageError):
        j.next_seq()
    done = {}

    def other_thread():
        done["seq"] = j.next_seq()

    t = threading.Thread(target=other_thread)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive() and done["seq"] == 1  # no deadlock after a failed BEGIN
    # COMMIT failure: rolled back, lock released, next transaction proceeds

    def flaky_commit(sql, *a):
        if sql == "COMMIT" and calls["n"] == 1:
            calls["n"] += 1
            raise RuntimeError("injected COMMIT failure")
        return real_execute(sql, *a)

    j.conn = Proxy(j.conn._c)
    j.conn.execute = flaky_commit
    with pytest.raises(StorageError):
        j.append("usage", "usage", {"x": 1})
    t = threading.Thread(target=other_thread)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive() and done["seq"] == 2
    assert j.pending("usage")[0] == []  # the failed append was rolled back


def test_r29_runtime_log_is_per_launch_and_bounded(tmp_path):
    sup = RuntimeSupervisor(tmp_path / "rt", simulate=False)
    fake = tmp_path / "llama-server"
    fake.write_text(
        '#!/bin/sh\necho "load_tensors: offloaded 29/29 layers to GPU"\necho "load_tensors: CUDA0 model buffer size = 1000 MiB"\nwhile true; do sleep 1; done\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    # first launch: health will time out (fake server has no HTTP); we only inspect the log identity
    from convoy_agent.runtime import RuntimeError_

    with pytest.raises(RuntimeError_):
        sup.start(
            release_id="r",
            spec={"config": {}, "model": {"total_bytes": 1}},
            model_path=Path("m"),
            template_path=None,
            binary=fake,
            lib_dir=None,
            health_timeout_s=0.5,
        )
    first = sup.log_path
    assert first is not None and first.name == "runtime.1.log"
    with pytest.raises(RuntimeError_):
        sup.start(
            release_id="r",
            spec={"config": {}, "model": {"total_bytes": 1}},
            model_path=Path("m"),
            template_path=None,
            binary=fake,
            lib_dir=None,
            health_timeout_s=0.5,
        )
    assert sup.log_path.name == "runtime.2.log" and sup.log_path != first
    tail = sup.log_tail()
    assert any("offloaded 29/29" in x for x in tail)
    argv = " ".join(sup.argv)
    assert "--verbosity 4" in argv and "--verbose-prompt" not in argv
    big = tmp_path / "rt" / "runtime.2.log"
    big.write_bytes(b"x" * (600 * 1024))
    assert sum(len(x) for x in sup.log_tail(max_bytes=256 * 1024)) <= 256 * 1024


def test_r30_tegrastats_continuous_process_is_sampled_and_reaped(tmp_path):
    fake = tmp_path / "tegrastats"
    fake.write_text(
        "#!/bin/sh\necho 'RAM 2345/7620MB (lfb 10x4MB) GR3D_FREQ 37% VDD_IN 6500mW/6000mW cpu@45.5C'\nwhile true; do sleep 1; done\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    t0 = time.monotonic()
    line = read_tegrastats_sample(str(fake), timeout_s=2.0)
    assert line and "GR3D_FREQ 37%" in line and time.monotonic() - t0 < 3.0
    from convoy_agent.hardware import parse_tegrastats

    t = parse_tegrastats(line)
    assert t["gpu_pct"] == 37.0 and t["power_w"] == 6.5
    silent = tmp_path / "silent"
    silent.write_text("#!/bin/sh\nwhile true; do sleep 1; done\n")
    silent.chmod(silent.stat().st_mode | stat.S_IEXEC)
    assert read_tegrastats_sample(str(silent), timeout_s=0.5) is None
    # no leftover child processes
    import subprocess

    ps = subprocess.run(["pgrep", "-f", str(fake)], capture_output=True, text=True)
    assert ps.stdout.strip() == ""


def test_r20_errors_counted_over_the_same_population():
    from convoy_agent.evaluator import evaluate, summarize

    s = summarize(
        [{"id": "a", "status": "ok", "passed": True}],
        [{"status": "ok", "latency_ms": 10}, {"status": "timeout"}],
        [],
        expected_cases=1,
    )
    assert (
        s["errors"]["evidence_consistent"] is False
        and evaluate(s, [{"metric": "errors.rate", "op": "max", "limit": 0}])[0] == "failed"
    )
    s = summarize(
        [{"id": "a", "status": "ok", "passed": True}],
        [{"case_id": "a", "status": "ok", "latency_ms": 10}],
        [],
        expected_cases=1,
    )
    assert s["errors"] == {"count": 0, "attempts": 1, "rate": 0.0, "evidence_consistent": True}
