"""Ingress validation shared by device-facing routes (R1, R2)."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from .ids import parse_iso

MAX_DEPTH = 12
MAX_KEYS = 2000
MAX_STR = 16 * 1024


def reject_invalid_json_values(obj: Any, _depth: int = 0, _count: list[int] | None = None) -> None:
    """422 on NaN/Infinity floats, absurd nesting, or oversized strings anywhere in the payload."""
    _count = _count if _count is not None else [0]
    if _depth > MAX_DEPTH:
        raise HTTPException(422, "payload nesting too deep")
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise HTTPException(
                422, "non-finite numbers are not accepted; report null for unavailable values"
            )
        return
    if isinstance(obj, str):
        if len(obj) > MAX_STR:
            raise HTTPException(422, "string value too long")
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _count[0] += 1
            if _count[0] > MAX_KEYS:
                raise HTTPException(422, "payload has too many values")
            if not isinstance(k, str) or len(k) > 256:
                raise HTTPException(422, "invalid key")
            reject_invalid_json_values(v, _depth + 1, _count)
    elif isinstance(obj, list):
        if len(obj) > MAX_KEYS:
            raise HTTPException(422, "list too long")
        for v in obj:
            reject_invalid_json_values(v, _depth + 1, _count)


def finite(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_ts(s: Any, field: str) -> datetime | None:
    if s is None:
        return None
    if not isinstance(s, str):
        raise HTTPException(422, f"{field} must be an ISO-8601 string")
    try:
        return parse_iso(s)
    except ValueError as e:
        raise HTTPException(422, f"{field} is not a valid timestamp") from e


def as_int(v: Any, field: str, *, ge: int = 0) -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int):
        raise HTTPException(422, f"{field} must be an integer")
    if v < ge:
        raise HTTPException(422, f"{field} must be >= {ge}")
    return v


TELEMETRY_NUMERIC = (
    "mem_total_mb",
    "mem_available_mb",
    "swap_used_mb",
    "cpu_pct",
    "gpu_pct",
    "power_w",
    "temp_max_c",
    "disk_free_mb",
)
TELEMETRY_STRINGS = ("runtime_state", "clock_confidence")
HARDWARE_STRINGS = (
    "arch",
    "os",
    "kernel",
    "python",
    "l4t_release",
    "jetson_model",
    "cuda_version",
    "nvcc",
    "nvidia_smi",
    "tegrastats",
    # measured GPU identity (agent hardware.query_gpu: `nvidia-smi --query-gpu=name,compute_cap`); the
    # platform tuple gate needs compute_capability from the REAL protocol, never from a profile default
    "gpu_name",
    "compute_capability",
)
HARDWARE_NUMERIC = ("cpu_count", "mem_total_mb", "swap_total_mb", "l4t_major", "seed")
_COMPUTE_CAPABILITY = re.compile(r"^\d{1,2}\.\d{1,2}$")


def _finite_or_none(v: Any, field: str) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise HTTPException(422, f"{field} must be a number or null")
    f = float(v)
    if not math.isfinite(f):
        raise HTTPException(422, f"{field} must be finite or null")
    return f


def typed_telemetry(t: Any) -> dict[str, Any] | None:
    """R1 follow-up: typed, finite telemetry. Null is distinct from 0; strings never become numbers."""
    if t is None:
        return None
    if not isinstance(t, dict):
        raise HTTPException(422, "telemetry must be an object")
    out: dict[str, Any] = {}
    for k in TELEMETRY_NUMERIC:
        if k in t:
            out[k] = _finite_or_none(t[k], f"telemetry.{k}")
    for k in TELEMETRY_STRINGS:
        if k in t:
            v = t[k]
            if v is not None and not isinstance(v, str):
                raise HTTPException(422, f"telemetry.{k} must be a string or null")
            out[k] = v[:32] if isinstance(v, str) else None
    temps = t.get("temps")
    if temps is not None:
        if not isinstance(temps, dict) or len(temps) > 64:
            raise HTTPException(422, "telemetry.temps must be an object of finite numbers")
        out["temps"] = {str(k)[:32]: _finite_or_none(v, f"telemetry.temps.{k}") for k, v in temps.items()}
    if "simulated" in t:
        out["simulated"] = bool(t["simulated"])
    return out


def typed_hardware(h: Any) -> dict[str, Any] | None:
    if h is None:
        return None
    if not isinstance(h, dict):
        raise HTTPException(422, "hardware must be an object")
    out: dict[str, Any] = {}
    for k in HARDWARE_STRINGS:
        if k in h:
            v = h[k]
            if v is not None and not isinstance(v, str):
                raise HTTPException(422, f"hardware.{k} must be a string or null")
            out[k] = v[:128] if isinstance(v, str) else None
    if out.get("compute_capability") is not None and not _COMPUTE_CAPABILITY.match(out["compute_capability"]):
        # not a measured "major.minor": kept as unmeasured so the tuple gate refuses it, never coerced
        out["compute_capability"] = None
    for k in HARDWARE_NUMERIC:
        if k in h:
            out[k] = _finite_or_none(h[k], f"hardware.{k}")
    if "simulated" in h:
        out["simulated"] = bool(h["simulated"])
    return out


def typed_runtime_evidence(r: Any) -> dict[str, Any] | None:
    if r is None:
        return None
    if not isinstance(r, dict):
        raise HTTPException(422, "runtime evidence must be an object")
    out: dict[str, Any] = {}
    for k in ("build_info", "model_path", "backend", "binary_sha256", "chat_template_sha256", "note"):
        if k in r:
            v = r[k]
            if v is not None and not isinstance(v, str):
                raise HTTPException(422, f"runtime.{k} must be a string or null")
            out[k] = v[:512] if isinstance(v, str) else None
    for k in ("n_ctx", "total_slots", "gpu_offloaded_layers", "gpu_total_layers", "cuda_devices"):
        if k in r:
            v = _finite_or_none(r[k], f"runtime.{k}")
            out[k] = int(v) if v is not None else None
    for k in ("intended_backend_ok", "simulated"):
        if k in r:
            v = r[k]
            if v is not None and not isinstance(v, bool):
                raise HTTPException(422, f"runtime.{k} must be a boolean or null")
            out[k] = v
    if isinstance(r.get("argv"), list):
        out["argv"] = [str(x)[:256] for x in r["argv"][:64]]
    return out
