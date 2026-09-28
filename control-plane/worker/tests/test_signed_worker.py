"""Public-only worker authority and revocation across real service admission."""

import json
import sys
import time
from types import SimpleNamespace

import pytest
from convoy_contracts.execution import sign_grant
from convoy_contracts.grants import GrantVerifier, SigningKeys
from convoy_worker.app import create_app
from convoy_worker.cli import main
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_visual_sessions import MANIFEST, PROBE, SECRET, Runtime, identity, request


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


def auth(keys, who, expiry, purpose="action"):
    return {"Authorization": "Bearer " + keys.signer.sign(who, purpose, expiry)}


def verifier(keys, purpose="action"):
    return GrantVerifier(keys.paths[purpose], purpose=purpose)


def test_worker_public_verifier_rejects_planner_and_hmac_authority(keys):
    runtime, who, expiry = Runtime(), identity(), time.time() + 60
    with TestClient(create_app(MANIFEST, runtime, grant_verifier=verifier(keys), probe_token=PROBE)) as api:
        for headers in (auth(keys, who, expiry, "planner"),
                        {"Authorization": "Bearer " + sign_grant(who, SECRET, expiry)}):
            assert api.post("/v1/sessions/start", json={"identity": who}, headers=headers).status_code == 401
        assert runtime.resets == []
        headers = auth(keys, who, expiry)
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=headers).status_code == 200
        result = api.post("/v1/decisions", json=request(who), headers=headers)
        assert result.status_code == 200 and result.json()["identity"] == who
        assert runtime.calls == 1
    for options in ({}, {"execution_secret": SECRET, "grant_verifier": verifier(keys)},
                    {"grant_verifier": verifier(keys, "planner")}):
        with pytest.raises(ValueError):
            create_app(MANIFEST, Runtime(), probe_token=PROBE, **options)


@pytest.mark.parametrize("stage", ["reset", "inference"])
def test_worker_rechecks_revocation_before_response_and_poisoned_session_cannot_replay(keys, stage):
    def revoke():
        keys.paths["action"].write_text(json.dumps({**keys.documents["action"], "keys": []}))

    class Revoking(Runtime):
        def reset_session(self, who):
            super().reset_session(who)
            if stage == "reset":
                revoke()

        def get_action(self, observation):
            action = super().get_action(observation)
            revoke()
            return action

    runtime, who = Revoking(), identity()
    headers = auth(keys, who, time.time() + 60)
    with TestClient(create_app(MANIFEST, runtime, grant_verifier=verifier(keys), probe_token=PROBE)) as api:
        started = api.post("/v1/sessions/start", json={"identity": who}, headers=headers)
        if stage == "reset":
            assert started.status_code == 401
        else:
            assert started.status_code == 200
            assert api.post("/v1/decisions", json=request(who), headers=headers).status_code == 401
        keys.paths["action"].write_text(json.dumps(keys.documents["action"]))
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=headers).status_code == 409
        assert api.post("/v1/decisions", json=request(who, 1), headers=headers).status_code == 409
        assert len(runtime.resets) == 1 and runtime.calls == int(stage == "inference")


def test_worker_preserves_original_expiry_and_rejects_expired_result(keys, monkeypatch):
    who, expiry = identity(), time.time() + 30

    class Expiring(Runtime):
        def get_action(self, observation):
            action = super().get_action(observation)
            monkeypatch.setattr("convoy_worker.app.time.time", lambda: expiry + 1)
            return action

    runtime = Expiring()
    headers = auth(keys, who, expiry)
    extended = auth(keys, who, expiry + 30)
    with TestClient(create_app(MANIFEST, runtime, grant_verifier=verifier(keys), probe_token=PROBE)) as api:
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=headers).status_code == 200
        assert api.post("/v1/decisions", json=request(who), headers=extended).status_code == 409
        assert runtime.calls == 0
        assert api.post("/v1/decisions", json=request(who), headers=headers).status_code == 504
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=headers).status_code == 401
        assert runtime.calls == 1


@pytest.mark.parametrize("fault", ["dual", "private", "corrupt", "empty_path", "empty_secret", "missing", "opposite", "opposite_empty"])
def test_worker_cli_rejects_bad_authorization_before_model_import(keys, monkeypatch, fault):
    for name in ("CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET", "CONVOY_EXECUTION_SIGNING_KEYS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVOY_ACTION_VERIFICATION_KEYS_FILE", str(keys.paths["action"]))
    if fault == "dual":
        monkeypatch.setenv("CONVOY_EXECUTION_SECRET", SECRET)
    elif fault == "private":
        monkeypatch.setenv("CONVOY_EXECUTION_SIGNING_KEYS_FILE", "never-read-private-key-path")
    elif fault == "corrupt":
        keys.paths["action"].write_text("invalid public key configuration")
    elif fault == "empty_path":
        monkeypatch.setenv("CONVOY_ACTION_VERIFICATION_KEYS_FILE", "")
    elif fault == "empty_secret":
        monkeypatch.setenv("CONVOY_EXECUTION_SECRET", "")
    elif fault in {"opposite", "opposite_empty"}:
        monkeypatch.setenv("CONVOY_PLANNER_EXECUTION_SECRET", SECRET if fault == "opposite" else "")
    else:
        monkeypatch.delenv("CONVOY_ACTION_VERIFICATION_KEYS_FILE")
    monkeypatch.setattr(sys, "argv", ["worker", "--release", "unused.json", "--factory", "model:load"])
    def forbid_import(*_):
        pytest.fail("model import must follow authorization configuration validation")
    monkeypatch.setattr("convoy_worker.cli.importlib.import_module", forbid_import)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
