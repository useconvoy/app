"""Deterministic, linear-time scorers shared by agent and server (§8.10).

Scorers: exact, label, any_of, json_field. No regex, no substring 'contains', no code execution.
Per-case evidence record: status, normalized output hash, extracted field, timings. The server can
recompute equality from the hash (exact/label/any_of) or from the extracted field (json_field)."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from typing import Any

SCORER_VERSION = "1"
MATCH_KINDS = ("exact", "label", "any_of", "json_field")
EVIDENCE_METHOD = {
    "exact": "normalized_hash",
    "label": "normalized_hash",
    "any_of": "normalized_hash",
    "json_field": "extracted_field",
}
MAX_EXPECTED_LEN = 1024


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = " ".join(s.strip().split())
    return s


def normalize_label(s: str) -> str:
    return normalize(s).lower().strip(" .!?:;\"'")


def nhash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def validate_case(case: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    cid = case.get("id")
    if not isinstance(cid, str) or not cid or len(cid) > 64:
        errs.append("id must be a non-empty string (<=64)")
    if not isinstance(case.get("prompt"), str) or not case["prompt"]:
        errs.append(f"{cid}: prompt must be a non-empty string")
    kind = case.get("match", "exact")
    if kind not in MATCH_KINDS:
        errs.append(f"{cid}: match must be one of {MATCH_KINDS}")
    exp = case.get("expected")
    if kind == "any_of":
        if (
            not isinstance(exp, list)
            or not exp
            or not all(isinstance(x, str) and len(x) <= MAX_EXPECTED_LEN for x in exp)
        ):
            errs.append(f"{cid}: any_of expects a non-empty list of strings")
    elif kind == "json_field":
        if not isinstance(case.get("field"), str) or not case["field"]:
            errs.append(f"{cid}: json_field needs a field name")
        if not isinstance(exp, (str, int, float, bool)):
            errs.append(f"{cid}: json_field expected must be a scalar")
    else:
        if not isinstance(exp, str) or len(exp) > MAX_EXPECTED_LEN:
            errs.append(f"{cid}: expected must be a string (<= {MAX_EXPECTED_LEN})")
    mt = case.get("max_tokens")
    if mt is not None and (not isinstance(mt, int) or mt < 1 or mt > 128):
        errs.append(f"{cid}: max_tokens must be 1..128")
    return errs


def expected_hashes(case: dict[str, Any]) -> list[str]:
    kind = case.get("match", "exact")
    if kind == "exact":
        return [nhash(normalize(str(case["expected"])))]
    if kind == "label":
        return [nhash(normalize_label(str(case["expected"])))]
    if kind == "any_of":
        return [nhash(normalize_label(str(x))) for x in case["expected"]]
    return []


def extract_json_field(output: str, field: str) -> tuple[bool, Any]:
    """Bounded: parse the first {...} block up to 64 KiB; returns (parsed_ok, value)."""
    s = output[: 64 * 1024]
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return False, None
    try:
        obj = json.loads(s[start : end + 1])
    except Exception:
        return False, None
    if not isinstance(obj, dict):
        return False, None
    return True, obj.get(field)


def score_case(case: dict[str, Any], output: str | None, status: str = "ok") -> dict[str, Any]:
    """Return the per-case evidence record. status in ok|timeout|error|cancelled."""
    cid = case.get("id")
    kind = case.get("match", "exact")
    rec: dict[str, Any] = {
        "id": cid,
        "status": status,
        "match": kind,
        "passed": False,
        "reason": None,
        "output_hash": None,
        "output_len": None,
        "extracted": None,
    }
    if status != "ok":
        rec["reason"] = status
        return rec
    if output is None:
        rec["reason"] = "no_output"
        return rec
    rec["output_len"] = len(output)
    if kind == "exact":
        h = nhash(normalize(output))
        rec["output_hash"] = h
        rec["passed"] = h in expected_hashes(case)
    elif kind in ("label", "any_of"):
        h = nhash(normalize_label(output))
        rec["output_hash"] = h
        rec["passed"] = h in expected_hashes(case)
    elif kind == "json_field":
        ok, val = extract_json_field(output, case.get("field", ""))
        rec["extracted"] = {
            "parsed": ok,
            "value": val if isinstance(val, (str, int, float, bool)) or val is None else str(val)[:256],
        }
        rec["passed"] = ok and _scalar_eq(val, case.get("expected"))
    else:
        rec["reason"] = f"unknown_match:{kind}"
        return rec
    rec["reason"] = "match" if rec["passed"] else "mismatch"
    return rec


def rescore_from_record(case: dict[str, Any], rec: dict[str, Any]) -> bool | None:
    """Server-side recomputation from the structured record. None when not recomputable."""
    if rec.get("status") != "ok":
        return False
    kind = case.get("match", "exact")
    if kind in ("exact", "label", "any_of"):
        h = rec.get("output_hash")
        return bool(h) and h in expected_hashes(case)
    if kind == "json_field":
        ex = rec.get("extracted") or {}
        return bool(ex.get("parsed")) and _scalar_eq(ex.get("value"), case.get("expected"))
    return None


def _scalar_eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or str(a).lower() == str(b).lower()
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, str) and isinstance(b, str):
        return normalize_label(a) == normalize_label(b)
    return str(a) == str(b)


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None when no data (never 0 for missing)."""
    if not values:
        return None
    xs = sorted(values)
    k = max(0, min(len(xs) - 1, int(math.ceil(p / 100.0 * len(xs)) - 1)))
    return float(xs[k])
