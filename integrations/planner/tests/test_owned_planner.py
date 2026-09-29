"""Owner lifecycle checks use tiny foreground children; no model-quality claims."""

import copy
import json
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import psutil
import pytest
from convoy_agent.owned_process import OwnedProcess
from convoy_contracts.execution import canonical_digest

# Imported pytest fixture; test parameters intentionally use its registration name.
# ruff: noqa: F811
from convoy_planner import owned
from convoy_planner.artifact import artifact_digest
from fastapi.testclient import TestClient
from test_planner import PROBE, Backend, manifest
from test_signed_planner import keys as signing_keys  # noqa: F401
from test_signed_planner import verifier


def receipt():
    return {"host": {"system": "Linux", "machine": "aarch64"},
            "source": {"repo": "https://github.com/ggml-org/llama.cpp", "commit": "f" * 40, "dirty": False},
            "archive": {"sha256": "b" * 64}, "binary": {"sha256": "c" * 64},
            "model": {"sha256": "a" * 64, "bytes": 123}}


@pytest.mark.parametrize("name", owned.FORBIDDEN_ENV)
def test_authority_conflicts_reject_before_reading_assets(monkeypatch, tmp_path, name):
    monkeypatch.setenv(name, "")
    args = SimpleNamespace(mode="inspect", assets=tmp_path / "never-read.json")
    with pytest.raises(ValueError, match="only its public"):
        owned.preflight(args)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("host,build", [("Darwin", "Linux"), ("Linux", "Darwin")])
def test_assets_must_match_linux_host(monkeypatch, tmp_path, host, build):
    for name in owned.FORBIDDEN_ENV:
        monkeypatch.delenv(name, raising=False)
    value = receipt()
    value["host"]["system"] = build
    monkeypatch.setattr(owned, "read_json", lambda _: value)
    monkeypatch.setattr(owned, "validate_text_receipt", lambda value: value)
    monkeypatch.setattr(owned.platform, "system", lambda: host)
    monkeypatch.setattr(owned.platform, "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="Linux aarch64"):
        owned.preflight(SimpleNamespace(mode="inspect", assets=tmp_path))


class Supervisor:
    def __init__(self, *_, **__):
        self.running = True

    def state(self):
        return "running" if self.running else "crashed"

    def stop(self, **_):
        self.running = False
        return {"stopped": True}


class Gateway:
    def __init__(self, *_, **__):
        self.mode, self.needs_restart = "production", False
        self.thread, self.server = threading.current_thread(), None

    def set_mode(self, mode):
        self.mode = mode

    def stop(self):
        self.server = None
        self.thread = None

    def drain(self, _):
        return True


@pytest.fixture
def lifetime(monkeypatch, tmp_path):
    monkeypatch.setattr(owned, "RuntimeSupervisor", Supervisor)
    monkeypatch.setattr(owned, "Gateway", Gateway)
    value = owned.OwnedPlanner(receipt(), tmp_path, None, ctx_size=2048, stop_timeout_s=3)
    value.backend = Backend()
    value.identity = copy.deepcopy(value.backend.identity)
    value.evidence["planner"] = manifest(value.backend)["planner"]
    value.accepting = True
    value.starting = False
    yield value
    value.close()


@pytest.mark.parametrize("options,expected", [({}, (2, 2)), ({"threads": 1, "threads_batch": 3}, (1, 3))])
def test_owned_cpu_counts_reach_native_config_and_artifact(monkeypatch, tmp_path, options, expected):
    class CapturedLaunch(Exception):
        pass

    configs = []
    class CapturingSupervisor(Supervisor):
        def start(self, **launch):
            configs.append(launch["spec"]["config"])
            raise CapturedLaunch()

    monkeypatch.setattr(owned, "RuntimeSupervisor", CapturingSupervisor)
    monkeypatch.setattr(owned, "Gateway", Gateway)
    monkeypatch.setattr(owned, "prepare_text_assets", lambda *_: (tmp_path / "model", tmp_path / "binary"))
    value = owned.OwnedPlanner(receipt(), tmp_path, None, ctx_size=2048, stop_timeout_s=3, **options)
    with pytest.raises(CapturedLaunch):
        value.start(120)
    config, = configs
    assert (config["threads"], config["threads_batch"]) == expected
    assert config["request_deadline_s"] == 30 and config["n_predict"] == 128 and config["gpu_layers"] == 0
    identity = Backend().identity
    identity["config_sha256"] = canonical_digest(config)
    pinned_artifact = artifact_digest(identity)
    identity["config_sha256"] = canonical_digest({**config, "threads_batch": expected[1] + 1})
    assert artifact_digest(identity) != pinned_artifact


def test_readiness_and_admission_reject_changed_identity(lifetime, signing_keys):
    bundle = manifest(lifetime.backend)
    app = lifetime.application(bundle, {"grant_verifier": verifier(signing_keys), "probe_token": PROBE})
    with TestClient(app) as client:
        assert client.get("/ready").json()["ready"] is True
        lifetime.backend.identity["gateway_incarnation"] = "replaced"
        assert client.get("/ready").status_code == 503
        assert client.post("/v1/probe", json={}, headers={"Authorization": "Bearer " + PROBE}).status_code == 503
        assert client.get("/health").status_code == 503
    assert lifetime.failure == "owned_runtime_identity_unavailable"
    assert lifetime.stop_requested.is_set()


def test_manifest_mismatch_never_binds_listener(lifetime, signing_keys):
    bundle = manifest(lifetime.backend)
    bundle["planner"]["artifact_sha256"] = "9" * 64
    with pytest.raises(ValueError, match="immutable manifest"):
        lifetime.application(bundle, {"grant_verifier": verifier(signing_keys), "probe_token": PROBE})
    assert lifetime.listener is None


def test_native_loss_stops_real_listener_without_restart(lifetime, signing_keys):
    app = lifetime.application(manifest(lifetime.backend), {"grant_verifier": verifier(signing_keys), "probe_token": PROBE})
    lifetime.serve(app, "127.0.0.1", 0)
    port = lifetime.evidence["planner_port"]
    assert httpx.get(f"http://127.0.0.1:{port}/ready").status_code == 200
    lifetime.supervisor.running = False
    lifetime.monitor()
    assert lifetime.failure == "owned_runtime_lost"
    assert all(lifetime.close().values())
    assert not lifetime.server_thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_gateway_thread_start_failure_closes_actual_bound_listener(tmp_path, monkeypatch):
    # Real Gateway binds before Thread.start. BaseServer.shutdown would hang here.
    with OwnedProcess(tmp_path / "owner") as owner:
        value = owned.OwnedPlanner(receipt(), tmp_path, owner, ctx_size=2048, stop_timeout_s=2)
        original = threading.Thread.start
        def fail_gateway(thread):
            if thread.name == "convoy-gateway":
                raise RuntimeError("injected")
            return original(thread)
        monkeypatch.setattr(threading.Thread, "start", fail_gateway)
        with pytest.raises(RuntimeError, match="injected"):
            value.gateway.start()
        port = value.gateway.port
        started = time.monotonic()
        assert all(value.close().values())
        assert time.monotonic() - started < 2
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.parametrize("stop", ["signal", "timeout"])
def test_startup_interrupt_unwinds_real_native_lock_and_reaps_child(tmp_path, stop):
    # Keep the real supervisor/start lock, OwnedProcess and output reader. Replace
    # only native argv and warmup: this is a sleeping Python child, not inference.
    script = r'''
import json, sys, time
from pathlib import Path
from types import SimpleNamespace
from convoy_agent import runtime
from convoy_planner import owned
root = Path(sys.argv[1])
receipt = {"source": {"repo": "test", "commit": "f"*40, "dirty": False},
           "archive": {"sha256": "b"*64}, "binary": {"sha256": "c"*64},
           "model": {"sha256": "a"*64, "bytes": 1}}
model = root / "model"
model.write_text("x")
receipt["model"]["sha256"] = owned.sha256(model)
owned.preflight = lambda args: (receipt, None, {})
owned.prepare_text_assets = lambda receipt, output: (model, Path(sys.executable))
runtime.argv_for = lambda cfg, **kw: [sys.executable, "-c", "import time; time.sleep(120)", kw["api_key_file"]]
def warming(self, timeout):
    (root / "warming.json").write_text(json.dumps({"pid": self.proc.pid}))
    while True:
        time.sleep(.05)
runtime.RuntimeSupervisor.wait_healthy = warming
args = SimpleNamespace(mode="inspect", output=root/"output", ctx_size=2048, threads=2, threads_batch=2,
                       startup_timeout_s=float(sys.argv[2]), stop_timeout_s=3)
raise SystemExit(owned.run(args))
'''
    process = subprocess.Popen([sys.executable, "-c", script, str(tmp_path), "10" if stop == "signal" else "1"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    native = None
    try:
        deadline = time.monotonic() + 8
        while not (tmp_path / "warming.json").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                pytest.fail("startup child did not reach warmup: " + process.communicate(timeout=1)[1])
            time.sleep(.02)
        native = psutil.Process(json.loads((tmp_path / "warming.json").read_text())["pid"])
        started = time.monotonic()
        if stop == "signal":
            process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=6)
        assert time.monotonic() - started < 5
        assert process.returncode == (0 if stop == "signal" else 1), stderr
        assert not native.is_running()
        evidence = json.loads((tmp_path / "output/result.json").read_text())
        assert all(evidence["cleanup"].values())
        assert evidence["status"] == ("interrupted" if stop == "signal" else "failed")
        if stop == "timeout":
            assert evidence["failure"] == "startup_timeout"
        record = json.loads((tmp_path / "output/native/owner/process.json").read_text())
        assert record["state"] == "stopped"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=6)
        if native is not None and native.is_running():
            # psutil retains and rechecks this exact child's creation identity.
            native.kill()
            native.wait(timeout=3)


def test_cleanup_thread_failure_still_terminates_native(lifetime, monkeypatch):
    def failed_start(thread):
        raise RuntimeError("thread capacity unavailable")
    lifetime.gateway.thread = None
    monkeypatch.setattr(threading.Thread, "start", failed_start)
    result = lifetime.close()
    assert result["native_stopped"] is True
    assert not lifetime.supervisor.running
    assert result["gateway_closed"] is False
    assert lifetime.failure == "cleanup_unresolved"


@pytest.mark.parametrize("purpose", ["action", "missing"])
def test_serve_needs_planner_verifier_before_asset_loading(signing_keys, monkeypatch, tmp_path, purpose):
    for name in owned.FORBIDDEN_ENV:
        monkeypatch.delenv(name, raising=False)
    path = signing_keys.paths["action"] if purpose == "action" else tmp_path / "missing.json"
    monkeypatch.setenv("CONVOY_PLANNER_VERIFICATION_KEYS_FILE", str(path))
    monkeypatch.setenv("CONVOY_PLANNER_PROBE_TOKEN", PROBE)
    args = SimpleNamespace(mode="serve", manifest=tmp_path / "never-read.json", assets=tmp_path / "no-assets")
    with pytest.raises(ValueError):
        owned.preflight(args)


@pytest.mark.parametrize("placement", ["development-local", "development-remote-cpu", "development-jetson-lan"])
def test_cpu_placement_is_a_declaration_not_hosted_evidence(lifetime, signing_keys, monkeypatch, tmp_path, placement):
    for name in owned.FORBIDDEN_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVOY_PLANNER_VERIFICATION_KEYS_FILE", str(signing_keys.paths["planner"]))
    monkeypatch.setenv("CONVOY_PLANNER_PROBE_TOKEN", PROBE)
    bundle = manifest(lifetime.backend)
    bundle["placement"]["planner"] = placement
    path = tmp_path / "release.json"
    path.write_text(json.dumps(bundle))
    monkeypatch.setattr(owned, "validate_text_receipt", lambda _: receipt())
    monkeypatch.setattr(owned.platform, "system", lambda: "Linux")
    monkeypatch.setattr(owned.platform, "machine", lambda: "aarch64")
    args = SimpleNamespace(mode="serve", manifest=path, assets=path)
    if placement == "development-jetson-lan":
        with pytest.raises(ValueError, match="CPU placement declaration"):
            owned.preflight(args)
    else:
        _, validated, authorization = owned.preflight(args)
        lifetime.application(validated, authorization)
        assert lifetime.evidence["declared_placement"] == bundle["placement"]
        assert "no cloud" in lifetime.evidence["scope"]


def test_owner_cannot_reinterpret_an_abbreviated_manifest_after_wrapper_validation(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["owned", "serve", "--assets", "/assets", "--output", "/state", "--man", "/other"])
    monkeypatch.setattr(owned, "run", lambda _: pytest.fail("invalid arguments reached native owner"))
    with pytest.raises(SystemExit) as error:
        owned.main()
    assert error.value.code == 2
    assert "unrecognized arguments: --man" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["result", "exception"])
def test_native_cleanup_retains_bounded_failure_reason(lifetime, monkeypatch, failure):
    def stop(**_):
        if failure == "exception":
            raise RuntimeError("private path and credential must never be retained")
        return {"stopped": False, "method": "owned_process", "reason": "child_exit_unverified",
                "seconds": 0.25, "pid": 123, "private_detail": "must not appear"}
    monkeypatch.setattr(lifetime.supervisor, "stop", stop)
    assert lifetime.close()["native_stopped"] is False
    expected = ({"stopped": False, "method": "owned_process", "reason": "child_exit_unverified", "seconds": 0.25}
                if failure == "result" else {"stopped": False, "error_type": "RuntimeError"})
    assert lifetime.evidence["native_stop"] == expected
    assert lifetime.failure == "cleanup_unresolved"
