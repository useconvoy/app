"""Optional Ed25519 execution authority; no key discovery or secret fallback.

Files are trusted local configuration, replaced atomically for key rotation. Each
operation rereads them; removing a key immediately revokes subsequent checks.
Issuer, audience and purpose are pinned for the lifetime of each object. Changing
those trust anchors requires a new object, rather than a key-only reload.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import stat
import time
from pathlib import Path

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .execution import validate_identity

MAX_FILE_BYTES = 64 * 1024
MAX_TOKEN_BYTES = 8192
_PURPOSES = frozenset({"action", "planner"})
_CLAIMS = {"v", "iss", "aud", "purpose", "identity", "iat", "nbf", "exp"}
_HEADER = {"alg", "typ", "kid"}
_TYP = "convoy-execution+jwt"


def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid grant document fields")


def _purpose(value):
    if not isinstance(value, str) or value not in _PURPOSES:
        raise ValueError("invalid grant purpose")
    return value


def _text(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 256
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("invalid grant trust anchor")
    return value


def _kid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("invalid grant key identifier")
    return value


def _finite(value):
    try:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid grant timestamp")
    except OverflowError:
        raise ValueError("invalid grant timestamp") from None
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate grant JSON field")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("nonfinite grant JSON number")


def _json(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid grant JSON") from None


def _path(value):
    if not isinstance(value, (str, Path)):
        raise ValueError("invalid grant configuration path")
    # A later chdir must not redirect a previously configured trust store.
    return Path(os.path.abspath(value))


def _read(path, *, private):
    try:
        # NONBLOCK avoids hanging if a regular file is replaced by a FIFO.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            forbidden = 0o077 if private else 0o022
            if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                    or info.st_mode & forbidden or not 0 < info.st_size <= MAX_FILE_BYTES):
                raise ValueError("unsafe grant configuration file")
            raw = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (len(raw) != info.st_size or len(raw) > MAX_FILE_BYTES
                    or (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    != (info.st_size, info.st_mtime_ns, info.st_ctime_ns)):
                raise ValueError("grant configuration changed while reading")
        return _json(raw)
    except (OSError, ValueError, TypeError, OverflowError):
        raise ValueError("invalid or unavailable grant configuration") from None


def _pem(value, *, private):
    try:
        label = "PRIVATE KEY" if private else "PUBLIC KEY"
        if (not isinstance(value, str) or len(value) > 4096
                or not re.fullmatch(r"-----BEGIN " + label + r"-----\r?\n[A-Za-z0-9+/=\r\n]+"
                                    r"-----END " + label + r"-----\s*", value)):
            raise ValueError("invalid PEM")
        if private:
            key = serialization.load_pem_private_key(value.encode("ascii"), password=None)
            expected = Ed25519PrivateKey
        else:
            key = serialization.load_pem_public_key(value.encode("ascii"))
            expected = Ed25519PublicKey
        if not isinstance(key, expected):
            raise ValueError("wrong key type")
        return key
    except (ValueError, TypeError, UnicodeError, UnsupportedAlgorithm):
        raise ValueError("invalid Ed25519 grant key") from None


def _public_bytes(key):
    if isinstance(key, Ed25519PrivateKey):
        key = key.public_key()
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _signing_document(path):
    value = _read(path, private=True)
    _exact(value, {"schema_version", "issuer", "active", "keys"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("unsupported grant configuration version")
    issuer = _text(value["issuer"])
    _exact(value["active"], _PURPOSES)
    active = {purpose: _kid(value["active"][purpose]) for purpose in _PURPOSES}
    if not isinstance(value["keys"], list) or not 1 <= len(value["keys"]) <= 8:
        raise ValueError("invalid signing key count")
    keys, audiences, key_purposes = {}, {}, {}
    for entry in value["keys"]:
        _exact(entry, {"kid", "purpose", "audience", "private_key_pem"})
        kid, purpose, audience = _kid(entry["kid"]), _purpose(entry["purpose"]), _text(entry["audience"])
        key = _pem(entry["private_key_pem"], private=True)
        material = _public_bytes(key)
        if (kid in keys or audiences.get(purpose, audience) != audience
                or key_purposes.get(material, purpose) != purpose):
            raise ValueError("inconsistent grant signing keys")
        keys[kid] = (purpose, audience, key)
        audiences[purpose] = audience
        key_purposes[material] = purpose
    if (set(audiences) != _PURPOSES or len(set(audiences.values())) != 2
            or any(kid not in keys or keys[kid][0] != purpose for purpose, kid in active.items())):
        raise ValueError("grant purposes require separate active keys and audiences")
    return issuer, audiences, active, keys


def _verification_document(path, purpose):
    value = _read(path, private=False)
    _exact(value, {"schema_version", "issuer", "audience", "purpose", "keys"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("unsupported grant configuration version")
    issuer, audience = _text(value["issuer"]), _text(value["audience"])
    if _purpose(value["purpose"]) != purpose:
        raise ValueError("wrong verification purpose")
    if not isinstance(value["keys"], list) or len(value["keys"]) > 8:
        raise ValueError("invalid verification key count")
    keys = {}
    for entry in value["keys"]:
        _exact(entry, {"kid", "public_key_pem"})
        kid = _kid(entry["kid"])
        if kid in keys:
            raise ValueError("duplicate verification key identifier")
        keys[kid] = _pem(entry["public_key_pem"], private=False)
    return issuer, audience, keys


class SigningKeys:
    """Private signing authority, used only by the mission-grant issuer."""

    def __init__(self, path: str | Path):
        self._path = _path(path)
        self._issuer, self._audiences, _, _ = _signing_document(self._path)

    def _load(self):
        issuer, audiences, active, keys = _signing_document(self._path)
        if issuer != self._issuer or audiences != self._audiences:
            raise ValueError("signing trust anchors changed; restart required")
        return issuer, audiences, active, keys

    def sign(self, identity: dict, purpose: str, expires_at: float) -> str:
        try:
            purpose = _purpose(purpose)
            validate_identity(identity)
            expires_at, now = _finite(expires_at), _finite(time.time())
            if not now < expires_at <= now + 3601:
                raise ValueError("invalid grant expiry")
            issuer, audiences, active, keys = self._load()
            token = jwt.encode(
                {"v": 1, "iss": issuer, "aud": audiences[purpose], "purpose": purpose,
                 "identity": dict(identity), "iat": now, "nbf": now, "exp": expires_at},
                keys[active[purpose]][2], algorithm="EdDSA",
                headers={"typ": _TYP, "kid": active[purpose]},
            )
            if len(token) > MAX_TOKEN_BYTES:
                raise ValueError("oversized grant")
            return token
        except (ValueError, TypeError, KeyError, jwt.PyJWTError):
            raise ValueError("cannot sign execution grant") from None

    def verification_document(self, purpose: str) -> dict:
        purpose = _purpose(purpose)
        issuer, audiences, _, keys = self._load()
        return {"schema_version": 1, "issuer": issuer, "audience": audiences[purpose], "purpose": purpose,
                "keys": [{"kid": kid, "public_key_pem": key.public_key().public_bytes(
                    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")}
                    for kid, (key_purpose, _, key) in keys.items() if key_purpose == purpose]}


class GrantVerifier:
    """Public-only trust store, rechecked at admission and after inference."""

    def __init__(self, path: str | Path, *, purpose: str):
        self._path, self._purpose = _path(path), _purpose(purpose)
        self._issuer, self._audience, _ = _verification_document(self._path, self._purpose)

    @property
    def purpose(self) -> str:
        return self._purpose

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def audience(self) -> str:
        return self._audience

    def verify(self, token: str, now: float | None = None) -> dict:
        try:
            issuer, audience, keys = _verification_document(self._path, self._purpose)
            if issuer != self._issuer or audience != self._audience:
                raise ValueError("verification trust anchors changed")
            current = _finite(time.time() if now is None else now)
            if not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN_BYTES:
                raise ValueError("invalid token size")
            parts = token.encode("ascii").split(b".")
            if len(parts) != 3 or any(not re.fullmatch(rb"[A-Za-z0-9_-]+", part) for part in parts):
                raise ValueError("invalid token encoding")
            header, payload = [_json(base64.b64decode(part + b"=" * (-len(part) % 4),
                                                     altchars=b"-_", validate=True)) for part in parts[:2]]
            _exact(header, _HEADER)
            _exact(payload, _CLAIMS)
            if header["alg"] != "EdDSA" or header["typ"] != _TYP or _kid(header["kid"]) not in keys:
                raise ValueError("unsupported token header")
            # Library verifies the signature and exact issuer/audience. Time checks
            # below use the caller's clock, with no leeway or renewed lifetime.
            claims = jwt.decode(token, keys[header["kid"]], algorithms=["EdDSA"], issuer=issuer, audience=audience,
                                options={"require": sorted(_CLAIMS), "strict_aud": True,
                                         "verify_exp": False, "verify_iat": False, "verify_nbf": False})
            if type(claims["v"]) is not int or claims["v"] != 1 or claims["purpose"] != self._purpose:
                raise ValueError("wrong grant version or purpose")
            identity = validate_identity(claims["identity"])
            issued, not_before, expires = (_finite(claims[field]) for field in ("iat", "nbf", "exp"))
            if not (not_before == issued <= current < expires <= issued + 3601):
                raise ValueError("expired or future grant")
            return {**identity, "expires_at": expires, **({"purpose": "planner"} if self._purpose == "planner" else {})}
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, jwt.PyJWTError):
            raise ValueError("invalid or expired execution grant") from None
