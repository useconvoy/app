"""Actual single-service onboarding and recovery with API-private signing keys and native physics."""
import json
import os
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("convoy_server", reason="requires the managed extra")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from convoy_agent.agent import AgentConfig  # noqa: E402
from convoy_agent.owned_process import OwnedProcess  # noqa: E402
from convoy_server import db  # noqa: E402
from convoy_server.app import create_app  # noqa: E402
from convoy_server.config import Settings  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from test_robot_qualification import fixture as model_fixture  # noqa: E402


def key_document():
    keys = []
    for purpose in ("action", "planner"):
        key = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        keys.append({"kid": purpose + "-1", "purpose": purpose, "audience": "convoy-" + purpose, "private_key_pem": key})
    return {"schema_version": 1, "issuer": "service-test", "active": {purpose: purpose + "-1" for purpose in ("action", "planner")}, "keys": keys}


def test_service_waits_for_registration_qualifies_runs_and_recovers_owned_worker(tmp_path):
    runtime_python = os.environ.get("CONVOY_TEST_RUNTIME_PYTHON", sys.executable)
    private = tmp_path / "api-private-keys.json"
    private.write_text(json.dumps(key_document()))
    private.chmod(0o600)
    password = secrets.token_urlsafe(24)
    settings = Settings(data_dir=tmp_path / "api", simulator=True, scheduler_inprocess=False,
        bootstrap_admin_email="service@example.test", bootstrap_admin_password=password,
        execution_signing_keys_file=str(private), execution_secret=None, planner_execution_secret=None)
    db.reset_engine()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = settings.public_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(settings, start_scheduler=False), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    service = state = None
    env = {key: value for key, value in os.environ.items() if key not in {
        "CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET", "CONVOY_EXECUTION_SIGNING_KEYS_FILE",
        "CONVOY_ACTION_VERIFICATION_KEYS_FILE", "CONVOY_PLANNER_VERIFICATION_KEYS_FILE"}}
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        with httpx.Client(base_url=origin, timeout=10, headers={"X-Convoy-Client": "web"}) as api, (tmp_path / "service.log").open("w") as log:
            assert api.post("/api/v1/auth/login", json={"email": settings.bootstrap_admin_email, "password": password}).status_code == 200

            def post(path, body):
                result = api.post("/api/v1/" + path, json=body, headers={"Idempotency-Key": secrets.token_hex(12)})
                assert result.is_success, result.text
                return result.json()

            def wait(check):
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    if check():
                        return
                    assert service is None or service.poll() is None, (tmp_path / "service.log").read_text()
                    time.sleep(.05)
                pytest.fail("service did not reach expected state: " + (tmp_path / "service.log").read_text())

            def get(path):
                result = api.get("/api/v1/" + path)
                assert result.is_success, result.text
                return result.json()

            project = post("projects", {"name": "One-command simulator"})
            setup = post("robot-connections/enrollments", {"project_id": project["id"], "name": "Arm runner", "simulated": True})
            claim = subprocess.run([runtime_python, "-m", "convoy_agent.cli", *shlex.split(setup["command"])[1:]],
                                   cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
            assert claim.returncode == 0, claim.stderr
            state = tmp_path / setup["data_dir"]
            command = [runtime_python, "-m", "convoy_sim.service", *shlex.split(setup["simulator_command"])[1:]]

            def start():
                return subprocess.Popen(command, cwd=tmp_path, env=env, stdout=log, stderr=log)

            service = start()
            status = state / "simulator-service/status.json"
            wait(lambda: status.exists() and json.loads(status.read_text())["phase"] == "waiting-for-registration")
            device = AgentConfig(state).data["device_id"]
            wait(lambda: get("robot-connections/" + device)["status"] == "online")
            duplicate = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=10)
            assert duplicate.returncode != 0 and service.poll() is None

            asset, spec, model = model_fixture(tmp_path)
            model["asset"]["uri"] = "artifact:service-arm"
            model["evidence"] = {"source": "imported"}
            spec.update(embodiment="arm", adapter="direct-joints", dynamics={"source": "unknown"}, simulations=[model])
            for joint in spec["joints"]:
                joint["evidence"] = {"source": "imported"}
            profile = post("robot-profiles", {"project_id": project["id"], "name": "Arm", "spec": spec, "expected_revision": 0})
            assert api.post(f"/api/v1/robot-profiles/{profile['id']}/simulation-assets/mujoco", content=asset.read_bytes(),
                            headers={"Content-Type": "application/octet-stream"}).status_code == 201
            robot = post("robot-registrations", {"project_id": project["id"], "name": "Arm sim", "kind": "simulated",
                "device_id": device, "profile_id": profile["id"], "simulation_engine": "mujoco"})
            post(f"robots/{robot['id']}/qualification", {})
            wait(lambda: get(f"robots/{robot['id']}")["qualification"]["state"] == "passed")
            public = state / "simulator-service/action-keys.json"
            assert "private_key" not in public.read_text()
            assert json.loads(public.read_text())["keys"][0]["kid"] == "action-1"
            config = post("configurations", {"project_id": project["id"], "name": "Reach target", "configuration": {
                "profile_id": profile["id"], "policy": {"kind": "reference"}, "instruction": "Reach", "targets": {"shoulder": .25}}})
            deployment = post("deployments", {"robot_id": robot["id"], "release_id": config["release"]["id"], "expected_generation": 0})
            wait(lambda: get("deployments/" + deployment["id"])["state"] == "ready")

            def run_task():
                task = post(f"robots/{robot['id']}/missions", {"deployment_id": deployment["id"], "expected_generation": 1, "seed": 0, "ttl_s": 60})
                wait(lambda: get("missions/" + task["id"])["state"] in {"completed", "failed", "unknown"})
                result = get("missions/" + task["id"])
                assert result["state"] == "completed", result
                episode = get("episodes/" + result["episode_id"])
                assert episode["summary"]["final_success"]
                from convoy_sim.trajectory import load_trace

                recording = episode["summary"]["recording"]
                assert recording["state"] == "recorded-locally"
                trajectory = load_trace(state / "trajectories" / f"{task['id']}.state.json", recording["sha256"])
                assert trajectory["identity"] == episode["identity"]
                return task["id"]

            first = run_task()
            # Rotation delivers only public material and existing worker verifiers reload it.
            rotated = json.loads(private.read_text())
            fresh = key_document()["keys"][0]
            fresh["kid"] = "action-2"
            rotated["keys"].append(fresh)
            rotated["active"]["action"] = "action-2"
            replacement = private.with_suffix(".next")
            replacement.write_text(json.dumps(rotated))
            replacement.chmod(0o600)
            replacement.replace(private)
            wait(lambda: len(json.loads(public.read_text())["keys"]) == 2)
            run_task()
            process = state / "managed-worker/process.json"
            before = json.loads(process.read_text())
            # Simulate owner loss while idle. The successor must stop the proven orphan
            # before it can prepare a replacement; completed tasks must never replay.
            service.kill()
            service.wait(timeout=10)
            service = start()
            wait(lambda: json.loads(process.read_text())["marker"] != before["marker"])
            wait(lambda: get("deployments/" + deployment["id"])["state"] == "ready")
            assert get("missions/" + first)["state"] == "completed"
            run_task()
            service.terminate()
            service.wait(timeout=45)
            assert service.returncode == 0, (tmp_path / "service.log").read_text()
            service = None
            assert json.loads(process.read_text())["state"] == "stopped"
            assert json.loads(status.read_text())["phase"] == "stopped"
    finally:
        if service is not None and service.poll() is None:
            service.terminate()
            try:
                service.wait(timeout=45)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=5)
        if state is not None and (state / "managed-worker/process.json").exists():
            with OwnedProcess(state / "managed-worker") as owner:
                owner.stop()
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        db.reset_engine()
        assert not thread.is_alive()
