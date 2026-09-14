from __future__ import annotations

import re
from typing import Any

REDACTED = "[redacted]"
_KEY_RE = re.compile(
    r"(?<![a-z0-9])(token|secret|password|passwd|authorization|credential|api[_-]?key|cookie|private[_-]?key)(?![a-z0-9])",
    re.I,
)  # whole-word-ish: token_hash / enrollment_token are secrets; tokens_in / max_tokens are metrics
# Bearer tokens, convoy tokens, HF tokens, AWS-ish keys, long hex/base64 blobs preceded by a key hint.
_VALUE_PATTERNS = [
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]{8,}"),
    re.compile(r"\bcv[dsa]_[a-z0-9]+_[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bcv[dsae]_[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*['\"]?[^\s'\",;]{6,}"),
]


def redact_text(s: str) -> str:
    out = s
    for pat in _VALUE_PATTERNS:
        out = pat.sub(lambda m: _keep_key(m.group(0)), out)
    return out


def _keep_key(match: str) -> str:
    if "=" in match or ":" in match:
        key = re.split(r"[=:]", match, maxsplit=1)[0]
        return f"{key}={REDACTED}"
    return REDACTED


def redact(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact secrets in JSON-like data. Safe on any type."""
    if _depth > 20:
        return REDACTED
    if isinstance(obj, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _KEY_RE.search(k) else redact(v, _depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(v, _depth + 1) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
