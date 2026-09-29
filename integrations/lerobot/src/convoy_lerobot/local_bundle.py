"""Fixed local Qwen/SmolVLA development activation, with verified process ownership.

The coordinator calls prepare only while idle and fences its result against fresh
server authority. This owner records local verification, never API readiness.
Construction and context entry do not recover or launch children: an unresolved
mission must not cause even an old process to be replaced.
"""

from __future__ import annotations

import json
import math
import os
import socket
import stat
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path

from convoy_agent.coordinator.binding import BindingObservation, PreparedBinding
from convoy_agent.coordinator.transport import PlannerHTTP, RemoteError, TransportError, WorkerHTTP
from convoy_agent.gateway import Gateway
from convoy_agent.owned_process import OwnedProcess
from convoy_agent.runtime import (
    LOG_FILE_CAP_BYTES,
    LOG_HEAD_BYTES,
    LOG_RING_BYTES,
    RuntimeSupervisor,
    _LogReader,
)
from convoy_contracts.execution import canonical_digest
from convoy_contracts.pairing import validate_planner_identity
from convoy_planner.artifact import STATIC_GATEWAY_FIELDS, artifact_descriptor, validate_gateway_identity

from .local_recipe import load_registry, preflight, recipe_for


class BundleError(RuntimeError):
    """Bounded local failure code; no raw model output, paths or credentials."""


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BundleError("unsafe_bundle_directory")


def _write(path: Path, value: dict) -> None:
    data = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    if len(data) > 16384:
        raise BundleError("bundle_state_exceeds_bound")
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


def _port() -> int:
    # Existing fixed CLIs require an integer port. Their authenticated exact-bundle
    # probes and retained child handles prevent a bind race from becoming readiness.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class LocalBundleOwner:
    def __init__(self, directory: Path, registry_path: Path, *, worker_execution_secret: str | None = None,
                 planner_execution_secret: str | None = None, worker_probe_token: str, planner_probe_token: str,
                 worker_verification_keys_file: Path | None = None,
                 planner_verification_keys_file: Path | None = None,
                 startup_timeout_s: float = 120.0, shutdown_timeout_s: float = 15.0):
        probes = (worker_probe_token, planner_probe_token)
        credentials = (worker_execution_secret, planner_execution_secret, *probes)
        self._verification_files = {}
        if worker_verification_keys_file is not None or planner_verification_keys_file is not None:
            if (worker_verification_keys_file is None or planner_verification_keys_file is None
                    or worker_execution_secret is not None or planner_execution_secret is not None):
                raise ValueError("configure both public verification files or both legacy secrets")
            from convoy_contracts.grants import GrantVerifier

            for role, purpose, path in (("worker", "action", worker_verification_keys_file),
                                        ("planner", "planner", planner_verification_keys_file)):
                path = Path(path).absolute()
                GrantVerifier(path, purpose=purpose)
                self._verification_files[role] = str(path)
            credentials = probes
        if (any(not isinstance(value, str) or len(value) < 32 for value in credentials)
                or len(set(credentials)) != len(credentials)):
            raise ValueError("distinct execution and probe credentials of at least 32 characters required")
        for value in (startup_timeout_s, shutdown_timeout_s):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 600:
                raise ValueError("local startup and shutdown bounds must be in (0, 600] seconds")
        self.directory, self.registry_path = Path(directory).absolute(), Path(registry_path)
        self._invocation_cwd = Path.cwd()
        self.startup_timeout_s, self.shutdown_timeout_s = startup_timeout_s, shutdown_timeout_s
        self._credentials = {"worker_probe": worker_probe_token, "planner_probe": planner_probe_token}
        if not self._verification_files:
            self._credentials.update(worker_secret=worker_execution_secret, planner_secret=planner_execution_secret)
        self._stack = None
        self._owners = {}
        self._children = {}
        self._readers = {}
        self._binding = None
        self._failed_target = None
        self._previous_verified = None
        self._touched = False
        self._supervisor = None
        self._gateway = None
        self._native_identity = None
        self._state_path = self.directory / "bundle.json"

    def __enter__(self):
        if self._stack is not None:
            raise BundleError("bundle_owner_already_entered")
        _private_directory(self.directory)
        stack = ExitStack()
        try:
            self._owners = {role: stack.enter_context(OwnedProcess(self.directory / role))
                            for role in ("native", "worker", "planner")}
            self._registry = load_registry(self.registry_path)
            self._touched = False
            self._binding = None
            self._failed_target = None
            self._previous_verified = None
            if self._state_path.exists():
                info = self._state_path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise BundleError("unsafe_bundle_state")
                with self._state_path.open("rb") as source:
                    raw = source.read(16385)
                if len(raw) > 16384:
                    raise BundleError("bundle_state_exceeds_bound")
                state = json.loads(raw)
                if not isinstance(state, dict) or state.get("schema_version") != 1:
                    raise BundleError("invalid_bundle_state")
                self._failed_target = state.get("failed_target")
                self._previous_verified = state.get("previous_verified_bundle")
            self._supervisor = RuntimeSupervisor(self.directory / "native-runtime", process_owner=self._owners["native"])
            self._gateway = self._new_gateway()
            self._stack = stack
            return self
        except BaseException:
            stack.close()
            raise

    def _new_gateway(self):
        return Gateway(self._supervisor, host="127.0.0.1", port=0, queue_depth=1, deadline_s=30)

    def _persist(self, phase: str, target=None, *, cleanup=None) -> None:
        value = {"schema_version": 1, "phase": phase, "target": target,
                 "failed_target": self._failed_target, "previous_verified_bundle": self._previous_verified}
        if cleanup is not None:
            value["cleanup"] = cleanup
        _write(self._state_path, value)

    @staticmethod
    def _target(deployment: dict, manifest: dict) -> dict:
        digest = canonical_digest(manifest)
        if (deployment.get("release", {}).get("digest") != digest
                or canonical_digest(deployment["release"].get("manifest")) != digest
                or not isinstance(deployment.get("id"), str) or not 1 <= len(deployment["id"]) <= 128
                or type(deployment.get("generation")) is not int or deployment["generation"] < 1):
            raise BundleError("deployment_identity_mismatch")
        return {"deployment_id": deployment["id"], "generation": deployment["generation"], "release_digest": digest}

    def _live(self) -> bool:
        gateway = self._gateway
        return bool(self._binding is not None and all(
            role in self._children and self._children[role].poll() is None for role in ("worker", "planner"))
            and self._supervisor.proc is not None and self._supervisor.proc.poll() is None
            and gateway.mode == "production" and not gateway.needs_restart and gateway.server is not None
            and gateway.thread is not None and gateway.thread.is_alive() and self._supervisor.health())

    def prepare(self, deployment: dict, manifest: dict) -> PreparedBinding:
        if self._stack is None:
            raise BundleError("bundle_owner_not_entered")
        target = self._target(deployment, manifest)
        if self._failed_target == target:
            raise BundleError("failed_bundle_requires_new_deployment")
        if (self._binding is not None and self._binding.observation.release_digest == target["release_digest"]
                and self._live()):
            return self._binding
        attempt = self.directory / "attempts" / uuid.uuid4().hex
        self._persist("preparing", target)
        try:
            # Operator rotation/removal can happen after owner construction. A
            # known-invalid public configuration must not interrupt loaded A.
            if self._verification_files:
                from convoy_contracts.grants import GrantVerifier

                for role, purpose in (("worker", "action"), ("planner", "planner")):
                    GrantVerifier(self._verification_files[role], purpose=purpose)
            recipe = recipe_for(self._registry, target["release_digest"])
            if recipe.manifest != manifest:
                raise BundleError("registered_manifest_mismatch")
            prepared = preflight(recipe, attempt, current_gateway_implementation=self._gateway.implementation_sha256)
        except Exception:
            # A is still loaded here. A failed B is never silently returned as B.
            self._failed_target = target
            self._persist("preflight_failed", target)
            raise BundleError("bundle_preflight_failed") from None
        self._touched = True
        self._binding = None
        try:
            stopped = self._stop_all()
            if not all(stopped.values()):
                raise BundleError("previous_bundle_cleanup_unresolved")
            self._persist("starting", target, cleanup=stopped)
            deadline = time.monotonic() + self.startup_timeout_s
            self._start_native(prepared, deadline)
            worker, planner = self._start_components(prepared, attempt)
            self._wait_ready(worker, planner, prepared, deadline)
            binding = PreparedBinding(uuid.uuid4().hex, worker, planner, BindingObservation(
                prepared.release_digest, manifest["profile"],
                manifest["action_manifest"]["policy"]["artifact_sha256"], manifest["planner"]["artifact_sha256"],
            ))
            self._failed_target = None
            self._previous_verified = {
                "binding_id": binding.binding_id, "observation": asdict(binding.observation),
                "endpoints": {"worker": worker.base_url, "planner": planner.base_url,
                              "gateway": f"http://127.0.0.1:{self._gateway.port}",
                              "native": self._supervisor.base_url},
                "gateway_identity": self._native_identity,
            }
            self._persist("verified", target)
            self._binding = binding
            return binding
        except BaseException:
            self._binding = None
            self._failed_target = target
            cleanup = self._stop_all()
            self._persist("failed", target, cleanup=cleanup)
            raise BundleError("bundle_start_failed" if all(cleanup.values()) else "bundle_cleanup_unresolved") from None

    def _start_native(self, prepared, deadline: float) -> None:
        native = self._supervisor.start(
            release_id="local-" + prepared.release_digest,
            spec={"model": {"file": {"sha256": prepared.gateway_identity["model_sha256"]}},
                  "runtime": {"artifact_sha256": prepared.gateway_identity["runtime_artifact_sha256"]},
                  "config": prepared.native_config},
            model_path=prepared.model_path, template_path=None, binary=prepared.binary_path,
            lib_dir=prepared.lib_dir, health_timeout_s=max(0.001, deadline - time.monotonic()),
        )
        if native.get("simulated") is not False or native.get("backend") != "CPU":
            raise BundleError("native_cpu_identity_unverified")
        self._gateway = self._new_gateway()
        self._gateway.start()
        self._gateway.set_mode("production")
        actual = validate_gateway_identity(self._gateway.runtime_identity(check_health=True))
        if any(actual[key] != prepared.gateway_identity[key] for key in STATIC_GATEWAY_FIELDS):
            raise BundleError("loaded_native_identity_mismatch")
        if canonical_digest(artifact_descriptor(actual)) != prepared.manifest["planner"]["artifact_sha256"]:
            raise BundleError("loaded_planner_artifact_mismatch")
        self._native_identity = actual

    def _environment(self, role: str, prepared) -> dict[str, str]:
        # Same trusted installation as preflight, including explicit development
        # PYTHONPATH. Never copy broad CONVOY, cloud, management, or signing env.
        names = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "PYTHONPATH", "VIRTUAL_ENV")
        env = {name: os.environ[name] for name in names if name in os.environ}
        if "PYTHONPATH" in env:
            # Child cwd is the fresh attempt directory; relative and empty entries
            # must keep their meaning from the trusted parent invocation.
            env["PYTHONPATH"] = os.pathsep.join(
                os.path.abspath(self._invocation_cwd / entry) for entry in env["PYTHONPATH"].split(os.pathsep)
            )
        env.update(PYTHONUNBUFFERED="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                   HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false")
        if role == "worker":
            env.update(CONVOY_WORKER_PROBE_TOKEN=self._credentials["worker_probe"],
                       CONVOY_SMOLVLA_ASSETS=str(prepared.action_assets))
        else:
            env.update(CONVOY_PLANNER_PROBE_TOKEN=self._credentials["planner_probe"])
        if self._verification_files:
            name = "CONVOY_ACTION_VERIFICATION_KEYS_FILE" if role == "worker" else "CONVOY_PLANNER_VERIFICATION_KEYS_FILE"
            env[name] = self._verification_files[role]
        else:
            name = "CONVOY_EXECUTION_SECRET" if role == "worker" else "CONVOY_PLANNER_EXECUTION_SECRET"
            env[name] = self._credentials[f"{role}_secret"]
        return env

    def _spawn_component(self, role: str, prepared, attempt: Path, port: int) -> None:
        def argv(marker: str):
            # Ownership marker is an existing CLI argument, not a new launcher or
            # customer-selected command. Exact manifest persisted before Popen.
            with Path(marker).open("w") as output:
                json.dump(prepared.manifest, output, sort_keys=True, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            command = [sys.executable, "-m"]
            if role == "worker":
                command += ["convoy_worker.cli", "--release", marker, "--factory", "convoy_lerobot.runtime:smolvla"]
            else:
                command += ["convoy_planner.cli", "serve", "--manifest", marker,
                            "--gateway-url", f"http://127.0.0.1:{self._gateway.port}"]
            return [*command, "--host", "127.0.0.1", "--port", str(port)]

        child = self._owners[role].start(argv, env=self._environment(role, prepared),
                                          cwd=attempt, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self._children[role] = child
        reader = _LogReader(child.stdout, attempt / f"{role}.log", head_bytes=LOG_HEAD_BYTES,
                            ring_bytes=LOG_RING_BYTES, file_cap=LOG_FILE_CAP_BYTES)
        self._readers[role] = reader
        reader.start()

    def _start_components(self, prepared, attempt: Path):
        worker_port = _port()
        for _ in range(16):
            planner_port = _port()
            if planner_port != worker_port:
                break
        else:
            raise BundleError("distinct_component_ports_unavailable")
        self._spawn_component("worker", prepared, attempt, worker_port)
        self._spawn_component("planner", prepared, attempt, planner_port)
        return (WorkerHTTP(f"http://127.0.0.1:{worker_port}", self._credentials["worker_probe"]),
                PlannerHTTP(f"http://127.0.0.1:{planner_port}", self._credentials["planner_probe"]))

    def _wait_ready(self, worker, planner, prepared, deadline: float) -> None:
        manifest = prepared.manifest
        for role, client in (("worker", worker), ("planner", planner)):
            while time.monotonic() < deadline:
                if any(child.poll() is not None for child in self._children.values()):
                    raise BundleError("component_exited_before_readiness")
                client.timeout_s = min(3, max(0.001, deadline - time.monotonic()))
                try:
                    probe = client.probe(prepared.release_digest, manifest["profile"])
                except (TransportError, RemoteError):
                    # Read-only readiness probes can retry; mission calls cannot.
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
                    continue
                if (probe.get("ready") is not True or probe.get("release_digest") != prepared.release_digest
                        or probe.get("profile") != manifest["profile"]):
                    raise BundleError("component_readiness_mismatch")
                if role == "worker":
                    policy = manifest["action_manifest"]["policy"]
                    if probe.get("runtime") != policy["runtime"] or probe.get("artifact_sha256") != policy["artifact_sha256"]:
                        raise BundleError("worker_readiness_mismatch")
                else:
                    if (probe.get("runtime") != manifest["planner"]["runtime"]
                            or probe.get("planner_artifact_sha256") != manifest["planner"]["artifact_sha256"]):
                        raise BundleError("planner_readiness_mismatch")
                    validate_planner_identity({key: probe[key] for key in (
                        "planner_artifact_sha256", "planner_incarnation", "runtime_generation",
                    )})
                client.timeout_s = 3
                break
            else:
                raise BundleError("component_readiness_timeout")
        if time.monotonic() >= deadline or any(child.poll() is not None for child in self._children.values()):
            raise BundleError("component_readiness_lost")

    def _stop_component(self, role: str, deadline: float) -> bool:
        try:
            remaining = max(0, deadline - time.monotonic())
            self._owners[role].stop(terminate_timeout=min(30, remaining * 0.7), kill_timeout=min(30, remaining * 0.3))
            child = self._children.get(role)
            if child is not None and child.poll() is None:
                return False
            reader = self._readers.get(role)
            if reader is not None:
                if reader.ident is None:
                    # Thread creation itself failed; the now-dead child cannot
                    # write again and no reader owns its pipe.
                    reader.pipe.close()
                else:
                    reader.join(timeout=max(0, deadline - time.monotonic()))
                    if reader.is_alive():
                        return False
            self._children.pop(role, None)
            self._readers.pop(role, None)
            return True
        except Exception:
            return False

    def _stop_all(self) -> dict[str, bool]:
        deadline = time.monotonic() + self.shutdown_timeout_s
        result = {}
        try:
            self._gateway.set_mode("closed")
            if (self._gateway.server is not None
                    and (self._gateway.thread is None or self._gateway.thread.ident is None)):
                # BaseServer.shutdown waits for serve_forever. If thread creation
                # or start failed, only the bound socket exists: close it directly.
                self._gateway.server.server_close()
                self._gateway.server = None
            else:
                self._gateway.stop()
            result["gateway"] = self._gateway.server is None
        except Exception:
            result["gateway"] = False
        for index, role in enumerate(("planner", "worker")):
            # Reserve a share for every role even if another cannot be stopped.
            budget = max(0, deadline - time.monotonic()) / (3 - index)
            result[role] = self._stop_component(role, time.monotonic() + budget)
        try:
            result["native"] = self._supervisor.stop(deadline=deadline).get("stopped") is True
        except Exception:
            result["native"] = False
        try:
            result["gateway"] = (self._gateway.drain(max(0, deadline - time.monotonic())) and result["gateway"])
        except Exception:
            result["gateway"] = False
        return result

    def close(self) -> None:
        if self._stack is None:
            return
        if self._touched:
            self._binding = None
            cleanup = self._stop_all()
            self._persist("stopped" if all(cleanup.values()) else "cleanup_unresolved", cleanup=cleanup)
            if not all(cleanup.values()):
                # Keep locks and local handles for a safe retry; a future process
                # recovers from the durable role records after this owner exits.
                raise BundleError("bundle_cleanup_unresolved")
        self._stack.close()
        self._stack = None

    def __exit__(self, *_):
        self.close()
