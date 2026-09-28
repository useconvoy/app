# ruff: noqa: F811
"""Bound completions use one captured launch identity across real gateway admission."""

import copy
import time

import pytest
from convoy_agent import gateway as gateway_module
from convoy_agent.runtime import RuntimeError_, RuntimeSupervisor
from test_gateway_runtime import _call, stack  # noqa: F401

CHAT = {"messages": [{"role": "user", "content": "What is the capital of France? One word."}], "max_tokens": 8}


def test_bound_completion_echoes_snapshot_and_rejects_change_before_work(stack):
    sup, gw, port, _ = stack
    code, identity = _call(port, None, method="GET", path="/v1/runtime-identity")
    assert code == 200 and identity["simulated"] is True  # fixture is never passed as real planner evidence
    assert identity["runtime_generation"] == sup.generation
    request = {"request": CHAT, "runtime_identity": identity, "deadline_s": 2}
    code, result = _call(port, request, path="/v1/bound-completions")
    assert code == 200 and result["convoy"]["runtime_identity"] == identity
    wrong = copy.deepcopy(identity)
    wrong["binary_sha256"] = "0" * 64
    served = gw.stats["served"]
    assert _call(port, {**request, "runtime_identity": wrong}, path="/v1/bound-completions")[0] == 409
    assert gw.stats["served"] == served
    gw._slot.acquire()
    started = time.monotonic()
    try:
        assert _call(port, request, path="/v1/bound-completions")[0] == 503
        assert time.monotonic() - started < 0.5  # no new planning queue behind chat
    finally:
        gw._slot.release()


def test_admission_change_during_completion_never_returns_a_bound_success(stack, monkeypatch):
    _, gw, port, _ = stack
    identity = gw.runtime_identity()
    original = gateway_module._completion_with_ttft

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        gw.set_mode("production")  # a new admission epoch, even if the same release returns
        return result

    monkeypatch.setattr(gateway_module, "_completion_with_ttft", changed)
    code, result = _call(port, {"request": CHAT, "runtime_identity": identity, "deadline_s": 2},
                        path="/v1/bound-completions")
    assert code == 503 and "choices" not in result and gw.stats["served"] == 0


def test_runtime_hash_mismatch_refuses_native_launch(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"changed model bytes")
    supervisor = RuntimeSupervisor(tmp_path / "runtime", simulate=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("native process must not start with a mismatched model")

    monkeypatch.setattr("convoy_agent.runtime.subprocess.Popen", forbidden)
    with pytest.raises(RuntimeError_, match="model bytes differ"):
        supervisor.start(release_id="r", spec={"config": {}, "model": {"file": {"sha256": "a" * 64}}},
                         model_path=model, template_path=None, binary=None, lib_dir=None)
    assert supervisor.generation == 0 and supervisor.started_at is None
    assert supervisor.run_intervals == [] and supervisor.up_time(0, supervisor.clock()) == 0
    assert supervisor.api_key is None and supervisor.api_key_file is None
    assert not (supervisor.workdir / "runtime.key").exists()
    with pytest.raises(RuntimeError_, match="provenance unavailable"):
        supervisor.provenance()
