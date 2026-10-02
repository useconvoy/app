"""Exercise the real API, inference worker and robot coordinator as separate processes.

Run in integrations/simulation with its `managed` extra. Owned services bind only
loopback and stop on exit. An explicit external planner remains owned by its
caller and requires existing Ed25519 authority. Retains local state and evidence
in a fresh private directory. The default policy is SCRIPTED.
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
from convoy_agent.coordinator.transport import PlannerHTTP

EXECUTION_ENV = {
    "CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET",
    "CONVOY_EXECUTION_SIGNING_KEYS_FILE", "CONVOY_EXECUTION_SIGNING_JSON",
    "CONVOY_ACTION_VERIFICATION_KEYS_FILE", "CONVOY_ACTION_VERIFICATION_JSON",
    "CONVOY_PLANNER_VERIFICATION_KEYS_FILE", "CONVOY_PLANNER_VERIFICATION_JSON",
}
PROBE_ENV = {"CONVOY_WORKER_PROBE_TOKEN", "CONVOY_PLANNER_PROBE_TOKEN"}


def process_environment(env: dict, *, role: str) -> dict:
    """Pass only the execution authority used by this owned service."""
    allowed = {
        "api": {"CONVOY_EXECUTION_SIGNING_KEYS_FILE", "CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET"},
        "worker": {"CONVOY_ACTION_VERIFICATION_KEYS_FILE", "CONVOY_EXECUTION_SECRET", "CONVOY_WORKER_PROBE_TOKEN"},
        "planner": {"CONVOY_PLANNER_VERIFICATION_KEYS_FILE", "CONVOY_PLANNER_EXECUTION_SECRET", "CONVOY_PLANNER_PROBE_TOKEN"},
        "coordinator": PROBE_ENV,
        "jobs": set(),
    }[role]
    result = {key: value for key, value in env.items() if key not in (EXECUTION_ENV | PROBE_ENV) - allowed}
    if role in {"worker", "planner", "coordinator"}:
        for key in ("DATABASE_URL", "CONVOY_ADMIN_EMAIL", "CONVOY_ADMIN_PASSWORD", "CONVOY_DATA_DIR"):
            result.pop(key, None)
    elif role == "jobs":
        result.pop("CONVOY_ADMIN_EMAIL", None)
        result.pop("CONVOY_ADMIN_PASSWORD", None)
    return result


def probe_external_planner(client: PlannerHTTP, manifest: dict) -> dict:
    from convoy_contracts.execution import canonical_digest
    from convoy_contracts.pairing import validate_planner_identity

    reply = client.probe(canonical_digest(manifest), manifest["profile"])
    if (reply.get("ready") is not True or reply.get("release_digest") != canonical_digest(manifest)
            or reply.get("profile") != manifest["profile"] or reply.get("runtime") != manifest["planner"]["runtime"]
            or reply.get("planner_artifact_sha256") != manifest["planner"]["artifact_sha256"]):
        raise ValueError("external planner readiness differs from the requested release")
    return validate_planner_identity({key: reply.get(key) for key in (
        "planner_artifact_sha256", "planner_incarnation", "runtime_generation",
    )})


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
        except sqlite3.OperationalError as error:
            # The coordinator may still be writing the journal being read; a lock is transient.
            if "locked" not in str(error) and "busy" not in str(error):
                raise
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


def run(output: Path, *, faults: bool = False, serve: bool = False, postgres: bool = False,
        manifest: dict | None = None, runtime_factory: str = "convoy_sim.runtimes:scripted",
        coordinator_module: str = "convoy_sim.managed", policy_kind: str = "scripted", repeat: int = 1,
        planner_command: tuple[str, ...] | None = None, planner_evidence: str | None = None,
        planner_backend_kind: str | None = None, external_planner_url: str | None = None,
        planner_ca_file: Path | None = None, planner_probe_token: str | None = None,
        execution_signing_keys_file: Path | None = None, evaluation_job: bool = True) -> dict:
    if not 1 <= repeat <= 3:
        raise ValueError("repeat must be between one and three")
    if manifest is None:
        from convoy_sim.runtimes import reference_manifest
        manifest = reference_manifest()
    from convoy_contracts.pairing import PAIRED_PROFILE, action_manifest
    policy = action_manifest(manifest)
    paired = manifest["profile"] == PAIRED_PROFILE
    external = external_planner_url is not None
    if (paired != (bool(planner_command) or external)) or (planner_command and external):
        raise ValueError("paired releases require exactly one owned planner command or external planner endpoint")
    if paired and faults:
        raise ValueError("the scripted 500-step fault pack does not qualify the paired visual profile")
    if paired and (not planner_evidence or not planner_backend_kind):
        raise ValueError("paired acceptance must explicitly describe its planner evidence scope")
    if not external and (planner_ca_file is not None or planner_probe_token is not None):
        raise ValueError("explicit planner trust and probe settings require an external endpoint")
    external_client = None
    if external:
        if (execution_signing_keys_file is None or not isinstance(planner_probe_token, str)
                or len(planner_probe_token) < 32):
            raise ValueError("external planner requires existing signing authority and a planner probe credential")
        external_client = PlannerHTTP(external_planner_url, planner_probe_token, ca_file=planner_ca_file)
        if planner_ca_file is not None:
            planner_ca_file = Path(planner_ca_file).absolute()
    documents = {}
    if execution_signing_keys_file is not None:
        from convoy_contracts.grants import SigningKeys

        execution_signing_keys_file = Path(execution_signing_keys_file).absolute()
        signer = SigningKeys(execution_signing_keys_file)
        purposes = ("action", "planner") if planner_command else ("action",)
        documents = {purpose: signer.verification_document(purpose) for purpose in purposes}
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
           "CONVOY_WORKER_PROBE_TOKEN": secrets.token_urlsafe(48), "CONVOY_LOG_LEVEL": "WARNING"}
    # Explicit local configuration overrides inherited connection settings.
    for key in ("DATABASE_URL", "CONVOY_TEST_POSTGRES_URL", "CONVOY_SQLITE_WAL", "CONVOY_SEED_SIMULATOR",
                "CONVOY_PLANNER_PROBE_TOKEN", *EXECUTION_ENV):
        env.pop(key, None)
    if execution_signing_keys_file is not None:
        env["CONVOY_EXECUTION_SIGNING_KEYS_FILE"] = str(execution_signing_keys_file)
        public = output / "verification"
        public.mkdir(mode=0o700)
        for purpose, document in documents.items():
            path = public / (purpose + ".json")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(document, stream)
            env[f"CONVOY_{purpose.upper()}_VERIFICATION_KEYS_FILE"] = str(path.absolute())
    else:
        env["CONVOY_EXECUTION_SECRET"] = secrets.token_urlsafe(48)
        if paired:
            env["CONVOY_PLANNER_EXECUTION_SECRET"] = secrets.token_urlsafe(48)
    planner_port, planner_url = None, external_planner_url
    if planner_command:
        planner_port = free_port()
        while planner_port in {api_port, worker_port}:
            planner_port = free_port()
        planner_url = f"http://127.0.0.1:{planner_port}"
    if paired:
        env["CONVOY_PLANNER_PROBE_TOKEN"] = planner_probe_token if external else secrets.token_urlsafe(48)
    manifest_file = output / "release.json"
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")
    processes, logs = [], []
    database_resources = ExitStack()

    def start(label, module, *args):
        log = (output / f"{label}.log").open("w")
        logs.append(log)
        is_planner = planner_command and module == planner_command[0]
        role = ("planner" if is_planner else "worker" if module == "convoy_worker.cli"
                else "coordinator" if module == coordinator_module
                else "api" if module == "convoy_server.cli" and args[0] == "serve" else "jobs")
        child_env = process_environment(env, role=role)
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

    evidence = {"policy_kind": policy_kind, "physics": "MuJoCo", "execution": "offline lockstep",
                "database": {"backend": "sqlite", "cleanup": "retained_in_output"},
                "services": ["management API", "inference worker", "robot coordinator"], "cases": []}
    if paired:
        evidence.update(planner_evidence=planner_evidence, planner_backend_kind=planner_backend_kind,
                        configured_placement=manifest["placement"], planner_ownership="external" if external else "harness")
        evidence["services"].append("planner admission service")
    evidence["execution_authority"] = "ed25519" if execution_signing_keys_file is not None else "development-hmac"
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
        if external_client is not None:
            evidence["external_planner_probe_before_deployment"] = probe_external_planner(external_client, manifest)
        if postgres:
            database_resources.enter_context(disposable_postgres(env, evidence, output))
            migration = start("migration", "convoy_server.cli", "migrate")
            if migration.wait(timeout=60) != 0:
                raise RuntimeError("PostgreSQL migration failed; inspect migration.log")
            processes.remove(migration)  # a completed bootstrap task is not a live service
        api_process = start("api", "convoy_server.cli", "serve", "--host", "127.0.0.1", "--port", api_port)
        worker = start("worker", "convoy_worker.cli", "--release", manifest_file,
                       "--factory", runtime_factory, "--port", worker_port)
        if planner_command:
            start("planner", planner_command[0], *planner_command[1:], "--manifest", manifest_file,
                  "--port", planner_port)
        with httpx.Client(base_url=base, timeout=5) as api:
            health = wait_for(lambda: api.get("/api/health"), lambda response: response.status_code == 200).json()
            if postgres:
                assert health["db"]["backend"] == "postgresql", health
                assert health["db"]["schema"]["compatible"] is True, health
                evidence["database"].update(server_version=health["db"]["server_version"],
                                            schema_revision=health["db"]["schema"]["revision"])
            wait_for(lambda: httpx.get(worker_url + "/health"), lambda response: response.status_code == 200,
                     timeout=120)
            if planner_command:
                wait_for(lambda: httpx.get(planner_url + "/health"), lambda response: response.status_code == 200,
                         timeout=30)
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
                             "--robot-id", robot["id"], "--worker-url", worker_url,
                             *(["--planner-url", planner_url] if paired else []),
                             *(["--planner-ca-file", planner_ca_file] if planner_ca_file is not None else []))

            coordinator = start_coordinator("coordinator")
            ready = wait_for(lambda: get(f"/api/v1/deployments/{deployment['id']}"),
                             lambda item: item["state"] in {"ready", "blocked"})
            assert ready["state"] == "ready", ready
            assert get(f"/api/v1/missions?project_id={project['id']}") == [], "deployment started a mission"

            if serve:
                # A running job settles a requested cancellation on its next 0.2 s poll when no case
                # mission is active. A check that must observe that state first passes
                # evaluation_job=False and starts its own job; connection.json records which applies.
                if evaluation_job:
                    start("evaluations", "convoy_server.evaluation_worker")
                connection = output / "connection.json"
                descriptor = os.open(connection, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as stream:
                    json.dump({"api_url": base, "worker_url": worker_url, "email": email,
                               "password": password, "project_id": project["id"], "robot_id": robot["id"],
                               "release_id": release["id"], "manifest_path": str(manifest_file),
                               "harness_pid": os.getpid(), "planner_url": planner_url,
                               "planner_ownership": "external" if external else "harness" if paired else None,
                               "planner_evidence": planner_evidence,
                               "planner_backend_kind": planner_backend_kind,
                               "evaluation_job": evaluation_job}, stream)
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
                        "seed": seed, "ttl_s": min(300, policy["execution"]["mission_timeout_s"]),}
                mission = post(f"/api/v1/robots/{robot['id']}/missions", body, key)
                replay = post(f"/api/v1/robots/{robot['id']}/missions", body, key)
                assert mission["id"] == replay["id"], "start retry created another mission"
                return mission

            def finish(mission):
                result = wait_for(lambda: get(f"/api/v1/missions/{mission['id']}"),
                                  lambda item: item["state"] in {"completed", "failed", "cancelled", "unknown"},
                                  timeout=min(300, policy["execution"]["mission_timeout_s"]) + 15)
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
                               "--factory", runtime_factory, "--port", worker_port)
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
            except BaseException as error:  # noqa: BLE001 - still attempt every owned service's cleanup
                cleanup_errors.append(f"process cleanup: {type(error).__name__}: {error}")
        for log in logs:
            try:
                log.close()
            except OSError as error:
                cleanup_errors.append(f"log cleanup: {error}")
        try:
            database_resources.close()  # after attempting to stop every service
        except BaseException as error:  # noqa: BLE001 - preserve all cleanup failures in final evidence
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
    parser.add_argument("--no-evaluation-job", action="store_true",
                        help="with --serve: do not start the evaluation job; the caller starts and stops its own")
    args = parser.parse_args()
    if args.serve and args.faults:
        parser.error("choose the acceptance run (--faults) or an interactive stack (--serve)")
    if args.no_evaluation_job and not args.serve:
        parser.error("--no-evaluation-job applies only to an interactive stack (--serve)")
    def interrupted(*_):
        raise KeyboardInterrupt("pipeline interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = run(args.output.resolve(), faults=args.faults, serve=args.serve, postgres=args.postgres,
                     evaluation_job=not args.no_evaluation_job)
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps({"status": result["status"], "cases": [case["case"] for case in result["cases"]]}))


if __name__ == "__main__":
    main()
