"""Stateful model sessions cannot replay actions, reset live owners, or outlive close."""

import base64
import struct
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

from convoy_contracts.execution import (
    MAX_VISUAL_REQUEST_BYTES,
    VISUAL_INSTRUCTION,
    VISUAL_PROFILE,
    canonical_digest,
    sign_grant,
)
from convoy_worker.app import create_app
from convoy_worker.sessions import Sessions
from fastapi.testclient import TestClient

SECRET = "execution-secret-for-tests-only-1234567890"
PROBE = "probe-secret-for-tests-only-12345678901234"
MANIFEST = {
    "schema_version": 1, "profile": VISUAL_PROFILE,
    "policy": {"runtime": "visual-fixture", "artifact_sha256": "a" * 64},
    "environment": {"name": "pick-place-v3", "metaworld": "3.0.0", "mujoco": "3.3.0"},
    "execution": {"max_steps": 500, "decision_timeout_ms": 5000, "mission_timeout_s": 300},
}


def identity(mission="mission"):
    return {"robot_id": "robot", "device_id": "device", "mission_id": mission, "boot_id": "boot",
            "incarnation": "process", "authority_epoch": 1, "release_digest": canonical_digest(MANIFEST)}


def headers(who):
    return {"Authorization": "Bearer " + sign_grant(who, SECRET, time.time() + 60)}


def request(who, sequence=0):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 480, 480, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(bytes(480 * 1441))) + chunk(b"IEND", b""))
    return {"identity": who, "request_id": f"req-{sequence}", "observation_id": f"obs-{sequence}",
            "sequence": sequence, "observation": {"image_png_base64": base64.b64encode(png).decode(),
                    "state": [0.0] * 4, "instruction": VISUAL_INSTRUCTION},
            "deadline_monotonic_ns": time.monotonic_ns() + 5_000_000_000, "budget_ms": 5000}


class Runtime:
    runtime, artifact_sha256, profile = "visual-fixture", "a" * 64, VISUAL_PROFILE

    def __init__(self):
        self.resets, self.calls = [], 0

    def reset_session(self, who):
        self.resets.append(who)

    def get_action(self, observation):
        self.calls += 1
        return [0.1, 0.2, 0.3, 0.0]


def client(runtime):
    return TestClient(create_app(MANIFEST, runtime, execution_secret=SECRET, probe_token=PROBE))


def test_each_mission_resets_once_and_duplicate_or_changed_observation_never_reuses_actions():
    runtime = Runtime()
    with client(runtime) as api:
        who = identity()
        auth = headers(who)
        assert api.post("/v1/decisions", json=request(who, 1), headers=auth).status_code == 409
        for _ in range(2):
            assert api.post("/v1/sessions/start", json={"identity": who}, headers=auth).json()["next_sequence"] == 0
        assert len(runtime.resets) == 1
        assert api.post("/v1/decisions", json=request(who), headers=auth).status_code == 200
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=auth).json()["next_sequence"] == 1
        assert len(runtime.resets) == 1
        assert api.post("/v1/decisions", json=request(who), headers=auth).status_code == 409
        assert api.post("/v1/decisions", json=request(who, 2), headers=auth).status_code == 409
        reused = request(who, 1)
        reused["observation_id"] = "obs-0"
        assert api.post("/v1/decisions", json=reused, headers=auth).status_code == 409
        assert runtime.calls == 1
        assert api.post("/v1/decisions", json=request(who, 1), headers=auth).status_code == 200
        assert api.post("/v1/sessions/end", json={"identity": who}, headers=auth).status_code == 200
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=auth).status_code == 409
        next_who = identity("next")
        next_auth = headers(next_who)
        assert api.post("/v1/sessions/start", json={"identity": next_who}, headers=next_auth).status_code == 200
        assert len(runtime.resets) == 2
        assert api.post("/v1/sessions/end", json={"identity": who}, headers=auth).status_code == 200
        assert api.post("/v1/decisions", json=request(next_who), headers=next_auth).status_code == 200
        assert api.post("/v1/decisions", content=b" " * (MAX_VISUAL_REQUEST_BYTES + 1), headers=auth).status_code == 413


def test_close_during_inference_fences_result_and_unseen_delayed_start_without_reset_overlap():
    entered, release = threading.Event(), threading.Event()

    class Blocking(Runtime):
        def get_action(self, observation):
            entered.set()
            assert release.wait(5)
            return super().get_action(observation)

    runtime = Blocking()
    with client(runtime) as api, ThreadPoolExecutor(max_workers=1) as executor:
        who, next_who, unseen = identity(), identity("next"), identity("unseen")
        auth, next_auth, unseen_auth = headers(who), headers(next_who), headers(unseen)
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=auth).status_code == 200
        result = executor.submit(api.post, "/v1/decisions", json=request(who), headers=auth)
        assert entered.wait(5)
        try:
            assert api.post("/v1/sessions/end", json={"identity": who}, headers=auth).status_code == 200
            assert api.post("/v1/sessions/start", json={"identity": next_who}, headers=next_auth).status_code == 429
            assert api.post("/v1/sessions/end", json={"identity": unseen}, headers=unseen_auth).status_code == 200
        finally:
            release.set()
        assert result.result(5).status_code == 409
        assert api.post("/v1/sessions/start", json={"identity": unseen}, headers=unseen_auth).status_code == 409
        assert api.post("/v1/sessions/start", json={"identity": next_who}, headers=next_auth).status_code == 200
        assert len(runtime.resets) == 2


def test_invalid_model_result_poisoned_and_incarnation_original_expiry_bound():
    class Invalid(Runtime):
        def get_action(self, observation):
            return [2.0] * 4
    with client(Invalid()) as api:
        who, auth = identity(), headers(identity())
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=auth).status_code == 200
        changed = {**who, "incarnation": "other", "authority_epoch": 2}
        assert api.post("/v1/decisions", json=request(changed), headers=auth).status_code == 403
        assert api.post("/v1/decisions", json=request(who), headers=headers(who)).status_code == 409
        assert api.post("/v1/decisions", json=request(who), headers=auth).status_code == 502
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=auth).status_code == 409
        assert api.post("/v1/decisions", json=request(who, 1), headers=auth).status_code == 409


def test_retention_capacity_always_reserves_space_to_close_active_owner():
    sessions = Sessions()
    expiry = time.time() + 60
    for number in range(1023):
        sessions.close({**identity(str(number)), "expires_at": expiry})
    grant = {**identity("owner"), "expires_at": expiry}
    owner, _ = sessions.start(grant)
    sessions.close(grant)
    assert sessions.owner is None and len(sessions.closed) == 1024
    assert owner.grant == grant


def test_end_during_reset_never_publishes_a_ready_session():
    entered, release = threading.Event(), threading.Event()

    class BlockingReset(Runtime):
        def reset_session(self, who):
            entered.set()
            assert release.wait(5)
            super().reset_session(who)

    with client(BlockingReset()) as api, ThreadPoolExecutor(max_workers=1) as executor:
        who, auth = identity(), headers(identity())
        result = executor.submit(api.post, "/v1/sessions/start", json={"identity": who}, headers=auth)
        assert entered.wait(5)
        try:
            assert api.post("/v1/sessions/end", json={"identity": who}, headers=auth).status_code == 200
        finally:
            release.set()
        assert result.result(5).status_code == 409
        assert api.post("/v1/decisions", json=request(who), headers=auth).status_code == 409


def test_grant_expiring_during_inference_never_returns_an_action(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class Blocking(Runtime):
        def get_action(self, observation):
            entered.set()
            assert release.wait(5)
            return super().get_action(observation)

    with client(Blocking()) as api, ThreadPoolExecutor(max_workers=1) as executor:
        who, expiry = identity(), time.time() + 30
        auth = {"Authorization": "Bearer " + sign_grant(who, SECRET, expiry)}
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=auth).status_code == 200
        result = executor.submit(api.post, "/v1/decisions", json=request(who), headers=auth)
        assert entered.wait(5)
        try:
            monkeypatch.setattr("convoy_worker.app.time.time", lambda: expiry + 1)
        finally:
            release.set()
        assert result.result(5).status_code == 504
        assert api.post("/v1/sessions/start", json={"identity": who}, headers=auth).status_code == 401
