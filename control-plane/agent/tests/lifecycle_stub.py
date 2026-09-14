"""In-process stub of the device-facing control plane (real loopback HTTP, real agent `Client`) plus
fixture builders for lifecycle regressions. It is deliberately small and observable: every request is
logged in order, grants/outcomes/spool are recorded, and hooks let a test make the server slow, strict
or unreachable at an exact point of the flow."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from convoy_agent import gguf
from convoy_agent.agent import Agent
from convoy_agent.runtime_args import canonical_config

CHATML = "{% for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content }}<|im_end|>\n{% endfor %}<|im_start|>assistant\n"
CASES = [
    {"id": "c1", "prompt": "What is the capital of France? One word.", "match": "exact", "expected": "Paris", "max_tokens": 8},
    {"id": "c2", "prompt": "What is 7 times 8? Digits only.", "match": "exact", "expected": "56", "max_tokens": 8},
]  # fmt: skip


class Stub:
    def __init__(self):
        self.manifests: dict[str, dict[str, Any]] = {}
        self.plans: dict[str, dict[str, Any]] = {}
        self.blobs: dict[str, bytes] = {}
        self.archives: dict[str, bytes] = {}
        self.ops: list[dict[str, Any]] = []
        self.terminal: set[str] = set()
        self.generation = 0
        self.grant_ttl_s = 30.0
        self.grant_override: dict[str, Any] = {}
        self.grant_error: tuple[int, str] | None = (
            None  # e.g. (409, "cancellation requested"), (423, "paused")
        )
        self.progress_delay: dict[str, float] = {}
        self.on_progress: Callable[[str, dict[str, Any]], None] | None = None
        self.down = False
        self.dispatch_paused = False
        self.strict_eval_binding = True
        self.accepted_eval_ids: set[str] = set()
        self.log: list[tuple[str, str, Any]] = []
        self.outcomes: dict[str, list[dict[str, Any]]] = {}
        self.grants: dict[str, dict[str, Any]] = {}
        self.spool: list[dict[str, Any]] = []
        self.losses: list[dict[str, Any]] = []
        self.spool_calls: list[dict[str, Any]] = []
        self.reject_outcomes = False  # 503 on outcome posts only (spool keeps working)
        self.hold_blob: threading.Event | None = None  # blob GETs block until set (stalled download)
        self.blob_partial: threading.Event | None = None  # headers + ONE body byte, then hold until set
        self.drop_spool_response = False  # process the spool batch, then close without answering (lost ACK)
        self.quarantine = False
        self.cursor: dict[str, int] = {"critical": 0, "usage": 0, "telemetry": 0}
        self.lock = threading.Lock()
        stub = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code: int, obj: Any = None, raw: bytes | None = None):
                data = raw if raw is not None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header(
                    "Content-Type", "application/octet-stream" if raw is not None else "application/json"
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with stub.lock:
                    stub.log.append(("GET", self.path, None))
                if stub.down:
                    return self._send(503, {"detail": "down"})
                p = self.path
                if p.startswith("/api/agent/v1/releases/"):
                    m = stub.manifests.get(p.rsplit("/", 1)[1])
                    return self._send(200, m) if m else self._send(404, {"detail": "no release"})
                if p.startswith("/api/agent/v1/plans/"):
                    m = stub.plans.get(p.rsplit("/", 1)[1])
                    return self._send(200, m) if m else self._send(404, {"detail": "no plan"})
                if p.startswith("/api/sim/blobs/"):
                    if stub.hold_blob is not None:
                        stub.hold_blob.wait(120)  # the client sees a stalled response, not a refusal
                    b = stub.blobs.get(p.rsplit("/", 1)[1])
                    if b is not None and stub.blob_partial is not None:
                        # advertised full length, one byte delivered, then a stalled body
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Length", str(len(b)))
                        self.end_headers()
                        self.wfile.write(b[:1])
                        self.wfile.flush()
                        stub.blob_partial.wait(120)
                        return None
                    return self._send(200, raw=b) if b is not None else self._send(404, {"detail": "no blob"})
                if p.startswith("/api/agent/v1/runtime-artifacts/") and p.endswith("/archive"):
                    art = p.split("/")[5]
                    b = stub.archives.get(art)
                    return self._send(200, raw=b) if b is not None else self._send(404, {"detail": "no art"})
                return self._send(404, {"detail": "unknown"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                with stub.lock:
                    stub.log.append(("POST", self.path, body))
                if stub.down:
                    return self._send(503, {"detail": "down"})
                p = self.path
                if p == "/api/agent/v1/report":
                    with stub.lock:
                        ops = (
                            []
                            if stub.dispatch_paused
                            else [o for o in stub.ops if o["id"] not in stub.terminal]
                        )
                        gen = max([stub.generation] + [int(o["generation"]) for o in stub.ops])
                    return self._send(
                        200,
                        {
                            "server_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "poll_interval_s": 1,
                            "generation": gen,
                            "expected_active_release_id": None,
                            "operations": ops,
                            "dispatch_paused": "paused" if stub.dispatch_paused else None,
                            "applied": True,
                            "last_report_seq": body.get("seq"),
                            "live_seq": body.get("seq"),
                            "live_nonce": "nonce-1",
                            "quarantine": stub.quarantine,
                            "reconcile_challenge": None,
                        },
                    )
                if p.startswith("/api/agent/v1/operations/") and p.endswith("/grant"):
                    op_id = p.split("/")[5]
                    op = next((o for o in stub.ops if o["id"] == op_id), None)
                    if op is None:
                        return self._send(404, {"detail": "operation not found"})
                    if stub.grant_error:
                        return self._send(stub.grant_error[0], {"detail": stub.grant_error[1]})
                    g = {
                        "grant_id": f"grant-{op_id}",
                        "operation_id": op_id,
                        "generation": op["generation"],
                        "boot_id": body.get("boot_id"),
                        "nonce": body.get("nonce"),
                        "ttl_s": stub.grant_ttl_s,
                        "issued_at": None,
                        "expires_at": None,
                        "window_end": None,
                        "expected_active_release_id": op["payload"].get("expected_active_release_id"),
                        "recovery_release_id": body.get("recovery_release_id"),
                        "target_release_id": op["payload"].get("target_release_id"),
                        "release_digest": op["payload"].get("release_digest"),
                        "plan_digest": op["payload"].get("plan_digest"),
                        "artifact_sha256": op["payload"].get("artifact_sha256"),
                    }
                    g.update(stub.grant_override)
                    with stub.lock:
                        stub.grants[op_id] = g
                    return self._send(200, g)
                if p.startswith("/api/agent/v1/operations/") and p.endswith("/outcome"):
                    op_id = p.split("/")[5]
                    if stub.reject_outcomes:
                        return self._send(503, {"detail": "outcomes unavailable"})
                    if body.get("status") == "running":
                        stage = (body.get("progress") or {}).get("stage")
                        delay = stub.progress_delay.get(stage or "", 0.0)
                        if delay:
                            time.sleep(delay)
                        if stub.on_progress:
                            stub.on_progress(op_id, body.get("progress") or {})
                        if stub.down:
                            return self._send(503, {"detail": "down"})
                        return self._send(200, {"ok": True, "status": "running"})
                    with stub.lock:
                        stub.outcomes.setdefault(op_id, []).append(body)
                        if op_id in stub.terminal:
                            return self._send(200, {"ok": True, "duplicate": True})
                        if body.get("status") == "succeeded" and stub.strict_eval_binding:
                            cited = ((body.get("evidence") or {}).get("eval") or {}).get(
                                "eval_result_id"
                            ) or (body.get("result") or {}).get("eval_result_id")
                            if cited and cited not in stub.accepted_eval_ids:
                                stub.outcomes[op_id][-1] = {**body, "_rejected": 422}
                                return self._send(
                                    422, {"detail": "eval result not accepted for this operation"}
                                )
                        stub.terminal.add(op_id)
                    return self._send(200, {"ok": True, "status": body.get("status")})
                if p == "/api/agent/v1/spool":
                    # mirrors the server contract: records/loss ranges are stored only as a contiguous
                    # extension of the lane cursor; replays at/below the cursor are ignored; the rest is
                    # deferred. Loss ranges are persisted as visible coverage with the device's reason.
                    lane = body.get("lane")
                    accepted, deferred, ignored = [], [], []
                    with stub.lock:
                        for lr in body.get("loss_ranges") or []:
                            f, t = int(lr["from_seq"]), int(lr["to_seq"])
                            if t <= stub.cursor[lane]:
                                continue
                            if f <= stub.cursor[lane] + 1:
                                stub.losses.append({"lane": lane, **lr})
                                stub.cursor[lane] = t
                            else:
                                deferred.append([f, t])
                        for r in sorted(body.get("records") or [], key=lambda x: int(x["seq"])):
                            seq = int(r["seq"])
                            if seq <= stub.cursor[lane]:
                                ignored.append(seq)
                            elif seq == stub.cursor[lane] + 1:
                                stub.spool.append({"lane": lane, **r})
                                if lane == "critical" and r.get("kind") == "eval_result":
                                    stub.accepted_eval_ids.add(r["body"]["id"])
                                stub.cursor[lane] = seq
                                accepted.append(seq)
                            else:
                                deferred.append(seq)
                        stub.spool_calls.append(
                            {
                                "lane": lane,
                                "records": [int(r["seq"]) for r in body.get("records") or []],
                                "loss_ranges": body.get("loss_ranges") or [],
                                "accepted": accepted,
                                "deferred": deferred,
                            }
                        )
                        if stub.drop_spool_response:
                            # committed server-side, acknowledgement lost on the wire
                            self.close_connection = True
                            self.connection.close()
                            return None
                        return self._send(
                            200,
                            {
                                "committed_seq": stub.cursor[lane],
                                "next_expected_seq": stub.cursor[lane] + 1,
                                "accepted": accepted,
                                "rejected": [],
                                "deferred": deferred,
                                "ignored": ignored,
                            },
                        )
                return self._send(404, {"detail": "unknown"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # ---- observations ----
    def posts(self, suffix: str) -> list[tuple[int, Any]]:
        with self.lock:
            return [(i, b) for i, (m, p, b) in enumerate(self.log) if m == "POST" and p.endswith(suffix)]

    def first_index(self, pred: Callable[[tuple[str, str, Any]], bool]) -> int | None:
        with self.lock:
            for i, entry in enumerate(self.log):
                if pred(entry):
                    return i
        return None

    # ---- fixtures ----
    def add_release(
        self,
        rid: str,
        *,
        sim: dict[str, Any] | None = None,
        name: str | None = None,
        gguf_kv: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """`gguf_kv` overrides/extends the fixture GGUF header KVs (a value of None removes the key);
        without it the release is byte-identical to before."""
        model = _gguf_bytes(name or rid, gguf_kv)
        sha = hashlib.sha256(model).hexdigest()
        self.blobs[sha] = model
        archive, files = _sim_archive()
        art_sha = hashlib.sha256(archive).hexdigest()
        art_id = f"art_{rid}"
        self.archives[art_id] = archive
        spec = {
            "schema_version": "release_spec_v1",
            "model": {
                "source": "fixture",
                "repo": "convoy-sim/fixture",
                "revision": "0" * 40,
                "commit": "0" * 40,
                "file": {
                    "path": f"{rid}.gguf",
                    "size": len(model),
                    "sha256": sha,
                    "url": f"/api/sim/blobs/{sha}",
                    "auth": "none",
                },
                "total_bytes": len(model),
                "gguf": {
                    "architecture": "qwen2",
                    "kv_estimate_inputs": {"n_layers": 28, "n_kv_heads": 2, "head_dim": 128},
                },
                "verified": True,
            },  # fmt: skip
            "template": None,
            "runtime": {
                "name": "llama.cpp",
                "backend": "simulated",
                "artifact_sha256": art_sha,
                "artifact_files": files,
            },
            "config": canonical_config({"health_timeout_s": 10, **({"sim": sim} if sim else {})}),
            "budget": {},
            "platform": {"profile_id": "simulated-host", "target": "sim"},
        }
        m = {
            "release_id": rid, "name": rid, "version": "1", "digest": hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
            "build_status": "ready", "runtime_artifact_id": art_id, "artifact_size": len(archive), "simulated": True, "spec": spec,
        }  # fmt: skip
        self.manifests[rid] = m
        return m

    def add_plan(self, pid: str, rid: str, *, gates=None, sample_policy=None) -> dict[str, Any]:
        plan = {
            "id": pid, "name": pid, "release_id": rid, "digest": "plan-digest-" + pid, "simulated": True,
            "eval_set": {"id": "es1", "digest": "es-digest-1", "cases": CASES, "scorer": {"version": "1"}},
            "gates": gates or [{"metric": "quality.pass_rate", "op": "min", "limit": 1.0, "required": True}],
            "workload": {"warmup": 1, "ordering": "fixed", "max_tokens": 16, "temperature": 0.0, "seed": 42, "request_timeout_s": 10},
            "sample_policy": sample_policy or {"probation_min_s": 0.2, "probation_min_requests": 0},
        }  # fmt: skip
        self.plans[pid] = plan
        return plan

    def deploy_op(
        self, op_id: str, rid: str, generation: int, *, plan_id: str | None = None, expected_active=None
    ) -> dict[str, Any]:
        m = self.manifests[rid]
        plan = self.plans.get(plan_id) if plan_id else None
        payload = {
            "release_id": rid, "target_release_id": rid, "release_digest": m["digest"],
            "artifact_sha256": m["spec"]["runtime"]["artifact_sha256"],
            "artifact_files": [{"path": f["path"], "sha256": f["sha256"]} for f in m["spec"]["runtime"]["artifact_files"]],
            "simulated": True, "plan_id": plan["id"] if plan else None, "plan_digest": plan["digest"] if plan else None,
            "expected_active_release_id": expected_active, "recovery_release_id": None,
            "qualification": "plan" if plan else "bootstrap_no_plan",
        }  # fmt: skip
        op = {"id": op_id, "type": "deploy", "payload": payload, "generation": generation, "status": "delivered", "cancel_requested": False, "expected_active_release_id": expected_active}  # fmt: skip
        self.ops.append(op)
        return op

    def recover_op(self, op_id: str, rid: str, generation: int) -> dict[str, Any]:
        op = self.deploy_op(op_id, rid, generation)
        op["type"] = "recover"
        return op


def _gguf_bytes(name: str, overrides: dict[str, Any] | None = None) -> bytes:
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".gguf")
    os.close(fd)
    kv: dict[str, Any] = {
        "general.architecture": "qwen2",
        "general.name": f"fixture {name}",
        "general.file_type": 15,
        "qwen2.block_count": 28,
        "qwen2.context_length": 32768,
        "qwen2.embedding_length": 1536,
        "qwen2.attention.head_count": 12,
        "qwen2.attention.head_count_kv": 2,
        "qwen2.vocab_size": 151936,
        "tokenizer.ggml.model": "gpt2",
        "tokenizer.ggml.pre": "qwen2",
        "tokenizer.chat_template": CHATML,
        "convoy.simulated": True,
    }  # fmt: skip
    for k, v in (overrides or {}).items():
        if v is None:
            kv.pop(k, None)
        else:
            kv[k] = v
    gguf.write_minimal_gguf(path, kv, pad_bytes=4096)
    data = Path(path).read_bytes()
    os.unlink(path)
    return data


def _sim_archive() -> tuple[bytes, list[dict[str, Any]]]:
    script = b"#!/bin/sh\nexit 3\n"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo("bin/llama-server.sim")
        ti.size = len(script)
        ti.mode = 0o755
        ti.mtime = 0
        tf.addfile(ti, io.BytesIO(script))
    return buf.getvalue(), [
        {
            "path": "bin/llama-server.sim",
            "size": len(script),
            "sha256": hashlib.sha256(script).hexdigest(),
            "kind": "executable",
        }
    ]


def make_agent(tmp: Path, stub: Stub, name: str = "dev", **kw) -> Agent:
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "agent.json").exists():
        (d / "agent.json").write_text(
            json.dumps(
                {
                    "device_id": "dev_1",
                    "server": stub.base,
                    "name": name,
                    "simulate": True,
                    "seed": 1,
                    "robot_sim": False,
                }
            )
        )
        fd = os.open(str(d / "credential"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("secret")
    kw.setdefault("robot_sim", False)
    kw.setdefault("gateway_port", 0)
    return Agent(d, **kw)


def boot(agent: Agent) -> Agent:
    agent.acquire_lock()
    agent.start_local()
    return agent


def run_op(agent: Agent, op: dict[str, Any]) -> dict[str, Any]:
    """Execute one operation synchronously through the real executor, exactly as the worker does but
    WITHOUT posting the outcome (this is the crash point 'terminal row committed, outcome unposted')."""
    return agent.exec.execute(
        op, live_seq_getter=lambda: 1, boot_id=agent.boot_id, report_progress=agent._progress
    )


def crash(agent: Agent) -> None:
    """Simulate SIGKILL: nothing is flushed, nothing is journaled; resources of the dead process go away."""
    agent.gw.stop()
    agent.sup.stop()
    agent.journal.close()
    if agent.lock_fd is not None:
        os.close(agent.lock_fd)
        agent.lock_fd = None


def wait_for(fn: Callable[[], Any], timeout: float = 30.0, every: float = 0.05) -> Any:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise AssertionError("timed out waiting for condition")


FAKE_LLAMA_SERVER = f'''#!{sys.executable}
"""Fake llama-server for supervisor tests: real HTTP child that honours --port/--api-key-file, prints
startup backend lines, and chatters continuously when a CHATTY marker file exists in its cwd."""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
key = open(args[args.index("--api-key-file") + 1]).read().strip()
print("ggml_cuda_init: found 1 CUDA devices", flush=True)
print("load_tensors: offloaded 29/29 layers to GPU", flush=True)
print("load_tensors: CUDA0 model buffer size = 1000.00 MiB", flush=True)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path == "/health":
            body = b'{{"status":"ok"}}'
        elif self.headers.get("Authorization") != "Bearer " + key:
            self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers(); return
        elif self.path == "/props":
            body = json.dumps({{"build_info": "fake-b1", "n_ctx": 2048, "total_slots": 1}}).encode()
        elif self.path == "/slots":
            body = b'[{{"id":0,"is_processing":false}}]'
        else:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
srv = HTTPServer(("127.0.0.1", port), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
chatty = os.path.exists("CHATTY")
i = 0
while True:
    if chatty:
        for _ in range(200):
            i += 1
            sys.stdout.write("chatter line %d " % i + "x" * 180 + "\\n")
        sys.stdout.flush()
        time.sleep(0.005)
    else:
        time.sleep(0.2)
'''


def write_fake_llama_server(path: Path) -> Path:
    path.write_text(FAKE_LLAMA_SERVER)
    path.chmod(0o755)
    return path
