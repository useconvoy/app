"""Observable worker authorization, runtime identity and admission behavior."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from convoy_contracts.execution import PROFILE, canonical_digest, sign_grant
from convoy_worker.app import create_app
from fastapi.testclient import TestClient

SECRET = "execution-secret-for-tests-only-1234567890"
PROBE = "probe-secret-for-tests-only-12345678901234"
MANIFEST = {
    "schema_version": 1, "profile": PROFILE,
    "policy": {"runtime": "fixture", "artifact_sha256": "a" * 64},
    "environment": {"name": "pick-place-v3", "metaworld": "3.1.1", "mujoco": "3.3.0"},
    "execution": {"max_steps": 100, "decision_timeout_ms": 1000, "mission_timeout_s": 60},
}
IDENTITY = {
    "robot_id": "robot", "device_id": "device", "mission_id": "mission", "boot_id": "boot",
    "incarnation": "process", "authority_epoch": 1, "release_digest": canonical_digest(MANIFEST),
}


class Runtime:
    runtime = "fixture"
    artifact_sha256 = "a" * 64

    def get_action(self, observation):
        return [0.1, 0.2, 0.3, 0.0]


def request():
    return {
        "identity": IDENTITY.copy(), "request_id": "request", "observation_id": "observation",
        "sequence": 0, "observation": [0.0] * 39,
        "deadline_monotonic_ns": time.monotonic_ns() + 1_000_000_000, "budget_ms": 1000,
    }


def client(runtime=None):
    return TestClient(create_app(MANIFEST, runtime or Runtime(), execution_secret=SECRET, probe_token=PROBE))


def authorization():
    return {"Authorization": "Bearer " + sign_grant(IDENTITY, SECRET, time.time() + 60)}


def test_probe_cannot_execute_and_grant_binds_every_identity_field():
    with client() as api:
        probe = {"release_digest": canonical_digest(MANIFEST), "profile": PROFILE}
        assert api.post("/v1/probe", json=probe).status_code == 401
        assert api.post("/v1/probe", json=probe, headers={"Authorization": f"Bearer {PROBE}"}).status_code == 200
        assert api.post("/v1/decisions", json=request(), headers={"Authorization": f"Bearer {PROBE}"}).status_code == 401
        headers = authorization()
        for field in IDENTITY:
            body = request()
            body["identity"][field] = 2 if field == "authority_epoch" else (
                "b" * 64 if field == "release_digest" else "another"
            )
            assert api.post("/v1/decisions", json=body, headers=headers).status_code == 403
        body = request()
        result = api.post("/v1/decisions", json=body, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["action"] == [0.1, 0.2, 0.3, 0.0]
        for key in ("identity", "request_id", "observation_id", "sequence", "deadline_monotonic_ns"):
            assert result.json()[key] == body[key]
        assert api.post("/v1/decisions", content=b" " * 16385, headers=headers).status_code == 413


def test_saturated_worker_rejects_without_queueing_and_recovers():
    entered, release = threading.Event(), threading.Event()

    class Blocking(Runtime):
        def get_action(self, observation):
            entered.set()
            assert release.wait(5)
            return super().get_action(observation)

    with client(Blocking()) as api, ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(api.post, "/v1/decisions", json=request(), headers=authorization())
        assert entered.wait(5)
        try:
            second = api.post("/v1/decisions", json=request(), headers=authorization())
            assert second.status_code == 429
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
        assert api.post("/v1/decisions", json=request(), headers=authorization()).status_code == 200


def test_runtime_identity_and_invalid_action_fail_closed():
    wrong = Runtime()
    wrong.artifact_sha256 = "b" * 64
    with pytest.raises(ValueError, match="does not match"):
        client(wrong)

    class Invalid(Runtime):
        def get_action(self, observation):
            return [2.0, 0.0, 0.0, 0.0]

    with client(Invalid()) as api:
        assert api.post("/v1/decisions", json=request(), headers=authorization()).status_code == 502
