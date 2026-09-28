"""Exercise the real API, inference worker and robot coordinator as separate processes.

Run in integrations/simulation with its `managed` extra. All services bind only
loopback, use generated credentials and are stopped on exit. Retains local state
and evidence in a fresh private output directory. This uses a SCRIPTED policy.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from convoy_agent.agent import enroll


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for(read, accept, *, timeout: float = 30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = read()
            if accept(last):
                return last
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    raise RuntimeError(f"timed out waiting for pipeline state; last={last}")


def run(output: Path, *, faults: bool = False, manifest: dict | None = None,
        runtime_factory: str = "convoy_sim.runtimes:scripted",
        coordinator_module: str = "convoy_sim.managed", policy_kind: str = "scripted", repeat: int = 1) -> dict:
    if not 1 <= repeat <= 3:
        raise ValueError("repeat must be between one and three")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(output, 0o700)
    api_port, worker_port = free_port(), free_port()
    while worker_port == api_port:
        worker_port = free_port()
    base, worker_url = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{worker_port}"
    email, password = "developer@convoy.local", secrets.token_urlsafe(32)
    env = {**os.environ, "CONVOY_DATA_DIR": str(output / "server"), "CONVOY_SIMULATOR": "1",
           "CONVOY_SCHEDULER_INPROCESS": "0", "CONVOY_ADMIN_EMAIL": email,
           "CONVOY_ADMIN_PASSWORD": password, "CONVOY_PUBLIC_URL": base,
           "CONVOY_EXECUTION_SECRET": secrets.token_urlsafe(48),
           "CONVOY_WORKER_PROBE_TOKEN": secrets.token_urlsafe(48), "CONVOY_LOG_LEVEL": "WARNING"}
    # Explicit local configuration overrides inherited connection settings.
    for key in ("DATABASE_URL", "CONVOY_SQLITE_WAL", "CONVOY_SEED_SIMULATOR"):
        env.pop(key, None)
    if manifest is None:
        from convoy_sim.runtimes import reference_manifest
        manifest = reference_manifest()
    manifest_file = output / "release.json"
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")
    processes, logs = [], []

    def start(label, module, *args):
        log = (output / f"{label}.log").open("w")
        logs.append(log)
        process = subprocess.Popen([sys.executable, "-m", module, *map(str, args)], env=env,
                                   stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    evidence = {"policy_kind": policy_kind, "physics": "MuJoCo", "execution": "offline lockstep",
                "services": ["management API", "inference worker", "robot coordinator"], "cases": []}
    try:
        source_root = Path(__file__).resolve().parents[2]
        evidence["source_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_root, text=True, timeout=5,
        ).strip()
        evidence["source_dirty"] = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=source_root, text=True, timeout=5,
        ).strip())
    except (OSError, subprocess.SubprocessError):
        evidence["source_commit"] = None
    try:
        start("api", "convoy_server.cli", "serve", "--host", "127.0.0.1", "--port", api_port)
        worker = start("worker", "convoy_worker.cli", "--release", manifest_file,
                       "--factory", runtime_factory, "--port", worker_port)
        with httpx.Client(base_url=base, timeout=5) as api:
            wait_for(lambda: api.get("/api/health"), lambda response: response.status_code == 200)
            wait_for(lambda: httpx.get(worker_url + "/health"), lambda response: response.status_code == 200,
                     timeout=120)
            response = api.post("/api/v1/auth/login", json={"email": email, "password": password})
            response.raise_for_status()
            api.headers["X-Convoy-Client"] = "web"

            def post(path, body, key=None):
                response = api.post(path, json=body, headers={"Idempotency-Key": key or str(uuid.uuid4())})
                response.raise_for_status()
                return response.json()

            def get(path):
                response = api.get(path)
                response.raise_for_status()
                return response.json()

            project = post("/api/v1/projects", {"name": "Manipulation reference"})
            bootstrap = post("/api/v1/enrollments", {"label": "simulator", "simulated": True})
            enrolled = enroll(output / "robot", server=base, token=bootstrap["token"],
                              name="Virtual Sawyer", simulate=True)
            robot = post("/api/v1/robots", {"project_id": project["id"], "device_id": enrolled["device_id"],
                                           "name": "Virtual Sawyer", "profile": manifest["profile"]})
            application = post("/api/v1/applications", {"project_id": project["id"], "name": "Pick and place"})
            release = post(f"/api/v1/applications/{application['id']}/releases", {"manifest": manifest})
            deployment = post("/api/v1/deployments", {"robot_id": robot["id"], "release_id": release["id"],
                                                       "expected_generation": 0})

            def start_coordinator(label):
                return start(label, coordinator_module, "--data-dir", output / "robot",
                             "--robot-id", robot["id"], "--worker-url", worker_url)

            coordinator = start_coordinator("coordinator")
            ready = wait_for(lambda: get(f"/api/v1/deployments/{deployment['id']}"),
                             lambda item: item["state"] in {"ready", "blocked"})
            assert ready["state"] == "ready", ready
            assert get(f"/api/v1/missions?project_id={project['id']}") == [], "deployment started a mission"

            def start_mission(seed):
                key = str(uuid.uuid4())
                body = {"deployment_id": deployment["id"], "expected_generation": deployment["generation"],
                        "seed": seed, "ttl_s": min(300, manifest["execution"]["mission_timeout_s"]),}
                mission = post(f"/api/v1/robots/{robot['id']}/missions", body, key)
                replay = post(f"/api/v1/robots/{robot['id']}/missions", body, key)
                assert mission["id"] == replay["id"], "start retry created another mission"
                return mission

            def finish(mission):
                result = wait_for(lambda: get(f"/api/v1/missions/{mission['id']}"),
                                  lambda item: item["state"] in {"completed", "failed", "cancelled", "unknown"},
                                  timeout=min(300, manifest["execution"]["mission_timeout_s"]) + 15)
                episode = get(f"/api/v1/episodes/{result['episode_id']}") if result.get("episode_id") else None
                return {"mission": result, "episode": episode}

            for repetition in range(repeat):
                mission = start_mission(0)
                success = finish(mission)
                assert success["mission"]["state"] == "completed", success
                assert success["episode"] is not None
                assert success["episode"]["summary"]["final_success"] is True, success
                label = "full_lifecycle" if repetition == 0 else f"full_lifecycle_repeat_{repetition + 1}"
                evidence["cases"].append({"case": label, **success})

            if faults:
                # Read-only evidence check ensures failures occur after actual
                # physics steps, not merely during environment initialization.
                def applied_count(mission_id):
                    path = output / "robot" / "coordinator" / "execution.sqlite3"
                    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as journal:
                        return journal.execute(
                            "SELECT count(*) FROM commands WHERE mission_id=? AND state='applied'",
                            (mission_id,),
                        ).fetchone()[0]

                # A terminal mission must not block a new deployment generation.
                deployment = post("/api/v1/deployments", {
                    "robot_id": robot["id"], "release_id": release["id"],
                    "expected_generation": deployment["generation"],
                })
                wait_for(lambda: get(f"/api/v1/deployments/{deployment['id']}"),
                         lambda item: item["state"] == "ready")
                stop(coordinator)
                mission = start_mission(0)
                coordinator = start_coordinator("coordinator-idle-restart")
                next_run = finish(mission)
                assert next_run["mission"]["state"] == "completed", next_run
                evidence["cases"].append({"case": "redeploy_and_idle_restart", **next_run})

                mission = start_mission(1)
                wait_for(lambda: applied_count(mission["id"]), lambda count: count >= 10)
                post(f"/api/v1/missions/{mission['id']}/cancel", {"reason": "acceptance test"})
                cancelled = finish(mission)
                assert cancelled["mission"]["state"] == "cancelled", cancelled
                assert 10 <= cancelled["episode"]["summary"]["steps"] < 500
                evidence["cases"].append({"case": "cancel_running", **cancelled})

                mission = start_mission(2)
                wait_for(lambda: applied_count(mission["id"]), lambda count: count >= 10)
                # Abrupt loss exercises durable command/mission reconciliation.
                coordinator.kill()
                coordinator.wait(timeout=5)
                commands_before_restart = applied_count(mission["id"])
                coordinator = start_coordinator("coordinator-restarted")
                recovered = finish(mission)
                assert recovered["mission"]["state"] == "unknown", recovered
                assert applied_count(mission["id"]) == commands_before_restart
                recovered["applied_commands_before_restart"] = commands_before_restart
                response = api.post(f"/api/v1/robots/{robot['id']}/missions", json={
                    "deployment_id": deployment["id"], "expected_generation": deployment["generation"],
                    "seed": 3, "ttl_s": 120}, headers={"Idempotency-Key": str(uuid.uuid4())})
                assert response.status_code == 409, response.text
                evidence["cases"].append({"case": "restart_requires_recovery", **recovered})

            evidence.update(status="passed", release_digest=release["digest"], robot_id=robot["id"])
            stop(coordinator)
            stop(worker)
    except BaseException as error:
        evidence.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        for process in reversed(processes):
            stop(process)
        for log in logs:
            log.close()
        (output / "pipeline-result.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--faults", action="store_true")
    args = parser.parse_args()
    result = run(args.output.resolve(), faults=args.faults)
    print(json.dumps({"status": result["status"], "cases": [case["case"] for case in result["cases"]]}))


if __name__ == "__main__":
    main()
