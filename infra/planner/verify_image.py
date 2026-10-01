"""Qualify a real Linux ARM64 planner image locally; no robot or cloud claims.

Run with the lightweight planner environment. No weights are downloaded. Every
container is unique and explicitly owned; model requests are never retried.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from convoy_contracts.execution import (
    VISUAL_PROFILE,
    canonical_digest,
    validate_manifest,
)
from convoy_contracts.grants import SigningKeys
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLAN_ECHO_FIELDS,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
    validate_plan_result,
    validate_release_manifest,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from convoy_planner.artifact import STATIC_GATEWAY_FIELDS, validate_gateway_identity

STATE = "/run/convoy/state"
CLEANUP = {"native_stopped", "native_marker_removed", "gateway_closed", "gateway_drained",
           "planner_closed", "listeners_closed"}
# Runs inside only our generated container, under its non-root user. No key
# content is read. psutil's pinned identity guard is rechecked before any signal.
OWNED_NATIVE = r'''
import json,os,re,sys
from pathlib import Path
import psutil
assert psutil.__version__ == "7.2.2"
path=Path("/run/convoy/state/native/owner/process.json")
if not path.exists():
    print(json.dumps({"verified":False})); raise SystemExit(0)
r=json.loads(path.read_text())
if r.get("state") != "running":
    print(json.dumps({"verified":False})); raise SystemExit(0)
assert r["version"] == 1 and r["uid"] == os.getuid() == 10001
assert type(r["pid"]) is int and r["pid"] > 1
marker=Path(r["marker"])
assert marker.parent == path.parent and re.fullmatch(r"launch-[0-9a-f]{32}",marker.name)
assert r["exe"] == "/run/convoy/state/runtime/bin/llama-server"
p=psutil.Process(r["pid"])
assert p._ident[1] == r["created"] and tuple(p.uids()) == (r["uid"],)*3
assert os.path.realpath(p._proc.exe()) == r["exe"]
assert p.cmdline().count(r["marker"]) == 1 and p.is_running()
result={"verified":True,"pid":p.pid,"created":r["created"],"ready_exists":Path("/run/convoy/state/ready.json").exists()}
if sys.argv[1] == "kill":
    p.kill()
    p.wait(timeout=5)
    result["native_exit_observed"]=True
print(json.dumps(result))
'''
RESOURCE_READ = r'''
import json
from pathlib import Path
result={}
for name in ("memory.peak","memory.max","cpu.max"):
    p=Path("/sys/fs/cgroup")/name
    result[name]=p.read_text().strip() if p.is_file() else None
print(json.dumps(result))
'''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run(*command: str, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def text(*command: str) -> str:
    return run(*command, capture_output=True, text=True).stdout.strip()


def write(path: Path, value: dict) -> None:
    with path.open("w") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_state(container: str) -> dict:
    return json.loads(text("docker", "inspect", "--format", "{{json .State}}", container))


def existing_services() -> dict:
    ids = text("docker", "ps", "-aq").split()
    result = {}
    for identity in ids:
        name = text("docker", "inspect", "--format", "{{.Name}}", identity)
        if name.startswith("/convoy-v1-services-") or name == "/convoy-v1-postgres-postgres-1":
            state = inspect_state(identity)
            result[identity] = {"name": name, "pid": state["Pid"], "started_at": state["StartedAt"],
                                "running": state["Running"],
                                "restart_count": int(text("docker", "inspect", "--format", "{{.RestartCount}}", identity))}
    return result


def copy_json(container: str, path: str, *, optional=False) -> dict | None:
    copied = subprocess.run(["docker", "cp", container + ":" + path, "-"], capture_output=True, check=False)
    if copied.returncode and optional:
        return None
    if copied.returncode or len(copied.stdout) > 1024 * 1024:
        raise RuntimeError("container evidence is missing or exceeds its bound")
    with tarfile.open(fileobj=io.BytesIO(copied.stdout)) as archive:
        members = archive.getmembers()
        if len(members) != 1 or not members[0].isfile() or members[0].size > 65536:
            raise RuntimeError("unexpected container evidence shape")
        with archive.extractfile(members[0]) as stream:
            return json.load(stream)


def http(base: str, path: str, body=None, token=None, *, timeout=5) -> tuple[int, dict | None]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        return error.code, None
    with response:
        raw = response.read(65537)
        if len(raw) > 65536:
            raise RuntimeError("planner response exceeds its bound")
        return response.status, json.loads(raw)


def closed(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.3)
        return connection.connect_ex(("127.0.0.1", port)) != 0


def wait_exit(container: str, timeout=35) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = inspect_state(container)
        if not state["Running"]:
            return state
        time.sleep(0.15)
    raise RuntimeError("owned planner did not exit within its shutdown budget")


class Case:
    def __init__(self, name: str, image: str, output: Path):
        self.label, self.image, self.output = name, image, output / name
        self.output.mkdir(mode=0o700)
        self.name = "convoy-planner-check-" + uuid.uuid4().hex[:12]
        self.container = None
        self.port = None
        self.evidence = {}

    def start(self, *, env_file=None, manifest=None):
        command = ["docker", "create", "--name", self.name, "--init", "--cpus=2", "--memory=4g",
                   "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=256"]
        if env_file is None:
            command += ["--network=none"]
        else:
            command += ["--env-file", str(env_file), "--publish", "127.0.0.1::8080",
                        "--mount", f"type=bind,src={manifest},dst=/run/convoy/release.json,readonly"]
        command += [self.image, "inspect" if env_file is None else "serve", "--assets", "/opt/convoy/assets/assets.json",
                    "--output", STATE, "--ctx-size", "2048", "--threads", "2", "--threads-batch", "2",
                    "--startup-timeout-s", "120", "--stop-timeout-s", "20"]
        if env_file is not None:
            command += ["--manifest", "/run/convoy/release.json", "--host", "0.0.0.0", "--port", "8080"]
        self.container = text(*command)
        if text("docker", "inspect", "--format", "{{.Image}}", self.container) != self.image:
            raise RuntimeError("container image differs from captured immutable identity")
        run("docker", "start", self.container, capture_output=True)
        if env_file is not None:
            binding = text("docker", "port", self.container, "8080/tcp")
            if not binding.startswith("127.0.0.1:") or "\n" in binding:
                raise RuntimeError("planner listener is not exclusively host loopback")
            self.port = int(binding.rsplit(":", 1)[1])

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def await_ready(self):
        deadline = time.monotonic() + 140
        while time.monotonic() < deadline:
            ready = copy_json(self.container, STATE + "/ready.json", optional=True)
            if ready is not None:
                self.evidence["ready"] = ready
                return ready
            if not inspect_state(self.container)["Running"]:
                raise RuntimeError("planner exited before readiness evidence")
            time.sleep(0.25)
        raise RuntimeError("planner startup exceeded its qualification budget")

    def signal_owner(self):
        run("docker", "kill", "--signal=TERM", self.container, capture_output=True)

    def terminal(self, *, expected_exit: int):
        state = wait_exit(self.container)
        result = copy_json(self.container, STATE + "/result.json")
        record = copy_json(self.container, STATE + "/native/owner/process.json", optional=True)
        self.evidence["result"] = result
        self.evidence["exit_code"] = state["ExitCode"]
        self.evidence["native_record"] = None if record is None else {key: record.get(key) for key in ("state", "result", "exit_code")}
        self.evidence["published_listener_closed"] = self.port is None or closed(self.port)
        assert state["ExitCode"] == expected_exit and not state["OOMKilled"]
        assert CLEANUP <= result["cleanup"].keys() and all(result["cleanup"][key] is True for key in CLEANUP)
        assert record is not None and record["state"] == "stopped"
        assert self.evidence["published_listener_closed"]
        return result

    def native(self, operation: str) -> dict:
        value = text("docker", "exec", self.container, "python", "-c", OWNED_NATIVE, operation)
        return json.loads(value)

    def resources(self):
        return json.loads(text("docker", "exec", self.container, "python", "-c", RESOURCE_READ))

    def close(self):
        if self.container is None:
            return
        original_failure = sys.exc_info()[0] is not None
        failures = []
        try:
            if inspect_state(self.container)["Running"]:
                self.signal_owner()
                wait_exit(self.container)
        except Exception as error:  # noqa: BLE001 -- cleanup must preserve evidence and continue.
            failures.append({"operation": "graceful_shutdown", "type": type(error).__name__})
            self.evidence["forced_removal_required"] = True
        # Collect each available item even when graceful shutdown failed. A
        # failed qualification must retain its evidence before scoped removal.
        for name in ("ready", "result"):
            try:
                value = copy_json(self.container, STATE + "/" + name + ".json", optional=True)
                if value is not None:
                    self.evidence[name] = value
            except Exception as error:  # noqa: BLE001 -- cleanup must preserve evidence and continue.
                failures.append({"operation": "collect_" + name, "type": type(error).__name__})
        try:
            logs = subprocess.run(["docker", "logs", self.container], capture_output=True, check=False)
            # Access logging is disabled in the image; native keys and grants are
            # never logged. Preserve bounded diagnostics, never private state dirs.
            (self.output / "container.log").write_bytes((logs.stdout + logs.stderr)[-65536:])
        except Exception as error:  # noqa: BLE001 -- cleanup must preserve evidence and continue.
            failures.append({"operation": "collect_logs", "type": type(error).__name__})
        try:
            run("docker", "rm", "--force", self.container, capture_output=True)
            self.evidence["container_removed"] = not text("docker", "ps", "-aq", "--filter", "name=^" + self.name + "$")
            if not self.evidence["container_removed"]:
                raise RuntimeError("owned container remains after removal")
        except Exception as error:  # noqa: BLE001 -- cleanup must preserve evidence and continue.
            failures.append({"operation": "container_removal", "type": type(error).__name__})
        if failures:
            self.evidence["teardown_failures"] = failures
        write(self.output / "evidence.json", self.evidence)
        if failures and not original_failure:
            raise RuntimeError("owned case cleanup failed; bounded evidence was preserved")


def verify_identity(value: dict, assets: dict):
    assert value["host"] == {"system": "Linux", "machine": "aarch64"}
    assert value["runtime_source"] == assets["source"]
    assert value["model"] == {key: assets["model"][key] for key in ("sha256", "bytes")}
    assert value["runtime_archive_sha256"] == assets["archive"]["sha256"]
    assert value["native"]["backend"] == "CPU" and value["effective_configuration"]["gpu_layers"] == 0
    assert value["effective_configuration"]["threads"] == value["effective_configuration"]["threads_batch"] == 2
    # This CPU-only build emits no GPU offload line. Keep an absent measurement
    # absent instead of pretending the parser observed a numeric zero.
    assert value["native"]["gpu_offloaded_layers"] in (None, 0)
    assert value["native"]["binary_sha256"] == assets["binary"]["sha256"]
    identity = validate_gateway_identity(value["gateway_identity"])
    assert identity["simulated"] is False and identity["model_sha256"] == assets["model"]["sha256"]
    assert identity["runtime_artifact_sha256"] == assets["archive"]["sha256"]
    assert identity["binary_sha256"] == assets["binary"]["sha256"]
    assert value["artifact"]["gateway"] == {key: identity[key] for key in STATIC_GATEWAY_FIELDS}
    assert value["planner"] == {"runtime": PLANNER_RUNTIME, "artifact_sha256": canonical_digest(value["artifact"]),
                                "protocol_sha256": PLANNER_PROTOCOL_SHA256}


def create_signer(directory: Path):
    entries = []
    for purpose in ("action", "planner"):
        key = Ed25519PrivateKey.generate()
        entries.append({"kid": purpose + "-test", "purpose": purpose, "audience": "convoy-test-" + purpose,
                        "private_key_pem": key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()})
    private = directory / "signer.json"
    write(private, {"schema_version": 1, "issuer": "convoy-planner-image-check",
                    "active": {purpose: purpose + "-test" for purpose in ("action", "planner")}, "keys": entries})
    signer, probe = SigningKeys(private), secrets.token_urlsafe(48)
    env_file = directory / "planner.env"
    env_file.write_text("CONVOY_PLANNER_VERIFICATION_JSON=" + json.dumps(signer.verification_document("planner"))
                        + "\nCONVOY_PLANNER_PROBE_TOKEN=" + probe + "\n")
    env_file.chmod(0o600)
    return signer, probe, env_file


def run_cases(args, output: Path, image: str, temporary: Path) -> dict:
    inspected = Case("inspect", image, output)
    try:
        inspected.start()
        ready = inspected.await_ready()
        assets = copy_json(inspected.container, "/opt/convoy/assets/assets.json")
        verify_identity(ready, assets)
        assert inspected.terminal(expected_exit=0)["status"] == "stopped"
        planner = ready["planner"]
    finally:
        inspected.close()
    action = validate_manifest(json.loads(args.action_manifest.read_text()))
    assert action["profile"] == VISUAL_PROFILE
    manifest = validate_release_manifest({"schema_version": 2, "profile": PAIRED_PROFILE, "action_manifest": action,
        "planner": planner, "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID},
        "catalog_sha256": CATALOG_SHA256, "planning": {"timeout_ms": 30000},
        "placement": {"policy": "development-local-cpu", "planner": "development-local"}})
    write(output / "release.json", manifest)
    mounted = temporary / "release.json"
    mounted.write_text(json.dumps(manifest))
    mounted.chmod(0o444)
    signer, probe, env_file = create_signer(temporary)
    served = Case("serve", image, output)
    try:
        served.start(env_file=env_file, manifest=mounted)
        verify_identity(served.await_ready(), assets)
        assert http(served.base, "/ready")[0] == 200
        body = {"release_digest": canonical_digest(manifest), "profile": PAIRED_PROFILE}
        assert http(served.base, "/v1/probe", body)[0] == 401
        status, observed = http(served.base, "/v1/probe", body, probe)
        assert status == 200 and observed["planner_artifact_sha256"] == planner["artifact_sha256"]
        who = {"robot_id": "image-check", "device_id": "image-check", "mission_id": "image-check",
               "boot_id": "image-check", "incarnation": uuid.uuid4().hex, "authority_epoch": 1,
               "release_digest": canonical_digest(manifest)}
        expiry = time.time() + 120
        assert http(served.base, "/v1/sessions/start", {"identity": who}, signer.sign(who, "action", expiry))[0] == 401
        token = signer.sign(who, "planner", expiry)
        status, session = http(served.base, "/v1/sessions/start", {"identity": who}, token)
        assert status == 200 and session["next_sequence"] == 0 and session["identity"] == who
        request = {"identity": who, "request_id": uuid.uuid4().hex, "observation_id": "qualified-fixed-task",
                   "observation_digest": canonical_digest({"task": FIXED_TASK, "catalog_sha256": CATALOG_SHA256}),
                   "deadline_monotonic_ns": time.monotonic_ns() + 30_000_000_000, "budget_ms": 30000}
        start = time.monotonic()
        status, result = http(served.base, "/v1/plans", request, token, timeout=35)  # exactly one model request
        served.evidence["model_request_count"] = 1
        served.evidence["http_duration_ms"] = (time.monotonic() - start) * 1000
        served.evidence["plan_http_status"] = status
        if status != 200:
            raise RuntimeError("the one real planner request failed; it was not retried")
        result = validate_plan_result(result)
        assert all(result[key] == request[key] for key in PLAN_ECHO_FIELDS)
        assert all(result[key] == session[key] for key in ("planner_artifact_sha256", "planner_incarnation", "runtime_generation"))
        assert result["planner_artifact_sha256"] == planner["artifact_sha256"]
        assert result["decision"] == {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}}
        assert result["planner_duration_ms"] <= 30000 and time.monotonic_ns() < request["deadline_monotonic_ns"]
        served.evidence["plan"] = result
        assert http(served.base, "/v1/plans", request, token)[0] == 409
        served.evidence["repeat_plan_rejected"] = True
        served.evidence["action_purpose_rejected"] = True
        served.evidence["resources"] = served.resources()
        served.signal_owner()
        assert served.terminal(expected_exit=0)["status"] == "stopped"
    finally:
        served.close()
    lost = Case("native-loss", image, output)
    try:
        lost.start(env_file=env_file, manifest=mounted)
        verify_identity(lost.await_ready(), assets)
        killed = lost.native("kill")
        assert killed["verified"] and killed["native_exit_observed"]
        lost.evidence["native_signal_identity_verified"] = True
        try:
            status, _ = http(lost.base, "/ready")
            assert status == 503
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        lost.evidence["readiness_failed_after_native_loss"] = True
        assert lost.terminal(expected_exit=1)["status"] == "failed"
    finally:
        lost.close()
    startup = Case("startup-sigterm", image, output)
    try:
        startup.start(env_file=env_file, manifest=mounted)
        deadline = time.monotonic() + 130
        observed = None
        while time.monotonic() < deadline and inspect_state(startup.container)["Running"]:
            observed = startup.native("inspect")
            if observed["verified"]:
                break
            time.sleep(0.1)
        if not observed or not observed["verified"]:
            raise RuntimeError("startup cancellation never found a provable owned native process")
        startup.evidence["native_identity_verified_before_owner_signal"] = True
        startup.signal_owner()
        result = startup.terminal(expected_exit=0)
        startup.evidence["startup_interruption_exercised"] = not observed["ready_exists"] and result["status"] == "interrupted"
        assert startup.evidence["startup_interruption_exercised"], "readiness won the startup interruption race"
    finally:
        startup.close()
    return {"success": True, "image_id": image, "release_digest": canonical_digest(manifest),
            "model_sha256": assets["model"]["sha256"], "runtime_sha256": assets["archive"]["sha256"],
            "planner_artifact_sha256": planner["artifact_sha256"], "plan": served.evidence["plan"],
            "startup_interruption_exercised": startup.evidence["startup_interruption_exercised"],
            "scope": "one real CPU text-model plan; no action-policy, robot, cloud or latency qualification"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--action-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    # Preserve the exact driver used, including a failed attempt, without keys.
    shutil.copyfile(__file__, output / "verify-image-source.py")
    before = existing_services()
    image = text("docker", "image", "inspect", args.image, "--format", "{{.Id}}")
    evidence = {"success": False, "image_id": image, "driver_sha256": sha(Path(__file__))}
    credential_directory = None
    try:
        with tempfile.TemporaryDirectory(prefix="convoy-planner-image-") as directory:
            credential_directory = Path(directory)
            evidence.update(run_cases(args, output, image, credential_directory))
    except BaseException as error:
        evidence["failure_type"] = type(error).__name__
        raise
    finally:
        evidence["credentials_removed"] = credential_directory is None or not credential_directory.exists()
        evidence["original_services_count"] = len(before)
        evidence["original_services_unchanged"] = existing_services() == before
        write(output / "result.json", evidence)
    assert evidence["credentials_removed"] and evidence["original_services_unchanged"]
    print("Passed: actual planner image, signed text-model plan, lifecycle faults and cleanup. No cloud or robot execution.")


if __name__ == "__main__":
    main()
