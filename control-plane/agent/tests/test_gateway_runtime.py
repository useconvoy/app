from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from convoy_agent.gateway import Gateway
from convoy_agent.hardware import SimulatedSensors
from convoy_agent.runtime import RuntimeError_, RuntimeSupervisor, http_json
from convoy_agent.runtime_args import ConfigError, argv_for, canonical_config, scrub_env


def _call(port, body, headers=None, raw=None, method="POST", path="/v1/chat/completions"):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data if method == "POST" else None,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


@pytest.fixture()
def stack(tmp_path):
    sens = SimulatedSensors(str(tmp_path))
    sup = RuntimeSupervisor(tmp_path / "rt", simulate=True, sensors=sens)
    sup.start(
        release_id="rel_t",
        spec={"config": {}, "model": {"total_bytes": 1117320736}},
        model_path=Path("m.gguf"),
        template_path=None,
        binary=None,
        lib_dir=None,
        health_timeout_s=5,
    )
    spans = []
    gw = Gateway(sup, on_span=spans.append, deadline_s=5)
    port = gw.start()
    gw.set_mode("production")
    yield sup, gw, port, spans
    gw.stop()
    sup.stop()


def test_gateway_contract(stack):
    sup, gw, port, spans = stack
    code, out = _call(
        port,
        {
            "messages": [{"role": "user", "content": "What is the capital of France? One word."}],
            "max_tokens": 8,
        },
    )
    assert (
        code == 200
        and out["choices"][0]["message"]["content"] == "Paris"
        and out["usage"]["completion_tokens"] == 1
    )
    assert (
        spans
        and spans[0]["attrs"]["tokens_in"] > 0
        and spans[0]["attrs"]["runtime_generation"] == sup.generation
    )
    assert (
        _call(
            port,
            {"messages": [{"role": "user", "content": "hi"}]},
            headers={"Origin": "http://localhost:5173"},
        )[0]
        == 403
    )
    assert (
        _call(port, None, raw=b"messages=hi", headers={"Content-Type": "application/x-www-form-urlencoded"})[
            0
        ]
        == 415
    )
    assert _call(port, None, raw=b"{bad", headers={})[0] == 400
    for bad in (
        {"tools": []},
        {"response_format": {"type": "json"}},
        {"stream": True},
        {"chat_template_kwargs": {}},
        {"reasoning_effort": "low"},
    ):
        assert _call(port, {"messages": [{"role": "user", "content": "hi"}], **bad})[0] == 400, bad
    assert _call(port, {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]})[0] == 400
    assert _call(port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 129})[0] == 400
    assert _call(port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 0})[0] == 400
    code, out = _call(port, {"messages": [{"role": "user", "content": "word " * 2100}], "max_tokens": 8})
    assert code == 400 and out["error"]["type"] == "context_length_exceeded"
    assert _call(port, None, method="OPTIONS")[0] == 403
    # raw runtime is not reachable without the per-launch key; the key file is 0600 and never in argv output
    assert http_json(f"{sup.base_url}/props", None)[0] == 401
    assert http_json(f"{sup.base_url}/props", sup.api_key)[0] == 200
    assert (sup.api_key_file.stat().st_mode & 0o777) == 0o600
    assert not any(a.endswith("runtime.key") for a in sup.collect_evidence()["argv"])
    gw.set_mode("closed")
    assert _call(port, {"messages": [{"role": "user", "content": "hi"}]})[0] == 503
    gw.set_mode("eval")
    assert _call(port, {"messages": [{"role": "user", "content": "hi"}]})[0] == 503
    assert (
        _call(
            port,
            {"messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Convoy-Eval-Token": gw.eval_token},
        )[0]
        == 200
    )


def test_gateway_queue_overload_and_deadline_ownership(tmp_path):
    sens = SimulatedSensors(str(tmp_path))
    sup = RuntimeSupervisor(tmp_path / "rt", simulate=True, sensors=sens, sim_faults={"latency_ms": 400})
    sup.start(
        release_id="rel_t",
        spec={"config": {}, "model": {"total_bytes": 1}},
        model_path=Path("m"),
        template_path=None,
        binary=None,
        lib_dir=None,
        health_timeout_s=5,
    )
    gw = Gateway(sup, queue_depth=1, deadline_s=0.25)
    port = gw.start()
    gw.set_mode("production")
    try:
        import threading

        results = []

        def one():
            results.append(_call(port, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})[0])

        ts = [threading.Thread(target=one) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert 503 in results or 504 in results  # overload or deadline; never a hang
        assert gw.stats["overloaded"] + gw.stats["timeouts"] >= 1
        # after a deadline the gateway either confirmed idle or flagged a controlled restart
        time.sleep(0.6)
        assert sup.slots_idle() is True or gw.needs_restart
    finally:
        gw.stop()
        sup.stop()


def test_runtime_stop_is_verified_and_faults_are_honest(tmp_path):
    sens = SimulatedSensors(str(tmp_path))
    sup = RuntimeSupervisor(tmp_path / "rt", simulate=True, sensors=sens, sim_faults={"health_fail": True})
    with pytest.raises(RuntimeError_) as e:
        sup.start(
            release_id="r",
            spec={"config": {"health_timeout_s": 10}, "model": {"total_bytes": 1}},
            model_path=Path("m"),
            template_path=None,
            binary=None,
            lib_dir=None,
            health_timeout_s=1,
        )
    assert e.value.code == "HEALTH_TIMEOUT" and sup.state() == "stopped"
    sup2 = RuntimeSupervisor(tmp_path / "rt2", simulate=True, sensors=sens)
    ev = sup2.start(
        release_id="r",
        spec={"config": {}, "model": {"total_bytes": 1}},
        model_path=Path("m"),
        template_path=None,
        binary=None,
        lib_dir=None,
        health_timeout_s=5,
    )
    assert ev["backend"] == "simulated" and ev["intended_backend_ok"] is None and ev["simulated"] is True
    assert sup2.stop()["stopped"] is True and sup2.state() == "stopped" and not sup2.api_key_file.exists()


def test_runtime_config_strictness_and_argv():
    for bad in (
        {"fit": "on"},
        {"context_shift": True},
        {"cache_ram_mib": 8192},
        {"parallel": 2},
        {"batch_size": -4},
        {"ubatch_size": 4096, "batch_size": 256},
        {"flash_attn": "maybe"},
        {"unknown_setting": 1},
        {"temperature": "0.5"},
        {"seed": True},
        {"n_predict": 2048},
    ):
        with pytest.raises(ConfigError):
            canonical_config(bad)
    argv = argv_for(
        {"flash_attn": "off", "gpu_layers": 0},
        model_path="/m.gguf",
        template_path="/t.jinja",
        host="127.0.0.1",
        port=1,
        api_key_file="/k",
    )
    s = " ".join(argv)
    for flag in (
        "--fit off",
        "--parallel 1",
        "--ctx-size 2048",
        "--n-predict 128",
        "--batch-size 256",
        "--ubatch-size 128",
        "--gpu-layers 0",
        "--cache-ram 0",
        "--no-context-shift",
        "--flash-attn off",
        "--offline",
        "--no-webui",
        "--cors-origins",
        "--no-cors-credentials",
        "--api-key-file /k",
        "--chat-template-file /t.jinja",
    ):
        assert flag in s, flag
    env = scrub_env(
        {
            "PATH": "/bin",
            "LLAMA_ARG_N_GPU_LAYERS": "0",
            "LLAMA_SERVER_DEBUG_FAKE_TIMING": "1",
            "HF_TOKEN": "x",
            "HOME": "/h",
        }
    )
    assert env == {"PATH": "/bin", "HOME": "/h"}


@pytest.mark.parametrize("buffer,backend,intended_cuda", [
    ("CPU", "CPU", False),
    ("CPU_Mapped", "CPU", False),
    ("CPU_REPACK", "CPU", False),
    ("CUDA0", "CUDA0", True),
    ("Metal", "Metal", False),
    ("Vulkan0", "Vulkan0", False),
    ("CPU_UNKNOWN", None, None),
    ("CPU_REPACKED", None, None),
    ("XPU", None, None),
])
def test_native_buffer_evidence_recognizes_only_known_backends(tmp_path, monkeypatch, buffer, backend, intended_cuda):
    sup = RuntimeSupervisor(tmp_path / "rt", simulate=False)
    binary = tmp_path / "llama-server"
    binary.write_bytes(b"metadata-only test binary")
    sup.argv = [str(binary)]
    lines = [f"0.00.122.355 I load_tensors:   {buffer} model buffer size =   877.31 MiB"]
    if backend in {"CUDA0", "Metal", "Vulkan0"}:
        lines.insert(0, "load_tensors: offloaded 29/29 layers to GPU")
    monkeypatch.setattr(sup, "props", lambda: {})
    monkeypatch.setattr(sup, "log_lines", lambda: lines)

    evidence = sup.collect_evidence()

    assert evidence["backend"] == backend
    assert evidence["intended_backend_ok"] is intended_cuda
