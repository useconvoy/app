"""Real API/SQLite admission with API-private keys; crypto edge cases live in contracts."""

import hashlib
import json
import os
import uuid

import pytest
from convoy_contracts.execution import VISUAL_PROFILE, verify_grant
from convoy_contracts.grants import GrantVerifier, SigningKeys
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
    verify_planner_grant,
)
from convoy_server import config, db
from convoy_server.app import create_app
from convoy_server.platform_models import Mission
from convoy_server.services.evaluations import claim_job, step_job
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

WEB = {"X-Convoy-Client": "web"}
CLAIM = {"boot_id": "test-boot", "incarnation": "test-coordinator", "authority_epoch": 1}


def key_document(suffix="1"):
    keys = []
    for purpose in ("action", "planner"):
        pem = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ).decode()
        keys.append({"kid": f"{purpose}-{suffix}", "purpose": purpose,
                     "audience": f"convoy-{purpose}", "private_key_pem": pem})
    return {"schema_version": 1, "issuer": "convoy-api-test",
            "active": {purpose: f"{purpose}-{suffix}" for purpose in ("action", "planner")}, "keys": keys}


def write_keys(path, document):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document))
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def post(client, path, body, expected=201):
    response = client.post(path, json=body, headers={**WEB, "Idempotency-Key": uuid.uuid4().hex})
    assert response.status_code == expected, response.text
    return response.json()


@pytest.fixture()
def paired_api(tmp_path):
    keys = tmp_path / "private-signing.json"
    document = key_document()
    write_keys(keys, document)
    settings = config.Settings(
        data_dir=tmp_path / "server", simulator=True, scheduler_inprocess=False,
        bootstrap_admin_email="signer@example.com", bootstrap_admin_password="test-password-123",
        execution_signing_keys_file=str(keys), execution_secret=None, planner_execution_secret=None,
    )
    db.reset_engine()
    config.set_settings(settings)
    try:
        with TestClient(create_app(settings, start_scheduler=False)) as admin:
            assert admin.post("/api/v1/auth/login", json={
                "email": settings.bootstrap_admin_email, "password": settings.bootstrap_admin_password,
            }).status_code == 200
            token = post(admin, "/api/v1/enrollments", {"label": "paired-signer", "simulated": True})["token"]
            secret = "device-test-secret-with-at-least-32-bytes"
            with TestClient(admin.app) as agent:
                enrolled = agent.post("/api/agent/v1/enroll", json={
                    "enrollment_token": token, "request_id": "signing-enrollment",
                    "secret_hash": hashlib.sha256(secret.encode()).hexdigest(),
                    "name": "Signing simulator", "simulated": True, "agent_version": "test",
                })
                assert enrolled.status_code == 200, enrolled.text
                device_id = enrolled.json()["device_id"]
                agent.headers["Authorization"] = f"Bearer cvd_{device_id}_{secret}"
                project = post(admin, "/api/v1/projects", {"name": "Private signing"})
                robot = post(admin, "/api/v1/robots", {
                    "project_id": project["id"], "device_id": device_id, "name": "Sawyer", "profile": PAIRED_PROFILE,
                })
                application = post(admin, "/api/v1/applications", {"project_id": project["id"], "name": "Paired task"})
                manifest = {
                    "schema_version": 2, "profile": PAIRED_PROFILE,
                    "action_manifest": {
                        "schema_version": 1, "profile": VISUAL_PROFILE,
                        "policy": {"runtime": "test-action", "artifact_sha256": "1" * 64},
                        "environment": {"name": "pick-place-v3", "metaworld": "3.0.0", "mujoco": "3.3.0"},
                        "execution": {"max_steps": 100, "decision_timeout_ms": 500, "mission_timeout_s": 120},
                    },
                    "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": "2" * 64,
                                "protocol_sha256": PLANNER_PROTOCOL_SHA256},
                    "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
                    "planning": {"timeout_ms": 1000},
                    "placement": {"policy": "development-local-cpu", "planner": "development-local"},
                }
                release = post(admin, f"/api/v1/applications/{application['id']}/releases", {"manifest": manifest})
                yield {"settings": settings, "keys": keys, "document": document, "admin": admin, "agent": agent,
                       "project": project, "robot": robot, "application": application, "release": release,
                       "prefix": f"/api/agent/v1/robots/{robot['id']}"}
    finally:
        db.reset_engine()


def deploy(p, *, expected=201):
    deployment = post(p["admin"], "/api/v1/deployments", {
        "robot_id": p["robot"]["id"], "release_id": p["release"]["id"], "expected_generation": 0,
    }, expected=expected)
    if expected == 201:
        ready(p, deployment)
    return deployment


def ready(p, deployment):
    result = p["agent"].post(f"{p['prefix']}/deployments/{deployment['id']}/report", json={
        "generation": deployment["generation"], "state": "ready", "release_digest": p["release"]["digest"],
    })
    assert result.status_code == 200, result.text


def start(p, deployment):
    return post(p["admin"], f"/api/v1/robots/{p['robot']['id']}/missions", {
        "deployment_id": deployment["id"], "expected_generation": deployment["generation"], "seed": 0, "ttl_s": 300,
    })


def claim(p, mission, expected=200, identity=None):
    response = p["agent"].post(f"{p['prefix']}/missions/{mission['id']}/claim", json=identity or CLAIM)
    assert response.status_code == expected, response.text
    return response.json()


def verifiers(p):
    signer = SigningKeys(p["keys"])
    result = {}
    for purpose in ("action", "planner"):
        path = p["keys"].with_name(f"{purpose}-public.json")
        public = signer.verification_document(purpose)
        assert "PRIVATE KEY" not in json.dumps(public)
        write_keys(path, public)
        result[purpose] = GrantVerifier(path, purpose=purpose)
    return result


def test_paired_api_signs_original_expiry_and_reclaim_retains_action_across_rotation(paired_api):
    p = paired_api
    account = p["admin"].get("/api/v1/auth/me").json()
    assert PAIRED_PROFILE in account["installation"]["execution_profiles"]
    mission = start(p, deploy(p))
    first = claim(p, mission)
    verify = verifiers(p)
    action = verify["action"].verify(first["grant"])
    planner = verify["planner"].verify(first["planner_grant"])
    assert action == {**first["identity"], "expires_at": mission["expires_at"]}
    assert planner == {**first["identity"], "purpose": "planner", "expires_at": mission["expires_at"]}
    assert action["release_digest"] == p["release"]["digest"]

    rotated = key_document("2")
    rotated["keys"] += p["document"]["keys"]  # old verification keys remain through existing grants' expiry
    write_keys(p["keys"], rotated)
    second = claim(p, mission)
    verify = verifiers(p)
    assert second["grant"] == first["grant"]
    assert second["planner_grant"] != first["planner_grant"]
    assert verify["action"].verify(second["grant"]) == action
    assert verify["planner"].verify(second["planner_grant"]) == planner
    p["keys"].unlink()
    assert claim(p, mission, expected=503) == {"error": "execution signing configuration is unavailable"}
    write_keys(p["keys"], rotated)
    assert claim(p, mission)["grant"] == first["grant"]
    claim(p, mission, expected=409, identity={**CLAIM, "incarnation": "other-coordinator"})
    report = p["agent"].post(f"{p['prefix']}/missions/{mission['id']}/report", json={
        "identity": first["identity"], "state": "unknown", "summary": {"execution_mode": "lockstep_offline"},
    })
    assert report.status_code == 200, report.text
    claim(p, mission, expected=409)
    public = json.dumps([account, p["admin"].get(f"/api/v1/missions/{mission['id']}").json()])
    assert str(p["keys"]) not in public and "PRIVATE KEY" not in public
    assert str(p["keys"]) not in repr(p["settings"])
    assert all(key["private_key_pem"] not in public for key in rotated["keys"])


@pytest.mark.parametrize("failure", ["action_conflict", "planner_conflict", "empty_conflict", "empty_file", "malformed", "missing", "permissions"])
def test_configured_signing_failure_disables_capability_and_never_consumes_mission(paired_api, failure):
    p = paired_api
    mission = start(p, deploy(p))
    if failure == "action_conflict":
        p["settings"].execution_secret = "legacy-action-secret-must-never-be-used"
    elif failure == "planner_conflict":
        p["settings"].planner_execution_secret = "legacy-planner-secret-must-never-be-used"
    elif failure == "empty_conflict":
        p["settings"].execution_secret = ""
    elif failure == "empty_file":
        p["settings"].execution_signing_keys_file = ""
    elif failure == "malformed":
        p["keys"].write_text('{"private_key_pem":"DO-NOT-LEAK-ME"}')
    elif failure == "missing":
        p["keys"].unlink()
    else:
        p["keys"].chmod(0o644)
    account = p["admin"].get("/api/v1/auth/me")
    assert account.status_code == 200
    assert account.json()["installation"]["execution_profiles"] == []
    denied = claim(p, mission, expected=503)
    assert denied == {"error": "execution signing configuration is unavailable"}
    retained = p["admin"].get(f"/api/v1/missions/{mission['id']}").json()
    assert retained["state"] == "requested" and retained["identity"] is None
    assert retained["expires_at"] == mission["expires_at"]
    assert "DO-NOT-LEAK-ME" not in account.text and str(p["keys"]) not in account.text
    p["settings"].execution_secret = p["settings"].planner_execution_secret = None
    p["settings"].execution_signing_keys_file = str(p["keys"])
    write_keys(p["keys"], p["document"])
    assert claim(p, mission)["mission"]["expires_at"] == mission["expires_at"]


def test_invalid_config_blocks_paired_deployment_without_advancing_generation(paired_api):
    p = paired_api
    p["keys"].unlink()
    denied = deploy(p, expected=503)
    assert denied == {"error": "execution signing configuration is unavailable"}
    robots = p["admin"].get("/api/v1/robots", params={"project_id": p["project"]["id"]}).json()
    assert robots[0]["generation"] == 0
    write_keys(p["keys"], p["document"])
    assert deploy(p)["generation"] == 1


def test_planner_signing_failure_rolls_back_action_grant_and_claim(paired_api, monkeypatch):
    p = paired_api
    mission = start(p, deploy(p))
    original = SigningKeys.sign
    signed = []

    def planner_failure(signer, identity, purpose, expires_at):
        signed.append(purpose)
        if purpose == "planner":
            raise ValueError("DO-NOT-LEAK-PRIVATE-CONFIG")
        return original(signer, identity, purpose, expires_at)

    monkeypatch.setattr(SigningKeys, "sign", planner_failure)
    assert claim(p, mission, expected=503) == {"error": "execution signing configuration is unavailable"}
    assert signed == ["action", "planner"]
    with db.session_scope() as session:
        retained = session.get(Mission, mission["id"])
        assert retained.state == "requested" and retained.identity is None and retained.grant is None
    monkeypatch.setattr(SigningKeys, "sign", original)
    assert claim(p, mission)["mission"]["expires_at"] == mission["expires_at"]


def test_keyless_evaluation_job_uses_existing_admission_without_access_to_signer(paired_api):
    p = paired_api
    suite = post(p["admin"], f"/api/v1/applications/{p['application']['id']}/evaluation-suites", {
        "name": "paired single seed", "reference_release_id": p["release"]["id"], "seeds": [0], "min_successes": 1,
    })
    body = {"suite_id": suite["id"], "release_id": p["release"]["id"], "robot_id": p["robot"]["id"]}
    p["keys"].unlink()
    post(p["admin"], "/api/v1/evaluations", body, expected=503)
    write_keys(p["keys"], p["document"])
    run = post(p["admin"], "/api/v1/evaluations", body)
    p["settings"].execution_signing_keys_file = None
    lease = claim_job("keyless-job")
    assert lease[0] == run["id"]
    assert step_job(run["id"], "keyless-job", lease[1])
    desired = p["agent"].get(p["prefix"] + "/desired").json()
    ready(p, desired["deployment"])
    assert step_job(run["id"], "keyless-job", lease[1])
    current = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    assert current["state"] == "running"
    mission = p["admin"].get(f"/api/v1/missions/{current['cases'][0]['mission_id']}").json()
    claim(p, mission, expected=503)
    p["settings"].execution_signing_keys_file = str(p["keys"])
    assert claim(p, mission)["mission"]["expires_at"] == mission["expires_at"]


def test_legacy_hmac_remains_explicit_when_no_private_file_is_configured(paired_api):
    p = paired_api
    p["settings"].execution_signing_keys_file = None
    p["settings"].execution_secret = "legacy-action-with-at-least-thirty-two-bytes"
    p["settings"].planner_execution_secret = "legacy-planner-with-at-least-thirty-two-bytes"
    mission = start(p, deploy(p))
    response = claim(p, mission)
    assert verify_grant(response["grant"], p["settings"].execution_secret)["expires_at"] == mission["expires_at"]
    assert verify_planner_grant(response["planner_grant"], p["settings"].planner_execution_secret)["expires_at"] == mission["expires_at"]
    assert claim(p, mission) == response
    p["settings"].execution_signing_keys_file = str(p["keys"])
    claim(p, mission, expected=503)  # even a stored HMAC action is not returned under conflicting config
