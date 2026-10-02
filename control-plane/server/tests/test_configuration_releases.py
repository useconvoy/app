"""Configuration inputs produce immutable releases without inventing robot interfaces."""
import json
from copy import deepcopy

from conftest import login, make_user
from convoy_contracts.execution import canonical_digest
from fastapi.testclient import TestClient
from test_platform_lifecycle import post
from test_robot_registry import profile, spec


def configuration(prof):
    return {"profile_id": prof["id"], "policy": {"kind": "reference"},
            "instruction": "Reach the target", "targets": {"shoulder": 0.25}}


def test_configuration_is_atomic_idempotent_and_revisions_are_immutable(app, admin):
    project = post(admin, "/api/v1/projects", {"name": "Lab"})
    prof = profile(admin, project)
    body = {"project_id": project["id"], "name": "Joint task", "configuration": configuration(prof)}
    invalid = deepcopy(body)
    invalid["configuration"]["targets"]["shoulder"] = 2
    post(admin, "/api/v1/configurations", invalid, expected=422)
    assert admin.get(f"/api/v1/applications?project_id={project['id']}").json() == []
    created = post(admin, "/api/v1/configurations", body)
    assert created == post(admin, "/api/v1/configurations", body)
    application, release = created["application"], created["release"]
    assert admin.get(f"/api/v1/applications/{application['id']}").json() == application
    manifest = release["manifest"]
    assert manifest["environment"]["robot_profile_sha256"] == prof["digest"]
    assert manifest["policy"]["artifact_sha256"] == canonical_digest({"target_joint_positions": [0.25]})
    assert manifest["interface"]["control_rate_hz"] == 50
    setup = admin.get(f"/api/v1/applications/{application['id']}/releases/{release['id']}/setup").json()
    assert canonical_digest(json.loads(setup["manifest_json"])) == release["digest"]
    assert canonical_digest(json.loads(setup["reference_policy_json"])) == manifest["policy"]["artifact_sha256"]
    assert "50.0" in setup["manifest_json"]  # preserves Python canonical numeric representation
    assert admin.get(f"/api/v1/applications/{application['id']}/releases/missing/setup").status_code == 404
    revised = configuration(prof)
    revised["targets"]["shoulder"] = -0.2
    revised["execution"] = {"timing": {"mode": "realtime", "max_observation_age_ms": 100, "max_physics_lag_ms": 20, "fallback": "hold-position"}}
    path = f"/api/v1/applications/{application['id']}/configuration-releases"
    newer = post(admin, path, revised)
    assert newer["release"]["id"] != release["id"]
    assert newer["release"]["manifest"]["execution"]["timing"] == revised["execution"]["timing"]
    bad = deepcopy(revised)
    bad["execution"]["timing"]["max_observation_age_ms"] = 0
    post(admin, path, bad, key="invalid-timing", expected=422)
    # Raw release callers must receive the same contract checks as the form API.
    for index, execution in enumerate((None, {**newer["release"]["manifest"]["execution"], "timing": None},
                                       {**newer["release"]["manifest"]["execution"], "timing": bad["execution"]["timing"]})):
        invalid_manifest = {**newer["release"]["manifest"], "execution": execution}
        post(admin, f"/api/v1/applications/{application['id']}/releases", {"manifest": invalid_manifest},
             key=f"raw-invalid-timing-{index}", expected=422)
    assert post(admin, path, revised, key="same-content")["release"] == newer["release"]
    releases = admin.get(f"/api/v1/applications/{application['id']}/releases").json()
    assert len(releases) == 2 and release in releases
    assert admin.get(f"/api/v1/deployments?project_id={project['id']}").json() == []


def test_configuration_preserves_joint_order_and_rejects_unsupported_profiles(app, admin):
    project = post(admin, "/api/v1/projects", {"name": "Lab"})
    description = spec()
    description["joints"].insert(0, {**description["joints"][0], "name": "elbow"})
    prof = profile(admin, project, description)
    cfg = configuration(prof)
    cfg["targets"]["elbow"] = -0.4
    cfg["policy"] = {"kind": "installed", "runtime": "customer-joint-policy-v1", "artifact_sha256": "b" * 64}
    body = {"project_id": project["id"], "name": "Installed policy", "configuration": cfg}
    result = post(admin, "/api/v1/configurations", body)
    manifest = result["release"]["manifest"]
    assert manifest["interface"]["joint_names"] == ["elbow", "shoulder"]
    assert manifest["task"]["target_joint_positions"] == [-0.4, 0.25]
    assert manifest["policy"]["artifact_sha256"] == "b" * 64
    for index, targets in enumerate(({"shoulder": 0.25}, {"shoulder": 0.25, "elbow": -0.4, "extra": 0})):
        post(admin, "/api/v1/configurations", {**body, "configuration": {**cfg, "targets": targets}}, key=f"targets-{index}", expected=422)
    description["simulations"][0]["controller"] = "velocity"
    unsupported = profile(admin, project, description, revision=1)
    post(admin, "/api/v1/configurations", {**body, "configuration": {**cfg, "profile_id": unsupported["id"]}}, key="unsupported", expected=422)
    description["simulations"][0]["controller"] = "position"
    description["joints"][0].pop("lower")
    description["joints"][0].pop("upper")
    unbounded = profile(admin, project, description, revision=2)
    post(admin, "/api/v1/configurations", {**body, "configuration": {**cfg, "profile_id": unbounded["id"]}}, key="unbounded", expected=422)


def test_configuration_authority_is_project_scoped(app, admin):
    project = post(admin, "/api/v1/projects", {"name": "Lab"})
    prof = profile(admin, project)
    body = {"project_id": project["id"], "name": "Joint task", "configuration": configuration(prof)}
    created = post(admin, "/api/v1/configurations", body)
    other_project = post(admin, "/api/v1/projects", {"name": "Other lab"}, key="other-project")
    post(admin, "/api/v1/configurations", {**body, "project_id": other_project["id"]}, key="wrong-project", expected=404)
    make_user(admin, "other@example.test", "admin")
    with TestClient(app) as outsider:
        login(outsider, "other@example.test", "password-123")
        assert outsider.get(f"/api/v1/applications/{created['application']['id']}").status_code == 404
        assert outsider.get(f"/api/v1/applications/{created['application']['id']}/releases/{created['release']['id']}/setup").status_code == 404
        post(outsider, "/api/v1/configurations", body, expected=404)
        post(outsider, f"/api/v1/applications/{created['application']['id']}/configuration-releases", configuration(prof), expected=404)
    make_user(admin, "viewer@example.test", "viewer")
    with TestClient(app) as viewer:
        login(viewer, "viewer@example.test", "password-123")
        post(viewer, "/api/v1/configurations", body, expected=403)
