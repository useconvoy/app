"""Preserve the old action identity while authorizing one independently bound planner."""

import copy
import time

import pytest
from convoy_contracts.execution import VISUAL_PROFILE, canonical_digest, sign_grant, verify_grant
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
    action_manifest,
    evaluation_contract,
    sign_planner_grant,
    validate_plan_request,
    validate_plan_result,
    validate_release_manifest,
    verify_planner_grant,
)

IDENTITY = {"robot_id": "r", "device_id": "d", "mission_id": "m", "boot_id": "b", "incarnation": "i",
            "release_digest": "a" * 64, "authority_epoch": 1}
SECRET = "planner-test-secret-at-least-32-characters"


def manifest():
    return {"schema_version": 2, "profile": PAIRED_PROFILE,
        "action_manifest": {"schema_version": 1, "profile": VISUAL_PROFILE,
            "policy": {"runtime": "qualified-action", "artifact_sha256": "a" * 64},
            "environment": {"name": "pick-place-v3", "metaworld": "3.0.0", "mujoco": "3.3.0"},
            "execution": {"max_steps": 500, "decision_timeout_ms": 5000, "mission_timeout_s": 300}},
        "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": "b" * 64,
                    "protocol_sha256": PLANNER_PROTOCOL_SHA256},
        "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
        "planning": {"timeout_ms": 30000},
        "placement": {"policy": "development-local-cpu", "planner": "development-jetson-lan"}}


def test_bundle_preserves_action_identity_and_comparison_semantics():
    value = manifest()
    original = copy.deepcopy(value)
    assert validate_release_manifest(value) is value
    child = value["action_manifest"]
    assert action_manifest(value) is child and action_manifest(child) is child
    assert value == original
    candidate = copy.deepcopy(value)
    candidate["action_manifest"]["policy"]["artifact_sha256"] = "c" * 64
    candidate["planner"]["artifact_sha256"] = "d" * 64
    assert canonical_digest(candidate) != canonical_digest(value)
    assert evaluation_contract(candidate) == evaluation_contract(value)
    candidate["planning"]["timeout_ms"] = 20000
    assert evaluation_contract(candidate) != evaluation_contract(value)
    for key, invalid in (("catalog_sha256", "0" * 64), ("task", {"instruction": "open door"}),
                         ("placement", {"policy": "jetson", "planner": "cloud"})):
        with pytest.raises(ValueError):
            validate_release_manifest({**value, key: invalid})
    bad = copy.deepcopy(value)
    bad["planner"]["protocol_sha256"] = "e" * 64
    with pytest.raises(ValueError):
        validate_release_manifest(bad)


def test_planner_grants_do_not_cross_purpose_or_key_and_keep_original_expiry():
    expiry = time.time() + 60
    token = sign_planner_grant(IDENTITY, SECRET, expiry)
    assert verify_planner_grant(token, SECRET) == {**IDENTITY, "purpose": "planner", "expires_at": expiry}
    for invalid, key, now in ((token, SECRET + "x", None), (token, SECRET, expiry),
                              (token + "x", SECRET, None), (sign_grant(IDENTITY, SECRET, expiry), SECRET, None)):
        with pytest.raises(ValueError):
            verify_planner_grant(invalid, key, now)
    with pytest.raises(ValueError):
        verify_grant(token, SECRET)


def test_plan_envelope_cannot_expand_task_or_hide_unqualified_parameters():
    request = {"identity": IDENTITY, "request_id": "q", "observation_id": "o",
               "observation_digest": "f" * 64, "deadline_monotonic_ns": 100, "budget_ms": 1000}
    validate_plan_request(request)
    with pytest.raises(ValueError):
        validate_plan_request({**request, "task": "open door"})
    result = {**{key: value for key, value in request.items() if key != "budget_ms"},
              "planner_artifact_sha256": "b" * 64, "planner_incarnation": "p", "runtime_generation": 1,
              "decision": {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}},
              "planner_duration_ms": 15}
    validate_plan_result(result)
    for decision in ({"kind": "skill", "skill_id": SKILL_ID, "parameters": {"goal": [0, 0, 0]}},
                     {"kind": "skill", "skill_id": "open_door", "parameters": {}},
                     {"kind": "decline", "reason": "unsupported_task", "message": "extra"}):
        with pytest.raises(ValueError):
            validate_plan_result({**result, "decision": decision})
