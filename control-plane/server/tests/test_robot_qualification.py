"""Readiness reports are pinned to a requested profile and the admitted device binding."""
from copy import deepcopy
from datetime import timedelta

from conftest import FakeAgent, enrollment_token, login, make_user
from convoy_server.db import session_scope, write_txn
from convoy_server.ids import utcnow
from convoy_server.models import Device
from convoy_server.platform_models import Robot
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


def test_registered_deployment_pins_interfaces_and_rechecks_qualification_at_claim(app, admin, settings):
    settings.execution_secret = "local-execution-test-secret-at-least-32-bytes"
    prof, agent, robot = setup_robot(app, admin)
    request = post(admin, f"/api/v1/robots/{robot['id']}/qualification", {})
    assert agent.client.post(f"/api/agent/v1/qualifications/{request['id']}/report", json=passing(request)).status_code == 200
    application = post(admin, "/api/v1/applications", {"project_id": robot["project_id"], "name": "Joint target"})
    manifest = {
        "schema_version": 3, "profile": "registered-joint-policy-v1",
        "policy": {"runtime": "installed-policy", "artifact_sha256": "b" * 64},
        "environment": {"engine": "mujoco", "version": "3.3.0", "robot_profile_sha256": prof["digest"], "asset_sha256": "a" * 64},
        "interface": {"joint_names": ["shoulder"], "command_interface": "joint-position", "action_bounds": [[-1, 1]], "control_rate_hz": 50},
        "task": {"instruction": "Reach the target", "target_joint_positions": [0.25], "position_tolerance": 0.01, "velocity_tolerance": 0.02},
        "execution": {"max_steps": 100, "decision_timeout_ms": 1000, "mission_timeout_s": 60},
    }
    for field in ("asset", "joints", "bounds", "cadence"):
        wrong = deepcopy(manifest)
        if field == "asset":
            wrong["environment"]["asset_sha256"] = "c" * 64
        elif field == "joints":
            wrong["interface"]["joint_names"] = ["elbow"]
        elif field == "bounds":
            wrong["interface"]["action_bounds"] = [[-2, 2]]
        else:
            wrong["interface"]["control_rate_hz"] = 25
        release = post(admin, f"/api/v1/applications/{application['id']}/releases", {"manifest": wrong}, key=field)
        post(admin, "/api/v1/deployments", {"robot_id": robot["id"], "release_id": release["id"], "expected_generation": 0}, key=field, expected=409)
    release = post(admin, f"/api/v1/applications/{application['id']}/releases", {"manifest": manifest}, key="matching")
    with session_scope() as db, write_txn(db):
        db.get(Robot, robot["id"]).profile = "custom-unqualified"  # prior registry release
    deployment = post(admin, "/api/v1/deployments", {"robot_id": robot["id"], "release_id": release["id"], "expected_generation": 0}, key="matching")
    assert admin.get(f"/api/v1/robots/{robot['id']}").json()["profile"] == "registered-joint-policy-v1"
    base = f"/api/agent/v1/robots/{robot['id']}"
    assert agent.client.post(f"{base}/deployments/{deployment['id']}/report", json={"generation": 1, "state": "ready", "release_digest": release["digest"]}).status_code == 200
    mission = post(admin, f"/api/v1/robots/{robot['id']}/missions", {"deployment_id": deployment["id"], "expected_generation": 1, "seed": 0, "ttl_s": 60})
    listed = admin.get(f"/api/v1/missions?project_id={robot['project_id']}&robot_id={robot['id']}")
    assert listed.status_code == 200 and [m["id"] for m in listed.json()] == [mission["id"]]
    assert admin.get(f"/api/v1/missions?project_id={robot['project_id']}&robot_id=missing").status_code == 404
    # A queued task does not retain admission when the required verification is no longer current.
    post(admin, f"/api/v1/robots/{robot['id']}/qualification", {}, key="recheck")
    response = agent.client.post(f"{base}/missions/{mission['id']}/claim", json={"boot_id": "boot", "incarnation": "runner", "authority_epoch": 1})
    assert response.status_code == 409
    assert admin.get(f"/api/v1/missions/{mission['id']}").json()["state"] == "requested"
