"""Optional host integration test against a REAL llama-server binary (not the simulator).

Run with:
  LLAMA_SERVER_BIN=/path/to/llama-server TEST_GGUF_PATH=/path/to/model.gguf \
    uv run --package convoy-agent pytest agent/tests/integration -q -s

Without both variables the test is SKIPPED and says so; it never fabricates a pass. Evidence produced
here is labelled as host CPU / supplied fixture evidence only (not Convoy qualification, not Jetson CUDA).
Optional: TEST_TEMPLATE_FILE for fixtures without an embedded chat template (reviewed, test-only)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

BIN = os.environ.get("LLAMA_SERVER_BIN")
GGUF = os.environ.get("TEST_GGUF_PATH")
TEMPLATE = os.environ.get("TEST_TEMPLATE_FILE")

pytestmark = pytest.mark.skipif(
    not (BIN and GGUF),
    reason="optional: set LLAMA_SERVER_BIN and TEST_GGUF_PATH to run against a real runtime (unavailable => skipped, not passed)",
)


@pytest.mark.timeout(600)
def test_real_runtime_contract(tmp_path: Path):
    from convoy_agent.probe import runtime_probe

    for k in list(os.environ):
        if k.startswith("LLAMA_ARG_") or k == "LLAMA_SERVER_DEBUG_FAKE_TIMING":
            os.environ.pop(k)
    res = runtime_probe(
        Path(BIN),
        Path(GGUF),
        template_file=Path(TEMPLATE) if TEMPLATE else None,
        gpu_layers=os.environ.get("TEST_GPU_LAYERS", "0"),
    )
    print("\n" + json.dumps(res, indent=2, default=str))
    (tmp_path / "probe.json").write_text(json.dumps(res, default=str))
    failed = [c for c in res["checks"] if not c["ok"]]
    assert res.get("ok"), f"failed checks: {failed}; error={res.get('error')}; log_tail={res.get('log_tail')}"


@pytest.mark.timeout(900)
def test_real_runtime_cancel_during_active_generation_then_serves_again(tmp_path: Path):
    """Active-generation cancellation against a REAL llama-server (CPU component test; never Jetson/CUDA
    identity). The warm request measures first-token (ttft) and total latency; the deadline is placed
    strictly inside the observed first-token..completion interval with margins, so the timed-out
    request must show a POSITIVE locally observed streamed-token count (native generation was active
    when it was cancelled). Afterwards the runtime is confirmed idle or the owned child is replaced,
    another request is served, and the final cleanup (child reaped, key file/record removed, listener
    closed) is asserted. Honesty: the replacement here is driven by the test through the supervisor;
    it is not proof of Agent.tick's automatic supervision (covered by the simulated-agent tests).
    Measured timings and the spans are written to <tmp>/cancel_diag.json before any assertion."""
    import socket
    import time

    from convoy_agent.gateway import Gateway
    from convoy_agent.hardware import Sensors
    from convoy_agent.runtime import RuntimeSupervisor, proc_start_ticks

    for k in list(os.environ):
        if k.startswith("LLAMA_ARG_") or k == "LLAMA_SERVER_DEBUG_FAKE_TIMING":
            os.environ.pop(k)
    n_predict = 160
    spec = {
        "config": {
            "ctx_size": 2048,
            "n_predict": n_predict,
            "gpu_layers": int(os.environ.get("TEST_GPU_LAYERS", "0")),
        },
        "model": {"total_bytes": Path(GGUF).stat().st_size},
    }
    sup = RuntimeSupervisor(tmp_path / "rt", simulate=False, sensors=Sensors(str(tmp_path)))
    spans: list[dict] = []
    gw = Gateway(sup, deadline_s=300, on_span=spans.append)
    diag: dict = {"label": "host CPU runtime evidence only; NOT Convoy qualification, NOT Jetson CUDA"}
    diag_path = tmp_path / "cancel_diag.json"

    def persist() -> None:
        diag["spans"] = spans[-6:]
        diag_path.write_text(json.dumps(diag, indent=2, default=str))
        print("\n" + json.dumps(diag, indent=2, default=str))

    def start() -> dict:
        return sup.start(
            release_id="probe",
            spec=spec,
            model_path=Path(GGUF),
            template_path=Path(TEMPLATE) if TEMPLATE else None,
            binary=Path(BIN),
            lib_dir=Path(BIN).parent,
            health_timeout_s=180,
        )

    def chat(body: dict, timeout: float = 400.0) -> tuple[int, dict | None]:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{gw.port}/v1/chat/completions",
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

    child_pid = None
    try:
        start()
        child_pid = sup.proc.pid
        gw.start()
        gw.set_mode("production")
        # a fixture that keeps generating: counting produces a long, EOS-free output on instruct models
        long_prompt = [
            {
                "role": "user",
                "content": "Count upwards from 1 to 400 as plain numbers separated by commas. Do not stop early and do not explain.",
            }
        ]
        # 1) calibration on WARMED, repeated, comparable requests. Request timing on a fresh child varies
        #    substantially between requests (root's host: 202.0 s total / 18.1 s TTFT on the first request,
        #    then 46.8 s / 4.9 s on the next identical one; no cause is asserted, only the variability), so
        #    one measurement cannot place a deadline inside the NEXT request's generation window. The
        #    same request is repeated until two consecutive runs agree (total and TTFT each within 30 %),
        #    at most CAL_MAX_RUNS times; the window is then taken conservatively from that pair: the
        #    latest first token and the earliest completion observed, so a request that behaves like
        #    either run is still cut inside its generation.
        CAL_MAX_RUNS = 5
        runs: list[dict] = []
        diag["calibration"] = runs

        def measure() -> dict:
            code, warm = chat({"messages": long_prompt, "max_tokens": n_predict})
            rec = {
                "code": code,
                "usage": (warm or {}).get("usage"),
                "convoy": (warm or {}).get("convoy"),
            }
            runs.append(rec)
            persist()
            assert code == 200 and warm["usage"]["completion_tokens"] >= 1, warm
            ttft_ms = warm["convoy"]["ttft_ms"]
            assert ttft_ms is not None and ttft_ms > 0, (
                "streamed TTFT is required to prove generation started"
            )
            rec["total_s"] = float(warm["convoy"]["latency_ms"]) / 1000.0
            rec["ttft_s"] = float(ttft_ms) / 1000.0
            rec["tokens_out"] = int(warm["usage"]["completion_tokens"])
            return rec

        def comparable(a: dict, b: dict) -> bool:
            close = lambda x, y: abs(x - y) <= 0.30 * max(x, y)  # noqa: E731
            return close(a["total_s"], b["total_s"]) and close(a["ttft_s"], b["ttft_s"])

        prev = measure()
        pair = None
        for _ in range(CAL_MAX_RUNS - 1):
            cur = measure()
            if comparable(prev, cur):
                pair = (prev, cur)
                break
            prev = cur
        diag["calibration_pairs_comparable"] = pair is not None
        persist()
        assert pair is not None, (
            f"no two consecutive warmed runs agreed within 30 % in {len(runs)} attempts "
            f"(totals {[round(r['total_s'], 2) for r in runs]} s, ttft {[round(r['ttft_s'], 2) for r in runs]} s); "
            "the generation window is not stable enough to place a deadline inside it (see cancel_diag.json)"
        )
        ttft_s = max(pair[0]["ttft_s"], pair[1]["ttft_s"])  # latest first token seen
        total_s = min(pair[0]["total_s"], pair[1]["total_s"])  # earliest completion seen
        gen_s = total_s - ttft_s
        n_out = min(pair[0]["tokens_out"], pair[1]["tokens_out"])
        diag["measured"] = {
            "ttft_s": ttft_s,
            "total_s": total_s,
            "generation_interval_s": gen_s,
            "tokens_out": n_out,
            "from_runs": [runs.index(pair[0]) + 1, runs.index(pair[1]) + 1],
        }
        persist()
        assert n_out >= 24 and gen_s >= 2.0, (
            f"fixture generated only {n_out} tokens over {gen_s:.2f}s; the generation interval is too short "
            "to cancel inside it with margins (see cancel_diag.json)"
        )
        # 2) deadline strictly inside (latest first token, earliest completion): after the first token
        #    by >= 25% of the interval (min 0.75 s), and before completion by the same margin
        margin = max(0.75, 0.25 * gen_s)
        deadline_s = ttft_s + margin
        assert deadline_s <= total_s - margin, (ttft_s, total_s, deadline_s)
        gw.deadline_s = deadline_s
        diag["deadline_s"] = deadline_s
        t0 = time.monotonic()
        code, out = chat({"messages": long_prompt, "max_tokens": n_predict})
        elapsed = time.monotonic() - t0
        gw.deadline_s = 300
        t_spans = [s for s in spans if s["status"] == "timeout"]
        diag["cancel"] = {
            "code": code,
            "body": out,
            "elapsed_s": elapsed,
            "timeout_span": t_spans[-1] if t_spans else None,
            "window": {"ttft_s": ttft_s, "total_s": total_s, "deadline_s": deadline_s},
        }
        persist()
        # the generation window must have been real: a request that finished before the deadline is an
        # honest failure with the comparison recorded, never a pass
        assert code == 504 and out and out["error"]["type"] == "timeout", (
            f"no cancellation happened: HTTP {code} after {elapsed:.2f}s against a deadline of "
            f"{deadline_s:.2f}s placed inside the calibrated window [{ttft_s:.2f}, {total_s:.2f}] s "
            f"(see cancel_diag.json)",
            out,
        )
        assert elapsed >= 0.9 * deadline_s, (elapsed, deadline_s)  # the deadline, not an early error
        assert t_spans, "the cancelled request must emit a timeout span"
        streamed = t_spans[-1]["attrs"].get("tokens_streamed")
        assert isinstance(streamed, int) and streamed >= 1, (
            "the deadline must interrupt ACTIVE native generation: locally observed streamed tokens "
            f"= {streamed!r} (see cancel_diag.json)"
        )
        # 3) ownership: idle proven, or a controlled replacement of the owned child
        idle = sup.slots_idle()
        replaced = False
        if gw.needs_restart or idle is not True:
            gen_before = sup.generation
            stop = sup.stop()
            assert stop["stopped"] is True and sup.state() == "stopped", stop
            start()
            child_pid = sup.proc.pid
            assert sup.generation > gen_before and sup.health(), "replacement child must be fresh and healthy"
            gw.needs_restart = False
            replaced = True
        assert sup.slots_idle() is True
        diag["ownership"] = {
            "idle_after_cancel": idle,
            "controlled_replacement": replaced,
            "needs_restart": gw.needs_restart,
        }
        # 4) production serves again through the same gateway
        code, again = chat(
            {"messages": [{"role": "user", "content": "Reply with one word: hello"}], "max_tokens": 8}
        )
        diag["again"] = {"code": code, "content": ((again or {}).get("choices") or [{}])[0].get("message")}
        persist()
        assert code == 200 and again["choices"][0]["message"]["content"], again
        # 5) final cleanup is part of the verdict: child exit verified, key file and record removed,
        #    the native listener closed
        key_file = sup.api_key_file
        port = sup.port
        gw.set_mode("closed")
        stop = sup.stop()
        diag["cleanup"] = {"stop": stop}
        persist()
        assert stop["stopped"] is True and sup.state() == "stopped", stop
        assert proc_start_ticks(child_pid) is None, "runtime child must be exited and reaped"
        assert key_file is not None and not key_file.exists(), "per-launch api key file must be removed"
        assert not (sup.workdir / "child.json").exists(), "ownership record cleared after a verified stop"
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
    finally:
        gw.stop()
        sup.stop()  # runs on every failure path so no native child outlives the test
        persist()
