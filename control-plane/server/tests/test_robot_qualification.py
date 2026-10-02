"""Readiness reports are pinned to a requested profile and the admitted device binding."""
from datetime import timedelta

from conftest import FakeAgent, enrollment_token, login, make_user
from convoy_server.db import session_scope, write_txn
from convoy_server.ids import utcnow
from convoy_server.models import Device
from convoy_server.robot_registry_models import RobotQualification
from fastapi.testclient import TestClient
from test_platform_lifecycle import post
from test_robot_registry import profile


def setup_robot(app, admin):
    project = post(admin, "/api/v1/projects", {"name": "Lab"})
    prof = profile(admin, project)
    agent = FakeAgent(app)
    agent.enroll(enrollment_token(admin))
    robot = post(admin, "/api/v1/robot-registrations", {"project_id": project["id"], "name": "Arm",
                 "device_id": agent.device_id, "profile_id": prof["id"], "kind": "simulated", "simulation_engine": "mujoco"})
    return prof, agent, robot


def passing(request):
    return {"profile_digest": request["profile_digest"], "binding_epoch": request["binding_epoch"],
            "asset_sha256": "a" * 64, "state": "passed", "detail": "loaded",
            "evidence": {"engine_version": "3.3.0", "joint_names": ["shoulder"], "camera_names": [],
                         "command_interface": "joint-position", "checks": ["asset-digest", "model-load", "joint-contract", "controller-contract", "physics-step"],
                         "steps": 200, "sim_seconds": 0.4, "wall_seconds": 0.2}}


def test_report_is_immutable_scoped_and_bound_to_real_request(app, admin):
    prof, agent, robot = setup_robot(app, admin)
    path = f"/api/v1/robots/{robot['id']}/qualification"
    request = post(admin, path, {})
    assert request == post(admin, path, {})
    post(admin, path, {}, key="duplicate", expected=409)
    desired = agent.client.get("/api/agent/v1/registry").json()
    assert desired["profile"]["digest"] == prof["digest"]
    assert desired["qualification"]["id"] == request["id"]
    report_path = f"/api/agent/v1/qualifications/{request['id']}/report"
    payload = passing(request)
    other = FakeAgent(app)
    other.enroll(enrollment_token(admin))
    assert other.client.post(report_path, json=payload).status_code == 404
    assert admin.post(report_path, json=payload).status_code == 401
    assert agent.client.post(report_path, json={**payload, "asset_sha256": "b" * 64}).status_code == 422
    assert agent.client.post(report_path, json={**payload, "profile_digest": "b" * 64}).status_code == 409
    assert agent.client.post(report_path, json={**payload, "evidence": {**payload["evidence"], "steps": 0}}).status_code == 422
    result = agent.client.post(report_path, json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "passed"
    assert agent.client.post(report_path, json=payload).json() == result.json()
    assert agent.client.post(report_path, json={**payload, "detail": "changed"}).status_code == 409
    assert admin.get(path).json()["qualification"] == result.json()
    make_user(admin, "other@example.test", "admin")
    with TestClient(app) as outsider:
        login(outsider, "other@example.test", "password-123")
        assert outsider.get(path).status_code == 404
    # Readiness survives reads, but it never implies calibrated dynamics or deployment authority.
    assert "not policy timing" in result.json()["scope"]


def test_expiry_rebinding_and_failed_reports_do_not_appear_ready(app, admin):
    _, agent, robot = setup_robot(app, admin)
    path = f"/api/v1/robots/{robot['id']}/qualification"
    request = post(admin, path, {})
    with session_scope() as db, write_txn(db):
        db.get(RobotQualification, request["id"]).expires_at = utcnow() - timedelta(seconds=1)
    assert admin.get(path).json()["qualification"]["state"] == "expired"
    report_path = f"/api/agent/v1/qualifications/{request['id']}/report"
    assert agent.client.post(report_path, json=passing(request)).status_code == 409
    current = post(admin, path, {}, key="retry")
    assert current["generation"] == 2
    with session_scope() as db, write_txn(db):
        db.get(Device, agent.device_id).binding_epoch += 1
    assert admin.get(path).json()["qualification"]["state"] == "stale"
    assert agent.client.post(f"/api/agent/v1/qualifications/{current['id']}/report", json=passing(current)).status_code == 409
    latest = post(admin, path, {}, key="new-binding")
    result = agent.client.post(f"/api/agent/v1/qualifications/{latest['id']}/report", json={
        "profile_digest": latest["profile_digest"], "binding_epoch": latest["binding_epoch"],
        "state": "failed", "detail": "missing model asset", "evidence": {},
    })
    assert result.status_code == 200 and result.json()["state"] == "failed"
