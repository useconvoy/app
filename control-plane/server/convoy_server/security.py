from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)

ROLE_ORDER = {"viewer": 1, "operator": 2, "admin": 3}
ROLES = tuple(ROLE_ORDER)


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


INVALID_HASH_PREFIX = "!invalidated!"


def verify_password(pw: str, hashed: str) -> bool:
    if not hashed or hashed.startswith(INVALID_HASH_PREFIX):
        return False
    try:
        return _ph.verify(hashed, pw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def make_session_token() -> tuple[str, str]:
    tok = "cvs_" + new_secret(32)
    return tok, hash_token(tok)


def make_api_token() -> tuple[str, str, str]:
    """Returns (token, prefix, hash). Prefix is shown in the UI to identify the token."""
    tok = "cva_" + new_secret(32)
    return tok, tok[:12], hash_token(tok)


def make_enrollment_token() -> tuple[str, str]:
    tok = "cve_" + new_secret(24)
    return tok, hash_token(tok)


def device_credential(device_id: str, secret: str) -> str:
    """Bearer form presented by the agent: 'cvd_<device_id>_<secret>'. The agent generates `secret`
    locally; the server stores only sha256 of this bearer string (the enrollment hash commitment)."""
    return f"cvd_{device_id}_{secret}"


def parse_device_credential(cred: str) -> str | None:
    if not cred.startswith("cvd_"):
        return None
    parts = cred.split("_", 3)
    # cvd_dev_xxxxxxxxxxxx_<secret>
    if len(parts) < 4 or parts[1] != "dev":
        return None
    return f"{parts[1]}_{parts[2]}"


def device_secret_from_credential(cred: str) -> str | None:
    parts = cred.split("_", 3)
    return parts[3] if len(parts) == 4 else None


def role_at_least(role: str, needed: str) -> bool:
    return ROLE_ORDER.get(role, 0) >= ROLE_ORDER.get(needed, 99)
