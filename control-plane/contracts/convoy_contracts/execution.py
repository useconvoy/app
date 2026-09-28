"""The first supported action profile. No controller or transport dependencies.

Robot monotonic timestamps are opaque at the worker. The coordinator checks
their original values when a result arrives, immediately before execution.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import time
from typing import Any

PROFILE = "metaworld-sawyer-pick-place-v1"
IDENTITY_FIELDS = {
    "robot_id", "device_id", "mission_id", "boot_id", "incarnation", "release_digest", "authority_epoch",
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _keys(value: Any, expected: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{name} must contain exactly {', '.join(sorted(expected))}")


def _text(value: Any, name: str, maximum: int = 128) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"invalid {name}")


def _integer(value: Any, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")


def _number(value: Any, name: str, minimum: float, maximum: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"invalid finite {name}")


def _digest(value: Any, name: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def validate_manifest(value: dict) -> dict:
    _keys(value, {"schema_version", "profile", "policy", "environment", "execution"}, "manifest")
    _integer(value["schema_version"], "schema_version", 1, 1)
    if value["profile"] != PROFILE:
        raise ValueError("unsupported action profile")
    policy = value["policy"]
    _keys(policy, {"runtime", "artifact_sha256"}, "policy")
    _text(policy["runtime"], "policy runtime")
    _digest(policy["artifact_sha256"], "artifact_sha256")
    if value["environment"] != {"name": "pick-place-v3", "metaworld": "3.1.1", "mujoco": "3.3.0"}:
        raise ValueError("unsupported environment; the complete pinned simulator version is required")
    execution = value["execution"]
    _keys(execution, {"max_steps", "decision_timeout_ms", "mission_timeout_s"}, "execution")
    _integer(execution["max_steps"], "max_steps", 1, 500)
    _integer(execution["decision_timeout_ms"], "decision_timeout_ms", 1, 30000)
    _integer(execution["mission_timeout_s"], "mission_timeout_s", 1, 3600)
    return value


def validate_identity(value: dict) -> dict:
    _keys(value, IDENTITY_FIELDS, "identity")
    for name in IDENTITY_FIELDS - {"authority_epoch", "release_digest"}:
        _text(value[name], name)
    _digest(value["release_digest"], "release_digest")
    _integer(value["authority_epoch"], "authority_epoch", 1, 2**53 - 1)
    return value


def _vector(value: Any, count: int, name: str, bound: float) -> None:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"{name} must contain {count} numbers")
    for item in value:
        _number(item, name, -bound, bound)


def validate_request(value: dict) -> dict:
    _keys(value, {"identity", "request_id", "observation_id", "sequence", "observation",
                  "deadline_monotonic_ns", "budget_ms"}, "request")
    validate_identity(value["identity"])
    _text(value["request_id"], "request_id")
    _text(value["observation_id"], "observation_id")
    _integer(value["sequence"], "sequence", 0, 2**53 - 1)
    _integer(value["deadline_monotonic_ns"], "deadline_monotonic_ns", 1, 2**63 - 1)
    _number(value["budget_ms"], "budget_ms", 0.001, 30000)
    _vector(value["observation"], 39, "observation", 1e6)
    return value


def validate_result(value: dict) -> dict:
    _keys(value, {"identity", "request_id", "observation_id", "sequence", "deadline_monotonic_ns",
                  "action", "policy_duration_ms"}, "result")
    validate_identity(value["identity"])
    _text(value["request_id"], "request_id")
    _text(value["observation_id"], "observation_id")
    _integer(value["sequence"], "sequence", 0, 2**53 - 1)
    _integer(value["deadline_monotonic_ns"], "deadline_monotonic_ns", 1, 2**63 - 1)
    _number(value["policy_duration_ms"], "policy_duration_ms", 0, 86400000)
    _vector(value["action"], 4, "action", 1)
    return value


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _secret(secret: str) -> bytes:
    if not isinstance(secret, str) or len(secret.encode()) < 32:
        raise ValueError("execution signing secret must contain at least 32 bytes")
    return secret.encode()


def sign_grant(identity: dict, secret: str, expires_at: float) -> str:
    validate_identity(identity)
    _number(expires_at, "expires_at", 1, 2**53 - 1)
    now = time.time()
    if not now < expires_at <= now + 3601:
        raise ValueError("grant expiry must be within one hour")
    payload = _b64(canonical_json({**identity, "expires_at": expires_at}))
    signature = hmac.new(_secret(secret), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(signature)}"


def verify_grant(token: str, secret: str, now: float | None = None) -> dict:
    try:
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError("invalid grant")
        payload, signature = token.split(".")
        expected = _b64(hmac.new(_secret(secret), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid grant signature")
        value = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True))
        _keys(value, IDENTITY_FIELDS | {"expires_at"}, "grant")
        validate_identity({key: value[key] for key in IDENTITY_FIELDS})
        _number(value["expires_at"], "expires_at", 1, 2**53 - 1)
        current = time.time() if now is None else now
        if not current < value["expires_at"] <= current + 3601:
            raise ValueError("grant expired or outside allowed lifetime")
        return value
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise ValueError("invalid or expired execution grant") from error
