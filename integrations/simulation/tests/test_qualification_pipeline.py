"""Real enrollment, HTTP API, persisted profile lineage and isolated native simulator."""
from __future__ import annotations

import json
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
from test_robot_qualification import fixture as model_fixture  # noqa: E402


def test_registered_physical_robot_has_a_verified_simulation(tmp_path):
    password = secrets.token_urlsafe(24)
    settings = Settings(data_dir=tmp_path / "server", simulator=True, scheduler_inprocess=False,
                        bootstrap_admin_email="qualification@example.test", bootstrap_admin_password=password)
    db.reset_engine()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(settings, start_scheduler=False), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
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
            # The next request really rereads the installed bytes and persists a failure.
            (assets / model["asset"]["sha256"]).write_bytes(b"changed asset")
            post(f"robots/{robot['id']}/qualification", {})
            failed = subprocess.run(command, capture_output=True, text=True, timeout=75, check=False)
            assert failed.returncode == 1
            observed = client.get(f"/api/v1/robots/{robot['id']}").json()["qualification"]
            assert observed["state"] == "failed"
            assert "digest" in observed["report"]["detail"]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        db.reset_engine()
        assert not thread.is_alive(), "local qualification API did not shut down"
