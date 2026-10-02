"""Project connection setup uses existing one-use enrollment without persisting its secret."""
import shlex
from datetime import timedelta

from conftest import WEB, FakeAgent, login, make_user
from convoy_server.db import session_scope
from convoy_server.ids import utcnow
from convoy_server.models import EnrollmentToken
from fastapi.testclient import TestClient
from test_platform_lifecycle import post

ROOT = "/api/v1/robot-connections"


def setup(admin, **extra):
    project = post(admin, "/api/v1/projects", {"name": "Connection lab"})
    result = admin.post(ROOT + "/enrollments", json={"project_id": project["id"], "name": "Arm's $(hostname)", **extra}, headers=WEB)
    assert result.status_code == 201, result.text
    return project, result.json()


def token(body):
    arguments = shlex.split(body["command"])
    return arguments[arguments.index("--token") + 1]


def test_connection_claim_discovery_and_no_secret_recovery(app, admin):
    _, body = setup(admin, simulated=True)
    arguments = shlex.split(body["command"])
    assert arguments[arguments.index("--name") + 1] == "Arm's $(hostname)"
    assert "--simulate" in arguments and "--host-inventory" in arguments
    assert body["data_dir"].endswith(body["enrollment"]["id"])
    assert "--no-robot-sim" in shlex.split(body["run_command"])
    path = ROOT + "/enrollments/" + body["enrollment"]["id"]
    pending = admin.get(path)
    assert pending.json()["device"] is None
    assert token(body) not in pending.text and "command" not in pending.json()
    agent = FakeAgent(app)
    claimed = agent.enroll(token(body), hardware={"arch": "aarch64", "mem_total_mb": 7619, "cpu_count": 6,
                                                 "private_extra": "not displayed", "gpu_name": None})
    assert claimed.status_code == 200
    result = admin.get(path).json()
    assert result["enrollment"]["status"] == "consumed"
    computer = admin.get(ROOT + "/" + agent.device_id).json()
    assert computer == result["device"]
    assert computer["hardware"]["arch"] == "aarch64" and computer["hardware"]["mem_total_mb"] == 7619
    assert computer["hardware"]["gpu_name"] is None and computer["hardware"]["synthetic"] is False
    assert "private_extra" not in computer["hardware"]
    assert admin.post(path + "/cancel", json={}, headers=WEB).status_code == 409
    assert admin.get(ROOT + "/" + agent.device_id).status_code == 200
    with session_scope() as db:
        row = db.get(EnrollmentToken, body["enrollment"]["id"])
        assert row.token_hash != token(body)


def test_connection_setup_ownership_cancellation_and_expiry(app, admin):
    project, body = setup(admin)
    assert "--simulate" not in shlex.split(body["command"])
    path = ROOT + "/enrollments/" + body["enrollment"]["id"]
    for role in ("admin", "viewer"):
        email = f"other-{role}@example.test"
        make_user(admin, email, role)
        with TestClient(app) as other:
            login(other, email, "password-123")
            assert other.get(path).status_code == 404
            assert other.post(ROOT + "/enrollments", json={"project_id": project["id"], "name": "Other"}, headers=WEB).status_code == (404 if role == "admin" else 403)
            assert other.post(path + "/cancel", json={}, headers=WEB).status_code == (404 if role == "admin" else 403)
    for _ in range(2):
        cancelled = admin.post(path + "/cancel", json={}, headers=WEB)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["enrollment"]["status"] == "revoked"
    assert FakeAgent(app).enroll(token(body), simulated=False).status_code != 200
    _, expired = setup(admin)
    with session_scope() as db:
        db.get(EnrollmentToken, expired["enrollment"]["id"]).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert admin.get(ROOT + "/enrollments/" + expired["enrollment"]["id"]).json()["enrollment"]["status"] == "expired"
    assert FakeAgent(app).enroll(token(expired), simulated=False).status_code != 200
