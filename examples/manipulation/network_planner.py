"""One real Qwen -> SmolVLA/MuJoCo mission across a private Docker network and TLS.

Owns only its uniquely created containers/network and ephemeral credentials.
Uses existing images and model assets; never pulls images or downloads weights.
This is local network qualification, not WAN, AWS, Jetson or real-time evidence.
"""

# Import the current checkout after sys.path setup, rather than stale editable installs.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / path for path in (
    "control-plane/agent", "control-plane/contracts", "control-plane/server", "control-plane/worker",
    "integrations/simulation/src", "integrations/lerobot/src", "integrations/planner/src",
)]
sys.path[:0] = [str(path) for path in SOURCES]

import pipeline
from convoy_agent.coordinator.transport import PlannerHTTP, TransportError
from convoy_contracts.execution import canonical_digest
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLAN_ECHO_FIELDS,
    SKILL_ID,
    validate_plan_result,
    validate_release_manifest,
)
from convoy_lerobot.acceptance import collect_evidence
from convoy_lerobot.artifact import reference_manifest, verify_assets, verify_versions
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from development_signing import development_grants

CADDY = "caddy@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b"
CADDYFILE = """{
    admin off
    persist_config off
    auto_https off
    grace_period 5s
    servers {
        protocols h1 h2
    }
}
https://localhost:8443 {
    tls /tmp/convoy-ingress/service.crt /tmp/convoy-ingress/service.key
    reverse_proxy planner:8080 {
        lb_retries 0
        lb_try_duration 0s
        transport http {
            dial_timeout 3s
            response_header_timeout 35s
            keepalive off
        }
    }
}
"""
SCOPE = ("Actual Qwen fixed-task admission and pretrained SmolVLA/MuJoCo, seed0 CPU offline lockstep; "
         "verified host-to-Caddy TLS and private Docker network to Linux ARM64 planner. "
         "No WAN, AWS, Jetson, physical robot, general planning or timing-SLA qualification.")


def image_tools():
    spec = importlib.util.spec_from_file_location("convoy_network_image_tools", ROOT / "infra/planner/verify_image.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_bytes(path: Path, content: bytes, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)


def copy_ingress(directory: Path, container: str):
    """Keep keys private without inheriting host UIDs across Docker Desktop."""
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        root = tarfile.TarInfo("convoy-ingress")
        root.type, root.mode = tarfile.DIRTYPE, 0o700
        archive.addfile(root)  # TarInfo defaults to container root uid/gid 0.
        for name in ("Caddyfile", "service.crt", "service.key"):
            data = (directory / name).read_bytes()
            member = tarfile.TarInfo("convoy-ingress/" + name)
            member.mode, member.size = 0o600, len(data)
            archive.addfile(member, io.BytesIO(data))
    subprocess.run(["docker", "cp", "-", container + ":/tmp/"],
                   input=payload.getvalue(), capture_output=True, check=True)


def certificates(directory: Path) -> tuple[Path, Path, Path]:
    """Only the ingress key is written; CA signing keys stay in this process."""
    now = dt.datetime.now(dt.UTC)

    def ca(label):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)])
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .sign(key, hashes.SHA256()))
        return key, certificate

    key, root = ca("Convoy ephemeral network test CA")
    _, wrong = ca("Untrusted independent network test CA")
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(root.subject).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
        .sign(key, hashes.SHA256()))
    ingress = directory / "ingress"
    ingress.mkdir(mode=0o700)
    trusted, untrusted = directory / "ca.crt", directory / "wrong-ca.crt"
    for path, certificate in ((trusted, root), (untrusted, wrong), (ingress / "service.crt", leaf)):
        private_bytes(path, certificate.public_bytes(serialization.Encoding.PEM))
    private_bytes(ingress / "service.key", leaf_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private_bytes(ingress / "Caddyfile", CADDYFILE.encode())
    return trusted, untrusted, ingress


def collect_plan(output: Path, manifest: dict, probe: dict) -> dict:
    with sqlite3.connect(f"{(output / 'robot/coordinator/execution.sqlite3').as_uri()}?mode=ro", uri=True) as journal:
        rows = journal.execute("SELECT mission_id,request_json,result_json,state FROM plans").fetchall()
    if len(rows) != 1:
        raise RuntimeError("the one mission must have exactly one durable planner request")
    mission, request, response, state = rows[0]
    request, result = json.loads(request), validate_plan_result(json.loads(response))
    if (state != "accepted" or result["identity"]["mission_id"] != mission
            or any(result[key] != request[key] for key in PLAN_ECHO_FIELDS)
            or any(result[key] != value for key, value in probe.items())
            or result["identity"]["release_digest"] != canonical_digest(manifest)
            or result["decision"] != {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}}):
        raise RuntimeError("durable plan differs from the live qualified planner or mission")
    return {"mission_id": mission, "state": state, "request": request, "result": result}


def record_outcome(evidence: dict, result: dict, *, require_comparison: bool) -> dict:
    """Retain the measured comparison even when it disqualifies this run."""
    case = result["cases"][0]
    comparison = result.get("direct_comparison")
    evidence.update(mission=case["mission"], episode=case["episode"], direct_comparison=comparison)
    summary = case["episode"]["summary"]
    if case["mission"]["state"] != "completed" or summary["final_success"] is not True:
        raise RuntimeError("the managed mission did not complete the qualified task")
    steps = summary["steps"]
    if require_comparison and (
        not isinstance(comparison, dict) or comparison.get("exact_actions_equal") is not True
        or comparison.get("direct_success") is not True or type(steps) is not int or steps < 1
        or comparison.get("direct_steps") != steps or comparison.get("managed_steps") != steps
    ):
        raise RuntimeError("the supplied direct baseline differs from the successful managed rollout")
    return case


def run(args) -> dict:
    if not re.fullmatch(r"(?:sha256:[a-f0-9]{64}|[^\s]+@sha256:[a-f0-9]{64})", args.planner_image):
        raise ValueError("planner image must be an immutable local image ID or repository digest")
    if bool(args.direct_report) != bool(args.direct_trace):
        raise ValueError("supply both direct comparison files or neither")
    verify_versions()
    verify_assets(args.action_assets.resolve(strict=True))
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    shutil.copyfile(__file__, output / "network-planner-source.py")
    tools = image_tools()
    evidence = {"schema_version": 1, "status": "failed", "scope": SCOPE, "planner_backend_kind": "llamacpp-text-model",
                "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    before = tools.existing_services()
    network = ingress_network = ingress_id = served = None
    ingress_removed = network_removed = ingress_network_removed = True
    port = None
    credentials = None
    cleanup_errors = []
    try:
        image = tools.text("docker", "image", "inspect", args.planner_image, "--format", "{{.Id}}")
        caddy = tools.text("docker", "image", "inspect", CADDY, "--format", "{{.Id}}")
        if tools.text("docker", "image", "inspect", image, "--format", "{{.Os}}/{{.Architecture}}") != "linux/arm64":
            raise ValueError("planner image must be Linux ARM64")
        evidence.update(planner_image_id=image, ingress_image=CADDY, ingress_image_id=caddy,
                        source_commit=tools.text("git", "-C", str(ROOT), "rev-parse", "HEAD"),
                        source_dirty=bool(tools.text("git", "-C", str(ROOT), "status", "--porcelain")))
        inspected = tools.Case("inspect", image, output)
        try:
            inspected.start()
            identity = inspected.await_ready()
            assets = tools.copy_json(inspected.container, "/opt/convoy/assets/assets.json")
            tools.verify_identity(identity, assets)
            if identity["gateway_identity"]["model_sha256"] != "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e":
                raise RuntimeError("network qualification requires the pinned Qwen model")
            inspected.terminal(expected_exit=0)
        finally:
            inspected.close()
        manifest = validate_release_manifest({"schema_version": 2, "profile": PAIRED_PROFILE,
            "action_manifest": reference_manifest(), "planner": identity["planner"],
            "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
            "planning": {"timeout_ms": 30000},
            "placement": {"policy": "development-local-cpu", "planner": "development-local"}})
        tools.write(output / "release.json", manifest)
        evidence.update(release_digest=canonical_digest(manifest), planner_artifact=identity["artifact"])
        with tempfile.TemporaryDirectory(prefix="convoy-network-planner-") as temporary:
            credentials = Path(temporary)
            signing, public = development_grants(credentials)
            probe_token = secrets.token_urlsafe(48)
            ca, wrong_ca, ingress_files = certificates(credentials)
            env_file = credentials / "planner.env"
            planner_public = json.loads(Path(public["CONVOY_PLANNER_VERIFICATION_KEYS_FILE"]).read_text())
            private_bytes(env_file, ("CONVOY_PLANNER_VERIFICATION_JSON=" + json.dumps(planner_public)
                + "\nCONVOY_PLANNER_PROBE_TOKEN=" + probe_token + "\n").encode())
            evidence["public_verification"] = {purpose: json.loads(Path(path).read_text()) for purpose, path in (
                ("action", public["CONVOY_ACTION_VERIFICATION_KEYS_FILE"]),
                ("planner", public["CONVOY_PLANNER_VERIFICATION_KEYS_FILE"]))}
            evidence["tls"] = {"hostname": "localhost", "ca_sha256": tools.sha(ca),
                               "certificate_sha256": tools.sha(ingress_files / "service.crt"),
                               "proxy_retries": 0, "upstream": "private HTTP within the internal Docker network"}
            private_bytes(output / "Caddyfile", CADDYFILE.encode())
            release = credentials / "release.json"
            private_bytes(release, json.dumps(manifest).encode(), mode=0o444)
            network = tools.text("docker", "network", "create", "--internal", "convoy-network-check-" + uuid.uuid4().hex[:12])
            network_removed = False
            # Keep the planner on its internal bridge; use a separate ingress
            # bridge for the host port. Only Caddy joins this second bridge.
            ingress_network = tools.text("docker", "network", "create", "convoy-ingress-check-" + uuid.uuid4().hex[:12])
            ingress_network_removed = False
            served = tools.Case("planner", image, output)
            served.container = tools.text("docker", "create", "--pull=never", "--name", served.name,
                "--init", "--network", network, "--network-alias", "planner", "--cpus=2", "--memory=4g",
                "--pids-limit=256", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--env-file", str(env_file), image)
            tools.run("docker", "cp", str(release), served.container + ":/run/convoy/release.json", capture_output=True)
            planner_info = json.loads(tools.text("docker", "inspect", served.container))[0]
            if planner_info["Image"] != image or planner_info["HostConfig"]["PortBindings"]:
                raise RuntimeError("planner image identity or private endpoint changed")
            tools.run("docker", "start", served.container, capture_output=True)
            live = served.await_ready()
            tools.verify_identity(live, assets)
            if live["planner"] != manifest["planner"]:
                raise RuntimeError("serving planner differs from the inspected immutable artifact")
            # The pinned upstream binary's file capability needs this bounding-set
            # entry to execute, even though our listener uses the high port 8443.
            ingress_id = tools.text("docker", "create", "--pull=never", "--name", "convoy-tls-check-" + uuid.uuid4().hex[:12],
                "--network", ingress_network, "--publish", "127.0.0.1::8443", "--cpus=0.5", "--memory=128m",
                "--pids-limit=64", "--cap-drop=ALL", "--cap-add=NET_BIND_SERVICE", "--security-opt=no-new-privileges",
                "--tmpfs", "/data:rw,nosuid,nodev,size=4m", "--tmpfs", "/config:rw,nosuid,nodev,size=4m",
                "--user=0:0", "--entrypoint=caddy", caddy,
                "run", "--config", "/tmp/convoy-ingress/Caddyfile", "--adapter", "caddyfile")
            ingress_removed = False
            tools.run("docker", "network", "connect", network, ingress_id, capture_output=True)
            copy_ingress(ingress_files, ingress_id)
            tools.run("docker", "start", ingress_id, capture_output=True)
            binding = tools.text("docker", "port", ingress_id, "8443/tcp")
            if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", binding):
                raise RuntimeError("TLS ingress must publish only one host-loopback port")
            port = int(binding.rsplit(":", 1)[1])
            network_info = json.loads(tools.text("docker", "network", "inspect", network))[0]
            ingress_info = json.loads(tools.text("docker", "network", "inspect", ingress_network))[0]
            def network_ids(container):
                connections = json.loads(tools.text("docker", "inspect", "--format",
                                                   "{{json .NetworkSettings.Networks}}", container))
                return {item["NetworkID"] for item in connections.values()}
            planner_networks, ingress_networks = network_ids(served.container), network_ids(ingress_id)
            if (network_info["Internal"] is not True
                    or set(network_info["Containers"]) != {ingress_id, served.container}
                    or ingress_info["Internal"] is not False or set(ingress_info["Containers"]) != {ingress_id}
                    or planner_networks != {network} or ingress_networks != {network, ingress_network}):
                raise RuntimeError("network topology differs from isolated planner and dedicated TLS ingress")
            evidence["network"] = {"internal": True, "network_id": network, "planner_container_id": served.container,
                "ingress_container_id": ingress_id, "planner_published_ports": [], "ingress_binding": binding,
                "ingress_network_id": ingress_network, "ingress_internal": False,
                "planner_network_ids": sorted(planner_networks), "ingress_network_ids": sorted(ingress_networks)}
            url = f"https://localhost:{port}"
            client = PlannerHTTP(url, probe_token, ca_file=str(ca))
            deadline = time.monotonic() + 20
            while True:
                try:
                    probe = pipeline.probe_external_planner(client, manifest)
                    break
                except TransportError:
                    if time.monotonic() >= deadline or not tools.inspect_state(ingress_id)["Running"]:
                        raise
                    time.sleep(0.1)  # Readiness only; model proposals are never retried.
            evidence["serving_probe"] = probe
            with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
                context = ssl.create_default_context(cafile=str(wrong_ca))
                try:
                    with context.wrap_socket(connection, server_hostname="localhost"):
                        raise RuntimeError("the unrelated test CA was incorrectly trusted")
                except ssl.SSLCertVerificationError:
                    evidence["tls"]["wrong_ca_handshake_rejected"] = True
            arguments = {"manifest": manifest, "runtime_factory": "convoy_lerobot.runtime:smolvla",
                "coordinator_module": "convoy_agent.coordinator.paired", "policy_kind": "pretrained_vision_language_action",
                "external_planner_url": url, "planner_probe_token": probe_token,
                "execution_signing_keys_file": Path(signing["CONVOY_EXECUTION_SIGNING_KEYS_FILE"]),
                "planner_evidence": SCOPE, "planner_backend_kind": "llamacpp-text-model"}
            wrong_output = output / "wrong-ca"
            try:
                pipeline.run(wrong_output, planner_ca_file=wrong_ca, **arguments)
                raise RuntimeError("pipeline unexpectedly admitted the wrong CA")
            except TransportError as error:
                if error.category != "transport" or (wrong_output / "api.log").exists() or (wrong_output / "robot").exists():
                    raise RuntimeError("wrong-CA rejection did not precede local process/action admission") from None
                evidence["wrong_ca_pipeline"] = {"rejected": True, "local_processes_started": 0, "actions": 0}
            # Fresh probe after the negative case separates TLS refusal from an unavailable planner.
            if pipeline.probe_external_planner(client, manifest) != probe:
                raise RuntimeError("planner identity changed before the real mission")
            active = time.monotonic()
            pipeline.run(output / "pipeline", planner_ca_file=ca, **arguments)
            evidence["pipeline_wall_seconds"] = time.monotonic() - active
            collect_evidence(output / "pipeline", args.direct_report, args.direct_trace)
            evidence["plan"] = collect_plan(output / "pipeline", manifest, probe)
            result = json.loads((output / "pipeline/visual-result.json").read_text())
            case = record_outcome(evidence, result, require_comparison=args.direct_report is not None)
            if (case["mission"]["id"] != evidence["plan"]["mission_id"]
                    or case["episode"]["summary"]["planner_backend_kind"] != "llamacpp-text-model"
                    or case["episode"]["summary"]["planner_accepted"] is not True):
                raise RuntimeError("episode does not belong to the admitted real-model plan")
            evidence.update(actions_sha256=tools.sha(output / "pipeline/actions.jsonl"), resources=served.resources())
            served.signal_owner()
            served.terminal(expected_exit=0)
            evidence["status"] = "passed"
    except BaseException as error:
        evidence["failure_type"] = type(error).__name__
        raise
    finally:
        # Only captured IDs are touched. Continue all cleanup even if one object failed.
        if ingress_id:
            try:
                tools.run("docker", "stop", "--time=10", ingress_id, capture_output=True)
                logs = subprocess.run(["docker", "logs", ingress_id], capture_output=True, check=False)
                private_bytes(output / "ingress.log", (logs.stdout + logs.stderr)[-65536:])
            except Exception as error:  # noqa: BLE001 - attempt every owned cleanup independently
                cleanup_errors.append({"operation": "ingress_stop", "type": type(error).__name__})
            try:
                tools.run("docker", "rm", "--force", "--volumes", ingress_id, capture_output=True)
                ingress_removed = True
            except Exception as error:  # noqa: BLE001 - attempt every owned cleanup independently
                cleanup_errors.append({"operation": "ingress_remove", "type": type(error).__name__})
        if served:
            try:
                served.close()
                cleanup_errors.extend(served.evidence.get("teardown_failures", []))
            except Exception as error:  # noqa: BLE001 - attempt every owned cleanup independently
                cleanup_errors.append({"operation": "planner_cleanup", "type": type(error).__name__})
        if network:
            try:
                tools.run("docker", "network", "rm", network, capture_output=True)
                network_removed = True
            except Exception as error:  # noqa: BLE001 - retain explicit cleanup uncertainty
                cleanup_errors.append({"operation": "network_remove", "type": type(error).__name__})
        if ingress_network:
            try:
                tools.run("docker", "network", "rm", ingress_network, capture_output=True)
                ingress_network_removed = True
            except Exception as error:  # noqa: BLE001 - remove each owned network independently
                cleanup_errors.append({"operation": "ingress_network_remove", "type": type(error).__name__})
        unchanged = False
        try:
            unchanged = tools.existing_services() == before
        except Exception as error:  # noqa: BLE001 - preserve evidence if Docker cannot be queried
            cleanup_errors.append({"operation": "verify_existing_services", "type": type(error).__name__})
        evidence["cleanup"] = {"credentials_removed": credentials is None or not credentials.exists(),
            "original_services_unchanged": unchanged, "ingress_removed": ingress_removed, "network_removed": network_removed,
            "ingress_network_removed": ingress_network_removed,
            "planner_removed": served is None or served.container is None or served.evidence.get("container_removed") is True,
            "tls_listener_closed": port is None or tools.closed(port), "errors": cleanup_errors}
        if cleanup_errors or not all(value for key, value in evidence["cleanup"].items() if key != "errors"):
            evidence["status"] = "failed"
        tools.write(output / "network-result.json", evidence)
    if evidence["status"] != "passed":
        raise RuntimeError("network qualification or owned cleanup failed; evidence retained")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--planner-image", required=True, help="existing immutable Linux ARM64 image ID or digest")
    parser.add_argument("--action-assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--direct-report", type=Path)
    parser.add_argument("--direct-trace", type=Path)
    args = parser.parse_args()
    os.environ.update(PYTHONPATH=os.pathsep.join(map(str, SOURCES)),
        CONVOY_SMOLVLA_ASSETS=str(args.action_assets.resolve()), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false",
        MUJOCO_GL="glfw" if sys.platform == "darwin" else "egl")
    def interrupted(*_):
        raise KeyboardInterrupt("network qualification interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = run(args)
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps({"status": result["status"], "scope": SCOPE,
                      "actions": result["episode"]["summary"]["steps"], "cleanup": result["cleanup"]}))


if __name__ == "__main__":
    main()
