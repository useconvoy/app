"""Public-only planner authority stays distinct and is rechecked after model work."""

import json
import time
from types import SimpleNamespace

import pytest
from convoy_contracts.execution import canonical_digest
from convoy_contracts.grants import GrantVerifier, SigningKeys
from convoy_contracts.pairing import sign_planner_grant
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_planner import PROBE, SECRET, Backend, manifest, request

from convoy_planner.app import create_app
from convoy_planner.cli import execution_options


@pytest.fixture
def keys(tmp_path):
    entries = []
    for purpose in ("action", "planner"):
        pem = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        entries.append({"kid": purpose + "-1", "purpose": purpose, "audience": "convoy-" + purpose,
                        "private_key_pem": pem})
    private = tmp_path / "issuer.json"
    private.write_text(json.dumps({"schema_version": 1, "issuer": "convoy-test",
                                  "active": {p: p + "-1" for p in ("action", "planner")}, "keys": entries}))
    private.chmod(0o600)
    signer = SigningKeys(private)
    paths, documents = {}, {}
    for purpose in ("action", "planner"):
        paths[purpose] = tmp_path / (purpose + "-public.json")
        documents[purpose] = signer.verification_document(purpose)
        paths[purpose].write_text(json.dumps(documents[purpose]))
        paths[purpose].chmod(0o600)
    return SimpleNamespace(signer=signer, paths=paths, documents=documents)


def who(bundle):
    return {"robot_id": "robot", "device_id": "device", "mission_id": "mission", "boot_id": "boot",
            "incarnation": "process", "authority_epoch": 1, "release_digest": canonical_digest(bundle)}


def auth(keys, identity, expiry, purpose="planner"):
    return {"Authorization": "Bearer " + keys.signer.sign(identity, purpose, expiry)}


def verifier(keys, purpose="planner"):
    return GrantVerifier(keys.paths[purpose], purpose=purpose)


def test_planner_public_verifier_rejects_action_and_hmac_authority(keys):
    backend = Backend()
    bundle = manifest(backend)
    identity, expiry = who(bundle), time.time() + 60
    with TestClient(create_app(bundle, backend, grant_verifier=verifier(keys), probe_token=PROBE)) as api:
        for headers in (auth(keys, identity, expiry, "action"),
                        {"Authorization": "Bearer " + sign_planner_grant(identity, SECRET, expiry)}):
            assert api.post("/v1/sessions/start", json={"identity": identity}, headers=headers).status_code == 401
        headers = auth(keys, identity, expiry)
        assert api.post("/v1/sessions/start", json={"identity": identity}, headers=headers).status_code == 200
        result = api.post("/v1/plans", json=request(identity), headers=headers)
        assert result.status_code == 200 and result.json()["identity"] == identity
        assert backend.calls == 1
    for options in ({}, {"execution_secret": SECRET, "grant_verifier": verifier(keys)},
                    {"grant_verifier": verifier(keys, "action")}):
        with pytest.raises(ValueError):
            create_app(bundle, Backend(), probe_token=PROBE, **options)


@pytest.mark.parametrize("stage", ["snapshot", "inference"])
def test_planner_revocation_after_backend_work_poisons_original_session(keys, stage):
    backend = Backend()
    bundle = manifest(backend)
    identity = who(bundle)
    headers = auth(keys, identity, time.time() + 60)
    def revoke():
        keys.paths["planner"].write_text(json.dumps({**keys.documents["planner"], "keys": []}))
    if stage == "snapshot":
        inspect = backend.inspect
        def revoked_snapshot():
            result = inspect()
            revoke()
            return result
        backend.inspect = revoked_snapshot
    else:
        backend.hook = revoke
    with TestClient(create_app(bundle, backend, grant_verifier=verifier(keys), probe_token=PROBE)) as api:
        started = api.post("/v1/sessions/start", json={"identity": identity}, headers=headers)
        if stage == "snapshot":
            assert started.status_code == 401
        else:
            assert started.status_code == 200
            assert api.post("/v1/plans", json=request(identity), headers=headers).status_code == 401
        keys.paths["planner"].write_text(json.dumps(keys.documents["planner"]))
        assert api.post("/v1/sessions/start", json={"identity": identity}, headers=headers).status_code == 409
        assert api.post("/v1/plans", json=request(identity), headers=headers).status_code == 409
        assert backend.calls == int(stage == "inference")


def test_planner_original_expiry_cannot_be_extended_and_expired_result_is_rejected(keys, monkeypatch):
    backend = Backend()
    bundle = manifest(backend)
    identity, expiry = who(bundle), time.time() + 30
    backend.hook = lambda: monkeypatch.setattr("convoy_planner.app.time.time", lambda: expiry + 1)
    headers, extended = auth(keys, identity, expiry), auth(keys, identity, expiry + 30)
    with TestClient(create_app(bundle, backend, grant_verifier=verifier(keys), probe_token=PROBE)) as api:
        assert api.post("/v1/sessions/start", json={"identity": identity}, headers=headers).status_code == 200
        assert api.post("/v1/plans", json=request(identity), headers=extended).status_code == 409
        assert backend.calls == 0
        assert api.post("/v1/plans", json=request(identity), headers=headers).status_code == 409
        assert api.post("/v1/sessions/start", json={"identity": identity}, headers=headers).status_code == 401
        assert backend.calls == 1


@pytest.mark.parametrize("fault", ["dual", "private", "corrupt", "same_hmac", "empty_path", "empty_secret", "missing", "opposite", "opposite_empty"])
def test_planner_cli_rejects_conflicting_or_private_authorization(keys, monkeypatch, fault):
    for name in ("CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET", "CONVOY_EXECUTION_SIGNING_KEYS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVOY_PLANNER_VERIFICATION_KEYS_FILE", str(keys.paths["planner"]))
    if fault == "dual":
        monkeypatch.setenv("CONVOY_PLANNER_EXECUTION_SECRET", SECRET)
    elif fault == "private":
        monkeypatch.setenv("CONVOY_EXECUTION_SIGNING_KEYS_FILE", "never-read-private-key-path")
    elif fault == "corrupt":
        keys.paths["planner"].write_text("invalid public key configuration")
    elif fault == "empty_path":
        monkeypatch.setenv("CONVOY_PLANNER_VERIFICATION_KEYS_FILE", "")
    elif fault == "empty_secret":
        monkeypatch.setenv("CONVOY_PLANNER_EXECUTION_SECRET", "")
    elif fault in {"opposite", "opposite_empty"}:
        monkeypatch.setenv("CONVOY_EXECUTION_SECRET", SECRET if fault == "opposite" else "")
    elif fault == "missing":
        monkeypatch.delenv("CONVOY_PLANNER_VERIFICATION_KEYS_FILE")
    else:
        monkeypatch.delenv("CONVOY_PLANNER_VERIFICATION_KEYS_FILE")
        monkeypatch.setenv("CONVOY_PLANNER_EXECUTION_SECRET", SECRET)
        monkeypatch.setenv("CONVOY_EXECUTION_SECRET", SECRET)
    with pytest.raises(ValueError):
        execution_options()
