"""Evidence lanes (spool ingest), eval result validation + server re-scoring, usage counters, retention.

Contracts (§8.10, §1 row 36, R20): batches carry a lane and per-record sequence numbers; the server
commits a cursor and replays never recount usage; loss ranges are explicit; eval results are validated
against the plan's eval set (case identity/count/completion) and independently re-scored where the
evidence method allows; the verdict is recomputed with the shared evaluator."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from convoy_agent.evaluator import EVALUATOR_VERSION, METRICS, evaluate, summarize
from convoy_agent.scoring import rescore_from_record
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ..config import get_settings
from ..db import write_txn
from ..ids import iso, new_id, utcnow
from ..models import (
    Device,
    EvalResult,
    EvalSet,
    Failure,
    LaneCursor,
    LogEntry,
    LossRange,
    Operation,
    Plan,
    Release,
    Span,
    TelemetrySample,
    UsageDaily,
    UsageUnknownInterval,
)
from ..redaction import redact
from ..validation import finite

LANES = ("critical", "usage", "telemetry")
KINDS = {
    "critical": ("eval_result", "failure"),
    "usage": ("usage",),
    "telemetry": ("log", "span", "telemetry"),
}
MAX_RECORDS = 500
MAX_RECORD_BYTES = 512 * 1024
MAX_LOSS_SPAN = 1_000_000
MAX_TIMINGS = 512
MAX_SENSOR_SAMPLES = 720
PERF_SECTIONS = ("latency", "throughput", "memory", "thermal", "power")
SUMMARY_TOLERANCE = (
    1e-3  # relative tolerance when comparing a supplied aggregate with the server recomputation
)


class EvidenceError(ValueError):
    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


def _ts(v: Any) -> datetime:
    f = finite(v)
    if f is None or f < 0 or f > 4102444800:
        return utcnow()
    return datetime.fromtimestamp(f, tz=timezone.utc)


def _valid_ranges(loss_ranges: list[dict[str, Any]] | None) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for lr in loss_ranges or []:
        if not isinstance(lr, dict):
            continue
        a, b = lr.get("from_seq"), lr.get("to_seq")
        if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, int) or not isinstance(b, int):
            continue
        if a < 1 or b < a or b - a > MAX_LOSS_SPAN:
            continue
        out.append((a, b, str(lr.get("reason", "device_reported"))[:64]))
    return out


def _covered(ranges: list[tuple[int, int, str]], lo: int, hi: int) -> bool:
    """True when every sequence in [lo, hi] lies inside the union of the declared loss ranges."""
    cur = lo
    for a, b, _ in sorted(ranges):
        if b < cur:
            continue
        if a > cur:
            return False
        cur = b + 1
        if cur > hi:
            return True
    return cur > hi


def _restoration_context(db: DbSession, dev: Device) -> dict[str, Any]:
    """The binding and restoration context a declared loss is bound to (not just a repeatable epoch)."""
    from ..models import Installation

    inst = db.get(Installation, 1)
    rf = (inst.restored_from if inst else None) or {}
    return {
        "binding_epoch": dev.binding_epoch or 1,
        "credential_issued_at": iso(dev.credential_issued_at),
        "restored_at": rf.get("restored_at"),
        "restored_file": rf.get("file"),
        "quarantined_at": iso(inst.quarantined_at) if inst else None,
    }


def _record_losses(
    db: DbSession,
    dev: Device,
    lane: str,
    ranges: list[tuple[int, int, str]],
    lo: int,
    hi: int,
    ctx: dict[str, Any] | None = None,
) -> None:
    """Persist the declared ranges clipped to the segment [lo, hi] the cursor actually advanced over."""
    for a, b, reason in sorted(ranges):
        fa, fb = max(a, lo), min(b, hi)
        if fa <= fb:
            db.add(
                LossRange(
                    device_id=dev.id, lane=lane, from_seq=fa, to_seq=fb, reason=reason, context=ctx or {}
                )
            )


def ingest(
    db: DbSession,
    dev: Device,
    lane: str,
    records: list[dict[str, Any]],
    loss_ranges: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """R48: the cursor advances ONLY over a contiguous union of committed records (accepted or explicitly
    rejected) and validated declared-loss ranges. Records beyond a gap that no loss range covers are not
    stored (reported as `deferred`) so the device keeps them and re-sends after declaring the loss; an ACK
    therefore never authorises deletion of a record the server did not commit."""
    if lane not in LANES:
        raise EvidenceError("unknown lane")
    if len(records) > MAX_RECORDS:
        raise EvidenceError("too many records in one batch")
    ranges = _valid_ranges(loss_ranges)
    from ..auth import StaleBinding, assert_admitted_binding

    with write_txn(db):
        try:
            dev = assert_admitted_binding(db, dev)  # a rebind/revoke since admission mutates nothing
        except StaleBinding as e:
            raise EvidenceError(str(e), 401) from e
        ctx = _restoration_context(db, dev)
        cur = db.get(LaneCursor, (dev.id, lane))
        if cur is None:
            cur = LaneCursor(device_id=dev.id, lane=lane, committed_seq=0)
            db.add(cur)
        committed = cur.committed_seq
        accepted = 0
        rejected: list[dict[str, Any]] = []
        deferred: list[int] = []
        seen: set[int] = set()
        ordered: list[tuple[int, dict[str, Any]]] = []
        for r in records:
            seq = r.get("seq") if isinstance(r, dict) else None
            if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
                rejected.append({"seq": seq, "reason": "bad seq"})
                continue
            if seq in seen:
                rejected.append({"seq": seq, "reason": "duplicate seq in batch"})
                continue
            seen.add(seq)
            ordered.append((seq, r))
        ordered.sort(key=lambda x: x[0])
        stopped = False
        for seq, r in ordered:
            if seq <= committed:
                continue  # replay after a lost ACK: already committed, never recounted
            if stopped:
                deferred.append(seq)
                continue
            if seq > committed + 1:
                if not _covered(ranges, committed + 1, seq - 1):
                    stopped = True
                    deferred.append(seq)
                    continue
                _record_losses(db, dev, lane, ranges, committed + 1, seq - 1, ctx)
                committed = seq - 1
            kind = r.get("kind")
            body = r.get("body")
            if kind not in KINDS[lane] or not isinstance(body, dict) or len(str(body)) > MAX_RECORD_BYTES:
                rejected.append({"seq": seq, "reason": f"invalid kind/body for lane {lane}"})
                db.add(
                    LossRange(
                        device_id=dev.id, lane=lane, from_seq=seq, to_seq=seq, reason="rejected_malformed"
                    )
                )
                committed = seq  # explicit rejection is a commitment: the device may drop the record
                continue
            try:
                _store(db, dev, kind, redact(body), seq=seq)
                accepted += 1
            except EvidenceError as e:
                if e.status == 403:
                    raise  # R44: a record naming another device's operation refuses the whole batch
                rejected.append({"seq": seq, "reason": str(e)})
                db.add(
                    LossRange(
                        device_id=dev.id,
                        lane=lane,
                        from_seq=seq,
                        to_seq=seq,
                        reason=f"rejected:{str(e)[:40]}",
                    )
                )
            committed = seq
        # trailing declared losses that start exactly at the frontier extend it (contiguously, repeatedly)
        progressed = True
        while progressed:
            progressed = False
            for a, b, _ in sorted(ranges):
                if a <= committed + 1 <= b:
                    _record_losses(db, dev, lane, ranges, committed + 1, b, ctx)
                    committed = b
                    progressed = True
                    break
        if committed != cur.committed_seq or accepted:
            cur.committed_seq = committed
            cur.updated_at = utcnow()  # the cursor timestamp moves only with real progress
    # `next_expected_seq` tells a device whose durable ACK frontier is AHEAD of this server (the server was
    # restored to an older snapshot) exactly where the server's history ends, so it can declare the
    # unrecoverable pre-restore span as loss and resume exactly once (restore-gap reconciliation).
    return {
        "committed_seq": committed,
        "next_expected_seq": committed + 1,
        "accepted": accepted,
        "rejected": rejected,
        "deferred": deferred,
    }


USAGE_SCHEMA = 2  # current measured usage populations (agent `USAGE_SCHEMA`)
USAGE_METRICS = (
    "inference_requests",
    "tokens_in",
    "tokens_out",
    "inference_minutes",  # schema 2: inference slot busy time
    "runtime_up_minutes",  # supervisor child running
    "agent_up_minutes",  # agent process uptime
    "contact_minutes",  # schema 2: contact time counted only within the report cadence
    "contacts",  # successful report round trips
    "reconnects",  # contacts after a gap longer than the cadence
    "unknown_coverage_s",  # explicit unobserved intervals (crash before a durable checkpoint)
    "mixed_online_minutes",  # unversioned producers: process elapsed OR contact time (not attributable)
    "mixed_active_minutes",  # unversioned producers: runtime loaded OR inference busy (not attributable)
    "download_bytes",
    "eval_runs",
)
# The only names an UNVERSIONED usage record may contribute: the invariant counters (their meaning never
# changed across producers) plus the mixed renames applied in `_store`. Every other metric name is
# schema-2-only; an unversioned body carrying one is dropped from that record (never stored under the
# measured name, never renamed), while its counters remain valid.
USAGE_UNVERSIONED_METRICS = ("inference_requests", "tokens_in", "tokens_out", "download_bytes", "eval_runs")
USAGE_UNVERSIONED_NAMES = ("active_minutes", "online_minutes", "connected_minutes")
USAGE_MIXED_NAMES = ("mixed_active_minutes", "mixed_online_minutes")
UNKNOWN_INTERVAL_TOLERANCE_S = 1.0  # |(to_ts - from_ts) - unknown_coverage_s| allowed
MAX_TS = 4102444800  # 2100-01-01


def _unknown_interval(body: dict[str, Any]) -> tuple[datetime, datetime, float, str] | None:
    """Validate a schema-2 record's `unknown_interval` against its `unknown_coverage_s`. Returns the
    bounds, the seconds and the reason, or None when the record declares no interval. Nothing is ever
    inferred: a record with a daily sum but no interval stores only the sum."""
    iv = body.get("unknown_interval")
    if iv is None:
        return None
    if not isinstance(iv, dict):
        raise EvidenceError("usage unknown_interval must be an object")
    a, b = finite(iv.get("from_ts")), finite(iv.get("to_ts"))
    if a is None or b is None or not (0 <= a <= MAX_TS and 0 <= b <= MAX_TS):
        raise EvidenceError("usage unknown_interval from_ts/to_ts must be finite epoch timestamps")
    if a > b:
        raise EvidenceError("usage unknown_interval from_ts exceeds to_ts")
    seconds = finite(body.get("unknown_coverage_s"))
    if seconds is None or seconds < 0:
        raise EvidenceError("usage unknown_interval needs a finite unknown_coverage_s")
    if abs((b - a) - seconds) > UNKNOWN_INTERVAL_TOLERANCE_S:
        raise EvidenceError("usage unknown_interval span disagrees with unknown_coverage_s")
    reason = _s(iv.get("reason"), 64) or "unobserved"
    return (
        datetime.fromtimestamp(a, tz=timezone.utc),
        datetime.fromtimestamp(b, tz=timezone.utc),
        seconds,
        reason,
    )


# schema-2 producer diagnostics: (count field, wall-clock anchor field, scope). A lapse in the agent's
# durable checkpoint persistence that later recovered is evidence about the producer, not about lost
# requests: it is logged, never turned into a usage metric, a loss range or an unknown interval.
CHECKPOINT_DIAGNOSTICS = (
    ("checkpoint_failures", "checkpoint_failures_since_wall", "current"),
    ("previous_checkpoint_failures", "previous_undurable_since_wall", "previous_incarnation"),
)


def _checkpoint_diagnostics(body: dict[str, Any]) -> list[tuple[int, float | None, str]]:
    """Validate the checkpoint-lapse diagnostics a schema-2 usage record may carry. Returns the
    (count, anchor, scope) triples with a count > 0; an ill-formed value disposes the record."""
    out: list[tuple[int, float | None, str]] = []
    for count_key, anchor_key, scope in CHECKPOINT_DIAGNOSTICS:
        raw = body.get(count_key)
        if raw is None:
            continue
        n = finite(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
        if n is None or n < 0 or n != int(n):
            raise EvidenceError(f"usage {count_key} must be a finite non-negative integer")
        anchor_raw = body.get(anchor_key)
        anchor = None
        if anchor_raw is not None:
            anchor = finite(anchor_raw)
            if anchor is None or not (0 <= anchor <= MAX_TS):
                raise EvidenceError(f"usage {anchor_key} must be a finite epoch timestamp or null")
        if n > 0:
            out.append((int(n), anchor, scope))
    return out


def _store(db: DbSession, dev: Device, kind: str, body: dict[str, Any], *, seq: int | None = None) -> None:
    if kind == "log":
        db.add(
            LogEntry(
                ts=_ts(body.get("ts")),
                device_id=dev.id,
                operation_id=_s(body.get("operation_id"), 32),
                trace_id=_s(body.get("trace_id"), 64),
                level=_s(body.get("level"), 8) or "info",
                source=_s(body.get("source"), 32) or "agent",
                message=str(body.get("message", ""))[:4000],
                attrs=body.get("attrs") if isinstance(body.get("attrs"), dict) else {},
            )
        )
    elif kind == "span":
        if not body.get("trace_id") or not body.get("span_id"):
            raise EvidenceError("span needs trace_id and span_id")
        try:
            db.add(
                Span(
                    trace_id=str(body["trace_id"])[:64],
                    span_id=str(body["span_id"])[:32],
                    parent_span_id=_s(body.get("parent_span_id"), 32),
                    device_id=dev.id,
                    operation_id=_s(body.get("operation_id"), 32),
                    name=str(body.get("name", "span"))[:128],
                    kind=_s(body.get("kind"), 32) or "internal",
                    status=_s(body.get("status"), 16) or "ok",
                    start_ts=_ts(body.get("start_ts")),
                    duration_ms=finite(body.get("duration_ms")),
                    attrs=_finite_attrs(body.get("attrs")),
                )
            )
            db.flush()
        except IntegrityError:
            db.rollback() if False else None
            raise EvidenceError("duplicate span") from None
    elif kind == "telemetry":
        db.add(
            TelemetrySample(
                ts=_ts(body.get("ts")),
                device_id=dev.id,
                mem_total_mb=finite(body.get("mem_total_mb")),
                mem_available_mb=finite(body.get("mem_available_mb")),
                cpu_pct=finite(body.get("cpu_pct")),
                gpu_pct=finite(body.get("gpu_pct")),
                power_w=finite(body.get("power_w")),
                temp_max_c=finite(body.get("temp_max_c")),
                disk_free_mb=finite(body.get("disk_free_mb")),
                runtime_state=_s(body.get("runtime_state"), 32),
                clock_confidence=_s(body.get("clock_confidence"), 16) or "unknown",
                extra={},
            )
        )
    elif kind == "failure":
        if not body.get("code"):
            raise EvidenceError("failure needs code")
        db.add(
            Failure(
                id=new_id("fail"),
                ts=_ts(body.get("ts")),
                device_id=dev.id,
                operation_id=_s(body.get("operation_id"), 32),
                release_id=_s(body.get("release_id"), 32),
                trace_id=_s(body.get("trace_id"), 64),
                code=str(body["code"])[:64],
                stage=_s(body.get("stage"), 32),
                message=str(body.get("message", ""))[:2000],
                details=body.get("details") if isinstance(body.get("details"), dict) else {},
                simulated=dev.simulated,
            )
        )
    elif kind == "usage":
        day = _ts(body.get("ts")).strftime("%Y-%m-%d")
        schema = body.get("schema")
        interval = None
        diagnostics: list[tuple[int, float | None, str]] = []
        if schema is None:
            # Unversioned record. Across unversioned agents `online_minutes` / `connected_minutes` meant
            # process elapsed time or (loosely bounded) contact time and `active_minutes` meant "runtime
            # loaded" or inference busy time; the producer cannot be told apart, so the values are kept
            # under explicit MIXED names (meaning not attributable) and are never summed with a measured
            # population. Within that population `online_minutes` was an alias of `connected_minutes`
            # whenever both were sent, so one value is taken, never their sum. Only the invariant
            # counters and the two renames are taken from the body: a schema-2-only name (measured
            # minutes, contacts, unknown coverage, an unknown interval) in an unversioned record is
            # dropped, never stored as measured.
            online = body.get("online_minutes", body.get("connected_minutes"))
            body = {
                **{k: body.get(k) for k in USAGE_UNVERSIONED_METRICS},
                "mixed_online_minutes": online,
                "mixed_active_minutes": body.get("active_minutes"),
            }
        elif schema == USAGE_SCHEMA:
            if any(k in body for k in USAGE_UNVERSIONED_NAMES):
                raise EvidenceError("usage schema 2 record carries unversioned metric names")
            if any(k in body for k in USAGE_MIXED_NAMES) and body.get("legacy_checkpoint") is not True:
                # only the recovery of a pre-rework checkpoint may declare mixed durations explicitly
                raise EvidenceError(
                    "usage schema 2 record carries mixed metric names without legacy provenance"
                )
            interval = _unknown_interval(body)  # validated before anything is written
            diagnostics = _checkpoint_diagnostics(body)
        else:
            raise EvidenceError(f"unsupported usage record schema {schema!r}")
        if interval is not None:
            from_dt, to_dt, seconds, reason = interval
            db.add(
                UsageUnknownInterval(
                    device_id=dev.id,
                    from_ts=from_dt,
                    to_ts=to_dt,
                    seconds=seconds,
                    reason=reason,
                    previous_incarnation=_s(body.get("previous_incarnation"), 64),
                    record_seq=seq,
                    simulated=dev.simulated,
                    received_at=utcnow(),
                )
            )
        for n, anchor, scope in diagnostics:
            # diagnostic only, committed with the record (same transaction as the cursor advance)
            since = iso(datetime.fromtimestamp(anchor, tz=timezone.utc)) if anchor is not None else "unknown"
            incarnation = _s(body.get("previous_incarnation"), 64)
            tail = (
                f"reported by restart recovery of incarnation {incarnation or 'unknown'}"
                if scope == "previous_incarnation"
                else "(later recovered)"
            )
            db.add(
                LogEntry(
                    ts=_ts(body.get("ts")),
                    device_id=dev.id,
                    level="warning",
                    source="usage",
                    message=(
                        "usage checkpoint persistence lapsed on the device: "
                        f"{n} failed checkpoint writes since {since} {tail}"
                    ),
                    attrs={
                        "lane": "usage",
                        "record_seq": seq,
                        "checkpoint_failures": n,
                        "since_wall": anchor,
                        "scope": scope,
                        "previous_incarnation": incarnation,
                        "schema": USAGE_SCHEMA,
                    },
                )
            )
        for metric in USAGE_METRICS:
            v = finite(body.get(metric))
            if v is None or v < 0:
                continue
            row = db.scalar(
                select(UsageDaily).where(
                    UsageDaily.day == day, UsageDaily.device_id == dev.id, UsageDaily.metric == metric
                )
            )
            if row is None:
                db.add(UsageDaily(day=day, device_id=dev.id, metric=metric, value=v, simulated=dev.simulated))
                db.flush()  # later records in the same batch must see this row
            else:
                row.value = (row.value or 0.0) + v
    elif kind == "eval_result":
        store_eval_result(db, dev, body)
    else:
        raise EvidenceError(f"unknown kind {kind}")


def _s(v: Any, n: int) -> str | None:
    return str(v)[:n] if isinstance(v, str) and v else None


def _finite_attrs(a: Any) -> dict[str, Any]:
    if not isinstance(a, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in list(a.items())[:64]:
        if isinstance(v, bool) or v is None or isinstance(v, str):
            out[str(k)[:64]] = v if not isinstance(v, str) else v[:512]
        elif isinstance(v, (int, float)):
            out[str(k)[:64]] = finite(v)
    return out


def _walk_finite(obj: Any, depth: int = 0) -> None:
    if depth > 10:
        raise EvidenceError("evidence nesting too deep")
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        raise EvidenceError("non-finite metric in evidence")
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_finite(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _walk_finite(v, depth + 1)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _contradictions(supplied: dict[str, Any], recomputed: dict[str, Any], prefix: str = "") -> list[str]:
    """Numeric leaves the device supplied that disagree with (or are unsupported by) the recomputation."""
    out: list[str] = []
    for k, v in supplied.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.extend(
                _contradictions(
                    v, recomputed.get(k) if isinstance(recomputed.get(k), dict) else {}, key + "."
                )
            )
        elif _num(v):
            r = recomputed.get(k)
            if not _num(r):
                if k in ("samples", "requests", "attempts"):
                    continue
                out.append(f"{key}: supplied {v} but no structured evidence supports it")
            elif abs(float(v) - float(r)) > SUMMARY_TOLERANCE * max(1.0, abs(float(r))):
                out.append(f"{key}: supplied {v}, recomputed {r}")
    return out


def _validate_timings(timings: Any, case_ids: list[str]) -> list[dict[str, Any]]:
    if timings is None:
        return []
    if not isinstance(timings, list) or len(timings) > MAX_TIMINGS:
        raise EvidenceError("timings must be a bounded list")
    out = []
    for t in timings:
        if not isinstance(t, dict):
            raise EvidenceError("each timing must be an object")
        for k in ("latency_ms", "ttft_ms", "queue_ms", "tok_s"):
            if t.get(k) is not None and not _num(t[k]):
                raise EvidenceError(f"timing {k} must be numeric")
        out.append(t)
    ids = sorted(str(t.get("case_id")) for t in out)
    if ids != sorted(case_ids):
        raise EvidenceError("timings must correspond one-to-one with the scored cases")
    return out


def _validate_sensors(samples: Any) -> list[dict[str, Any]]:
    if samples is None:
        return []
    if not isinstance(samples, list) or len(samples) > MAX_SENSOR_SAMPLES:
        raise EvidenceError("sensor_samples must be a bounded list")
    out = []
    for s in samples:
        if not isinstance(s, dict):
            raise EvidenceError("each sensor sample must be an object")
        for k in ("mem_used_mb", "mem_available_mb", "temp_max_c", "power_w", "gpu_pct"):
            if s.get(k) is not None and not _num(s[k]):
                raise EvidenceError(f"sensor {k} must be numeric")
        out.append(s)
    return out


def _bound_operation(
    db: DbSession, dev: Device, body: dict[str, Any], plan: Plan, rel: Release
) -> Operation | None:
    """R44: an eval result may cite an operation only when it is this device's deploy/eval operation for
    the same plan, release and generation. Foreign operations refuse the batch (403); mismatches reject
    the record. Nothing is written before these checks."""
    op_id = _s(body.get("operation_id"), 32)
    if not op_id:
        return None
    op = db.get(Operation, op_id)
    if op is None:
        raise EvidenceError("eval result cites an unknown operation")
    if op.device_id != dev.id:
        raise EvidenceError("eval result cites another device's operation", 403)
    if op.type not in ("deploy", "eval"):
        raise EvidenceError(f"eval result cites a {op.type} operation")
    if op.plan_id != plan.id:
        raise EvidenceError("eval result plan does not match the operation's plan")
    if op.release_id != rel.id:
        raise EvidenceError("eval result release does not match the operation's release")
    gen = body.get("generation")
    if isinstance(gen, bool) or not isinstance(gen, int) or gen != op.generation:
        raise EvidenceError("eval result must cite the operation's generation")
    return op


def store_eval_result(db: DbSession, dev: Device, body: dict[str, Any]) -> EvalResult:
    """Validate against the plan's eval set, re-score where possible, recompute EVERY aggregate from the
    structured records (cases, timings, sensor samples) and the verdict with the shared evaluator (R20,
    R45). A supplied aggregate that contradicts the recomputation rejects the record; a metric with no
    structured evidence is unavailable (required gates on it -> inconclusive), never trusted."""
    _walk_finite(body)
    rid = _s(body.get("id"), 32)
    plan = db.get(Plan, body.get("plan_id")) if body.get("plan_id") else None
    if plan is None:
        raise EvidenceError("eval result cites an unknown plan")
    if plan.digest != body.get("plan_digest"):
        raise EvidenceError("plan digest mismatch")
    if plan.simulated != dev.simulated:
        raise EvidenceError("simulated/physical mismatch between plan and device")
    es = db.get(EvalSet, plan.eval_set_id)
    if es is None or es.digest != body.get("eval_set_digest"):
        raise EvidenceError("eval set digest mismatch")
    rel = db.get(Release, body.get("release_id")) if body.get("release_id") else None
    if rel is None or rel.id != plan.release_id or rel.digest != body.get("release_digest"):
        raise EvidenceError("release binding mismatch")
    op = _bound_operation(db, dev, body, plan, rel)
    if rid and db.get(EvalResult, rid):
        return db.get(EvalResult, rid)  # idempotent replay
    cases_in = body.get("cases") if isinstance(body.get("cases"), list) else []
    want_ids = [c["id"] for c in es.cases]
    got_ids = [c.get("id") for c in cases_in]
    if len(got_ids) != len(set(got_ids)):
        raise EvidenceError("duplicate case ids in evidence")
    if set(got_ids) - set(want_ids):
        raise EvidenceError("evidence contains cases not in the eval set")
    by_id = {c["id"]: c for c in es.cases}
    # independent re-scoring from structured records where the method allows
    rescored: list[dict[str, Any]] = []
    methods: dict[str, str] = {}
    for rec in cases_in:
        case = by_id[rec["id"]]
        server = rescore_from_record(case, rec)
        method = {
            "exact": "normalized_hash",
            "label": "normalized_hash",
            "any_of": "normalized_hash",
            "json_field": "extracted_field",
        }.get(case.get("match", "exact"), "device_attested")
        methods[rec["id"]] = (
            "independently_rescored"
            if method == "normalized_hash" and server is not None
            else (
                "structured_rescored"
                if method == "extracted_field" and server is not None
                else "device_attested"
            )
        )
        rescored.append(
            {
                **rec,
                "passed": bool(server) if server is not None else bool(rec.get("passed")),
                "device_passed": bool(rec.get("passed")),
                "server_method": methods[rec["id"]],
            }
        )
    timings = _validate_timings(body.get("timings"), [str(i) for i in got_ids])
    sensors = _validate_sensors(body.get("sensor_samples"))
    prov = body.get("provenance") if isinstance(body.get("provenance"), dict) else {}
    runtime_attested = prov.get("runtime") if isinstance(prov.get("runtime"), dict) else {}
    n_exp = len(want_ids)
    recomputed = summarize(
        rescored,
        timings,
        sensors,
        runtime_props={
            "gpu_offloaded_layers": runtime_attested.get("gpu_offloaded_layers"),
            "backend": runtime_attested.get("backend"),
        },
        expected_cases=n_exp,
    )
    supplied = body.get("summary") if isinstance(body.get("summary"), dict) else {}
    # quality/errors/coverage are re-derived from the case records (a device that lied there is recorded
    # as device_verdict vs server_verdict, R20); the PERFORMANCE aggregates must agree with the structured
    # timing/sensor records they claim to summarise, or the record is refused (R45)
    bad = _contradictions({k: v for k, v in supplied.items() if k in PERF_SECTIONS}, recomputed)
    if bad:
        raise EvidenceError("summary contradicts structured records: " + "; ".join(bad[:4]))
    summary = recomputed
    cov = body.get("coverage") or {}
    verdict, gates = evaluate(
        summary,
        plan.gates,
        complete=bool(cov.get("complete", len(rescored) == n_exp)) and len(rescored) == n_exp,
        cancelled=bool(cov.get("cancelled")),
    )
    if body.get("device_verdict") not in ("passed", "failed", "inconclusive"):
        raise EvidenceError("device verdict missing")
    for g in gates:
        src = METRICS.get(g.get("metric"), ("", ""))[1]
        if src == "runtime_props":
            g["evidence_method"] = "device_attested"
        elif g.get("status") == "unavailable":
            g["evidence_method"] = "unavailable"
        else:
            g["evidence_method"] = "server_recomputed"
    row = EvalResult(
        id=rid or new_id("evr"), device_id=dev.id, operation_id=op.id if op else None, release_id=rel.id, plan_id=plan.id, eval_set_id=es.id,
        device_verdict=body["device_verdict"], server_verdict=verdict, gates=gates, summary=summary, cases=rescored,
        coverage={**cov, "expected": n_exp, "scored": len(rescored), "completed": summary["coverage"]["completed"], "case_methods": methods, "evaluator_version": EVALUATOR_VERSION,
                  "timings": len(timings), "sensor_samples": len(sensors), "generation": op.generation if op else None},
        provenance=prov, simulated=dev.simulated, baseline=(body.get("stage") == "baseline"), stage=_s(body.get("stage"), 16) or "eval",
    )  # fmt: skip
    db.add(row)
    db.flush()
    if op is not None:
        op.progress = {**(op.progress or {}), "eval_result_id": row.id, "server_verdict": verdict}
    return row


# ---------------------------------------------------------------- queries
def compare(db: DbSession, baseline: EvalResult, candidate: EvalResult) -> dict[str, Any]:
    reasons: list[str] = []
    bp, cp = db.get(Plan, baseline.plan_id), db.get(Plan, candidate.plan_id)
    if baseline.eval_set_id != candidate.eval_set_id:
        reasons.append("different eval sets")
    if baseline.simulated != candidate.simulated:
        reasons.append("simulated vs physical evidence")
    if bp and cp and bp.profile_id != cp.profile_id:
        reasons.append("different hardware profiles")
    if bp and cp and (bp.workload or {}) != (cp.workload or {}):
        reasons.append("workload differs (treatment change must be explicit)")
    bd, cd = baseline.device_id, candidate.device_id
    if bd != cd:
        reasons.append("different devices: per-device percentiles are shown side by side, never averaged")
    metrics = [
        "quality.pass_rate",
        "errors.count",
        "latency.p50_ms",
        "latency.p95_ms",
        "latency.ttft_p95_ms",
        "throughput.tok_s_mean",
        "memory.peak_used_mb",
        "thermal.max_c",
        "power.avg_w",
    ]

    def get(s: dict[str, Any], m: str) -> Any:
        cur: Any = s
        for part in m.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        return cur

    deltas = {}
    for m in metrics:
        b, c = get(baseline.summary, m), get(candidate.summary, m)
        d = None
        ratio = None
        if isinstance(b, (int, float)) and isinstance(c, (int, float)) and not isinstance(b, bool):
            d = c - b
            ratio = (c / b) if b not in (0, 0.0) else None  # no ratio on a zero baseline
        deltas[m] = {"baseline": b, "candidate": c, "delta": d, "ratio": ratio}
    return {
        "comparable": not reasons,
        "reasons": reasons,
        "baseline": {
            "id": baseline.id,
            "device_id": bd,
            "release_id": baseline.release_id,
            "verdict": baseline.server_verdict,
        },
        "candidate": {
            "id": candidate.id,
            "device_id": cd,
            "release_id": candidate.release_id,
            "verdict": candidate.server_verdict,
        },
        "deltas": deltas,
    }


UNKNOWN_INTERVALS_LIMIT = 200  # newest overlapping `unknown_intervals` returned by /usage per call


def usage(db: DbSession, day_from: str, day_to: str) -> dict[str, Any]:
    rows = list(db.scalars(select(UsageDaily).where(UsageDaily.day >= day_from, UsageDaily.day <= day_to)))
    devices: dict[str, dict[str, Any]] = {}
    totals: dict[str, float] = {}
    days: dict[str, dict[str, float]] = {}
    for r in rows:
        d = devices.setdefault(
            r.device_id, {"device_id": r.device_id, "simulated": r.simulated, "metrics": {}}
        )
        d["metrics"][r.metric] = d["metrics"].get(r.metric, 0.0) + r.value
        totals[r.metric] = totals.get(r.metric, 0.0) + r.value
        days.setdefault(r.day, {})[r.metric] = days.setdefault(r.day, {}).get(r.metric, 0.0) + r.value
    for d in devices.values():
        d["name"] = None
    # server-derived: stored, validated eval results received in the range (all verdicts). Runs that never
    # reached the server are not counted; this is provenance "server-stored", distinct from device counters.
    from sqlalchemy import func

    lo = datetime.strptime(day_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    hi = datetime.strptime(day_to, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    received = db.execute(
        select(EvalResult.device_id, func.count(EvalResult.id))
        .where(EvalResult.created_at >= lo, EvalResult.created_at < hi)
        .group_by(EvalResult.device_id)
    ).all()
    for did, n in received:
        d = devices.setdefault(did, {"device_id": did, "simulated": None, "metrics": {}, "name": None})
        d["metrics"]["eval_results_received"] = int(n)
    for d in devices.values():
        d["metrics"].setdefault("eval_results_received", 0)  # the derived count is known (0) for every row
    totals["eval_results_received"] = float(sum(int(n) for _, n in received))
    # metadata for the whole union: usage rows and eval-only rows alike
    rows_ = (
        {d.id: d for d in db.scalars(select(Device).where(Device.id.in_(list(devices))))} if devices else {}
    )
    for did, d in devices.items():
        if did in rows_:
            d["name"], d["simulated"] = rows_[did].name, rows_[did].simulated
    losses = [
        {
            "device_id": x.device_id,
            "lane": x.lane,
            "from_seq": x.from_seq,
            "to_seq": x.to_seq,
            "reason": x.reason,
            "context": x.context,
            "reported_at": iso(x.reported_at),
        }
        for x in db.scalars(select(LossRange).order_by(LossRange.id.desc()).limit(200))
    ]
    lanes = [
        {
            "device_id": c.device_id,
            "lane": c.lane,
            "committed_seq": c.committed_seq,
            "updated_at": iso(c.updated_at),
        }
        for c in db.scalars(select(LaneCursor))
    ]
    # explicit unobserved intervals (schema-2 `unknown_interval`) overlapping the requested UTC days, as
    # the producer declared them; distinct from `loss_ranges` (transport loss, journal sequence spans).
    # The response is bounded to the newest UNKNOWN_INTERVALS_LIMIT rows (from_ts DESC, id DESC); the
    # audit rows themselves are never pruned, and the total is counted rather than loaded.
    overlap = (UsageUnknownInterval.from_ts < hi, UsageUnknownInterval.to_ts >= lo)
    unknown_total = int(db.scalar(select(func.count(UsageUnknownInterval.id)).where(*overlap)) or 0)
    unknown_rows = list(
        db.scalars(
            select(UsageUnknownInterval)
            .where(*overlap)
            .order_by(UsageUnknownInterval.from_ts.desc(), UsageUnknownInterval.id.desc())
            .limit(UNKNOWN_INTERVALS_LIMIT)
        )
    )
    missing = {x.device_id for x in unknown_rows} - set(rows_)
    if missing:
        rows_.update({d.id: d for d in db.scalars(select(Device).where(Device.id.in_(list(missing))))})
    unknown_intervals = [
        {
            "id": x.id,
            "device_id": x.device_id,
            "name": rows_[x.device_id].name if x.device_id in rows_ else None,
            "simulated": x.simulated,
            "from_ts": iso(x.from_ts),
            "to_ts": iso(x.to_ts),
            "seconds": x.seconds,
            "reason": x.reason,
            "previous_incarnation": x.previous_incarnation,
            "received_at": iso(x.received_at),
        }
        for x in unknown_rows
    ]
    return {
        "from": day_from,
        "to": day_to,
        "days": [{"day": k, **v} for k, v in sorted(days.items())],
        "devices": sorted(devices.values(), key=lambda x: x.get("name") or ""),
        "totals": totals,
        "coverage": {
            "loss_ranges": losses,
            "lanes": lanes,
            "unknown_intervals": unknown_intervals,
            "unknown_intervals_total": unknown_total,
            "unknown_intervals_limit": UNKNOWN_INTERVALS_LIMIT,
            "unknown_intervals_truncated": unknown_total > UNKNOWN_INTERVALS_LIMIT,
        },
        "note": "measured counters only (sums of device-reported deltas); eval_results_received is server-derived; no cost estimates or savings claims",
        "derived": {
            "eval_results_received": "server-stored eval results whose receipt time falls in the range (all verdicts)"
        },
    }


def apply_retention(db: DbSession, worker: Any = None) -> dict[str, int]:
    s = get_settings()
    now = utcnow()
    out = {}
    with write_txn(db):
        if worker is not None:
            worker.fenced(db)  # R53 follow-up: retention is a worker-owned mutation
        cutoff = now - timedelta(days=s.retention_detail_days)
        out["telemetry"] = db.execute(delete(TelemetrySample).where(TelemetrySample.ts < cutoff)).rowcount
        out["logs"] = db.execute(delete(LogEntry).where(LogEntry.ts < cutoff)).rowcount
        out["spans"] = db.execute(delete(Span).where(Span.start_ts < cutoff)).rowcount
        agg_cutoff = now - timedelta(days=s.retention_aggregate_days)
        from ..models import Report

        out["reports"] = db.execute(delete(Report).where(Report.receipt_ts < agg_cutoff)).rowcount
        from ..models import AuditLog

        out["audit"] = db.execute(
            delete(AuditLog).where(AuditLog.ts < now - timedelta(days=s.retention_audit_days))
        ).rowcount
        # coverage evidence (loss_ranges, usage_unknown_intervals) is not pruned: there is no retention
        # window for declared losses, and the unknown intervals follow the same rule
    return out
