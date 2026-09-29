"""One fixed text-to-skill admission followed by the unchanged visual policy.

Planner monotonic deadlines belong to the coordinator and are echoed unchanged.
This profile adds composition, not perception, arbitrary tasks or real-time claims.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from .execution import (
    IDENTITY_FIELDS,
    PROFILES,
    VISUAL_INSTRUCTION,
    VISUAL_PROFILE,
    _digest,
    _integer,
    _keys,
    _number,
    _text,
    canonical_digest,
    canonical_json,
    validate_identity,
    validate_manifest,
)

PAIRED_PROFILE = "metaworld-smolvla-text-skill-v1"
PLANNER_RUNTIME = "convoy-llamacpp-text-skill-v1"
CONTROLLED_PLANNER_RUNTIME = "convoy-controlled-text-skill-v1"
FIXED_TASK = VISUAL_INSTRUCTION
SKILL_ID = "pick_place_puck"
CATALOG = {"schema_version": 1, "skills": [{"id": SKILL_ID, "parameters": {},
    "instruction": FIXED_TASK, "action_profile": VISUAL_PROFILE}],
    "scope": "one-qualified-puck-and-goal-in-pinned-metaworld-scene"}
CATALOG_SHA256 = canonical_digest(CATALOG)
SYSTEM_PROMPT = (
    "You select a skill from a fixed robot catalog. The only skill is pick_place_puck: "
    "pick and place the puck at the goal in the qualified scene. Its parameters are empty. "
    'For that task return exactly {"kind":"skill","skill_id":"pick_place_puck","parameters":{}}. '
    'Otherwise return exactly {"kind":"decline","reason":"unsupported_task"}. '
    "Return only one JSON object, without reasoning, markdown, comments or extra keys. /no_think"
)
PLANNER_PROTOCOL = {"schema_version": 1, "system_prompt": SYSTEM_PROMPT,
    "user_prompt": FIXED_TASK, "parser": "single-json-object-no-duplicate-keys-exact-catalog-v1",
    "generation": {"max_tokens": 128, "temperature": 0.0, "seed": 42, "stream": False},
    "max_output_bytes": 2048}
PLANNER_PROTOCOL_SHA256 = canonical_digest(PLANNER_PROTOCOL)
PLAN_ECHO_FIELDS = {"identity", "request_id", "observation_id", "observation_digest", "deadline_monotonic_ns"}
PLANNER_IDENTITY_FIELDS = {"planner_artifact_sha256", "planner_incarnation", "runtime_generation"}


def release_profiles() -> frozenset[str]:
    return PROFILES | {PAIRED_PROFILE}


def validate_release_manifest(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("profile") != PAIRED_PROFILE:
        return validate_manifest(value)
    _keys(value, {"schema_version", "profile", "action_manifest", "planner", "task", "catalog_sha256",
                  "planning", "placement"}, "paired manifest")
    _integer(value["schema_version"], "schema_version", 2, 2)
    action = validate_manifest(value["action_manifest"])
    if action["profile"] != VISUAL_PROFILE:
        raise ValueError("paired release requires the qualified visual action profile")
    _keys(value["planner"], {"runtime", "artifact_sha256", "protocol_sha256"}, "planner")
    if value["planner"]["runtime"] not in {PLANNER_RUNTIME, CONTROLLED_PLANNER_RUNTIME}:
        raise ValueError("unsupported planner runtime")
    _digest(value["planner"]["artifact_sha256"], "planner artifact")
    if value["planner"]["protocol_sha256"] != PLANNER_PROTOCOL_SHA256:
        raise ValueError("planner prompt/parser protocol mismatch")
    if value["task"] != {"instruction": FIXED_TASK, "skill_id": SKILL_ID}:
        raise ValueError("this profile runs only the fixed qualified task")
    if value["catalog_sha256"] != CATALOG_SHA256:
        raise ValueError("skill catalog mismatch")
    _keys(value["planning"], {"timeout_ms"}, "planning")
    _integer(value["planning"]["timeout_ms"], "planning timeout_ms", 1, 30000)
    # Explicit reference placement, without claiming hosted cloud or Jetson motor-policy qualification.
    planner_placements = (("development-local-controlled",)
                          if value["planner"]["runtime"] == CONTROLLED_PLANNER_RUNTIME
                          else ("development-jetson-lan", "development-local", "development-remote-cpu"))
    _keys(value["placement"], {"policy", "planner"}, "placement")
    if (value["placement"]["policy"] != "development-local-cpu"
            or value["placement"]["planner"] not in planner_placements):
        raise ValueError("unsupported qualification placement")
    return value


def action_manifest(value: dict) -> dict:
    validate_release_manifest(value)
    return value["action_manifest"] if value["profile"] == PAIRED_PROFILE else value


def evaluation_contract(value: dict) -> dict:
    validate_release_manifest(value)
    if value["profile"] != PAIRED_PROFILE:
        return {key: item for key, item in value.items() if key != "policy"}
    # Protocol version pins prompt/parser/catalog semantics. Only model/runtime artifacts
    # are swappable candidates; task, simulator, action timing and placement remain fixed.
    return {**{key: item for key, item in value.items() if key not in {"planner", "action_manifest"}},
            "planner": {key: item for key, item in value["planner"].items() if key != "artifact_sha256"},
            "action_manifest": evaluation_contract(value["action_manifest"])}


def validate_plan_request(value: dict) -> dict:
    _keys(value, PLAN_ECHO_FIELDS | {"budget_ms"}, "planner request")
    validate_identity(value["identity"])
    for field in ("request_id", "observation_id"):
        _text(value[field], field)
    _digest(value["observation_digest"], "observation_digest")
    _integer(value["deadline_monotonic_ns"], "deadline_monotonic_ns", 1, 2**63 - 1)
    _number(value["budget_ms"], "budget_ms", 0.001, 30000)
    return value


def validate_planner_identity(value: dict) -> dict:
    _keys(value, PLANNER_IDENTITY_FIELDS, "planner identity")
    _digest(value["planner_artifact_sha256"], "planner_artifact_sha256")
    _text(value["planner_incarnation"], "planner_incarnation")
    _integer(value["runtime_generation"], "runtime_generation", 1, 2**53 - 1)
    return value


def validate_decision(value: dict) -> dict:
    if value == {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}}:
        return value
    if value == {"kind": "decline", "reason": "unsupported_task"}:
        return value
    raise ValueError("planner decision is outside the qualified catalog")


def validate_plan_result(value: dict) -> dict:
    _keys(value, PLAN_ECHO_FIELDS | PLANNER_IDENTITY_FIELDS | {"decision", "planner_duration_ms"}, "planner result")
    validate_plan_request({**{key: value[key] for key in PLAN_ECHO_FIELDS}, "budget_ms": 1})
    validate_planner_identity({key: value[key] for key in PLANNER_IDENTITY_FIELDS})
    validate_decision(value["decision"])
    _number(value["planner_duration_ms"], "planner_duration_ms", 0, 86400000)
    return value


def _secret(value: str) -> bytes:
    if not isinstance(value, str) or len(value.encode()) < 32:
        raise ValueError("planner execution secret must contain at least 32 bytes")
    return value.encode()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def sign_planner_grant(identity: dict, secret: str, expires_at: float) -> str:
    validate_identity(identity)
    _number(expires_at, "expires_at", 1, 2**53 - 1)
    if not time.time() < expires_at <= time.time() + 3601:
        raise ValueError("planner grant expiry must be within one hour")
    payload = _b64(canonical_json({**identity, "purpose": "planner", "expires_at": expires_at}))
    return payload + "." + _b64(hmac.new(_secret(secret), payload.encode(), hashlib.sha256).digest())


def verify_planner_grant(token: str, secret: str, now: float | None = None) -> dict:
    try:
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError("invalid planner grant")
        payload, signature = token.split(".")
        expected = _b64(hmac.new(_secret(secret), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        value = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True))
        _keys(value, IDENTITY_FIELDS | {"expires_at", "purpose"}, "planner grant")
        if value["purpose"] != "planner":
            raise ValueError("wrong grant purpose")
        validate_identity({key: value[key] for key in IDENTITY_FIELDS})
        _number(value["expires_at"], "expires_at", 1, 2**53 - 1)
        current = time.time() if now is None else now
        if not current < value["expires_at"] <= current + 3601:
            raise ValueError("grant expired or outside allowed lifetime")
        return value
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise ValueError("invalid or expired planner grant") from error
