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
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

import httpx
from convoy_agent.agent import enroll

from convoy_sim.runtimes import reference_manifest


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


@contextmanager
def disposable_postgres(env: dict, evidence: dict, output: Path):
    """Own one fresh database; never migrate or drop the caller's endpoint database."""
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    endpoint = os.environ.get("CONVOY_TEST_POSTGRES_URL")
    if not endpoint:
        raise ValueError("--postgres requires CONVOY_TEST_POSTGRES_URL with CREATE DATABASE permission")
    url = make_url(endpoint)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("CONVOY_TEST_POSTGRES_URL must use postgresql or postgresql+psycopg")
    name = "convoy_pipeline_" + uuid.uuid4().hex
    created = False
    evidence["database"] = {"backend": "postgresql", "name": name, "cleanup": "not_created"}
    # DDL uses autocommit because CREATE/DROP DATABASE cannot run in a transaction.
    with psycopg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False),
                         autocommit=True, connect_timeout=5,
                         options="-c statement_timeout=30000") as admin:
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created = True
            evidence["database"]["cleanup"] = "pending"
            # Non-secret ownership breadcrumb survives an uncatchable harness kill.
            (output / "postgres-database.json").write_text(json.dumps(evidence["database"], indent=2) + "\n")
            env["DATABASE_URL"] = url.set(drivername="postgresql+psycopg", database=name).render_as_string(
                hide_password=False,
            )
            yield
        finally:
            env.pop("DATABASE_URL", None)
            if created:
                # Only the unguessable database whose creation we acknowledged is owned here.
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
                evidence["database"]["cleanup"] = "dropped"
                (output / "postgres-database.json").write_text(json.dumps(evidence["database"], indent=2) + "\n")


def run(output: Path, *, faults: bool = False, serve: bool = False, postgres: bool = False) -> dict:
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
    for key in ("DATABASE_URL", "CONVOY_TEST_POSTGRES_URL", "CONVOY_SQLITE_WAL", "CONVOY_SEED_SIMULATOR"):
        env.pop(key, None)
    manifest = reference_manifest()
    manifest_file = output / "release.json"
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")
    processes, logs = [], []
    database_resources = ExitStack()

    def start(label, module, *args):
        log = (output / f"{label}.log").open("w")
        logs.append(log)
        child_env = dict(env)
        if module in {"convoy_worker.cli", "convoy_sim.managed"}:
            for key in ("DATABASE_URL", "CONVOY_ADMIN_EMAIL", "CONVOY_ADMIN_PASSWORD", "CONVOY_DATA_DIR"):
                child_env.pop(key, None)
        if module == "convoy_sim.managed":
            child_env.pop("CONVOY_EXECUTION_SECRET", None)
        process = subprocess.Popen([sys.executable, "-m", module, *map(str, args)], env=child_env,
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

    evidence = {"policy_kind": "scripted", "physics": "MuJoCo", "execution": "offline lockstep",
                "database": {"backend": "sqlite", "cleanup": "retained_in_output"},
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
        if postgres:
            database_resources.enter_context(disposable_postgres(env, evidence, output))
            migration = start("migration", "convoy_server.cli", "migrate")
            if migration.wait(timeout=60) != 0:
                raise RuntimeError("PostgreSQL migration failed; inspect migration.log")
            processes.remove(migration)  # a completed bootstrap task is not a live service
        api_process = start("api", "convoy_server.cli", "serve", "--host", "127.0.0.1", "--port", api_port)
        worker = start("worker", "convoy_worker.cli", "--release", manifest_file,
                       "--factory", "convoy_sim.runtimes:scripted", "--port", worker_port)
        with httpx.Client(base_url=base, timeout=5) as api:
            health = wait_for(lambda: api.get("/api/health"), lambda response: response.status_code == 200).json()
            if postgres:
                assert health["db"]["backend"] == "postgresql", health
                assert health["db"]["schema"]["compatible"] is True, health
                evidence["database"].update(server_version=health["db"]["server_version"],
                                            schema_revision=health["db"]["schema"]["revision"])
            wait_for(lambda: httpx.get(worker_url + "/health"), lambda response: response.status_code == 200)
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
                return start(label, "convoy_sim.managed", "--data-dir", output / "robot",
                             "--robot-id", robot["id"], "--worker-url", worker_url)

            coordinator = start_coordinator("coordinator")
            ready = wait_for(lambda: get(f"/api/v1/deployments/{deployment['id']}"),
                             lambda item: item["state"] in {"ready", "blocked"})
            assert ready["state"] == "ready", ready
            assert get(f"/api/v1/missions?project_id={project['id']}") == [], "deployment started a mission"

            if serve:
                connection = output / "connection.json"
                descriptor = os.open(connection, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as stream:
                    json.dump({"api_url": base, "worker_url": worker_url, "email": email,
                               "password": password, "project_id": project["id"], "robot_id": robot["id"],
                               "release_id": release["id"], "manifest_path": str(manifest_file),
                               "harness_pid": os.getpid()}, stream)
                print(f"Ready and idle. Local connection settings: {connection}", flush=True)
                stopped = threading.Event()
                signal.signal(signal.SIGTERM, lambda *_: stopped.set())
                signal.signal(signal.SIGINT, lambda *_: stopped.set())
                while not stopped.wait(0.5):
                    if any(process.poll() is not None for process in processes):
                        raise RuntimeError("a development service stopped; inspect its local log")
                evidence.update(status="stopped", release_digest=release["digest"], robot_id=robot["id"])
                return evidence

            def start_mission(seed):
                key = str(uuid.uuid4())
                body = {"deployment_id": deployment["id"], "expected_generation": deployment["generation"],
                        "seed": seed, "ttl_s": 120}
                mission = post(f"/api/v1/robots/{robot['id']}/missions", body, key)
                replay = post(f"/api/v1/robots/{robot['id']}/missions", body, key)
                assert mission["id"] == replay["id"], "start retry created another mission"
                return mission

            def finish(mission):
                result = wait_for(lambda: get(f"/api/v1/missions/{mission['id']}"),
                                  lambda item: item["state"] in {"completed", "failed", "cancelled", "unknown"},
                                  timeout=130)
                episode = get(f"/api/v1/episodes/{result['episode_id']}") if result.get("episode_id") else None
                return {"mission": result, "episode": episode}

            mission = start_mission(0)
            success = finish(mission)
            assert success["mission"]["state"] == "completed", success
            assert success["episode"] is not None
            assert success["episode"]["summary"]["final_success"] is True, success
            evidence["cases"].append({"case": "full_lifecycle", **success})

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

                mission = start_mission(0)
                wait_for(lambda: applied_count(mission["id"]), lambda count: count >= 10)
                stop(api_process)

                def local_mission_state():
                    path = output / "robot" / "coordinator" / "execution.sqlite3"
                    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as journal:
                        return journal.execute("SELECT state FROM missions WHERE id=?", (mission["id"],)).fetchone()[0]

                assert wait_for(local_mission_state, lambda state: state in {"completed", "failed", "unknown"}) == "completed"
                assert applied_count(mission["id"]) == 500
                api_process = start("api-restarted", "convoy_server.cli", "serve", "--host", "127.0.0.1", "--port", api_port)
                wait_for(lambda: api.get("/api/health"), lambda response: response.status_code == 200)
                offline = finish(mission)
                assert offline["mission"]["state"] == "completed", offline
                evidence["cases"].append({"case": "management_outage_and_report_replay", **offline})

                mission = start_mission(1)
                wait_for(lambda: applied_count(mission["id"]), lambda count: count >= 10)
                stop(worker)
                unavailable = finish(mission)
                assert unavailable["mission"]["state"] == "failed", unavailable
                assert 10 <= unavailable["episode"]["summary"]["steps"] < 500
                evidence["cases"].append({"case": "inference_outage_stops_actions", **unavailable})
                worker = start("worker-restarted", "convoy_worker.cli", "--release", manifest_file,
                               "--factory", "convoy_sim.runtimes:scripted", "--port", worker_port)
                wait_for(lambda: httpx.get(worker_url + "/health"), lambda response: response.status_code == 200)

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
        cleanup_errors = []
        for process in reversed(processes):
            try:
                stop(process)
            except BaseException as error:
                cleanup_errors.append(f"process cleanup: {type(error).__name__}: {error}")
        for log in logs:
            try:
                log.close()
            except OSError as error:
                cleanup_errors.append(f"log cleanup: {error}")
        try:
            database_resources.close()  # after attempting to stop every service
        except BaseException as error:
            cleanup_errors.append(f"database cleanup: {type(error).__name__}: {error}")
        if cleanup_errors:
            evidence.update(status="failed", cleanup_errors=cleanup_errors)
        (output / "pipeline-result.json").write_text(json.dumps(evidence, indent=2) + "\n")
        if cleanup_errors:
            raise RuntimeError("pipeline cleanup failed; inspect pipeline-result.json")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--faults", action="store_true")
    parser.add_argument("--serve", action="store_true", help="leave a seeded, ready, idle stack for console use")
    parser.add_argument("--postgres", action="store_true",
                        help="create a disposable database using CONVOY_TEST_POSTGRES_URL; drop it on exit")
    args = parser.parse_args()
    if args.serve and args.faults:
        parser.error("choose the acceptance run (--faults) or an interactive stack (--serve)")
    def interrupted(*_):
        raise KeyboardInterrupt("pipeline interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = run(args.output.resolve(), faults=args.faults, serve=args.serve, postgres=args.postgres)
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps({"status": result["status"], "cases": [case["case"] for case in result["cases"]]}))


if __name__ == "__main__":
    main()
