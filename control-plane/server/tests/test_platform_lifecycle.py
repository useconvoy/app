"""Exercise the lifecycle across authenticated HTTP and durable SQLite boundaries."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from conftest import WEB, FakeAgent, enrollment_token, login, make_user
from convoy_contracts.execution import PROFILE, canonical_digest, verify_grant
from convoy_server import migrations
from convoy_server.db import make_engine, reset_engine, session_scope, write_txn
from convoy_server.ids import utcnow
from convoy_server.platform_models import Mission
from fastapi.testclient import TestClient


def post(client, path, body, key="create", expected=201):
    response = client.post(path, json=body, headers={**WEB, "Idempotency-Key": key})
    assert response.status_code == expected, response.text
    return response.json()


@pytest.fixture()
def pipeline(app, admin, settings):
    settings.execution_secret = "local-test-signing-secret-with-32-bytes"
    agent = FakeAgent(app)
    assert agent.enroll(enrollment_token(admin)).status_code == 200
    project = post(admin, "/api/v1/projects", {"name": "Simulation"})
    robot = post(
        admin,
        "/api/v1/robots",
        {"project_id": project["id"], "device_id": agent.device_id, "name": "Sawyer", "profile": PROFILE},
    )
    application = post(admin, "/api/v1/applications", {"project_id": project["id"], "name": "Pick-place"})
    manifest = {
        "schema_version": 1,
        "profile": PROFILE,
        "policy": {"runtime": "test-scripted", "artifact_sha256": "1" * 64},
        "environment": {"name": "pick-place-v3", "metaworld": "3.1.1", "mujoco": "3.3.0"},
        "execution": {"max_steps": 100, "decision_timeout_ms": 500, "mission_timeout_s": 120},
    }
    release = post(admin, f"/api/v1/applications/{application['id']}/releases", {"manifest": manifest})
    deployment = post(
        admin,
        "/api/v1/deployments",
        {"robot_id": robot["id"], "release_id": release["id"], "expected_generation": 0},
    )
    base = f"/api/agent/v1/robots/{robot['id']}"
    report = {"generation": 1, "state": "ready", "release_digest": release["digest"]}
    assert agent.client.post(f"{base}/deployments/{deployment['id']}/report", json=report).status_code == 200
    return {
        "agent": agent,
        "project": project,
        "robot": robot,
        "application": application,
        "release": release,
        "deployment": deployment,
        "base": base,
        "admin": admin,
        "settings": settings,
    }


def start(pipeline, key="start", ttl=60):
    p = pipeline
    return post(
        p["admin"],
        f"/api/v1/robots/{p['robot']['id']}/missions",
        {
            "deployment_id": p["deployment"]["id"],
            "expected_generation": 1,
            "seed": 7,
            "ttl_s": ttl,
        },
        key=key,
    )


def claim(pipeline, mission):
    p = pipeline
    body = {"boot_id": "boot-one", "incarnation": "coordinator-one", "authority_epoch": 1}
    response = p["agent"].client.post(f"{p['base']}/missions/{mission['id']}/claim", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def test_lifecycle_is_idempotent_fenced_and_produces_immutable_episode(pipeline):
    p = pipeline
    mission = start(p)
    assert mission == start(p)  # retry does not start a second mission or extend authorization
    assert mission["state"] == "requested" and mission["identity"] is None
    ready = {"generation": 1, "state": "ready", "release_digest": p["release"]["digest"]}
    ready_path = f"{p['base']}/deployments/{p['deployment']['id']}/report"
    before = p["admin"].get(f"/api/v1/deployments/{p['deployment']['id']}").json()
    assert p["admin"].get(
        f"/api/v1/deployments?project_id={p['project']['id']}&robot_id={p['robot']['id']}"
    ).json() == [before]
    assert (
        p["agent"].client.post(ready_path, json=ready).json() == before
    )  # lost ack/restart, no new activation
    assert p["release"]["digest"] == canonical_digest(p["release"]["manifest"])
    claimed, claim_body = claim(p, mission)
    path = f"{p['base']}/missions/{mission['id']}"
    assert p["agent"].client.post(f"{path}/claim", json=claim_body).json() == claimed
    grant = verify_grant(claimed["grant"], p["settings"].execution_secret)
    assert grant["expires_at"] == mission["expires_at"]
    assert (
        p["agent"].client.post(f"{path}/claim", json={**claim_body, "incarnation": "restart"}).status_code
        == 409
    )
    payload = {"identity": claimed["identity"], "state": "completed", "summary": {"success": True}}
    assert p["agent"].client.post(f"{path}/report", json=payload).status_code == 409
    assert (
        p["agent"]
        .client.post(f"{path}/report", json={"identity": claimed["identity"], "state": "running"})
        .status_code
        == 200
    )
    finished = p["agent"].client.post(f"{path}/report", json=payload)
    assert finished.status_code == 200, finished.text
    assert p["agent"].client.post(f"{path}/report", json=payload).json() == finished.json()
    assert (
        p["agent"].client.post(f"{path}/report", json={**payload, "summary": {"success": False}}).status_code
        == 409
    )
    episode = finished.json()["episode"]
    assert p["admin"].get(f"/api/v1/episodes/{episode['id']}").json() == episode
    assert len(p["admin"].get("/api/v1/episodes", params={"mission_id": mission["id"]}).json()) == 1


def test_human_auth_project_scope_and_device_ownership(app, admin, pipeline):
    p = pipeline
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/projects").status_code == 401
    assert admin.post("/api/v1/projects", json={"name": "x"}, headers=WEB).status_code == 422
    assert (
        admin.post("/api/v1/projects", json={"name": "x"}, headers={"Idempotency-Key": "csrf"}).status_code
        == 403
    )
    assert (
        admin.post(
            "/api/v1/projects", json={"name": "different"}, headers={**WEB, "Idempotency-Key": "create"}
        ).status_code
        == 409
    )
    make_user(admin, "another@example.com", "operator")
    with TestClient(app) as other:
        login(other, "another@example.com", "password-123")
        assert other.get("/api/v1/projects").json() == []
        assert other.get("/api/v1/robots", params={"project_id": p["project"]["id"]}).status_code == 404
        own = post(other, "/api/v1/projects", {"name": "Other project"})
        assert (
            other.post(
                "/api/v1/robots",
                json={
                    "project_id": own["id"],
                    "device_id": p["agent"].device_id,
                    "name": "steal",
                    "profile": PROFILE,
                },
                headers={**WEB, "Idempotency-Key": "robot"},
            ).status_code
            == 404
        )
    foreign = FakeAgent(app, name="other-device")
    foreign.enroll(enrollment_token(admin))
    assert foreign.client.get(f"{p['base']}/desired").status_code == 404
    make_user(admin, "viewer@example.com", "viewer")
    with TestClient(app) as viewer:
        login(viewer, "viewer@example.com", "password-123")
        assert (
            viewer.post(
                "/api/v1/projects", json={"name": "x"}, headers={**WEB, "Idempotency-Key": "viewer"}
            ).status_code
            == 403
        )


def test_cancel_races_unknown_and_generation_conflicts(pipeline):
    p = pipeline
    mission = start(p)
    path = f"{p['base']}/missions/{mission['id']}"
    cancelled = post(p["admin"], f"/api/v1/missions/{mission['id']}/cancel", {"reason": "stop"}, expected=200)
    assert cancelled["state"] == "cancel_requested"
    null_report = {"identity": None, "state": "cancelled"}
    response = p["agent"].client.post(f"{path}/report", json=null_report)
    assert response.status_code == 200 and response.json()["mission"]["state"] == "cancelled"
    assert p["agent"].client.post(f"{path}/report", json=null_report).json() == response.json()
    mission = start(p, key="second")
    claimed, _ = claim(p, mission)
    path = f"{p['base']}/missions/{mission['id']}"
    post(p["admin"], f"/api/v1/missions/{mission['id']}/cancel", {"reason": "stop"}, expected=200)
    running = p["agent"].client.post(
        f"{path}/report", json={"identity": claimed["identity"], "state": "running"}
    )
    assert running.json()["mission"]["state"] == "cancel_requested"
    unknown = p["agent"].client.post(
        f"{path}/report", json={"identity": claimed["identity"], "state": "unknown"}
    )
    assert unknown.json()["mission"]["state"] == "unknown"
    assert (
        p["agent"]
        .client.post(f"{path}/report", json={"identity": claimed["identity"], "state": "running"})
        .status_code
        == 409
    )
    request = {"robot_id": p["robot"]["id"], "release_id": p["release"]["id"], "expected_generation": 1}
    assert (
        p["admin"]
        .post("/api/v1/deployments", json=request, headers={**WEB, "Idempotency-Key": "blocked"})
        .status_code
        == 409
    )
    p["agent"].client.post(f"{path}/report", json={"identity": claimed["identity"], "state": "cancelled"})
    next_deploy = post(p["admin"], "/api/v1/deployments", request, key="next")
    assert next_deploy["generation"] == 2 and next_deploy["state"] == "requested"
    old_report = {"generation": 1, "state": "ready", "release_digest": p["release"]["digest"]}
    assert (
        p["agent"]
        .client.post(f"{p['base']}/deployments/{p['deployment']['id']}/report", json=old_report)
        .status_code
        == 409
    )


def test_expiry_missing_secret_and_revoked_binding(pipeline):
    p = pipeline
    mission = start(p, ttl=300)
    assert mission["expires_at"] - utcnow().timestamp() <= 120  # manifest bounds requested TTL
    path = f"{p['base']}/missions/{mission['id']}"
    body = {"boot_id": "boot", "incarnation": "instance", "authority_epoch": 1}
    p["settings"].execution_secret = None
    assert p["agent"].client.post(f"{path}/claim", json=body).status_code == 503
    assert p["admin"].get(f"/api/v1/missions/{mission['id']}").json()["state"] == "requested"
    with session_scope() as db, write_txn(db):
        db.get(Mission, mission["id"]).expires_at = utcnow() - timedelta(seconds=1)
    assert p["agent"].client.post(f"{path}/claim", json=body).status_code == 410
    expired = p["admin"].get(f"/api/v1/missions/{mission['id']}").json()
    assert expired["state"] == "failed" and expired["episode_id"]
    assert p["agent"].client.get(f"{p['base']}/desired").json()["mission"]["state"] == "failed"
    from convoy_server.models import Device

    with session_scope() as db, write_txn(db):
        db.get(Device, p["agent"].device_id).credential_revoked_at = utcnow()
    assert p["agent"].client.get(f"{p['base']}/desired").status_code == 401


def test_bounded_reports_and_stale_identity(pipeline):
    p = pipeline
    mission = start(p)
    claimed, _ = claim(p, mission)
    path = f"{p['base']}/missions/{mission['id']}/report"
    bad = {**claimed["identity"], "release_digest": "2" * 64}
    assert p["agent"].client.post(path, json={"identity": bad, "state": "running"}).status_code == 409
    large = {str(i): "x" * 10000 for i in range(7)}
    assert (
        p["agent"]
        .client.post(path, json={"identity": claimed["identity"], "state": "failed", "summary": large})
        .status_code
        == 422
    )
    assert (
        p["admin"].post("/api/v1/projects", content=b"x" * (256 * 1024 + 1), headers=WEB).status_code == 413
    )


def test_additive_platform_upgrade_preserves_legacy_records(app, admin, settings):
    """A real old-schema fixture upgrades without rewriting legacy release/user meanings."""
    from convoy_server.models import Base
    from helpers import seed

    seed(admin)

    reset_engine()
    path = settings.data_dir / "convoy.db"
    connection = sqlite3.connect(path)
    before = connection.execute("SELECT id,email,role FROM users ORDER BY id").fetchall()
    releases_before = connection.execute("SELECT * FROM releases ORDER BY id").fetchall()
    assert releases_before
    for table in reversed(Base.metadata.sorted_tables):
        if table.name.startswith("platform_"):
            connection.execute(f'DROP TABLE "{table.name}"')
    connection.execute("PRAGMA user_version=3")
    connection.commit()
    connection.close()
    engine = make_engine(settings)
    try:
        result = migrations.ensure_schema(engine)
        assert result["user_version"] == 4
        assert len(result["add_tables"]) == 8 and not result["rebuild"]
    finally:
        engine.dispose()
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT id,email,role FROM users ORDER BY id").fetchall() == before
    assert connection.execute("SELECT * FROM releases ORDER BY id").fetchall() == releases_before
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
