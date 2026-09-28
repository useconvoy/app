"""Paired releases retain one mission authority and require both components' evidence."""

import copy

import pytest
from conftest import FakeAgent, enrollment_token
from convoy_contracts.execution import VISUAL_PROFILE, canonical_digest, verify_grant
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
    verify_planner_grant,
)
from convoy_server.ids import utcnow
from test_evaluation_lifecycle import evaluate, ready, suite, tick
from test_platform_lifecycle import claim, post, start


@pytest.fixture()
def paired(app, admin, settings):
    settings.execution_secret = "action-test-secret-with-at-least-32-bytes"
    settings.planner_execution_secret = "planner-test-secret-with-at-least-32-bytes"
    agent = FakeAgent(app)
    assert agent.enroll(enrollment_token(admin)).status_code == 200
    project = post(admin, "/api/v1/projects", {"name": "Paired simulation"})
    robot = post(admin, "/api/v1/robots", {
        "project_id": project["id"], "device_id": agent.device_id, "name": "Sawyer",
        "profile": PAIRED_PROFILE,
    })
    application = post(admin, "/api/v1/applications", {"project_id": project["id"], "name": "Paired task"})
    manifest = {
        "schema_version": 2,
        "profile": PAIRED_PROFILE,
        "action_manifest": {
            "schema_version": 1,
            "profile": VISUAL_PROFILE,
            "policy": {"runtime": "test-visual-policy", "artifact_sha256": "1" * 64},
            "environment": {"name": "pick-place-v3", "metaworld": "3.0.0", "mujoco": "3.3.0"},
            "execution": {"max_steps": 100, "decision_timeout_ms": 500, "mission_timeout_s": 120},
        },
        "planner": {
            "runtime": PLANNER_RUNTIME, "artifact_sha256": "2" * 64,
            "protocol_sha256": PLANNER_PROTOCOL_SHA256,
        },
        "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID},
        "catalog_sha256": CATALOG_SHA256,
        "planning": {"timeout_ms": 1000},
        "placement": {"policy": "development-local-cpu", "planner": "development-jetson-lan"},
    }
    release = post(admin, f"/api/v1/applications/{application['id']}/releases", {"manifest": manifest})
    return {
        "agent": agent, "admin": admin, "settings": settings, "project": project,
        "robot": robot, "application": application, "release": release,
        "base": f"/api/agent/v1/robots/{robot['id']}",
    }


def deploy(p, expected=201):
    deployment = post(p["admin"], "/api/v1/deployments", {
        "robot_id": p["robot"]["id"], "release_id": p["release"]["id"], "expected_generation": 0,
    }, expected=expected)
    if expected == 201:
        p["deployment"] = deployment
        response = p["agent"].client.post(f"{p['base']}/deployments/{deployment['id']}/report", json={
            "generation": 1, "state": "ready", "release_digest": p["release"]["digest"],
        })
        assert response.status_code == 200, response.text
    return deployment


def test_paired_mission_grants_bind_outer_bundle_and_preserve_child_ttl(paired):
    p = paired
    deploy(p)
    before = utcnow().timestamp()
    mission = start(p, ttl=300)
    after = utcnow().timestamp()
    child = p["release"]["manifest"]["action_manifest"]
    assert before + child["execution"]["mission_timeout_s"] <= mission["expires_at"] <= after + 120
    claimed, body = claim(p, mission)
    path = f"{p['base']}/missions/{mission['id']}"
    assert p["agent"].client.post(path + "/claim", json=body).json() == claimed
    identity = claimed["identity"]
    assert identity["release_digest"] == p["release"]["digest"] == canonical_digest(p["release"]["manifest"])
    assert identity["release_digest"] != canonical_digest(child)
    action = verify_grant(claimed["grant"], p["settings"].execution_secret)
    planner = verify_planner_grant(claimed["planner_grant"], p["settings"].planner_execution_secret)
    assert action == {**identity, "expires_at": mission["expires_at"]}
    assert planner == {**identity, "purpose": "planner", "expires_at": mission["expires_at"]}
    # A valid signature cannot turn the other purpose into authority, even when
    # the verifier is deliberately given that token's correct signing key.
    for verify, token, secret in (
        (verify_grant, claimed["planner_grant"], p["settings"].planner_execution_secret),
        (verify_planner_grant, claimed["grant"], p["settings"].execution_secret),
        (verify_grant, claimed["grant"], p["settings"].planner_execution_secret),
        (verify_planner_grant, claimed["planner_grant"], p["settings"].execution_secret),
    ):
        with pytest.raises(ValueError):
            verify(token, secret)
    for verify, token, secret in (
        (verify_grant, claimed["grant"], p["settings"].execution_secret),
        (verify_planner_grant, claimed["planner_grant"], p["settings"].planner_execution_secret),
    ):
        with pytest.raises(ValueError):
            verify(token, secret, now=mission["expires_at"])
    assert p["agent"].client.post(path + "/report", json={"identity": identity, "state": "running"}).status_code == 200
    finished = p["agent"].client.post(path + "/report", json={"identity": identity, "state": "completed"})
    assert finished.status_code == 200, finished.text
    assert finished.json()["episode"]["release_digest"] == p["release"]["digest"]


@pytest.mark.parametrize("bad_secret", [None, "too-short", "same-as-action"])
def test_paired_admission_rejects_missing_or_shared_planner_authority(paired, bad_secret):
    p = paired
    settings = p["settings"]
    valid = settings.planner_execution_secret
    invalid = settings.execution_secret if bad_secret == "same-as-action" else bad_secret
    settings.planner_execution_secret = invalid
    deploy(p, expected=503)
    robots = p["admin"].get("/api/v1/robots", params={"project_id": p["project"]["id"]}).json()
    assert robots[0]["generation"] == 0
    settings.planner_execution_secret = valid
    deploy(p)  # The rejected request did not consume the idempotency key or generation.
    mission = start(p)
    settings.planner_execution_secret = invalid
    response = p["agent"].client.post(f"{p['base']}/missions/{mission['id']}/claim", json={
        "boot_id": "boot-one", "incarnation": "coordinator-one", "authority_epoch": 1,
    })
    assert response.status_code == 503, response.text
    retained = p["admin"].get(f"/api/v1/missions/{mission['id']}").json()
    assert retained["state"] == "requested" and retained["identity"] is None
    assert retained["expires_at"] == mission["expires_at"]
    settings.planner_execution_secret = valid
    claimed, _ = claim(p, mission)
    assert verify_planner_grant(claimed["planner_grant"], valid)["expires_at"] == mission["expires_at"]


@pytest.mark.parametrize("missing_key", ["execution_secret", "planner_execution_secret"])
def test_keyless_evaluation_job_requires_api_admission_and_cannot_issue_grants(paired, missing_key):
    p = paired
    settings = p["settings"]
    secrets = settings.execution_secret, settings.planner_execution_secret
    spec = suite(p, seeds=[7], minimum=1)
    setattr(settings, missing_key, None)
    deploy(p, expected=503)
    post(p["admin"], "/api/v1/evaluations", {
        "suite_id": spec["id"], "release_id": p["release"]["id"], "robot_id": p["robot"]["id"],
    }, expected=503)
    assert p["admin"].get("/api/v1/evaluations", params={"project_id": p["project"]["id"]}).json() == []
    settings.execution_secret, settings.planner_execution_secret = secrets
    run = evaluate(p, spec)
    # This mirrors the separate job process: it gets DB access but no signing keys.
    settings.execution_secret = settings.planner_execution_secret = None
    ready(p, run)
    row = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    assert row["state"] == "running" and row["deployment_id"]
    mission_id = row["cases"][0]["mission_id"]
    rejected = p["agent"].client.post(f"{p['base']}/missions/{mission_id}/claim", json={
        "boot_id": "boot", "incarnation": "coordinator", "authority_epoch": 1,
    })
    assert rejected.status_code == 503, rejected.text
    mission = p["admin"].get(f"/api/v1/missions/{mission_id}").json()
    assert mission["state"] == "requested" and mission["identity"] is None
    settings.execution_secret, settings.planner_execution_secret = secrets
    claimed, _ = claim(p, mission)
    assert verify_grant(claimed["grant"], secrets[0])["mission_id"] == mission_id
    assert verify_planner_grant(claimed["planner_grant"], secrets[1])["mission_id"] == mission_id


@pytest.mark.parametrize("evidence", ["missing", "unaccepted", "wrong-identity", "wrong-artifact", "declined", "matched"])
def test_paired_evaluation_requires_matched_accepted_planner_evidence(paired, evidence):
    p = paired
    spec = suite(p, seeds=[7], minimum=1)
    post(p["admin"], f"/api/v1/applications/{p['application']['id']}/evaluation-gate", {
        "suite_id": spec["id"], "expected_generation": 0,
    }, expected=200)
    run = evaluate(p, spec)
    ready(p, run)
    row = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    mission = p["admin"].get(f"/api/v1/missions/{row['cases'][0]['mission_id']}").json()
    claimed, _ = claim(p, mission)
    identity = claimed["identity"]
    proposal = {
        "identity": copy.deepcopy(identity), "request_id": "plan-one", "observation_id": "frame-one",
        "observation_digest": "3" * 64, "deadline_monotonic_ns": 1000000000,
        "planner_artifact_sha256": p["release"]["manifest"]["planner"]["artifact_sha256"],
        "planner_incarnation": "planner-one", "runtime_generation": 1,
        "decision": {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}},
        "planner_duration_ms": 15,
    }
    summary = {
        "seed": 7, "execution_mode": "lockstep_offline", "final_success": True,
        "wall_duration_s": 1.0, "steps": 50,
        "policy_runtime": p["release"]["manifest"]["action_manifest"]["policy"]["runtime"],
        "planner_accepted": True, "planner_result": proposal,
    }
    if evidence == "missing":
        del summary["planner_result"]
    elif evidence == "unaccepted":
        summary["planner_accepted"] = False
    elif evidence == "wrong-identity":
        proposal["identity"]["mission_id"] = "another-mission"
    elif evidence == "wrong-artifact":
        proposal["planner_artifact_sha256"] = "4" * 64
    elif evidence == "declined":
        proposal["decision"] = {"kind": "decline", "reason": "unsupported_task"}
    path = f"{p['base']}/missions/{mission['id']}/report"
    assert p["agent"].client.post(path, json={"identity": identity, "state": "running"}).status_code == 200
    finished = p["agent"].client.post(path, json={"identity": identity, "state": "completed", "summary": summary})
    assert finished.status_code == 200, finished.text
    tick()
    result = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    passed = evidence == "matched"
    assert result["state"] == "completed"
    assert result["report"]["passed"] is passed
    assert result["report"]["cases"][0]["evidence_valid"] is passed
    assert result["report"]["successes"] == int(passed)
    assert result["report"]["planner"] == p["release"]["manifest"]["planner"]
    post(p["admin"], f"/api/v1/evaluations/{run['id']}/promote", {}, expected=201 if passed else 409)
    qualification = p["admin"].get(
        f"/api/v1/applications/{p['application']['id']}/qualification",
        params={"release_id": p["release"]["id"]},
    ).json()
    assert qualification["deployment_allowed"] is passed
