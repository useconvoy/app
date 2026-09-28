"""Deterministic protocol faults are distinct from real model-quality evidence."""

import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from convoy_contracts.execution import VISUAL_PROFILE, canonical_digest, sign_grant
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    CONTROLLED_PLANNER_RUNTIME,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
    sign_planner_grant,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient

from convoy_planner.app import create_app
from convoy_planner.artifact import artifact_descriptor, artifact_digest, implementation_sources
from convoy_planner.backend import GatewayBackend
from convoy_planner.controlled import BACKEND_KIND, ControlledBackend, controlled_descriptor
from convoy_planner.protocol import parse_decision
from convoy_planner.sessions import Sessions

SECRET = "planner-test-secret-not-a-real-deployment-1234"
PROBE = "planner-test-probe-not-a-real-deployment-12345"
DECISION = {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}}


class Backend:
    def __init__(self):
        self.identity = {"release_id": "legacy-r", "runtime_generation": 1, "gateway_incarnation": "gateway-i",
            "gateway_epoch": 1, "model_sha256": "a" * 64, "runtime_artifact_sha256": "b" * 64,
            "binary_sha256": "c" * 64, "template_sha256": "d" * 64, "config_sha256": "e" * 64,
            "implementation_sha256": {"gateway.py": "f" * 64, "runtime.py": "0" * 64}, "simulated": False}
        self.calls = 0
        self.hook = None
        self.offline = False
        self.decision = DECISION

    def inspect(self):
        if self.offline:
            raise ConnectionError("not available")
        return copy.deepcopy(self.identity)

    def propose(self, identity, budget_s):
        self.calls += 1
        if self.hook:
            self.hook()
        if self.offline or identity != self.identity:
            raise ConnectionError("changed or unavailable")
        return copy.deepcopy(self.decision)


def manifest(backend):
    return {"schema_version": 2, "profile": PAIRED_PROFILE,
        "action_manifest": {"schema_version": 1, "profile": VISUAL_PROFILE,
            "policy": {"runtime": "action", "artifact_sha256": "a" * 64},
            "environment": {"name": "pick-place-v3", "metaworld": "3.0.0", "mujoco": "3.3.0"},
            "execution": {"max_steps": 500, "decision_timeout_ms": 5000, "mission_timeout_s": 300}},
        "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": artifact_digest(backend.identity),
                    "protocol_sha256": PLANNER_PROTOCOL_SHA256},
        "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
        "planning": {"timeout_ms": 30000},
        "placement": {"policy": "development-local-cpu", "planner": "development-jetson-lan"}}


def setup():
    backend = Backend()
    bundle = manifest(backend)
    client = TestClient(create_app(bundle, backend, execution_secret=SECRET, probe_token=PROBE))
    return backend, bundle, client


def authority(bundle, mission="m", lifetime=60):
    identity = {"robot_id": "r", "device_id": "d", "mission_id": mission, "boot_id": "b", "incarnation": "i",
                "release_digest": canonical_digest(bundle), "authority_epoch": 1}
    token = sign_planner_grant(identity, SECRET, time.time() + lifetime)
    return identity, {"Authorization": "Bearer " + token}


def request(identity, budget_ms=1000):
    return {"identity": identity, "request_id": "q", "observation_id": "o", "observation_digest": "f" * 64,
            "deadline_monotonic_ns": time.monotonic_ns() + 1_000_000_000, "budget_ms": budget_ms}


def test_parser_accepts_only_complete_catalog_objects():
    assert parse_decision(json.dumps(DECISION)) == DECISION
    assert parse_decision('{"kind":"decline","reason":"unsupported_task"}')["kind"] == "decline"
    for content in ("```json\n" + json.dumps(DECISION) + "\n```", json.dumps(DECISION) + " trailing",
                    '{"kind":"skill","kind":"decline","reason":"unsupported_task"}',
                    '{"kind":"skill","skill_id":"pick_place_puck","parameters":{"x":NaN}}',
                    json.dumps({**DECISION, "parameters": {"goal": [0, 0, 0]}}), "x" * 2049):
        with pytest.raises(ValueError):
            parse_decision(content)


def test_artifact_binds_bytes_and_semantics_but_not_current_launch(tmp_path):
    backend = Backend()
    original = artifact_digest(backend.identity)
    backend.identity.update(runtime_generation=2, gateway_incarnation="next", release_id="same-content-new-id")
    assert artifact_digest(backend.identity) == original
    backend.identity["model_sha256"] = "1" * 64
    assert artifact_digest(backend.identity) != original
    sources = implementation_sources()
    changed = tmp_path / "protocol.py"
    changed.write_text(sources["convoy_planner/protocol.py"].read_text() + "\n# changed source\n")
    assert artifact_descriptor(backend.identity, {**sources, "convoy_planner/protocol.py": changed}) != artifact_descriptor(backend.identity)
    backend.identity["simulated"] = True
    with pytest.raises(ValueError):
        artifact_digest(backend.identity)


def test_one_plan_per_session_and_next_mission_captures_new_generation():
    backend, bundle, client = setup()
    identity, headers = authority(bundle)
    probe = client.post("/v1/probe", json={"release_digest": identity["release_digest"], "profile": PAIRED_PROFILE},
                        headers={"Authorization": "Bearer " + PROBE})
    assert probe.status_code == 200
    first = client.post("/v1/sessions/start", json={"identity": identity}, headers=headers).json()
    assert first["next_sequence"] == 0
    result = client.post("/v1/plans", json=request(identity), headers=headers)
    assert result.status_code == 200 and result.json()["decision"] == DECISION
    for key in ("planner_artifact_sha256", "planner_incarnation", "runtime_generation"):
        assert result.json()[key] == first[key] == probe.json()[key]
    assert client.post("/v1/sessions/start", json={"identity": identity}, headers=headers).json()["next_sequence"] == 1
    assert client.post("/v1/plans", json={**request(identity), "request_id": "new"}, headers=headers).status_code == 409
    assert backend.calls == 1
    assert client.post("/v1/sessions/end", json={"identity": identity}, headers=headers).status_code == 200
    assert client.post("/v1/sessions/start", json={"identity": identity}, headers=headers).status_code == 409
    backend.identity["runtime_generation"] = 2
    other, other_headers = authority(bundle, "m2")
    assert client.post("/v1/sessions/start", json={"identity": other}, headers=other_headers).json()["runtime_generation"] == 2
    assert client.post("/v1/plans", json=request(other), headers=other_headers).status_code == 200
    assert backend.calls == 2


def test_cancellation_fences_inflight_completion_and_unseen_start():
    backend, bundle, client = setup()
    identity, headers = authority(bundle)
    assert client.post("/v1/sessions/start", json={"identity": identity}, headers=headers).status_code == 200
    entered, release = threading.Event(), threading.Event()
    backend.hook = lambda: (entered.set(), release.wait(3))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/v1/plans", json=request(identity), headers=headers)
        assert entered.wait(2)
        assert client.post("/v1/sessions/end", json={"identity": identity}, headers=headers).status_code == 200
        other, other_headers = authority(bundle, "m2")
        assert client.post("/v1/sessions/start", json={"identity": other}, headers=other_headers).status_code == 429
        release.set()
        assert pending.result().status_code == 409
    assert client.post("/v1/sessions/end", json={"identity": other}, headers=other_headers).status_code == 200
    assert client.post("/v1/sessions/start", json={"identity": other}, headers=other_headers).status_code == 409


@pytest.mark.parametrize("fault", ["offline", "generation", "malformed", "late", "expired"])
def test_fault_never_regenerates_a_plan(fault):
    backend, bundle, client = setup()
    identity, headers = authority(bundle, lifetime=0.2 if fault == "expired" else 60)
    assert client.post("/v1/sessions/start", json={"identity": identity}, headers=headers).status_code == 200
    if fault == "offline":
        backend.offline = True
    if fault == "generation":
        backend.identity["runtime_generation"] += 1
    if fault == "malformed":
        backend.decision = {**DECISION, "parameters": {"goal": "unqualified"}}
    if fault in {"late", "expired"}:
        backend.hook = lambda: time.sleep(0.3 if fault == "expired" else 0.02)
    response = client.post("/v1/plans", json=request(identity, 1 if fault == "late" else 1000), headers=headers)
    assert response.status_code in {409, 502, 504}
    backend.offline, backend.hook, backend.decision = False, None, DECISION
    assert client.post("/v1/plans", json=request(identity), headers=headers).status_code in {401, 409}
    assert backend.calls == 1


def test_readiness_request_bounds_and_cross_purpose_rejections():
    backend, bundle, client = setup()
    identity, headers = authority(bundle)
    backend.identity["model_sha256"] = "1" * 64
    probe = {"release_digest": identity["release_digest"], "profile": PAIRED_PROFILE}
    assert client.post("/v1/probe", json=probe, headers={"Authorization": "Bearer " + PROBE}).status_code == 409
    backend.offline = True
    assert client.post("/v1/probe", json=probe, headers={"Authorization": "Bearer " + PROBE}).status_code == 503
    token = sign_grant(identity, SECRET, time.time() + 60)
    assert client.post("/v1/sessions/start", json={"identity": identity}, headers={"Authorization": "Bearer " + token}).status_code == 401
    assert client.post("/v1/plans", json={**request(identity), "task": "open door"}, headers=headers).status_code == 422
    assert client.post("/v1/plans", content="x" * 16385, headers=headers).status_code == 413
    for address in ("http://192.168.1.229:8080", "http://user:pass@localhost:8080", "http://localhost:8080?x=1"):
        with pytest.raises(ValueError):
            GatewayBackend(address)


def test_active_session_retains_capacity_to_cancel_at_tombstone_limit():
    sessions = Sessions()
    expiry = time.time() + 30
    for number in range(1023):
        sessions.close({"mission_id": str(number), "expires_at": expiry})
    grant = {"mission_id": "active", "expires_at": expiry}
    active, _ = sessions.start(grant)
    with pytest.raises(HTTPException) as full:
        sessions.close({"mission_id": "unseen", "expires_at": expiry})
    assert full.value.status_code == 429
    sessions.close(grant)
    assert len(sessions.closed) == 1024 and sessions.owner is None
    with pytest.raises(HTTPException):
        sessions.check(active)


def test_controlled_runtime_is_explicit_and_cannot_claim_real_model_provenance():
    bundle = manifest(Backend())
    descriptor = controlled_descriptor()
    assert descriptor["backend_kind"] == BACKEND_KIND and "gateway" not in descriptor
    bundle["planner"].update(runtime=CONTROLLED_PLANNER_RUNTIME, artifact_sha256=canonical_digest(descriptor))
    bundle["placement"]["planner"] = "development-local-controlled"
    backend = ControlledBackend()
    client = TestClient(create_app(bundle, backend, execution_secret=SECRET, probe_token=PROBE))
    identity, headers = authority(bundle)
    probe = client.post("/v1/probe", json={"release_digest": identity["release_digest"], "profile": PAIRED_PROFILE},
                        headers={"Authorization": "Bearer " + PROBE})
    assert probe.status_code == 200 and probe.json()["runtime"] == CONTROLLED_PLANNER_RUNTIME
    assert client.post("/v1/sessions/start", json={"identity": identity}, headers=headers).status_code == 200
    assert client.post("/v1/plans", json=request(identity), headers=headers).json()["decision"] == DECISION
    with pytest.raises(ValueError):
        artifact_descriptor(backend.inspect())
    with pytest.raises(ValueError):
        create_app({**bundle, "placement": {"policy": "development-local-cpu", "planner": "development-jetson-lan"}},
                   backend, execution_secret=SECRET, probe_token=PROBE)
