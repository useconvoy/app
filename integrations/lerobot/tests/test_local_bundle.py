"""Real foreground children exercise activation ownership; heavy model loads are stubbed."""

from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from convoy_agent.owned_process import OwnedProcess
from convoy_agent.runtime import LOG_FILE_CAP_BYTES, LOG_HEAD_BYTES, LOG_RING_BYTES, _LogReader
from convoy_contracts.execution import canonical_digest
from convoy_contracts.pairing import PAIRED_PROFILE, PLANNER_RUNTIME
from convoy_planner.artifact import artifact_descriptor

from convoy_lerobot import local_bundle
from convoy_lerobot.local_recipe import LocalRecipe, PreparedRecipe

SLEEP = "import time; print('owned child ready', flush=True); time.sleep(120)"


@pytest.fixture
def bundles(tmp_path, monkeypatch):
    identities, registry = {}, {}
    implementation = {name: "a" * 64 for name in (
        "gateway.py", "runtime.py", "owned_process.py", "runtime_args.py", "psutil-7.2.2",
    )}
    for context in (2048, 4096):
        identity = {
            "model_sha256": "b" * 64, "runtime_artifact_sha256": "c" * 64,
            "binary_sha256": "d" * 64, "template_sha256": "e" * 64,
            "config_sha256": canonical_digest({"ctx_size": context}), "implementation_sha256": implementation,
            "simulated": False, "release_id": "old-qualified", "runtime_generation": 1,
            "gateway_incarnation": "old-incarnation", "gateway_epoch": 1,
        }
        manifest = {"profile": PAIRED_PROFILE, "action_manifest": {
            "policy": {"runtime": "test-policy", "artifact_sha256": "f" * 64}},
            "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": canonical_digest(artifact_descriptor(identity))}}
        identities[context] = identity
        registry[canonical_digest(manifest)] = LocalRecipe(manifest, identity, {}, tmp_path,
                                                          {"ctx_size": context})
    monkeypatch.setattr(local_bundle, "load_registry", lambda _: registry)

    def preflight(recipe, output, **_):
        output.mkdir(parents=True, mode=0o700)
        return PreparedRecipe(canonical_digest(recipe.manifest), recipe.manifest, recipe.gateway_identity,
                              recipe.native_config, recipe.action_assets, tmp_path / "model",
                              tmp_path / "binary", tmp_path / "lib", output / "runtime")

    monkeypatch.setattr(local_bundle, "preflight", preflight)

    class Supervisor:
        def __init__(self, workdir, *, process_owner):
            self.owner, self.proc, self.identity = process_owner, None, None
            self.base_url = "http://127.0.0.1:12346"

        def start(self, **args):
            self.identity = copy.deepcopy(identities[args["spec"]["config"]["ctx_size"]])
            self.identity.update(release_id=args["release_id"])
            self.proc = self.owner.start(lambda marker: [sys.executable, "-c", SLEEP, marker])
            return {"simulated": False, "backend": "CPU"}

        def health(self):
            return self.proc is not None and self.proc.poll() is None

        def stop(self, *, deadline):
            result = self.owner.stop(terminate_timeout=min(30, max(0, deadline - time.monotonic()) / 2),
                                     kill_timeout=min(30, max(0, deadline - time.monotonic()) / 2))
            stopped = self.proc is None or self.proc.poll() is not None
            if stopped:
                self.proc = None
            return {**result, "stopped": stopped}

    class Gateway:
        def __init__(self, supervisor, **_):
            self.sup = supervisor
            self.implementation_sha256 = implementation
            self.mode, self.server, self.thread = "closed", None, None
            self.needs_restart, self.port = False, 12345
            self.incarnation = uuid.uuid4().hex

        def start(self):
            self.server, self.thread = object(), SimpleNamespace(ident=1, is_alive=lambda: True)

        def set_mode(self, mode):
            self.mode = mode

        def runtime_identity(self, **_):
            return {**self.sup.identity, "gateway_incarnation": self.incarnation}

        def stop(self):
            self.server = None

        def drain(self, _):
            return True

    monkeypatch.setattr(local_bundle, "RuntimeSupervisor", Supervisor)
    monkeypatch.setattr(local_bundle, "Gateway", Gateway)
    original_spawn = local_bundle.LocalBundleOwner._spawn_component

    def tiny_spawn(owner, role, prepared, attempt, port):
        # Real OwnedProcess + Popen + output-drain paths; no heavyweight model.
        child = owner._owners[role].start(lambda marker: [sys.executable, "-c", SLEEP, marker],
                                         env=owner._environment(role, prepared), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT)
        owner._children[role] = child
        reader = _LogReader(child.stdout, attempt / f"{role}.log", head_bytes=LOG_HEAD_BYTES,
                            ring_bytes=LOG_RING_BYTES, file_cap=LOG_FILE_CAP_BYTES)
        owner._readers[role] = reader
        reader.start()

    monkeypatch.setattr(local_bundle.LocalBundleOwner, "_spawn_component", tiny_spawn)

    class Client:
        def __init__(self, base_url, token):
            self.base_url, self.token = base_url, token

        def probe(self, digest, profile):
            manifest = registry[digest].manifest
            if self.token == "w" * 32:
                return {"ready": True, "release_digest": digest, "profile": profile,
                        **manifest["action_manifest"]["policy"]}
            return {"ready": True, "release_digest": digest, "profile": profile,
                    "runtime": PLANNER_RUNTIME,
                    "planner_artifact_sha256": manifest["planner"]["artifact_sha256"],
                    "planner_incarnation": "planner-process", "runtime_generation": 1}

    monkeypatch.setattr(local_bundle, "WorkerHTTP", Client)
    monkeypatch.setattr(local_bundle, "PlannerHTTP", Client)
    options = {"directory": tmp_path / "installation", "registry_path": tmp_path / "registry.json",
               "worker_execution_secret": "action-secret-" + "a" * 32, "planner_execution_secret": "planner-secret-" + "b" * 32,
               "worker_probe_token": "w" * 32, "planner_probe_token": "p" * 32,
               "startup_timeout_s": 3, "shutdown_timeout_s": 3}
    deployments = [{"id": str(index), "generation": index, "release": {"digest": digest,
                    "manifest": recipe.manifest}} for index, (digest, recipe) in enumerate(registry.items(), 1)]
    return SimpleNamespace(options=options, deployments=deployments, preflight=preflight,
                           original_spawn=original_spawn, registry=registry)


def prepare(owner, deployment):
    return owner.prepare(deployment, deployment["release"]["manifest"])


def configure_public_keys(bundles, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    for role, purpose in (("worker", "action"), ("planner", "planner")):
        del bundles.options[f"{role}_execution_secret"]
        public_key = Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        path = tmp_path / (purpose + ".json")
        path.write_text(json.dumps({"schema_version": 1, "issuer": "test-api", "audience": purpose,
                                    "purpose": purpose, "keys": [{"kid": purpose, "public_key_pem": public_key}]}))
        path.chmod(0o600)
        bundles.options[f"{role}_verification_keys_file"] = path


def processes(owner):
    return [owner._supervisor.proc, *owner._children.values()]


def state(owner):
    return json.loads(owner._state_path.read_text())


def test_same_live_binding_reuses_children_and_replacement_changes_identity(bundles, monkeypatch):
    first, second = bundles.deployments
    bundles.options["shutdown_timeout_s"] = 600  # helper signal waits still must remain <=30s
    ports = iter((12341, 12341, 12342, 12343, 12344))
    monkeypatch.setattr(local_bundle, "_port", lambda: next(ports))
    with local_bundle.LocalBundleOwner(**bundles.options) as owner:
        a = prepare(owner, first)
        original = processes(owner)
        readers = list(owner._readers.values())
        assert a.worker.base_url != a.planner.base_url
        assert prepare(owner, first) is a
        assert processes(owner) == original
        b = prepare(owner, second)
        assert b.binding_id != a.binding_id
        assert all(child.poll() is not None for child in original)
        assert all(not reader.is_alive() for reader in readers)
        assert b.observation.release_digest == second["release"]["digest"]
        assert state(owner)["previous_verified_bundle"]["binding_id"] == b.binding_id
        current = processes(owner)
    assert all(child.poll() is not None for child in current)
    assert state(owner)["phase"] == "stopped"
    assert all(state(owner)["cleanup"].values())
    for role in ("native", "worker", "planner"):
        assert json.loads((owner.directory / role / "process.json").read_text())["state"] == "stopped"
    assert not any(value in owner._state_path.read_text() for value in owner._credentials.values())


@pytest.mark.parametrize("fault", ["unknown", "corrupt"])
def test_preflight_failure_leaves_a_alive_and_never_returns_it_for_b(bundles, monkeypatch, fault):
    first, second = bundles.deployments
    with local_bundle.LocalBundleOwner(**bundles.options) as owner:
        a = prepare(owner, first)
        original = processes(owner)
        if fault == "unknown":
            del owner._registry[second["release"]["digest"]]
        else:
            def corrupt(*_, **__):
                raise ValueError("private invalid model path must not enter state")
            monkeypatch.setattr(local_bundle, "preflight", corrupt)
        with pytest.raises(local_bundle.BundleError, match="bundle_preflight_failed"):
            prepare(owner, second)
        assert all(child.poll() is None for child in original)
        assert prepare(owner, first) is a
        assert state(owner)["phase"] == "preflight_failed"
        assert state(owner)["previous_verified_bundle"]["binding_id"] == a.binding_id
        with pytest.raises(local_bundle.BundleError, match="requires_new_deployment"):
            prepare(owner, second)
        assert "private invalid" not in owner._state_path.read_text()


def test_failed_partial_warmup_attempts_all_cleanup_and_retains_unresolved_handle(bundles, monkeypatch):
    owner = local_bundle.LocalBundleOwner(**bundles.options).__enter__()
    original_stop = owner._stop_component
    retained = []

    def failed_warm(*_):
        retained.extend(processes(owner))
        # Simulate failed output draining for a still-owned action process.
        monkeypatch.setattr(owner, "_stop_component", lambda role, deadline:
                            False if role == "worker" else original_stop(role, deadline))
        raise RuntimeError("private warmup failure")

    monkeypatch.setattr(owner, "_wait_ready", failed_warm)
    try:
        with pytest.raises(local_bundle.BundleError, match="bundle_cleanup_unresolved"):
            prepare(owner, bundles.deployments[0])
        assert owner._binding is None
        assert retained[0].poll() is not None and retained[2].poll() is not None
        assert retained[1].poll() is None and owner._children["worker"] is retained[1]
        assert state(owner)["cleanup"] == {"gateway": True, "planner": True, "worker": False, "native": True}
        with pytest.raises(local_bundle.BundleError, match="bundle_cleanup_unresolved"):
            owner.close()
        assert owner._stack is not None
    finally:
        monkeypatch.setattr(owner, "_stop_component", original_stop)
        owner.close()
    assert all(child.poll() is not None for child in retained)
    assert state(owner)["phase"] == "stopped"


def test_dead_child_forces_new_binding_and_restart_never_adopts_disk_ready(bundles):
    deployment = bundles.deployments[0]
    with local_bundle.LocalBundleOwner(**bundles.options) as owner:
        first = prepare(owner, deployment)
        original = processes(owner)
        original[1].terminate()  # retained exact Popen, never a PID lookup
        original[1].wait(timeout=3)
        second = prepare(owner, deployment)
        assert first.binding_id != second.binding_id
        assert all(child.poll() is not None for child in original)
    with local_bundle.LocalBundleOwner(**bundles.options) as successor:
        assert successor._binding is None
        assert successor._children == {}
        assert prepare(successor, deployment).binding_id not in {first.binding_id, second.binding_id}


def test_entry_and_unused_close_leave_previous_owned_child_untouched(bundles):
    directory = bundles.options["directory"]
    directory.mkdir(mode=0o700)
    role = directory / "worker"
    with OwnedProcess(role) as previous:
        child = previous.start(lambda marker: [sys.executable, "-c", SLEEP, marker])
    try:
        with local_bundle.LocalBundleOwner(**bundles.options) as owner:
            assert owner._binding is None
            assert child.poll() is None
        assert child.poll() is None
    finally:
        with OwnedProcess(role) as recovery:
            recovery.stop()
        child.wait(timeout=3)


@pytest.mark.parametrize("public_only", [False, True])
def test_fixed_launcher_records_manifest_and_scopes_child_environment(bundles, monkeypatch, tmp_path, public_only):
    monkeypatch.setenv("CONVOY_DATABASE_URL", "private-database")
    monkeypatch.setenv("CONVOY_ADMIN_PASSWORD", "private-admin")
    monkeypatch.setenv("CONVOY_EXECUTION_SIGNING_KEYS_FILE", "/private/api-signing.json")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "private-cloud")
    if public_only:
        configure_public_keys(bundles, tmp_path)
    invocation, child_cwd, absolute_sources = (tmp_path / name for name in ("invocation", "child", "absolute"))
    for path in (invocation / "src", child_cwd / "src", absolute_sources):
        path.mkdir(parents=True)
    (invocation / "src/qualified_relative.py").write_text("VALUE = 'trusted-relative'\n")
    (invocation / "qualified_empty.py").write_text("VALUE = 'trusted-empty'\n")
    (absolute_sources / "qualified_absolute.py").write_text("VALUE = 'trusted-absolute'\n")
    (child_cwd / "src/qualified_relative.py").write_text("VALUE = 'wrong-child-cwd'\n")
    monkeypatch.chdir(invocation)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(("src", "", str(absolute_sources))))
    captured = []

    class Capture:
        def start(self, argv_for_marker, **kwargs):
            marker = tmp_path / (uuid.uuid4().hex + ".json")
            marker.touch(mode=0o600)
            captured.append((argv_for_marker(str(marker)), kwargs, json.loads(marker.read_text())))
            # Exercise the real reader and cleanup bookkeeping using a harmless
            # exited process; no policy/planner imports or model requests.
            return subprocess.Popen([sys.executable, "-c",
                "import qualified_relative, qualified_empty, qualified_absolute; "
                "print(qualified_relative.VALUE, qualified_empty.VALUE, qualified_absolute.VALUE)"],
                cwd=kwargs["cwd"], env=kwargs["env"], stdout=kwargs["stdout"], stderr=kwargs["stderr"])

    with local_bundle.LocalBundleOwner(**bundles.options) as owner:
        recipe = next(iter(bundles.registry.values()))
        prepared = bundles.preflight(recipe, tmp_path / "staged")
        for role in ("worker", "planner"):
            original = owner._owners[role]
            owner._owners[role] = Capture()
            bundles.original_spawn(owner, role, prepared, child_cwd, 1234)
            assert owner._children[role].wait(timeout=3) == 0
            owner._readers[role].join(timeout=3)
            assert (child_cwd / f"{role}.log").read_text().strip() == "trusted-relative trusted-empty trusted-absolute"
            owner._owners[role] = original
        worker, planner = captured
        assert worker[0][:3] == [sys.executable, "-m", "convoy_worker.cli"]
        assert worker[0][worker[0].index("--factory") + 1] == "convoy_lerobot.runtime:smolvla"
        assert planner[0][:4] == [sys.executable, "-m", "convoy_planner.cli", "serve"]
        for _, args, manifest in captured:
            assert manifest == recipe.manifest
            assert not {"CONVOY_DATABASE_URL", "CONVOY_ADMIN_PASSWORD", "AWS_SECRET_ACCESS_KEY",
                        "CONVOY_EXECUTION_SIGNING_KEYS_FILE"} & args["env"].keys()
            assert args["env"]["PYTHONPATH"].split(os.pathsep) == [
                str(invocation / "src"), str(invocation), str(absolute_sources),
            ]
        assert "CONVOY_PLANNER_EXECUTION_SECRET" not in worker[1]["env"]
        assert "CONVOY_EXECUTION_SECRET" not in planner[1]["env"]
        if public_only:
            for role, purpose, capture in (("worker", "ACTION", worker), ("planner", "PLANNER", planner)):
                assert not {"CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET"} & capture[1]["env"].keys()
                assert capture[1]["env"][f"CONVOY_{purpose}_VERIFICATION_KEYS_FILE"] == str(
                    bundles.options[f"{role}_verification_keys_file"],
                )
            assert "CONVOY_PLANNER_VERIFICATION_KEYS_FILE" not in worker[1]["env"]
            assert "CONVOY_ACTION_VERIFICATION_KEYS_FILE" not in planner[1]["env"]
        else:
            assert worker[1]["env"]["CONVOY_EXECUTION_SECRET"] != planner[1]["env"]["CONVOY_PLANNER_EXECUTION_SECRET"]


def test_bundle_rejects_mixed_or_partial_authorization_config(bundles, tmp_path):
    with pytest.raises(ValueError, match="both public verification files or both legacy secrets"):
        local_bundle.LocalBundleOwner(**bundles.options, worker_verification_keys_file=tmp_path / "action.json")
    del bundles.options["worker_execution_secret"], bundles.options["planner_execution_secret"]
    with pytest.raises(ValueError, match="both public verification files or both legacy secrets"):
        local_bundle.LocalBundleOwner(**bundles.options, worker_verification_keys_file=tmp_path / "action.json")


@pytest.mark.parametrize("role,fault", [("worker", "missing"), ("planner", "malformed"), ("worker", "unsafe")])
def test_invalid_public_configuration_preserves_loaded_bundle(bundles, tmp_path, role, fault):
    configure_public_keys(bundles, tmp_path)
    with local_bundle.LocalBundleOwner(**bundles.options) as owner:
        prepare(owner, bundles.deployments[0])
        original = processes(owner)
        path = bundles.options[f"{role}_verification_keys_file"]
        if fault == "missing":
            path.unlink()
        elif fault == "malformed":
            path.write_text("not valid public key configuration")
        else:
            path.chmod(0o666)
        with pytest.raises(local_bundle.BundleError, match="bundle_preflight_failed"):
            prepare(owner, bundles.deployments[1])
        assert processes(owner) == original and all(child.poll() is None for child in original)
        assert state(owner)["phase"] == "preflight_failed"


def test_gateway_thread_start_failure_closes_bound_socket_and_native_child(bundles, monkeypatch):
    opened, children = [], []
    gateway_type = local_bundle.Gateway

    def fail_start(gateway):
        server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        gateway.server = server
        gateway.port = server.server_address[1]
        gateway.thread = threading.Thread(target=server.serve_forever)
        opened.append(gateway.port)
        children.append(gateway.sup.proc)
        def cannot_start():
            raise RuntimeError("thread creation failed")
        monkeypatch.setattr(gateway.thread, "start", cannot_start)
        gateway.thread.start()

    monkeypatch.setattr(gateway_type, "start", fail_start)
    # A real bound server with no serve_forever thread would block forever in
    # BaseServer.shutdown. Use the actual gateway stop contract in this fixture.
    def stop(gateway):
        if gateway.server:
            gateway.server.shutdown()
            gateway.server.server_close()
            gateway.server = None
    monkeypatch.setattr(gateway_type, "stop", stop)
    started = time.monotonic()
    with local_bundle.LocalBundleOwner(**bundles.options) as owner:
        with pytest.raises(local_bundle.BundleError, match="bundle_start_failed"):
            prepare(owner, bundles.deployments[0])
        assert owner._binding is None
        assert owner._gateway.server is None
        assert all(state(owner)["cleanup"].values())
        assert children[0].poll() is not None
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", opened[0])) != 0
    assert time.monotonic() - started < 3
