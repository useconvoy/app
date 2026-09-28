"""Disposable local wrapper checks. No AWS calls, credentials or model downloads."""
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/app/aws-runtime")
sys.path.insert(0, "/app/runtime")


def signing_document():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    keys = []
    for purpose in ("action", "planner"):
        pem = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        keys.append({"kid": purpose + "-1", "purpose": purpose, "audience": "convoy-" + purpose,
                     "private_key_pem": pem})
    return {"schema_version": 1, "issuer": "convoy-wrapper-check",
            "active": {purpose: purpose + "-1" for purpose in ("action", "planner")}, "keys": keys}


def configure_signing():
    from convoy_contracts.grants import SigningKeys
    from entrypoint import configure_execution

    for role in ("scheduler", "scheduler-health", "evaluations", "migrate"):
        configure_execution(role)  # No signing material is required by database-only jobs.
    os.environ["CONVOY_EXECUTION_SIGNING_JSON"] = json.dumps(signing_document())
    for role in ("scheduler", "scheduler-health", "evaluations", "migrate"):
        try:
            configure_execution(role)
        except ValueError:
            pass
        else:
            raise AssertionError("database job accepted execution signing configuration")
    configure_execution("api")
    assert "CONVOY_EXECUTION_SIGNING_JSON" not in os.environ
    path = Path(os.environ["CONVOY_EXECUTION_SIGNING_KEYS_FILE"])
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    return SigningKeys(path)


def check_claim(client, signer):
    from convoy_contracts.execution import PROFILE
    from convoy_contracts.grants import GrantVerifier

    web = {"X-Convoy-Client": "web"}
    def post(path, body, headers=None, expected=201):
        response = client.post(path, json=body, headers=headers or {**web, "Idempotency-Key": secrets.token_hex(16)})
        assert response.status_code == expected, f"{path}: unexpected status {response.status_code}"
        return response.json()

    token = post("/api/v1/enrollments", {"label": "signing-check", "simulated": True})["token"]
    secret = secrets.token_urlsafe(32)
    device = post("/api/agent/v1/enroll", {"enrollment_token": token, "request_id": "check-enrollment",
                  "secret_hash": hashlib.sha256(secret.encode()).hexdigest(), "name": "CPU check",
                  "simulated": True, "agent_version": "test"}, expected=200)
    agent = {"Authorization": f"Bearer cvd_{device['device_id']}_{secret}"}
    project = post("/api/v1/projects", {"name": "signing-check"})
    robot = post("/api/v1/robots", {"project_id": project["id"], "device_id": device["device_id"],
                                  "name": "CPU check", "profile": PROFILE})
    application = post("/api/v1/applications", {"project_id": project["id"], "name": "signing-check"})
    release = post(f"/api/v1/applications/{application['id']}/releases", {"manifest": {
        "schema_version": 1, "profile": PROFILE, "policy": {"runtime": "test-runtime", "artifact_sha256": "a" * 64},
        "environment": {"name": "pick-place-v3", "metaworld": "3.1.1", "mujoco": "3.3.0"},
        "execution": {"max_steps": 100, "decision_timeout_ms": 1000, "mission_timeout_s": 120},
    }})
    deployment = post("/api/v1/deployments", {"robot_id": robot["id"], "release_id": release["id"], "expected_generation": 0})
    prefix = f"/api/agent/v1/robots/{robot['id']}"
    post(f"{prefix}/deployments/{deployment['id']}/report", {"generation": 1, "state": "ready",
         "release_digest": release["digest"]}, headers=agent, expected=200)
    mission = post(f"/api/v1/robots/{robot['id']}/missions", {"deployment_id": deployment["id"],
                   "expected_generation": 1, "seed": 0, "ttl_s": 300})
    who = {"boot_id": "wrapper-check", "incarnation": "wrapper-coordinator", "authority_epoch": 1}
    claimed = post(f"{prefix}/missions/{mission['id']}/claim", who, headers=agent, expected=200)
    assert post(f"{prefix}/missions/{mission['id']}/claim", who, headers=agent, expected=200) == claimed
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "action.json"
        path.write_text(json.dumps(signer.verification_document("action")))
        path.chmod(0o600)
        assert GrantVerifier(path, purpose="action").verify(claimed["grant"]) == {
            **claimed["identity"], "expires_at": mission["expires_at"]}


def database_check():
    import psycopg
    from bootstrap import configure_roles
    from convoy_server.config import Settings, set_settings
    from convoy_server.migrations import migrate_database
    from fastapi.testclient import TestClient
    from hosted import app

    password = os.environ["TEST_ADMIN_PASSWORD"]
    with psycopg.connect(host="postgres", dbname="convoy", user="convoy_admin", password=password) as owner:
        owner.execute("CREATE ROLE limited_admin LOGIN CREATEROLE PASSWORD 'disposable-limited-admin'")
        owner.execute("ALTER DATABASE convoy OWNER TO limited_admin")
    with psycopg.connect(host="postgres", dbname="convoy", user="limited_admin", password="disposable-limited-admin") as admin:
        configure_roles(admin, "disposable-migration-password", "disposable-runtime-password")

    migrator = "postgresql+psycopg://convoy_migrator:disposable-migration-password@postgres/convoy"
    runtime = "postgresql+psycopg://convoy_app:disposable-runtime-password@postgres/convoy"
    result = migrate_database(Settings(database_url=migrator, data_dir=Path("/tmp/migration")))
    assert result["ok"] and result["target_revision"] == "0002_evaluations"
    signer = configure_signing()
    set_settings(Settings(database_url=runtime, data_dir=Path("/tmp/api"), simulator=True,
                          bootstrap_admin_email="operator@example.com",
                          bootstrap_admin_password="disposable-bootstrap-password"))
    with TestClient(app()) as client:
        assert client.get("/api/health").json()["db"]["backend"] == "postgresql"
        response = client.post("/api/v1/auth/login", json={"email":"operator@example.com", "password":"disposable-bootstrap-password"})
        assert response.status_code == 200, response.text
        response = client.post("/api/v1/projects", json={"name":"runtime-role-check"},
                               headers={"X-Convoy-Client":"web", "Idempotency-Key":"create-once"})
        assert response.status_code == 201, response.text
        assert client.get("/api/v1/projects").json()[0]["name"] == "runtime-role-check"
        check_claim(client, signer)
        for path in ("/api/v1/releases", "/api/v1/artifacts", "/api/v1/devices/test/chat", "/api/docs"):
            response = client.get(path)
            assert response.status_code == 404 and "CPU lifecycle staging" in response.text
    with psycopg.connect(host="postgres", dbname="convoy", user="convoy_app", password="disposable-runtime-password") as client:
        try:
            client.execute("CREATE TABLE must_not_create (id int)")
        except psycopg.errors.InsufficientPrivilege:
            client.rollback()
        else:
            raise AssertionError("runtime role unexpectedly owns DDL")
    with psycopg.connect(host="postgres", dbname="convoy", user="limited_admin", password="disposable-limited-admin") as admin:
        configure_roles(admin, "disposable-migration-password", "disposable-runtime-password")
    print("passed: real migration + API DML under runtime role, denied runtime DDL, legacy routes blocked, repeatable DB grants, API-private signing and exact mission grant replay")


def inference_check():
    from convoy_contracts.execution import canonical_digest, sign_grant
    from convoy_contracts.grants import SigningKeys

    with tempfile.TemporaryDirectory() as directory:
        private = Path(directory) / "private.json"
        private.write_text(json.dumps(signing_document()))
        private.chmod(0o600)
        signer = SigningKeys(private)
        public = signer.verification_document("action")
        probe = secrets.token_urlsafe(32)
        env = {name: value for name, value in os.environ.items() if not name.startswith((
            "CONVOY_EXECUTION_", "CONVOY_ACTION_", "CONVOY_PLANNER_", "TEST_"))}
        env.update(CONVOY_ACTION_VERIFICATION_JSON=json.dumps(public), CONVOY_WORKER_PROBE_TOKEN=probe)
        command = [sys.executable, "/app/aws-runtime/inference.py"]
        for extra in ({"CONVOY_EXECUTION_SECRET": ""}, {"CONVOY_EXECUTION_SIGNING_JSON": "never-log-this-private-input"}):
            rejected = subprocess.run(command, env={**env, **extra}, capture_output=True, text=True, timeout=15, check=False)
            assert rejected.returncode != 0 and "never-log-this" not in rejected.stdout + rejected.stderr
            assert "AWS inference configuration is unavailable" in rejected.stderr

        log = Path(directory) / "inference.log"
        with log.open("w") as stream:
            child = subprocess.Popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
        def request(path, body=None, token=None):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            req = urllib.request.Request("http://127.0.0.1:8080" + path,
                data=None if body is None else json.dumps(body).encode(), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=2) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, None

        try:
            for _ in range(60):
                if child.poll() is not None:
                    raise AssertionError("inference wrapper exited before ready")
                try:
                    if request("/health")[0] == 200:
                        break
                except urllib.error.URLError:
                    pass
                time.sleep(0.25)
            else:
                raise AssertionError("inference wrapper did not become ready")
            manifest = json.loads(Path("/app/release.json").read_text())
            digest = canonical_digest(manifest)
            probe_body = {"release_digest": digest, "profile": manifest["profile"]}
            assert request("/v1/probe", probe_body)[0] == 401
            assert request("/v1/probe", probe_body, probe)[0] == 200
            identity = {"robot_id": "robot", "device_id": "device", "mission_id": "mission", "boot_id": "boot",
                        "incarnation": "wrapper-check", "release_digest": digest, "authority_epoch": 1}
            body = {"identity": identity, "request_id": "request", "observation_id": "observation", "sequence": 0,
                    "observation": [0.1] * 39, "deadline_monotonic_ns": time.monotonic_ns() + 1_000_000_000,
                    "budget_ms": 1000}
            expiry = time.time() + 30
            for token in (signer.sign(identity, "planner", expiry), sign_grant(identity, "legacy-" * 8, expiry)):
                assert request("/v1/decisions", body, token)[0] == 401
            status, decision = request("/v1/decisions", body, signer.sign(identity, "action", expiry))
            assert status == 200 and decision["identity"] == identity and len(decision["action"]) == 4
        finally:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
                raise AssertionError("inference wrapper failed graceful shutdown") from None
        # Uvicorn completes its lifespan shutdown, then re-raises SIGTERM on
        # recent versions. The process code alone cannot establish clean shutdown.
        assert child.returncode in (0, -signal.SIGTERM)
        assert "Application shutdown complete." in log.read_text()
        try:
            request("/health")
        except urllib.error.URLError:
            pass
        else:
            raise AssertionError("inference listener remained after shutdown")
    print("passed: real public-only inference wrapper, signed action, planner/HMAC and conflicting keys rejected")


if os.environ.get("TEST_INFERENCE_ONLY") == "1":
    inference_check()
else:
    database_check()
