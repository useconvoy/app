"""Model upload is profile-owned, byte-verified and available only to the assigned simulator."""
import hashlib

from conftest import WEB, FakeAgent, enrollment_token, login, make_user
from convoy_contracts.assets import ROBOT_ASSET_MAX_BYTES
from fastapi.testclient import TestClient
from test_platform_lifecycle import post
from test_robot_registry import profile, spec

PAYLOAD = b'<mujoco model="upload-check"/>'
HEADERS = {**WEB, "Content-Type": "application/octet-stream"}


def setup(admin):
    project = post(admin, "/api/v1/projects", {"name": "Asset lab"})
    body = spec()
    body["simulations"][0]["asset"]["sha256"] = hashlib.sha256(PAYLOAD).hexdigest()
    prof = profile(admin, project, body)
    return project, prof, f"/api/v1/robot-profiles/{prof['id']}/simulation-assets"


def test_model_delivery_is_owned_pinned_and_device_scoped(app, admin):
    project, prof, path = setup(admin)
    assert admin.get(path).json()[0]["stored"] is False
    assert admin.post(path + "/mujoco", content=b"wrong", headers=HEADERS).status_code == 422
    for _ in range(2):
        uploaded = admin.post(path + "/mujoco", content=PAYLOAD, headers=HEADERS)
        assert uploaded.status_code == 201, uploaded.text
        assert uploaded.json()["size_bytes"] == len(PAYLOAD)
    assert admin.get(path).json()[0]["stored"] is True
    assert admin.get(f"/api/v1/robot-profiles/{prof['id']}").json() == prof
    make_user(admin, "other@example.test", "admin")
    with TestClient(app) as other:
        login(other, "other@example.test", "password-123")
        assert other.get(path).status_code == 404
        assert other.post(path + "/mujoco", content=PAYLOAD, headers=HEADERS).status_code == 404
    make_user(admin, "viewer@example.test", "viewer")
    with TestClient(app) as viewer:
        login(viewer, "viewer@example.test", "password-123")
        assert viewer.post(path + "/mujoco", content=PAYLOAD, headers=HEADERS).status_code == 403
    agent = FakeAgent(app)
    agent.enroll(enrollment_token(admin))
    download = f"/api/agent/v1/robot-assets/{prof['id']}/mujoco"
    assert agent.client.get(download).status_code == 404
    post(admin, "/api/v1/robot-registrations", {"project_id": project["id"], "name": "Simulator",
         "device_id": agent.device_id, "profile_id": prof["id"], "kind": "simulated", "simulation_engine": "mujoco"})
    response = agent.client.get(download)
    assert response.status_code == 200 and response.content == PAYLOAD
    assert agent.client.get(download.replace("mujoco", "isaac")).status_code == 404
    assert admin.get(download).status_code == 401
    physical = FakeAgent(app)
    physical.enroll(enrollment_token(admin, simulated=False), simulated=False)
    post(admin, "/api/v1/robot-registrations", {"project_id": project["id"], "name": "Physical",
         "device_id": physical.device_id, "profile_id": prof["id"], "kind": "physical"}, key="physical")
    assert physical.client.get(download).status_code == 404


def test_upload_bounds_quota_and_partial_cleanup(admin, settings):
    _, _, path = setup(admin)
    assert admin.post(path + "/mujoco", content=PAYLOAD, headers=WEB).status_code == 415
    assert admin.post(path + "/mujoco", content=PAYLOAD,
                      headers={**HEADERS, "Content-Length": str(ROBOT_ASSET_MAX_BYTES + 1)}).status_code == 413
    assert admin.post(path + "/mujoco", content=iter([b"x" * (ROBOT_ASSET_MAX_BYTES + 1)]), headers=HEADERS).status_code == 413
    settings.robot_asset_quota_bytes = len(PAYLOAD) - 1
    assert admin.post(path + "/mujoco", content=PAYLOAD, headers=HEADERS).status_code == 507
    assert admin.get(path).json()[0]["stored"] is False
    assert not list((settings.artifacts_dir / "robot-models").glob(".upload-*"))
    settings.robot_asset_quota_bytes = len(PAYLOAD)
    for _ in range(2):
        assert admin.post(path + "/mujoco", content=PAYLOAD, headers=HEADERS).status_code == 201
