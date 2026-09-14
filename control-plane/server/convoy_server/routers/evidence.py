from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, current_device, installation, require_role
from ..config import get_settings
from ..db import db_status, get_db
from ..ids import iso, utcnow
from ..models import (
    Backup,
    Device,
    EvalResult,
    Failure,
    LogEntry,
    Operation,
    Release,
    Rollout,
    Schedule,
    Span,
)
from ..serialize import device_status, eval_out
from ..services import evidence as ev
from ..services.operations import unsettled_predicate
from ..validation import reject_invalid_json_values

# The PROJECT's documented validation scope (docs/VERIFICATION.md §3, docs/JETSON_GUIDE.md §9), the same
# string on /overview and /settings. It says nothing about the installation or device being viewed:
# per-device runtime evidence and simulated/physical labels stay separate, and no device is qualified
# by it. Updated by hand from the operator's reported evidence, never computed.
HARDWARE_VALIDATION = (
    "Partial physical validation: one Orin Nano has measured CUDA/model and deadline results; the control "
    "plane's automatic backup is accepted with ten simulators; physical bounded shutdown and all-eleven live "
    "continuity remain pending. See the verification record."
)

router = APIRouter(prefix="/api", tags=["evidence"])


class SpoolIn(BaseModel):
    lane: str = Field(max_length=16)
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=ev.MAX_RECORDS)
    loss_ranges: list[dict[str, Any]] = Field(default_factory=list, max_length=64)


@router.post("/agent/v1/spool")
def spool(body: SpoolIn, dev: Device = Depends(current_device), db: DbSession = Depends(get_db)):
    # finite/nesting/size safeguards apply PER RECORD (and to the loss list): a full batch of ordinary
    # records is never rejected as a whole for its aggregate key count (a retained backlog must drain).
    # A single malformed record still 422s the batch; the agent isolates and disposes of it explicitly.
    for rec in body.records:
        reject_invalid_json_values(rec)
    reject_invalid_json_values(body.loss_ranges)
    from ..handlerlog import HandlerTimer

    with HandlerTimer(
        "spool", dev.id, lane=body.lane, records=len(body.records), loss_ranges=len(body.loss_ranges or [])
    ) as h:
        try:
            res = ev.ingest(db, dev, body.lane, body.records, body.loss_ranges)  # committed on return
        except ev.EvidenceError as e:
            raise HTTPException(e.status, str(e)) from e
        h.done(
            accepted=res.get("accepted"),
            committed_seq=res.get("committed_seq"),
            deferred=len(res.get("deferred") or []),
            rejected=len(res.get("rejected") or []),
        )
    return res


@router.get("/v1/evals")
def list_evals(
    device_id: str | None = None,
    release_id: str | None = None,
    plan_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    q = select(EvalResult)
    if device_id:
        q = q.where(EvalResult.device_id == device_id)
    if release_id:
        q = q.where(EvalResult.release_id == release_id)
    if plan_id:
        q = q.where(EvalResult.plan_id == plan_id)
    return [
        eval_out(e, brief=True) for e in db.scalars(q.order_by(EvalResult.created_at.desc()).limit(limit))
    ]


@router.get("/v1/evals/compare")
def compare_evals(
    baseline: str,
    candidate: str,
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    b, c = db.get(EvalResult, baseline), db.get(EvalResult, candidate)
    if not b or not c:
        raise HTTPException(404, "eval result not found")
    return ev.compare(db, b, c)


@router.get("/v1/evals/{eval_id}")
def get_eval(eval_id: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    e = db.get(EvalResult, eval_id)
    if not e:
        raise HTTPException(404, "eval result not found")
    return eval_out(e)


@router.get("/v1/failures")
def list_failures(
    device_id: str | None = None,
    code: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    q = select(Failure)
    if device_id:
        q = q.where(Failure.device_id == device_id)
    if code:
        q = q.where(Failure.code == code)
    return [
        {
            "id": f.id,
            "ts": iso(f.ts),
            "device_id": f.device_id,
            "operation_id": f.operation_id,
            "release_id": f.release_id,
            "trace_id": f.trace_id,
            "code": f.code,
            "stage": f.stage,
            "message": f.message,
            "details": f.details,
            "simulated": f.simulated,
        }
        for f in db.scalars(q.order_by(Failure.ts.desc()).limit(limit))
    ]


@router.get("/v1/traces/{trace_id}")
def get_trace(trace_id: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    spans = [
        {
            "trace_id": s.trace_id,
            "span_id": s.span_id,
            "parent_span_id": s.parent_span_id,
            "device_id": s.device_id,
            "operation_id": s.operation_id,
            "name": s.name,
            "kind": s.kind,
            "status": s.status,
            "start_ts": iso(s.start_ts),
            "duration_ms": s.duration_ms,
            "attrs": s.attrs,
        }
        for s in db.scalars(select(Span).where(Span.trace_id == trace_id).order_by(Span.start_ts))
    ]
    logs = [
        {
            "ts": iso(e.ts),
            "level": e.level,
            "source": e.source,
            "message": e.message,
            "attrs": e.attrs,
            "device_id": e.device_id,
        }
        for e in db.scalars(select(LogEntry).where(LogEntry.trace_id == trace_id).order_by(LogEntry.id))
    ]
    op = db.scalar(select(Operation).where(Operation.trace_id == trace_id))
    from ..serialize import operation_out

    if not spans and not logs and not op:
        raise HTTPException(404, "trace not found")
    return {
        "trace_id": trace_id,
        "spans": spans,
        "logs": logs,
        "operation": operation_out(op) if op else None,
    }


@router.get("/v1/logs")
def list_logs(
    device_id: str | None = None,
    operation_id: str | None = None,
    trace_id: str | None = None,
    level: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    q = select(LogEntry)
    if device_id:
        q = q.where(LogEntry.device_id == device_id)
    if operation_id:
        q = q.where(LogEntry.operation_id == operation_id)
    if trace_id:
        q = q.where(LogEntry.trace_id == trace_id)
    if level:
        q = q.where(LogEntry.level == level)
    rows = list(db.scalars(q.order_by(LogEntry.id.desc()).limit(limit)))
    return [
        {
            "id": e.id,
            "ts": iso(e.ts),
            "device_id": e.device_id,
            "operation_id": e.operation_id,
            "trace_id": e.trace_id,
            "level": e.level,
            "source": e.source,
            "message": e.message,
            "attrs": e.attrs,
        }
        for e in reversed(rows)
    ]


@router.get("/v1/usage")
def get_usage(
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    today = utcnow().strftime("%Y-%m-%d")
    day_from = from_ or (utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    day_to = to or today
    for d in (day_from, day_to):
        if len(d) != 10 or d[4] != "-" or d[7] != "-":
            raise HTTPException(422, "dates must be YYYY-MM-DD")
    return ev.usage(db, day_from, day_to)


@router.get("/v1/overview")
def overview(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    devs = list(db.scalars(select(Device)))
    by = {
        "total": len(devs),
        "online": 0,
        "offline": 0,
        "never_seen": 0,
        "credential_revoked": 0,
        "retired": 0,
        "simulated": sum(1 for d in devs if d.simulated),
    }
    for d in devs:
        by[device_status(d)] = by.get(device_status(d), 0) + 1
    # current operational state: a reconciled/released abandonment keeps its historical status but is
    # no longer unsettled (the same predicate backs `/operations?unsettled=true`)
    unsettled = db.scalar(select(func.count()).select_from(Operation).where(unsettled_predicate())) or 0
    uncertain = (
        db.scalar(
            select(func.count())
            .select_from(Operation)
            .where(unsettled_predicate(), Operation.status.in_(("uncertain", "abandoned_unconfirmed")))
        )
        or 0
    )
    active_rollouts = (
        db.scalar(
            select(func.count())
            .select_from(Rollout)
            .where(Rollout.status.in_(("canary", "awaiting_promotion", "expanding", "paused")))
        )
        or 0
    )
    failures_24h = (
        db.scalar(
            select(func.count()).select_from(Failure).where(Failure.ts >= utcnow() - timedelta(hours=24))
        )
        or 0
    )
    rel_total = db.scalar(select(func.count()).select_from(Release)) or 0
    rel_ready = (
        db.scalar(
            select(func.count())
            .select_from(Release)
            .where(Release.build_status == "ready", Release.retired_at.is_(None))
        )
        or 0
    )
    sched = [
        {
            "id": s.id,
            "name": s.name,
            "kind": s.kind,
            "next_run_at": iso(s.next_run_at),
            "next_civil": s.next_civil,
            "timezone": s.timezone,
            "enabled": s.enabled,
            "paused_at": iso(s.paused_at),
        }
        for s in db.scalars(
            select(Schedule)
            .where(Schedule.enabled.is_(True), Schedule.paused_at.is_(None))  # the scheduler's own predicate
            .order_by(Schedule.next_run_at)
            .limit(5)
        )
    ]
    inst = installation(db)
    return {
        "devices": by,
        "operations": {"unsettled": unsettled, "uncertain": uncertain},
        "rollouts": {"active": active_rollouts},
        "failures_24h": failures_24h,
        "schedules_next": sched,
        "releases": {"total": rel_total, "deployable": rel_ready},
        "simulator": get_settings().simulator,
        "quarantined_at": iso(inst.quarantined_at),
        "dispatch_paused_at": iso(inst.dispatch_paused_at),
        "hardware_validation": HARDWARE_VALIDATION,
    }


@router.get("/v1/settings")
def settings_view(p: Principal = Depends(require_role("viewer"))):
    s = get_settings()
    return {
        "retention": {
            "detail_days": s.retention_detail_days,
            "aggregate_days": s.retention_aggregate_days,
            "audit_days": s.retention_audit_days,
        },
        "grant_ttl_s": s.grant_ttl_s,
        "offline_after_s": s.offline_after_s,
        "heartbeat_interval_s": s.heartbeat_interval_s,
        "simulator": s.simulator,
        "public_url": s.public_url,
        "sqlite": db_status(),
        "artifact_quota_bytes": s.artifact_quota_bytes,
        "max_upload_bytes": s.max_upload_bytes,
        "hardware_validation": HARDWARE_VALIDATION,
    }


@router.get("/v1/docs/jetson-guide")
def jetson_guide(p: Principal = Depends(require_role("viewer"))):
    for cand in (
        Path(__file__).resolve().parents[3] / "docs" / "JETSON_GUIDE.md",
        Path(__file__).resolve().parents[1] / "docs" / "JETSON_GUIDE.md",
    ):
        if cand.exists():
            return {"markdown": cand.read_text(), "source": str(cand.name)}
    return {"markdown": "# Jetson guide\n\nThe guide file is not bundled in this build.", "source": None}


@router.get("/v1/admin/backups")
def list_backups(p: Principal = Depends(require_role("admin")), db: DbSession = Depends(get_db)):
    return [
        {
            "id": b.id,
            "path": b.path,
            "size": b.size,
            "sha256": b.sha256,
            "created_at": iso(b.created_at),
            "duration_ms": b.duration_ms,
        }
        for b in db.scalars(select(Backup).order_by(Backup.created_at.desc()).limit(50))
    ]


@router.post("/v1/admin/backups", status_code=201)
def take_backup_now(p: Principal = Depends(require_role("admin")), db: DbSession = Depends(get_db)):
    from ..services.backup import take_backup

    return take_backup(db, None)
