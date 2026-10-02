"""Versioned action profiles. No controller or transport dependencies.

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
import struct
import time
import zlib
from typing import Any

PROFILE = "metaworld-sawyer-pick-place-v1"
VISUAL_PROFILE = "metaworld-smolvla-pick-place-rgb-v1"
PROFILES = frozenset({PROFILE, VISUAL_PROFILE})
VISUAL_INSTRUCTION = "Pick and place a puck to a goal"
MAX_PNG_BYTES = 1024 * 1024
MAX_VISUAL_REQUEST_BYTES = ((MAX_PNG_BYTES + 2) // 3) * 4 + 16384
# This versioned profile fixes these semantics; changing them requires a new profile.
VISUAL_SPEC = {
    "image": {"encoding": "png-rgb8", "width": 480, "height": 480,
              "camera": "lerobot-0.6.1-metaworld-corner2",
              "camera_position": [0.75, 0.075, 0.7], "image_transform": "flip-both-axes",
              "max_encoded_bytes": MAX_PNG_BYTES},
    "state": "upstream-agent-pos-first-four-float32",
    "instruction": VISUAL_INSTRUCTION,
    "action": "normalized-cartesian-delta-and-gripper-four-float32",
    "actions_per_replan": 1,
    "stop_condition": "first-upstream-is-success-or-configured-horizon-at-most-500",
}
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
    if not isinstance(value["profile"], str) or value["profile"] not in PROFILES:
        raise ValueError("unsupported action profile")
    policy = value["policy"]
    _keys(policy, {"runtime", "artifact_sha256"}, "policy")
    _text(policy["runtime"], "policy runtime")
    _digest(policy["artifact_sha256"], "artifact_sha256")
    expected_metaworld = "3.0.0" if value["profile"] == VISUAL_PROFILE else "3.1.1"
    if value["environment"] != {"name": "pick-place-v3", "metaworld": expected_metaworld, "mujoco": "3.3.0"}:
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


def visual_png_bytes(encoded: Any) -> bytes:
    """Bounded, dependency-free PNG validation before any image-library decode.

    The wire format intentionally accepts only noninterlaced RGB8 IHDR/IDAT/IEND.
    It bounds both compressed bytes and inflated scanlines and verifies CRCs.
    """
    if not isinstance(encoded, str) or len(encoded) > ((MAX_PNG_BYTES + 2) // 3) * 4:
        raise ValueError("visual image exceeds encoded size limit")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError("invalid base64 PNG") from error
    if len(data) > MAX_PNG_BYTES or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("invalid or oversized PNG")
    offset, chunks, compressed = 8, [], bytearray()
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("truncated PNG")
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data) or kind not in {b"IHDR", b"IDAT", b"IEND"}:
            raise ValueError("unsupported or truncated PNG chunk")
        content = data[offset + 8:end - 4]
        if zlib.crc32(kind + content) != struct.unpack(">I", data[end - 4:end])[0]:
            raise ValueError("invalid PNG CRC")
        if kind == b"IHDR" and (chunks or content != struct.pack(">IIBBBBB", 480, 480, 8, 2, 0, 0, 0)):
            raise ValueError("image must be 480x480 noninterlaced RGB8")
        if kind == b"IDAT":
            compressed.extend(content)
        if kind == b"IEND" and (size != 0 or end != len(data)):
            raise ValueError("invalid PNG end")
        chunks.append(kind)
        offset = end
    if len(chunks) < 3 or chunks[0] != b"IHDR" or chunks[-1] != b"IEND" or any(
        kind != b"IDAT" for kind in chunks[1:-1]
    ):
        raise ValueError("invalid PNG structure")
    scanline_bytes = 480 * (480 * 3 + 1)
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, scanline_bytes + 1)
    except zlib.error as error:
        raise ValueError("invalid PNG compression") from error
    if (len(pixels) != scanline_bytes or not decoder.eof or decoder.unused_data or
            decoder.unconsumed_tail or any(pixels[i] > 4 for i in range(0, scanline_bytes, 1441))):
        raise ValueError("invalid or oversized PNG scanlines")
    return data


def validate_observation(value: Any, profile: str = PROFILE, manifest: dict | None = None) -> Any:
    from .registered import REGISTERED_PROFILE
    from .registered import validate_observation as registered_observation

    if profile == REGISTERED_PROFILE:
        if manifest is None or manifest.get("profile") != profile:
            raise ValueError("registered observation requires its release interface")
        return registered_observation(value, manifest)
    if profile == PROFILE:
        _vector(value, 39, "observation", 1e6)
    elif profile == VISUAL_PROFILE:
        _keys(value, {"image_png_base64", "state", "instruction"}, "visual observation")
        _vector(value["state"], 4, "state", 1e6)
        if value["instruction"] != VISUAL_INSTRUCTION:
            raise ValueError("instruction does not match the visual task profile")
        visual_png_bytes(value["image_png_base64"])
    else:
        raise ValueError("unsupported observation profile")
    return value


def validate_request(value: dict, profile: str = PROFILE, manifest: dict | None = None) -> dict:
    _keys(value, {"identity", "request_id", "observation_id", "sequence", "observation",
                  "deadline_monotonic_ns", "budget_ms"}, "request")
    validate_identity(value["identity"])
    _text(value["request_id"], "request_id")
    _text(value["observation_id"], "observation_id")
    _integer(value["sequence"], "sequence", 0, 2**53 - 1)
    _integer(value["deadline_monotonic_ns"], "deadline_monotonic_ns", 1, 2**63 - 1)
    _number(value["budget_ms"], "budget_ms", 0.001, 30000)
    validate_observation(value["observation"], profile, manifest)
    return value


def validate_result(value: dict, manifest: dict | None = None) -> dict:
    _keys(value, {"identity", "request_id", "observation_id", "sequence", "deadline_monotonic_ns",
                  "action", "policy_duration_ms"}, "result")
    validate_identity(value["identity"])
    _text(value["request_id"], "request_id")
    _text(value["observation_id"], "observation_id")
    _integer(value["sequence"], "sequence", 0, 2**53 - 1)
    _integer(value["deadline_monotonic_ns"], "deadline_monotonic_ns", 1, 2**63 - 1)
    _number(value["policy_duration_ms"], "policy_duration_ms", 0, 86400000)
    from .registered import REGISTERED_PROFILE, validate_action

    if manifest is not None and manifest.get("profile") == REGISTERED_PROFILE:
        validate_action(value["action"], manifest)
    else:
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
