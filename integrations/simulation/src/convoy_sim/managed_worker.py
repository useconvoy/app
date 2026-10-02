"""An owned local reference worker, prepared only behind the coordinator's idle gate.

Launch commands are installed code. A release supplies validated data, never a
factory, shell command, endpoint or credential. Existing external workers remain
available for operator-installed learned runtimes.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

from convoy_agent.coordinator.transport import RemoteError, TransportError, WorkerHTTP
from convoy_agent.owned_process import OwnedProcess
from convoy_agent.runtime import LOG_FILE_CAP_BYTES, LOG_HEAD_BYTES, LOG_RING_BYTES, _LogReader
from convoy_contracts.execution import canonical_digest, canonical_json
from convoy_contracts.registered import REGISTERED_PROFILE, validate_manifest
from convoy_worker.cli import execution_options

from .joint_reference import JointTargetRuntime


def _write(path, value):
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(8))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


class ManagedWorker:
    def __init__(self, directory, *, startup_timeout_s=30):
        if type(startup_timeout_s) not in (int, float) or not 0 < startup_timeout_s <= 120:
            raise ValueError("worker startup budget must be in (0, 120] seconds")
        # Validate operator-provided public keys or the explicit local HMAC test setup
        # without copying API signing keys or unrelated credentials into the child.
        execution_options()
        names = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "VIRTUAL_ENV")
        self.environment = {name: os.environ[name] for name in names if name in os.environ}
        if "PYTHONPATH" in os.environ:
            self.environment["PYTHONPATH"] = os.pathsep.join(str(Path(p or ".").absolute()) for p in os.environ["PYTHONPATH"].split(os.pathsep))
        if "CONVOY_ACTION_VERIFICATION_KEYS_FILE" in os.environ:
            self.environment["CONVOY_ACTION_VERIFICATION_KEYS_FILE"] = str(Path(os.environ["CONVOY_ACTION_VERIFICATION_KEYS_FILE"]).absolute())
        else:
            self.environment["CONVOY_EXECUTION_SECRET"] = os.environ["CONVOY_EXECUTION_SECRET"]
        self.environment.update(PYTHONUNBUFFERED="1", CONVOY_WORKER_PROBE_TOKEN=secrets.token_urlsafe(32))
        self.directory = Path(directory).absolute()
        self.owner = OwnedProcess(self.directory)
        self.timeout = startup_timeout_s
        self.child = self.reader = self.client = self.target = None
        self.failed_target = None
        self.entered = self.touched = False

    def __enter__(self):
        self.owner.__enter__()
        try:
            status = self.directory / "status.json"
            if status.exists():
                info = status.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 8192:
                    raise ValueError("invalid managed worker status file")
                value = json.loads(status.read_text())
                if value.get("schema_version") != 1:
                    raise ValueError("unsupported managed worker status")
                self.failed_target = value.get("failed_target")
            self.entered = True
            return self
        except BaseException:
            self.owner.__exit__()
            raise

    def _status(self, phase, target):
        _write(self.directory / "status.json", {"schema_version": 1, "phase": phase,
                "target": target, "failed_target": self.failed_target})

    def _stop(self):
        self.owner.stop(terminate_timeout=5, kill_timeout=3)
        if self.child is not None and self.child.poll() is None:
            raise RuntimeError("worker exit unresolved")
        if self.reader is not None:
            if self.reader.ident is None:
                self.reader.pipe.close()
            else:
                self.reader.join(timeout=3)
                if self.reader.is_alive():
                    raise RuntimeError("worker output drain unresolved")
        self.child = self.reader = self.client = self.target = None

    def prepare(self, deployment, manifest):
        if not self.entered:
            raise RuntimeError("managed worker owner must be entered")
        validate_manifest(manifest)
        digest = canonical_digest(manifest)
        if deployment.get("release", {}).get("digest") != digest or canonical_digest(deployment["release"].get("manifest")) != digest:
            raise ValueError("deployment release identity mismatch")
        target = {"deployment_id": deployment["id"], "generation": deployment["generation"], "release_digest": digest}
        if self.failed_target == target:
            raise RuntimeError("worker failed; request a new deployment to retry")
        if self.target == target and self.child is not None and self.child.poll() is None:
            return self.client
        reference = {"target_joint_positions": manifest["task"]["target_joint_positions"]}
        runtime = JointTargetRuntime(reference)
        if manifest["policy"] != {"runtime": runtime.runtime, "artifact_sha256": runtime.artifact_sha256}:
            raise ValueError("automatic preparation supports the pinned reference policy; use an installed worker for other policies")
        if "CONVOY_ACTION_VERIFICATION_KEYS_FILE" in self.environment:
            from convoy_contracts.grants import GrantVerifier

            GrantVerifier(self.environment["CONVOY_ACTION_VERIFICATION_KEYS_FILE"], purpose="action")
        # Everything above is preflight: an unsupported B must not stop loaded A.
        self.touched = True
        try:
            self._status("preparing", target)
            self._stop()  # Also verifies/stops a prior owner's orphan, only after idle admission.
            policy_path = self.directory / "reference.json"
            _write(policy_path, reference)
            env = {**self.environment, "CONVOY_JOINT_REFERENCE_FILE": str(policy_path)}
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]

            def command(marker):
                _write(Path(marker), manifest)
                return [sys.executable, "-m", "convoy_worker.cli", "--release", marker,
                        "--factory", "convoy_sim.joint_reference:from_file", "--host", "127.0.0.1", "--port", str(port)]

            self.child = self.owner.start(command, env=env, cwd=self.directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.reader = _LogReader(self.child.stdout, self.directory / "worker.log", head_bytes=LOG_HEAD_BYTES,
                                     ring_bytes=LOG_RING_BYTES, file_cap=LOG_FILE_CAP_BYTES)
            self.reader.start()
            worker = WorkerHTTP(f"http://127.0.0.1:{port}", env["CONVOY_WORKER_PROBE_TOKEN"])
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if self.child.poll() is not None:
                    raise RuntimeError("worker exited before readiness")
                worker.timeout_s = min(1, max(0.001, deadline - time.monotonic()))
                try:
                    probe = worker.probe(digest, REGISTERED_PROFILE)
                except (TransportError, RemoteError):
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                    continue
                if (probe.get("ready") is not True or probe.get("release_digest") != digest
                        or probe.get("profile") != REGISTERED_PROFILE
                        or probe.get("runtime") != runtime.runtime or probe.get("artifact_sha256") != runtime.artifact_sha256):
                    raise RuntimeError("worker readiness identity mismatch")
                if self.child.poll() is not None:
                    raise RuntimeError("worker exited during readiness")
                worker.timeout_s = 3
                self.client, self.target, self.failed_target = worker, target, None
                self._status("verified", target)
                return worker
            raise RuntimeError("worker startup deadline exceeded")
        except BaseException:
            self.failed_target = target
            try:
                self._stop()
            finally:
                self._status("failed", target)
            raise

    def __exit__(self, *_):
        try:
            if self.touched:
                self._stop()
                self._status("stopped", None)
        finally:
            self.entered = False
            self.owner.__exit__()
