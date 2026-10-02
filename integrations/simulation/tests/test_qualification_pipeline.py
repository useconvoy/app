"""Real enrollment, HTTP API, persisted profile lineage and isolated native simulator."""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("convoy_server", reason="install the managed extra for the HTTP pipeline")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from convoy_agent.agent import AgentConfig, enroll  # noqa: E402
from convoy_server import db  # noqa: E402
from convoy_server.app import create_app  # noqa: E402
from convoy_server.config import Settings  # noqa: E402
from convoy_worker.app import create_app as create_worker  # noqa: E402
from test_robot_qualification import fixture as model_fixture  # noqa: E402

from convoy_sim.joint_reference import JointTargetRuntime  # noqa: E402


def test_registered_physical_robot_has_a_verified_simulation(tmp_path):
    password = secrets.token_urlsafe(24)
    execution_secret, probe_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    settings = Settings(data_dir=tmp_path / "server", simulator=True, scheduler_inprocess=False,
                        bootstrap_admin_email="qualification@example.test", bootstrap_admin_password=password,
                        execution_secret=execution_secret)
    db.reset_engine()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(settings, start_scheduler=False), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    worker_server = worker_thread = worker_socket = coordinator = None
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "local qualification API did not start"
        with httpx.Client(base_url=origin, timeout=10, headers={"X-Convoy-Client": "web"}) as client:
            response = client.post("/api/v1/auth/login", json={"email": settings.bootstrap_admin_email, "password": password})
            assert response.status_code == 200

            def post(path, body):
                response = client.post(f"/api/v1/{path}", json=body, headers={"Idempotency-Key": secrets.token_hex(12)})
                assert response.is_success, response.text
                return response.json()

            project = post("projects", {"name": "Actual simulator acceptance"})
            asset, spec, model = model_fixture(tmp_path)
            model["asset"]["uri"] = "artifact:qualification-arm"
            model["evidence"] = {"source": "imported"}
            spec.update(embodiment="arm", adapter="direct-joints", dynamics={"source": "unknown"}, simulations=[model])
            for joint in spec["joints"]:
                joint["evidence"] = {"source": "imported"}
            profile = post("robot-profiles", {"project_id": project["id"], "name": "Custom arm", "expected_revision": 0, "spec": spec})
            source = None
            for simulated in (False, True):
                token = post("enrollments", {"label": "acceptance", "simulated": simulated})["token"]
                state = tmp_path / ("simulator" if simulated else "physical")
                enroll(state, server=origin, token=token, name=state.name, simulate=simulated)
                cfg = AgentConfig(state)
                body = {"project_id": project["id"], "name": state.name, "profile_id": profile["id"],
                        "device_id": cfg.data["device_id"], "kind": "simulated" if simulated else "physical"}
                if simulated:
                    body.update(source_robot_id=source["id"], simulation_engine="mujoco")
                robot = post("robot-registrations", body)
                if not simulated:
                    source = robot
            assert robot["source_robot_id"] == source["id"]
            assert robot["profile_id"] == source["profile_id"] == profile["id"]
            assert robot["qualification"] is None
            assets = tmp_path / "assets"
            assets.mkdir()
            (assets / model["asset"]["sha256"]).write_bytes(asset.read_bytes())
            requested = post(f"robots/{robot['id']}/qualification", {})
            command = [sys.executable, "-m", "convoy_sim.qualification.runner", "--data-dir", str(state),
                       "--assets", str(assets), "--once"]
            run = subprocess.run(command, capture_output=True, text=True, timeout=75, check=False)
            assert run.returncode == 0, run.stderr
            assert json.loads(run.stdout)["state"] == "passed"
            observed = client.get(f"/api/v1/robots/{robot['id']}").json()["qualification"]
            assert observed["id"] == requested["id"]
            assert observed["state"] == "passed"
            assert observed["report"]["evidence"]["max_joint_displacement"] > 0
            assert observed["report"]["evidence"]["steps"] == 200
            # Restarting a runner does not repeat a terminal request.
            restart = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
            assert json.loads(restart.stdout)["state"] == "idle"

            class DelayedReference(JointTargetRuntime):
                delay = 0
                override = None

                def get_action(self, observation):
                    time.sleep(self.delay)
                    return self.override if self.override is not None else super().get_action(observation)

            configured = post("configurations", {"project_id": project["id"], "name": "Joint target",
                "configuration": {"profile_id": profile["id"], "policy": {"kind": "reference"},
                                  "instruction": "Reach the shoulder target", "targets": {"shoulder": 0.25}}})
            release = configured["release"]
            setup = client.get(f"/api/v1/applications/{configured['application']['id']}/releases/{release['id']}/setup")
            assert setup.status_code == 200
            manifest = json.loads(setup.json()["manifest_json"])
            runtime = DelayedReference(json.loads(setup.json()["reference_policy_json"]))
            assert manifest["policy"]["artifact_sha256"] == runtime.artifact_sha256
            deployment = post("deployments", {"robot_id": robot["id"], "release_id": release["id"], "expected_generation": 0})
            worker_socket = socket.socket()
            worker_socket.bind(("127.0.0.1", 0))
            worker_origin = f"http://127.0.0.1:{worker_socket.getsockname()[1]}"
            worker_server = uvicorn.Server(uvicorn.Config(create_worker(manifest, runtime, execution_secret=execution_secret,
                                                                      probe_token=probe_token), log_level="error"))
            worker_thread = threading.Thread(target=worker_server.run, kwargs={"sockets": [worker_socket]}, daemon=True)
            worker_thread.start()
            deadline = time.monotonic() + 10
            while not worker_server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert worker_server.started
            with (tmp_path / "coordinator.log").open("w") as log:
                coordinator = subprocess.Popen([sys.executable, "-m", "convoy_sim.registered", "--data-dir", str(state),
                                                "--assets", str(assets), "--worker-url", worker_origin],
                                               env={**os.environ, "CONVOY_WORKER_PROBE_TOKEN": probe_token},
                                               stdout=log, stderr=log)

                def wait(path, states):
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline:
                        response = client.get(f"/api/v1/{path}")
                        assert response.is_success, response.text
                        value = response.json()
                        if value["state"] in states:
                            return value
                        assert coordinator.poll() is None, (tmp_path / "coordinator.log").read_text()
                        time.sleep(0.03)
                    pytest.fail(f"execution did not reach {states}: {value}; {(tmp_path / 'coordinator.log').read_text()}")

                assert wait(f"deployments/{deployment['id']}", {"ready", "blocked"})["state"] == "ready"
                mission_body = {"deployment_id": deployment["id"], "expected_generation": 1, "seed": 0, "ttl_s": 60}
                mission = post(f"robots/{robot['id']}/missions", mission_body)
                result = wait(f"missions/{mission['id']}", {"completed", "failed", "unknown"})
                assert result["state"] == "completed", result
                episode = client.get(f"/api/v1/episodes/{result['episode_id']}").json()
                assert episode["summary"]["final_success"], episode
                assert episode["summary"]["model_asset_sha256"] == model["asset"]["sha256"]
                assert episode["summary"]["steps"] > 1
                assert episode["summary"]["execution_mode"] == "lockstep_offline"

                # A real slow worker makes cancellation race with actual in-flight inference.
                runtime.delay = 0.15
                mission = post(f"robots/{robot['id']}/missions", mission_body)
                wait(f"missions/{mission['id']}", {"running"})
                cancelled = post(f"missions/{mission['id']}/cancel", {"reason": "operator stopped task"})
                assert cancelled["state"] == "cancel_requested"
                result = wait(f"missions/{mission['id']}", {"cancelled", "completed", "failed", "unknown"})
                assert result["state"] == "cancelled", result
                runtime.delay, runtime.override = 0, [1.5]
                mission = post(f"robots/{robot['id']}/missions", mission_body)
                result = wait(f"missions/{mission['id']}", {"completed", "failed", "unknown"})
                assert result["state"] == "failed", result
                episode = client.get(f"/api/v1/episodes/{result['episode_id']}").json()
                assert episode["summary"]["steps"] == 0
                coordinator.terminate()
                coordinator.wait(timeout=10)
                coordinator = None

            # The next request really rereads the installed bytes and persists a failure.
            (assets / model["asset"]["sha256"]).write_bytes(b"changed asset")
            post(f"robots/{robot['id']}/qualification", {})
            failed = subprocess.run(command, capture_output=True, text=True, timeout=75, check=False)
            assert failed.returncode == 1
            observed = client.get(f"/api/v1/robots/{robot['id']}").json()["qualification"]
            assert observed["state"] == "failed"
            assert "digest" in observed["report"]["detail"]
    finally:
        if coordinator is not None:
            coordinator.terminate()
            try:
                coordinator.wait(timeout=10)
            except subprocess.TimeoutExpired:
                coordinator.kill()
                coordinator.wait(timeout=5)
        if worker_server:
            worker_server.should_exit = True
            worker_thread.join(timeout=10)
            worker_socket.close()
            assert not worker_thread.is_alive()
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        db.reset_engine()
        assert not thread.is_alive(), "local qualification API did not shut down"
