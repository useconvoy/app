#!/usr/bin/env python3
"""Convoy gateway benchmark: reproducible, honest latency/throughput probes THROUGH the device's loopback
gateway (never llama-server directly), with the evidence Convoy already collects.

stdlib only, Python 3.10 (JetPack 6). No robot actuation: text chat completions only.

Honesty principles (kept by every code path):
  * unavailable stays unavailable: TTFT is body.convoy.ttft_ms exactly as the gateway measured it or
    "unavailable"; TPOT is derived ONLY as timings.predicted_ms / timings.predicted_n from the runtime's
    own generation timing, else unavailable; never from wall time
  * completion latency: gateway latency_ms (slot ownership to completion) and client wall ms (includes
    loopback HTTP overhead and queueing) are reported separately, never mixed
  * every percentile carries its sample size (fewer than 5 samples is flagged)
  * populations are never merged silently: aggregates are per matrix CELL (case x input band x output
    cap); anything that mixes cells is labelled "pooled across cells (mixture)"
  * synthetic probes are diagnostics, not customer acceptance
  * every attempted request (warmups included) is persisted to <out>/records.jsonl as it completes;
    a crash, a transport failure or Ctrl-C yields a PARTIAL report from what was persisted, never
    silently lost records
  * a client socket timeout is "client_timeout_no_response" (the gateway may still have completed
    or cancelled the request); it is never reported as a gateway cancellation

Modes:
  task   (default)  every case of the fixture, `--repeats` timed runs each (+`--warmup`)
  --smoke           the frozen 8-case deployment smoke (scripts/jetson/bench_smoke_v1.jsonl)
  --matrix          timing matrix for `--probe` case(s): input bands ~64/256/1024 rendered tokens
                    (deterministic filler; ACTUAL prompt tokens are recorded) x output caps 16/64/128
                    x 3 repeats = 27 timed requests per probe across 9 cells
  --render-check    diagnostic: render one conversation through the RUNTIME's /apply-template and
                    /tokenize, compare with the local reference rendering of the Qwen3 non-thinking
                    template (`--template PATH`, sha256 recorded); ok only when everything matches

Fixtures: `--cases v1` (scripts/jetson/bench_cases_v1.jsonl, FROZEN strict fixture, default),
`smoke`, `builtin` (six loose diagnostics doubled with a padded variant: "diagnostic, not the strict
fixture"), or a path to a JSONL file. The fixture's sha256 travels in the report.

Evidence (optional, read-only, via the control-plane API with an operator/viewer token): before and
after the run it snapshots the device's latest telemetry samples and afterwards fetches the spans of
every trace id (GET /api/v1/traces/{id}) with a bounded reconciliation for spool lag; trace results are
counted as requested / nonempty / empty / error and the evidence is marked partial while any trace is
missing. Absent evidence is reported as absent ("not fetched: no --server"), never as zero.

Usage (on the Jetson, or anywhere that can reach the device's loopback gateway):
  bench_gateway.py --gateway http://127.0.0.1:<port> --out ./bench-<date> [--cases v1|smoke|builtin|f.jsonl]
      [--repeats 5] [--warmup 1] [--smoke | --matrix [--probe ID ...]] [--timeout 45]
      [--server https://<host> --ca-file crt --token-file f --device-id dev_...] [--label "..."]
  bench_gateway.py --render-check --runtime http://127.0.0.1:<runtime-port> --api-key-file <0600 file>
      --template docs/templates/qwen3-nonthinking.jinja
  bench_gateway.py --finalize-only --out ./bench-<date>   (rebuild report.json from records.jsonl)
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "convoy-bench-2"
SCRIPT_DIR = Path(__file__).resolve().parent
FIXTURES = {
    "v1": SCRIPT_DIR / "bench_cases_v1.jsonl",
    "smoke": SCRIPT_DIR / "bench_smoke_v1.jsonl",
}
RAW_BODY_BOUND = 64 * 1024  # bytes of the raw response body kept per record (flagged when exceeded)
DEFAULT_CLIENT_TIMEOUT_S = 45.0  # gateway execution deadline is 30 s; leave room for bounded cleanup
GATEWAY_DEADLINE_S = 30.0  # documented gateway request_deadline_s (not configurable from here)
INPUT_BANDS = (64, 256, 1024)  # approximate rendered prompt tokens (target); actuals are recorded
OUTPUT_CAPS = (16, 64, 128)  # effective max_tokens per matrix cell
MATRIX_REPEATS = 3
CHARS_PER_TOKEN = 4.0  # heuristic used ONLY to size the filler; the gateway reports actual tokens
GEN_PROMPT = "<|im_start|>assistant\n<think>\n\n</think>\n\n"

# --------------------------------------------------------------------------- synthetic probes
LONG_PAD = (
    "Robot log excerpt (synthetic filler, ignore for the task): the mobile base traversed corridor B, "
    "lidar reported no obstacles, battery at 78 percent, wheel odometry consistent with the planner. "
)
FILLER = (
    "Context (synthetic filler, ignore for the task): the mobile base traversed corridor B at 0.3 m/s, "
    "lidar reported no obstacles within 3 m, battery at 78 percent, wheel odometry agreed with the "
    "planner, the arm stayed parked, thermal zones within limits, no operator intervention recorded. "
)
BUILTIN_NOTE = "builtin: diagnostic, not the strict fixture (loose contains/one_of/json_keys checks)"
BUILTIN_CASES: list[dict[str, Any]] = [
    {
        "id": "label-1",
        "kind": "label",
        "system": "Answer with exactly one word: SAFE or UNSAFE.",
        "prompt": "A person is standing 20 cm in front of the moving robot.",
        "expect": {"one_of": ["UNSAFE"]},
        "max_tokens": 4,
    },
    {
        "id": "label-2",
        "kind": "label",
        "system": "Answer with exactly one word: SAFE or UNSAFE.",
        "prompt": "The corridor ahead is empty and the robot moves at 0.2 m/s.",
        "expect": {"one_of": ["SAFE"]},
        "max_tokens": 4,
    },
    {
        "id": "json-1",
        "kind": "json",
        "system": "Reply with only a JSON object with keys action and target.",
        "prompt": "Command: pick up the red cup from the table.",
        "expect": {"json_keys": ["action", "target"]},
        "max_tokens": 48,
    },
    {
        "id": "json-2",
        "kind": "json",
        "system": "Reply with only a JSON object with keys action and target.",
        "prompt": "Command: go to the charging dock.",
        "expect": {"json_keys": ["action", "target"]},
        "max_tokens": 48,
    },
    {
        "id": "semantic-1",
        "kind": "semantic",
        "system": "Answer briefly.",
        "prompt": "What is the capital of France? One word.",
        "expect": {"contains": ["Paris"]},
        "max_tokens": 8,
    },
    {
        "id": "semantic-2",
        "kind": "semantic",
        "system": "Answer briefly.",
        "prompt": "How many wheels does a typical bicycle have? Digits only.",
        "expect": {"contains": ["2"]},
        "max_tokens": 8,
    },
]


def build_cases(base: list[dict[str, Any]], long_repeats: int = 40) -> list[dict[str, Any]]:
    """Each diagnostic probe twice: as given (short prompt) and with a padded context (long prompt)."""
    out = []
    for c in base:
        out.append({**c, "length": "short", "fixture": "builtin-diagnostic"})
        out.append(
            {
                **c,
                "id": c["id"] + "-long",
                "length": "long",
                "fixture": "builtin-diagnostic",
                "prompt": (LONG_PAD * long_repeats) + "\n\n" + c["prompt"],
            }
        )
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_fixture(spec: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Cases plus a fixture identity block (name, path, sha256, strict flag, header note)."""
    if spec == "builtin":
        return build_cases(BUILTIN_CASES), {
            "name": "builtin",
            "path": None,
            "sha256": None,
            "strict": False,
            "protocol_version": PROTOCOL_VERSION,
            "note": BUILTIN_NOTE,
        }
    path = FIXTURES.get(spec) or Path(spec)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    header: dict[str, Any] = {}
    if rows and "fixture" in rows[0] and "id" not in rows[0]:
        header = rows.pop(0)
    cases = []
    for r in rows:
        r.setdefault("kind", "custom")
        r["fixture"] = header.get("fixture") or path.name
        cases.append(r)
    validate_cases(cases)
    return cases, {
        "name": header.get("fixture") or path.name,
        "path": str(path),
        "sha256": sha256_file(path),
        "strict": all(c.get("scoring") in STRICT_RULES for c in cases),
        "frozen": bool(header.get("frozen")),
        "protocol_version": header.get("protocol_version") or "unversioned",
        "note": header.get("note") or "custom fixture",
        "case_count": len(cases),
    }


def load_cases(spec: str) -> list[dict[str, Any]]:
    return load_fixture(spec)[0]


STRICT_RULES = ("label", "exact", "json_exact")


def validate_cases(cases: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for c in cases:
        cid = c.get("id")
        if not cid or cid in seen:
            raise ValueError(f"fixture case id missing or duplicated: {cid!r}")
        seen.add(cid)
        if not isinstance(c.get("prompt"), str):
            raise ValueError(f"{cid}: prompt must be a string")
        rule = c.get("scoring")
        if rule is not None:
            if rule not in STRICT_RULES:
                raise ValueError(f"{cid}: scoring must be one of {STRICT_RULES}")
            if rule == "json_exact" and not isinstance(c.get("expected"), dict):
                raise ValueError(f"{cid}: json_exact needs a JSON object as expected")
            if rule in ("label", "exact") and not isinstance(c.get("expected"), str):
                raise ValueError(f"{cid}: {rule} needs a string as expected")


# --------------------------------------------------------------------------- scoring
def _norm_text(s: str) -> str:
    t = " ".join((s or "").split())
    t = t.strip().strip("\"'`")
    while t and t[-1] in ".!?":
        t = t[:-1].rstrip()
    return t.casefold()


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """The WHOLE response must be one JSON object (a single ```json fence around it is tolerated and
    recorded as such); any prose around it fails."""
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            t = "\n".join(lines[1:-1]).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def score_case(content: str, case: dict[str, Any]) -> bool | None:
    """Strict rules (fixture v1): label/exact = normalized exact equality (whitespace collapsed,
    surrounding quotes and trailing .!? stripped, case-folded; "12" never satisfies "2"); json_exact =
    the whole response parses as a JSON object equal to `expected` (null or wrong values fail, prose
    fails). Diagnostic builtin cases keep their loose `expect` block via check_expectation."""
    rule = case.get("scoring")
    if rule is None:
        return check_expectation(content, case.get("expect"))
    expected = case.get("expected")
    if rule == "label" or rule == "exact":
        return _norm_text(content) == _norm_text(str(expected))
    if rule == "json_exact":
        obj = _parse_json_object(content)
        return obj is not None and obj == expected
    return None


def check_expectation(content: str, expect: dict[str, Any] | None) -> bool | None:
    """Loose diagnostic checks for the builtin cases only (NOT the strict fixture): None when the case
    declares no expectation; JSON keys are checked on the first {...} object."""
    if not expect:
        return None
    text = (content or "").strip()
    if "one_of" in expect:
        return text.strip(" .\n").upper() in {x.upper() for x in expect["one_of"]}
    if "contains" in expect:
        return any(x.lower() in text.lower() for x in expect["contains"])
    if "json_keys" in expect:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return False
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return False
        return isinstance(obj, dict) and all(k in obj for k in expect["json_keys"])
    return None


# --------------------------------------------------------------------------- matrix padding
def pad_to_band(case: dict[str, Any], band: int, chars_per_token: float = CHARS_PER_TOKEN) -> dict[str, Any]:
    """Deterministic filler in the user turn so the rendered prompt lands near `band` tokens. The size
    is a chars-per-token heuristic; the gateway's reported prompt_tokens is the actual and is recorded.
    When the unpadded prompt already estimates at or above the band, no filler is added and the case
    says so (`pad_note`)."""
    base_chars = len(case.get("system") or "") + len(case["prompt"]) + len(GEN_PROMPT) + 40
    need_tokens = band - int(base_chars / chars_per_token)
    if need_tokens <= 0:
        return {
            **case,
            "input_band": band,
            "pad_chars": 0,
            "pad_note": "unpadded prompt already at or above the band",
        }
    need_chars = int(need_tokens * chars_per_token)
    reps = need_chars // len(FILLER) + 1
    filler = (FILLER * reps)[:need_chars]
    filler = filler[: filler.rfind(" ")] if " " in filler else filler
    return {
        **case,
        "input_band": band,
        "pad_chars": len(filler),
        "prompt": filler + "\n\n" + case["prompt"],
    }


def build_matrix(
    probes: list[dict[str, Any]],
    bands: tuple[int, ...] = INPUT_BANDS,
    caps: tuple[int, ...] = OUTPUT_CAPS,
    chars_per_token: float = CHARS_PER_TOKEN,
) -> list[dict[str, Any]]:
    """One case per (probe, band, cap) cell; repeats are applied by the runner."""
    out = []
    for p in probes:
        for band in bands:
            padded = pad_to_band(p, band, chars_per_token)
            for cap in caps:
                out.append({**padded, "max_tokens": cap, "output_cap": cap, "probe_id": p["id"]})
    return out


# --------------------------------------------------------------------------- measurement
def _post_json(
    url: str, body: dict[str, Any], headers: dict[str, str] | None = None, timeout: float = 120.0
) -> tuple[int, dict[str, Any] | None, dict[str, str], float]:
    """Diagnostic helper (render check): raises on transport failures; the benchmark path uses
    measure_one, which never raises."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json", **(headers or {})}
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return (
                r.status,
                (json.loads(raw) if raw else None),
                dict(r.headers.items()),
                (time.monotonic() - t0) * 1000,
            )
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            obj = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            obj = {"raw": raw[:200].decode("utf-8", "replace")}
        return e.code, obj, dict(e.headers.items()), (time.monotonic() - t0) * 1000


def _is_timeout(e: BaseException) -> bool:
    if isinstance(e, (socket.timeout, TimeoutError)):
        return True
    if isinstance(e, urllib.error.URLError) and isinstance(e.reason, (socket.timeout, TimeoutError)):
        return True
    return False


def _bounded_raw(raw: bytes) -> tuple[str, bool]:
    return raw[:RAW_BODY_BOUND].decode("utf-8", "replace"), len(raw) > RAW_BODY_BOUND


class InterruptedRequest(KeyboardInterrupt):
    """KeyboardInterrupt raised inside a request, carrying the partial record of that attempt."""

    def __init__(self, record: dict) -> None:
        super().__init__("interrupted during a request")
        self.record = record


def measure_one(
    gateway: str, case: dict[str, Any], max_tokens: int, timeout: float = DEFAULT_CLIENT_TIMEOUT_S
) -> dict[str, Any]:
    """One request through the gateway. NEVER raises: every transport/protocol failure becomes a typed
    record with its wall time. The record keeps the exact messages sent, the effective cap, the
    expected reference, the complete content, the bounded raw body and BOTH trace ids."""
    msgs = []
    if case.get("system"):
        msgs.append({"role": "system", "content": case["system"]})
    msgs.append({"role": "user", "content": case["prompt"]})
    cap = int(case.get("max_tokens") or max_tokens)
    body = {"messages": msgs, "max_tokens": cap, "temperature": 0.0, "seed": 42}
    rec: dict[str, Any] = {
        "case_id": case["id"],
        "kind": case.get("kind"),
        "fixture": case.get("fixture"),
        "input_band": case.get("input_band", "unpadded"),
        "output_cap": cap,
        "pad_chars": case.get("pad_chars", 0),
        "scoring": case.get("scoring") or ("loose:" + ",".join(case.get("expect") or {}) if case.get("expect") else None),
        "expected": case.get("expected", case.get("expect")),
        "request": {"messages": msgs, "max_tokens": cap, "temperature": 0.0, "seed": 42},
        "client_timeout_s": timeout,
    }  # fmt: skip
    if case.get("pad_note"):
        rec["pad_note"] = case["pad_note"]
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{gateway.rstrip('/')}/v1/chat/completions",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    status: int | None = None
    headers: dict[str, str] = {}
    raw = b""
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status = r.status
                headers = dict(r.headers.items())
                raw = r.read()
        except urllib.error.HTTPError as e:
            status = e.code
            headers = dict(e.headers.items())
            raw = e.read()
    except KeyboardInterrupt as ki:
        # the operator interrupted an in-flight request: record the attempt (with the header trace id
        # when the response headers had already arrived) so the partial report keeps it; this says
        # nothing about whether the gateway completed or cancelled the request
        rec["client_wall_ms"] = round((time.monotonic() - t0) * 1000, 2)
        rec["status"] = status
        rec["ok"] = False
        rec["error_type"] = "interrupted_client"
        rec["error_message"] = (
            "interrupted by the operator while waiting for/reading the response; NOT a gateway cancellation"
        )
        rec["trace_id_header"] = headers.get("X-Convoy-Trace-Id")
        rec["trace_id"] = rec["trace_id_header"]
        raise InterruptedRequest(rec) from ki
    except Exception as e:  # noqa: BLE001 typed below; the benchmark loop must never lose prior records
        rec["client_wall_ms"] = round((time.monotonic() - t0) * 1000, 2)
        rec["status"] = None
        rec["ok"] = False
        if _is_timeout(e):
            rec["error_type"] = "client_timeout_no_response"
            rec["error_message"] = (
                f"no response within the client socket timeout ({timeout}s); NOT a gateway cancellation: "
                "the gateway may still have completed or cancelled this request (check its span)"
            )
        elif isinstance(
            e,
            (
                ConnectionError,
                http.client.RemoteDisconnected,
                http.client.IncompleteRead,
                urllib.error.URLError,
                OSError,
            ),
        ):
            rec["error_type"] = "connection_error"
            rec["error_message"] = f"{type(e).__name__}: {str(e)[:200]}"
        else:
            rec["error_type"] = f"client_error_{type(e).__name__}"
            rec["error_message"] = str(e)[:200]
        rec.update(_trace_fields(headers, None))
        return rec
    rec["client_wall_ms"] = round((time.monotonic() - t0) * 1000, 2)
    rec["status"] = status
    rec["response_raw"], rec["response_raw_truncated"] = _bounded_raw(raw)
    rec["response_bytes"] = len(raw)
    try:
        obj = json.loads(raw) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        rec.update(
            {
                "ok": False,
                "error_type": "malformed_json",
                "error_message": f"{type(e).__name__}: {str(e)[:200]}",
            }
        )
        rec.update(_trace_fields(headers, None))
        return rec  # fmt: skip
    body_trace = (obj.get("convoy") or {}).get("trace_id") if isinstance(obj, dict) else None
    rec.update(_trace_fields(headers, body_trace))
    if status != 200 or not isinstance(obj, dict):
        err = (obj.get("error") if isinstance(obj, dict) else None) or {}
        rec.update(
            {
                "ok": False,
                "error_type": err.get("type") or f"http_{status}",
                "error_message": (err.get("message") or "")[:200],
            }
        )
        return rec
    cv = obj.get("convoy") or {}
    timings = cv.get("timings") or {}
    n_in = (obj.get("usage") or {}).get("prompt_tokens")
    n_out = (obj.get("usage") or {}).get("completion_tokens")
    choice = (obj.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content", "")
    pred_ms, pred_n = timings.get("predicted_ms"), timings.get("predicted_n")
    tpot = (float(pred_ms) / float(pred_n)) if (pred_ms is not None and pred_n) else None
    rec.update({
        "ok": True,
        "release_id": cv.get("release_id"),
        "simulated": bool(cv.get("simulated")),
        "prompt_tokens": n_in,
        "completion_tokens": n_out,
        "finish_reason": choice.get("finish_reason"),
        "gateway_latency_ms": cv.get("latency_ms"),
        "queue_ms": cv.get("queue_ms"),
        "ttft_ms": cv.get("ttft_ms"),  # None => unavailable (non-streaming runtime path), never approximated
        "ttft_source": "gateway measured" if cv.get("ttft_ms") is not None else "unavailable",
        "tpot_ms": None if tpot is None else round(tpot, 3),
        "tpot_source": "runtime timings predicted_ms/predicted_n" if tpot is not None else "unavailable (runtime timings missing)",
        "prompt_eval_ms": timings.get("prompt_ms"),
        "predicted_ms": pred_ms,
        "predicted_n": pred_n,
        "tok_s": timings.get("predicted_per_second"),
        "expectation_met": score_case(content, case),
        "content": content,
    })  # fmt: skip
    return rec


def _trace_fields(headers: dict[str, str], body_trace: str | None) -> dict[str, Any]:
    """Header trace id is canonical (it exists on every gateway response, rejections included); the
    body id must agree. A missing header id or a mismatch is an evidence defect, never silently
    tolerated."""
    hdr = None
    for k, v in headers.items():
        if k.lower() == "x-convoy-trace-id":
            hdr = v
            break
    out: dict[str, Any] = {"trace_id_header": hdr, "trace_id_body": body_trace, "trace_id": hdr or body_trace}
    defects = []
    if hdr is None:
        defects.append("missing_header_trace_id")
    if hdr is not None and body_trace is not None and hdr != body_trace:
        defects.append("trace_id_mismatch")
    out["trace_ids_agree"] = (hdr == body_trace) if (hdr is not None and body_trace is not None) else None
    out["evidence_defects"] = defects
    return out


# --------------------------------------------------------------------------- persistence
class Recorder:
    """Appends every attempted request to records.jsonl as it completes (flushed per record) so a crash,
    an exception or Ctrl-C never erases prior records; the in-memory list mirrors the file."""

    def __init__(self, path: Path):
        self.path = path
        self.records: list[dict[str, Any]] = []
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")

    def add(self, rec: dict[str, Any]) -> None:
        self.records.append(rec)
        self._f.write(json.dumps(rec, default=str) + "\n")
        self._f.flush()

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def read(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    out.append({"ok": False, "error_type": "unreadable_record", "phase": "unknown"})
        return out


# --------------------------------------------------------------------------- aggregation
def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    xs = sorted(values)
    k = max(0, min(len(xs) - 1, math.ceil(p / 100.0 * len(xs)) - 1))
    return xs[k]


def summarize(values: list[float | None]) -> dict[str, Any]:
    """p50/p95/min/max/mean with the sample size that produced them; unknowns are counted, not zero."""
    xs = [float(v) for v in values if v is not None]
    out: dict[str, Any] = {"n": len(xs), "unavailable": len(values) - len(xs)}
    if not xs:
        out["note"] = "no samples"
        return out
    out.update(
        {
            "p50": percentile(xs, 50),
            "p95": percentile(xs, 95),
            "min": min(xs),
            "max": max(xs),
            "mean": round(statistics.fmean(xs), 3),
        }
    )
    if len(xs) < 5:
        out["note"] = f"percentiles over only {len(xs)} sample(s): indicative, not a distribution"
    return out


METRICS = (
    "client_wall_ms",
    "gateway_latency_ms",
    "queue_ms",
    "ttft_ms",
    "tpot_ms",
    "prompt_eval_ms",
    "tok_s",
    "prompt_tokens",
    "completion_tokens",
)
POOLED_NOTE = "pooled across cells (mixture): not a cell, never a per-cell p50"


def _group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r.get("ok")]
    failures: dict[str, int] = {}
    for r in rows:
        if not r.get("ok"):
            key = r.get("error_type") or "unknown"
            failures[key] = failures.get(key, 0) + 1
    met = [r.get("expectation_met") for r in ok if r.get("expectation_met") is not None]
    return {
        "n": len(rows),
        "requests": len(rows),
        "ok": len(ok),
        "failures": failures,
        "expectation": {
            "checked": len(met),
            "met": sum(1 for m in met if m),
            "rules": sorted({str(r.get("scoring")) for r in rows if r.get("scoring")}),
            "note": "synthetic probes with strict/loose checks as declared per case; not customer acceptance",
        },
        "truncated": sum(1 for r in ok if r.get("finish_reason") == "length"),
        "evidence_defects": sum(len(r.get("evidence_defects") or []) for r in rows),
        "metrics": {m: summarize([r.get(m) for r in ok]) for m in METRICS},
    }


def cell_key(r: dict[str, Any]) -> str:
    return f"{r.get('case_id')}/band={r.get('input_band', 'unpadded')}/cap={r.get('output_cap')}"


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per matrix CELL (case x input band x output cap) with N per cell; everything else is labelled
    pooled. Warmup records must be excluded by the caller."""
    cells: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        cells.setdefault(cell_key(r), []).append(r)
    out_cells = {}
    for k, rows in sorted(cells.items()):
        g = _group(rows)
        g.update(
            {
                "case_id": rows[0].get("case_id"),
                "kind": rows[0].get("kind"),
                "input_band": rows[0].get("input_band", "unpadded"),
                "output_cap": rows[0].get("output_cap"),
                "actual_prompt_tokens": summarize([r.get("prompt_tokens") for r in rows if r.get("ok")]),
                "actual_completion_tokens": summarize(
                    [r.get("completion_tokens") for r in rows if r.get("ok")]
                ),
            }
        )
        out_cells[k] = g
    by_kind_cap: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_kind_cap.setdefault(f"{r.get('kind')}/cap={r.get('output_cap')}", []).append(r)
    pooled_kind = {}
    for k, rows in sorted(by_kind_cap.items()):
        g = _group(rows)
        g.update({"pooled": True, "note": POOLED_NOTE, "cells": sorted({cell_key(r) for r in rows})})
        pooled_kind[k] = g
    overall = _group(records)
    overall.update(
        {
            "pooled": True,
            "label": "pooled across cells (mixture)",
            "note": POOLED_NOTE,
            "cell_count": len(out_cells),
        }
    )
    return {"cells": out_cells, "pooled_by_kind_and_cap": pooled_kind, "overall": overall}  # fmt: skip


# --------------------------------------------------------------------------- evidence via the control plane
class Api:
    def __init__(self, server: str, token: str, ca_file: str | None):
        import ssl

        self.server = server.rstrip("/")
        self.token = token
        self.ctx = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()

    def get(self, path: str) -> Any:
        req = urllib.request.Request(self.server + path, headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=30, context=self.ctx) as r:
            return json.loads(r.read())


def _trace_state(result: Any) -> str:
    if isinstance(result, dict) and "error" in result and "spans" not in result:
        return "error"
    if isinstance(result, dict) and isinstance(result.get("spans"), list) and result["spans"]:
        return "nonempty"
    return "empty"


def snapshot_evidence(
    api: Any,
    device_id: str | None,
    trace_ids: list[str],
    out_dir: Path,
    *,
    reconcile_rounds: int = 3,
    reconcile_wait_s: float = 2.0,
) -> dict[str, Any]:
    """Device + latest telemetry + the spans of every trace id, written to <out_dir>/evidence.json.
    Trace results are counted as requested / nonempty / empty / error (an error or empty result is never
    "fetched"); missing traces are re-fetched up to `reconcile_rounds` times with `reconcile_wait_s`
    between rounds (spool lag), and the evidence stays "partial" while any trace is still missing."""
    if api is None or not device_id:
        return {"attempted": False, "reason": "not fetched: no --server/--token-file/--device-id given"}
    out_dir.mkdir(parents=True, exist_ok=True)
    ev: dict[str, Any] = {"attempted": True, "device_id": device_id}
    try:
        ev["device"] = api.get(f"/api/v1/devices/{device_id}")
        ev["telemetry_latest"] = api.get(f"/api/v1/devices/{device_id}/telemetry?limit=60")
        ev["telemetry_note"] = (
            f"latest {len(ev['telemetry_latest']) if isinstance(ev['telemetry_latest'], list) else '?'} retained samples as returned by the API (no time-range query exists)"
        )
    except Exception as e:  # noqa: BLE001
        ev["telemetry_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    traces: dict[str, Any] = {}
    states: dict[str, str] = {}
    ids = list(dict.fromkeys(t for t in trace_ids if t))

    def fetch(pending: list[str]) -> None:
        for t in pending:
            try:
                res = api.get(f"/api/v1/traces/{t}")
            except Exception as e:  # noqa: BLE001
                res = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            traces[t] = res
            states[t] = _trace_state(res)

    fetch(ids)
    rounds_used = 0
    for _ in range(max(0, reconcile_rounds)):
        missing = [t for t in ids if states.get(t) != "nonempty"]
        if not missing:
            break
        rounds_used += 1
        if reconcile_wait_s > 0:
            time.sleep(reconcile_wait_s)
        fetch(missing)
    counts = {
        "requested": len(ids),
        "nonempty": sum(1 for t in ids if states.get(t) == "nonempty"),
        "empty": sum(1 for t in ids if states.get(t) == "empty"),
        "error": sum(1 for t in ids if states.get(t) == "error"),
    }
    counts["partial"] = counts["nonempty"] < counts["requested"]
    counts["missing_trace_ids"] = [t for t in ids if states.get(t) != "nonempty"]
    counts["reconciliation"] = {
        "rounds_used": rounds_used,
        "max_rounds": reconcile_rounds,
        "wait_s": reconcile_wait_s,
        "completed": True,
    }
    counts["note"] = (
        "spans are the gateway's own records for the same trace ids; empty = the API answered with no spans "
        "(not yet spooled or not retained), error = the fetch failed; neither is counted as fetched"
    )
    ev["traces"] = traces
    ev["trace_states"] = states
    ev["trace_counts"] = counts
    (out_dir / "evidence.json").write_text(json.dumps(ev, indent=1, default=str))
    summary = {
        k: v for k, v in ev.items() if k not in ("telemetry_latest", "traces", "device", "trace_states")
    }
    summary["telemetry_samples"] = (
        len(ev["telemetry_latest"]) if isinstance(ev.get("telemetry_latest"), list) else None
    )
    summary["traces"] = counts
    summary.pop("trace_counts", None)
    summary["evidence_file"] = str(out_dir / "evidence.json")
    return summary


# --------------------------------------------------------------------------- render check (diagnostic)
def render_qwen3_nonthinking(messages: list[dict[str, Any]], add_generation_prompt: bool = True) -> str:
    """Local reference rendering of docs/templates/qwen3-nonthinking.jinja: system/user turns as
    '<|im_start|>ROLE\\nCONTENT<|im_end|>\\n', prior assistant turns with any reasoning stripped up to the
    last '</think>' (leading newlines removed), and the generation prompt ending in the empty think block.
    Verified subset: single system + single user turn (plus prior assistant turns with reasoning
    stripped); assistant turns after the last user query are NOT covered (see docs/templates/README.md)."""
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") if isinstance(m.get("content"), str) else ""
        if role in ("system", "user"):
            out.append(f"<|im_start|>{role}\n{content}<|im_end|>\n")
        elif role == "assistant":
            if "</think>" in content:
                content = content.split("</think>")[-1].lstrip("\n")
            out.append(f"<|im_start|>assistant\n{content}<|im_end|>\n")
    if add_generation_prompt:
        out.append(GEN_PROMPT)
    return "".join(out)


def render_check(
    runtime: str, api_key_file: str, messages: list[dict[str, str]], template_path: str | None
) -> dict[str, Any]:
    """Render one conversation through the RUNTIME's /apply-template (what the gateway does at admission)
    and tokenize it. ok ONLY when: the template file exists (sha256 recorded), the runtime rendered
    exactly the complete expected bytes, the rendering ends with the empty think block, and /tokenize
    returned a non-empty list of integer ids. Every failure carries its reason. Diagnostic only."""
    res: dict[str, Any] = {"ok": False, "template_path": template_path, "checks": {}}
    if not template_path or not Path(template_path).is_file():
        res["reason"] = "template_missing: --template PATH must name the pinned template file"
        return res
    res["template_sha256"] = sha256_file(Path(template_path))
    res["renderer"] = (
        "local reference for qwen3-nonthinking.jinja (single system + single user turn verified)"
    )
    expected = render_qwen3_nonthinking(messages)
    res["expected_prompt"] = expected
    res["expected_sha256"] = hashlib.sha256(expected.encode()).hexdigest()
    try:
        key = Path(api_key_file).read_text().strip()
    except OSError as e:
        res["reason"] = f"api_key_unreadable: {type(e).__name__}"
        return res
    try:
        st, out, _, _ = _post_json(
            f"{runtime.rstrip('/')}/apply-template",
            {"messages": messages},
            {"Authorization": f"Bearer {key}"},
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        res["reason"] = f"apply_template_transport: {type(e).__name__}: {str(e)[:200]}"
        return res
    res["checks"]["apply_template_status"] = st
    if st != 200 or not isinstance(out, dict) or not isinstance(out.get("prompt"), str):
        res["reason"] = f"apply_template_failed: http {st} or malformed body"
        res["error"] = out
        return res
    prompt = out["prompt"]
    res["prompt"] = prompt
    res["prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
    res["checks"]["ends_with_empty_think"] = prompt.endswith(GEN_PROMPT)
    res["ends_with_empty_think"] = res["checks"]["ends_with_empty_think"]
    res["checks"]["rendered_equals_expected"] = prompt == expected
    if prompt != expected:
        i = next(
            (i for i, (a, b) in enumerate(zip(prompt, expected, strict=False)) if a != b),
            min(len(prompt), len(expected)),
        )
        res["reason"] = "rendered_prompt_mismatch: runtime rendering differs from the local reference"
        res["first_difference_at"] = i
        res["rendered_tail"] = prompt[max(0, i - 40) : i + 80]
        res["expected_tail"] = expected[max(0, i - 40) : i + 80]
        return res
    if not res["checks"]["ends_with_empty_think"]:
        res["reason"] = "suffix_mismatch: rendering does not end with the empty think block"
        return res
    try:
        st2, tok, _, _ = _post_json(
            f"{runtime.rstrip('/')}/tokenize",
            {"content": prompt, "add_special": True, "parse_special": True},
            {"Authorization": f"Bearer {key}"},
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        res["reason"] = f"tokenize_transport: {type(e).__name__}: {str(e)[:200]}"
        return res
    res["checks"]["tokenize_status"] = st2
    ids = (tok or {}).get("tokens") if isinstance(tok, dict) else None
    valid = (
        st2 == 200
        and isinstance(ids, list)
        and len(ids) > 0
        and all(isinstance(x, int) and not isinstance(x, bool) for x in ids)
    )
    res["checks"]["tokens_nonempty_ints"] = bool(valid)
    if not valid:
        res["reason"] = (
            f"tokenize_failed: http {st2}, tokens={'missing' if ids is None else len(ids) if isinstance(ids, list) else 'malformed'}"
        )
        res["prompt_tokens"] = None
        return res
    res["prompt_tokens"] = len(ids)
    res["ok"] = True
    res["reason"] = "ok: runtime rendering equals the complete expected bytes; tokenized non-empty"
    return res


# --------------------------------------------------------------------------- main
def _plan(args: argparse.Namespace, cases: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], int]]:
    """Ordered list of (phase, case, repeat index)."""
    items: list[tuple[str, dict[str, Any], int]] = []
    if args.matrix:
        probes = [c for c in cases if c["id"] in set(args.probe)]
        missing = set(args.probe) - {c["id"] for c in probes}
        if missing:
            raise ValueError(f"--probe ids not in the fixture: {sorted(missing)}")
        bands = tuple(int(x) for x in args.bands.split(","))
        caps = tuple(int(x) for x in args.caps.split(","))
        for cell in build_matrix(probes, bands, caps, args.chars_per_token):
            for _ in range(args.warmup):
                items.append(("warmup", cell, 0))
            for i in range(args.matrix_repeats):
                items.append(("matrix", cell, i))
        return items
    for c in cases:
        for _ in range(args.warmup):
            items.append(("warmup", c, 0))
        for i in range(args.repeats):
            items.append(("task", c, i))
    return items


def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases_spec = "smoke" if args.smoke else args.cases
    cases, fixture = load_fixture(cases_spec)
    api = None
    if args.server and args.token_file and args.device_id:
        api = Api(args.server, Path(args.token_file).read_text().strip(), args.ca_file)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    settings = {
        "mode": "matrix" if args.matrix else ("smoke" if args.smoke else "task"),
        "repeats": args.matrix_repeats if args.matrix else args.repeats,
        "warmup": args.warmup,
        "max_tokens_default": args.max_tokens,
        "temperature": 0.0,
        "seed": 42,
        "cases": cases_spec,
        "case_count": len(cases),
        "client_timeout_s": args.timeout,
        "gateway_deadline_s": GATEWAY_DEADLINE_S,
        "raw_body_bound_bytes": RAW_BODY_BOUND,
        "probes": list(args.probe) if args.matrix else None,
        "input_bands": [int(x) for x in args.bands.split(",")] if args.matrix else None,
        "output_caps": [int(x) for x in args.caps.split(",")] if args.matrix else None,
        "chars_per_token_heuristic": args.chars_per_token if args.matrix else None,
        "filler_sha256": hashlib.sha256(FILLER.encode()).hexdigest() if args.matrix else None,
    }
    meta = {
        "schema": PROTOCOL_VERSION,
        "label": args.label,
        "started_at": started,
        "gateway": args.gateway,
        "settings": settings,
        "fixture": fixture,
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=1, default=str))
    before = snapshot_evidence(
        api, args.device_id, [], out_dir / "before", reconcile_rounds=0, reconcile_wait_s=0.0
    )
    recorder = Recorder(out_dir / "records.jsonl")
    partial: dict[str, Any] | None = None
    rc = 0
    try:
        for phase, case, i in _plan(args, cases):
            try:
                r = measure_one(args.gateway, case, args.max_tokens, args.timeout)
            except InterruptedRequest as ir:
                # the interrupted attempt keeps the phase it was actually in (a warmup stays a warmup and
                # is listed with the warmups, a timed attempt stays timed); the interruption itself is
                # carried by error_type=interrupted_client, never by the phase
                if ir.record is not None:
                    ir.record["phase"] = phase
                    ir.record["repeat"] = i
                raise
            r["phase"] = phase
            r["repeat"] = i
            recorder.add(r)
            if args.verbose:
                print(
                    json.dumps({k: v for k, v in r.items() if k not in ("response_raw", "request")}),
                    flush=True,
                )
    except KeyboardInterrupt as ki:
        rec = getattr(ki, "record", None)
        if rec is not None:  # the in-flight attempt is persisted before the partial report is built
            rec.setdefault("phase", "unknown")  # only reachable for a record raised outside the plan loop
            recorder.add(rec)
        partial = {
            "reason": "KeyboardInterrupt: run interrupted by the operator"
            + (" during a request" if rec else "")
        }
        rc = 130
    except Exception as e:  # noqa: BLE001
        partial = {"reason": f"{type(e).__name__}: {str(e)[:300]}"}
        rc = 1
    finally:
        recorder.close()
    try:
        trace_ids = [r["trace_id"] for r in recorder.records if r.get("trace_id")]
        after = snapshot_evidence(
            api, args.device_id, trace_ids, out_dir, reconcile_rounds=args.trace_reconcile_rounds,
            reconcile_wait_s=args.trace_reconcile_wait_s,
        )  # fmt: skip
    except KeyboardInterrupt:
        after = {"attempted": True, "error": "KeyboardInterrupt during evidence fetch", "partial": True}
        partial = partial or {"reason": "KeyboardInterrupt during evidence fetch"}
        rc = rc or 130
    except Exception as e:  # noqa: BLE001
        after = {"attempted": True, "error": f"{type(e).__name__}: {str(e)[:200]}", "partial": True}
    report = build_report(meta, recorder.records, before, after, partial)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    (out_dir / "summary.md").write_text(markdown_summary(report))
    print(markdown_summary(report))
    if rc:
        return rc
    timed = [r for r in recorder.records if r.get("phase") != "warmup"]
    return 0 if timed and all(r.get("ok") for r in timed) else 1


def build_report(
    meta: dict[str, Any],
    all_records: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
    partial: dict[str, Any] | None,
) -> dict[str, Any]:
    warm = [r for r in all_records if r.get("phase") == "warmup"]
    records = [r for r in all_records if r.get("phase") != "warmup"]
    defects = [
        {"case_id": r.get("case_id"), "phase": r.get("phase"), "repeat": r.get("repeat"), "defect": d,
         "trace_id_header": r.get("trace_id_header"), "trace_id_body": r.get("trace_id_body"), "status": r.get("status")}
        for r in all_records
        for d in (r.get("evidence_defects") or [])
    ]  # fmt: skip
    return {
        **meta,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "partial": partial is not None,
        "partial_reason": (partial or {}).get("reason"),
        "honesty": [
            "synthetic task probes, not a customer workload and not customer acceptance; strict fixture checks are exact/label/json_exact as declared per case",
            "TTFT is the gateway's measured value or unavailable; TPOT only from runtime generation timings; client wall time includes loopback HTTP and queueing and is reported separately",
            "warmup requests are excluded from aggregates (listed separately, persisted like every other request)",
            "cache_prompt is disabled on the gateway: no prompt-cache reuse is claimed",
            "no percentile is reported without its sample size; aggregates are per cell (case x input band x output cap); anything else is labelled pooled across cells (mixture)",
            "input bands are targets sized by a chars-per-token heuristic; the ACTUAL prompt tokens reported by the gateway are recorded per request and summarized per cell",
            "a client socket timeout (client_timeout_no_response) is not a gateway cancellation: the gateway may still have completed or cancelled the request",
            "trace evidence counts requested/nonempty/empty/error separately; an empty or error result is never counted as fetched; a missing header trace id or a header/body mismatch is an evidence defect",
            "the fixture (sha256 in `fixture`) was not tuned after results; a changed fixture is a new fixture file",
        ],
        "evidence_defects": defects,
        "warmup": warm,
        "records": records,
        "aggregate": aggregate(records),
        "evidence_before": before,
        "evidence_after": after,
    }


def finalize_only(out: str) -> int:
    """Rebuild report.json from what was persisted (records.jsonl + run_meta.json) after a process death."""
    out_dir = Path(out)
    meta_path = out_dir / "run_meta.json"
    if not meta_path.exists():
        print(f"{meta_path} missing: nothing to finalize", file=sys.stderr)
        return 2
    meta = json.loads(meta_path.read_text())
    records = Recorder.read(out_dir / "records.jsonl")
    before_path = out_dir / "before" / "evidence.json"
    before = {
        "attempted": before_path.exists(),
        "reason": "finalized from disk",
        "evidence_file": str(before_path),
    }
    after = {
        "attempted": False,
        "reason": "not fetched: finalized from persisted records without the API",
        "partial": True,
    }
    report = build_report(
        meta,
        records,
        before,
        after,
        {"reason": "finalized from persisted records (process did not complete)"},
    )
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    (out_dir / "summary.md").write_text(markdown_summary(report))
    print(markdown_summary(report))
    return 0  # fmt: skip


def markdown_summary(report: dict[str, Any]) -> str:
    agg = report["aggregate"]
    st = report["settings"]
    fx = report.get("fixture") or {}
    lines = [
        f"# Convoy gateway benchmark ({report.get('label') or 'unlabelled'})",
        "",
        f"gateway {report['gateway']} · mode {st.get('mode')} · fixture {fx.get('name')} (sha256 {fx.get('sha256') or 'n/a'}, {'strict' if fx.get('strict') else 'NOT strict: ' + str(fx.get('note'))}) · {st['case_count']} cases x {st['repeats']} repeats (+{st['warmup']} warmup each) · started {report['started_at']}",
        "",
    ]
    if report.get("partial"):
        lines += [
            f"**PARTIAL REPORT**: {report.get('partial_reason')} — built from the records persisted so far.",
            "",
        ]
    lines.append(
        "Synthetic probes, not a customer workload and not customer acceptance. Values come from the gateway/runtime; unavailable stays unavailable."
    )
    lines.append("")

    def fmt(sm: dict[str, Any]) -> str:
        if sm.get("n", 0) == 0:
            return f"unavailable (0 of {sm.get('unavailable', 0)})"
        s = f"p50 {sm['p50']:.1f} / p95 {sm['p95']:.1f} (n={sm['n']}"
        if sm.get("unavailable"):
            s += f", unavailable={sm['unavailable']}"
        return s + ")" + ("  [<5 samples]" if sm.get("n", 0) < 5 else "")

    def block(name: str, g: dict[str, Any]) -> None:
        m = g["metrics"]
        lines.append(
            f"## {name}: n={g['n']}, {g['ok']}/{g['requests']} ok, failures {g['failures'] or 'none'}, truncated {g['truncated']}, expectations met {g['expectation']['met']}/{g['expectation']['checked']}, evidence defects {g['evidence_defects']}"
        )
        if g.get("pooled"):
            lines.append(f"- {g.get('note')}")
        if "actual_prompt_tokens" in g:
            lines.append(
                f"- input band target {g['input_band']} -> actual prompt tokens {fmt(g['actual_prompt_tokens'])}; output cap {g['output_cap']} -> actual completion tokens {fmt(g['actual_completion_tokens'])}"
            )
        lines.append(
            f"- completion latency (gateway) ms: {fmt(m['gateway_latency_ms'])}; client wall ms: {fmt(m['client_wall_ms'])}; queue ms: {fmt(m['queue_ms'])}"
        )
        lines.append(
            f"- TTFT ms: {fmt(m['ttft_ms'])}; TPOT ms/token: {fmt(m['tpot_ms'])}; prompt eval ms: {fmt(m['prompt_eval_ms'])}; tok/s: {fmt(m['tok_s'])}"
        )
        lines.append("")

    block("overall — pooled across cells (mixture)", agg["overall"])
    for k, g in agg["cells"].items():
        block(f"cell {k}", g)
    for k, g in agg["pooled_by_kind_and_cap"].items():
        block(f"pooled {k}", g)
    ev = report.get("evidence_after") or {}
    if not ev.get("attempted"):
        lines.append(f"Evidence: {ev.get('reason', 'not fetched')}")
    elif ev.get("error"):
        lines.append(f"Evidence: attempted but failed ({ev['error']}); partial")
    else:
        tr = ev.get("traces") or {}
        lines.append(
            f"Evidence: telemetry samples {ev.get('telemetry_samples')}; traces requested {tr.get('requested')}, nonempty {tr.get('nonempty')}, empty {tr.get('empty')}, error {tr.get('error')}"
            + (" — PARTIAL (missing traces after reconciliation)" if tr.get("partial") else " — complete")
        )
    if report.get("evidence_defects"):
        lines.append(
            f"Evidence defects: {len(report['evidence_defects'])} (see report.json evidence_defects)"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gateway", default="http://127.0.0.1:0")
    ap.add_argument("--out", default="./bench-out")
    ap.add_argument(
        "--cases", default="v1", help="v1 (strict, default) | smoke | builtin (diagnostic) | path.jsonl"
    )
    ap.add_argument("--smoke", action="store_true", help="run the frozen 8-case deployment smoke fixture")
    ap.add_argument(
        "--matrix", action="store_true", help="timing matrix (bands x caps x repeats) for --probe case(s)"
    )
    ap.add_argument("--probe", action="append", default=None, help="fixture case id(s) used as matrix probes")
    ap.add_argument("--bands", default=",".join(str(b) for b in INPUT_BANDS))
    ap.add_argument("--caps", default=",".join(str(c) for c in OUTPUT_CAPS))
    ap.add_argument("--matrix-repeats", type=int, default=MATRIX_REPEATS)
    ap.add_argument("--chars-per-token", type=float, default=CHARS_PER_TOKEN)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=64, help="cap for cases that declare none")
    ap.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_CLIENT_TIMEOUT_S,
        help="client socket timeout (s); gateway deadline is 30 s",
    )
    ap.add_argument("--label", default="")
    ap.add_argument("--server")
    ap.add_argument("--ca-file")
    ap.add_argument("--token-file")
    ap.add_argument("--device-id")
    ap.add_argument("--trace-reconcile-rounds", type=int, default=3)
    ap.add_argument("--trace-reconcile-wait-s", type=float, default=2.0)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--finalize-only", action="store_true", help="rebuild report.json from <out>/records.jsonl"
    )
    ap.add_argument("--render-check", action="store_true")
    ap.add_argument("--runtime")
    ap.add_argument("--api-key-file")
    ap.add_argument("--template", help="pinned template file (sha256 recorded; required for --render-check)")
    ap.add_argument("--system", default="Answer briefly.")
    ap.add_argument("--prompt", default="What is the capital of France? One word.")
    args = ap.parse_args(argv)  # fmt: skip
    if args.probe is None:
        args.probe = ["sem-capital-france"]
    if args.render_check:
        if not (args.runtime and args.api_key_file and args.template):
            print("--render-check needs --runtime, --api-key-file and --template", file=sys.stderr)
            return 2
        res = render_check(
            args.runtime,
            args.api_key_file,
            [{"role": "system", "content": args.system}, {"role": "user", "content": args.prompt}],
            args.template,
        )
        print(json.dumps(res, indent=1))
        return 0 if res.get("ok") else 1
    if args.finalize_only:
        return finalize_only(args.out)
    if args.gateway.endswith(":0"):
        print(
            "--gateway must name the agent's gateway port (agent log line 'gateway=127.0.0.1:<port>')",
            file=sys.stderr,
        )
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
