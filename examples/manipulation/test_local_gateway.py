"""Shutdown evidence must not claim success when owned resources remain active."""

import json
import sys
from types import SimpleNamespace

import local_gateway
import pytest


@pytest.mark.parametrize("native_stopped,gateway_drained", [(False, True), (True, False)])
def test_incomplete_cleanup_records_failure(monkeypatch, tmp_path, native_stopped, gateway_drained):
    output = tmp_path / "gateway"
    assets = tmp_path / "assets.json"
    assets.write_text(json.dumps({"model": {"sha256": "a" * 64, "bytes": 1},
                                 "archive": {"sha256": "b" * 64}, "source": {"commit": "test"}}))
    native = {"simulated": False, "backend": "CPU", "build_info": "test", "binary_sha256": "c" * 64,
              "chat_template_sha256": "d" * 64, "gpu_offloaded_layers": 0, "gpu_total_layers": 1,
              "n_ctx": 2048, "total_slots": 1}
    identity = {"model_sha256": "a" * 64, "runtime_artifact_sha256": "b" * 64,
                "binary_sha256": "c" * 64, "template_sha256": "d" * 64, "config_sha256": "e" * 64,
                "implementation_sha256": {"gateway.py": "f" * 64, "runtime.py": "f" * 64},
                "simulated": False, "release_id": "test", "runtime_generation": 1,
                "gateway_incarnation": "test", "gateway_epoch": 1}
    supervisor = SimpleNamespace(config={}, start=lambda **_: native,
                                 stop=lambda **_: {"stopped": native_stopped, "method": "test"})
    gateway = SimpleNamespace(port=9100, stats={}, start=lambda: None, stop=lambda: None,
                              set_mode=lambda _: None, runtime_identity=lambda **_: identity,
                              drain=lambda _: gateway_drained)
    # Exercise the entry point and durable evidence without launching a model or socket.
    monkeypatch.setattr(local_gateway, "prepare", lambda *_: (tmp_path / "model", tmp_path / "binary"))
    monkeypatch.setattr(local_gateway, "RuntimeSupervisor", lambda *_, **__: supervisor)
    monkeypatch.setattr(local_gateway, "Gateway", lambda *_, **__: gateway)
    monkeypatch.setattr(local_gateway.threading, "Event", lambda: SimpleNamespace(wait=lambda _: True))
    monkeypatch.setattr(local_gateway.signal, "signal", lambda *_: None)
    monkeypatch.setattr(sys, "argv", ["local_gateway.py", "--assets", str(assets), "--output", str(output)])

    with pytest.raises(RuntimeError, match="owned gateway cleanup incomplete"):
        local_gateway.main()

    result = json.loads((output / "gateway-result.json").read_text())
    assert result["status"] == "failed"
    assert result["cleanup"]["stopped"] is native_stopped
    assert result["gateway_drained"] is gateway_drained
    assert result["cleanup_error"] == "owned gateway cleanup incomplete"
