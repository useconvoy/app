from __future__ import annotations

import secrets
import string
import time
from datetime import datetime, timezone

_ALPHABET = string.ascii_lowercase + string.digits


def new_id(prefix: str, n: int = 12) -> str:
    return f"{prefix}_{''.join(secrets.choice(_ALPHABET) for _ in range(n))}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso_ts(s: str) -> float:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).timestamp()


def monotonic() -> float:
    return time.monotonic()
