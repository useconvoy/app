from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, audit, require_role
from ..db import get_db, write_txn
from ..ids import iso, utcnow
from ..models import Device, EvalResult, Failure, LogEntry, Operation, Report, Span, TelemetrySample
from ..schemas import DeviceUpdate
from ..serialize import device_out, operation_out
from ..services import identity
from ..services import operations as ops

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


class DeployIn(BaseModel):
    release_id: str
    plan_id: str | None = None
    bootstrap: bool = False
    deadline_s: int | None = Field(default=None, ge=60, le=7 * 86400)


class EvalIn(BaseModel):
    plan_id: str
    baseline: bool = False


class ReasonIn(BaseModel):
    reason: str = ""


def _dev(db: DbSession, device_id: str) -> Device:
    d = db.get(Device, device_id)
    if not d:
        raise HTTPException(404, "device not found")
    return d


def _op_err(e: ops.OperationError) -> HTTPException:
    detail: dict[str, Any] = {"message": str(e)}
    if e.active is not None:
        detail["active_operation"] = operation_out(e.active)
    return HTTPException(e.status, detail)


@router.get("")
def list_devices(
    group: str | None = None, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)
):
    q = select(Device).order_by(Device.name)
    if group:
        q = q.where(Device.group_name == group)
    return [device_out(d, brief=True) for d in db.scalars(q)]


@router.get("/{device_id}")
def get_device(
    device_id: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)
):
    d = _dev(db, device_id)
    out = device_out(d)
    out["active_operation"] = (
        operation_out(db.get(Operation, d.active_operation_id))
        if d.active_operation_id and d.active_operation_id != "__pending__"
        else None
    )
    return out


@router.patch("/{device_id}")
def update_device(
    device_id: str,
    body: DeviceUpdate,
    p: Principal = Depends(require_role("operator")),
    db: DbSession = Depends(get_db),
):
    d = _dev(db, device_id)
    with write_txn(db):
        if body.name is not None:
            d.name = body.name[:255]
        if body.group_name is not None:
            d.group_name = body.group_name[:64]
        if body.notes is not None:
            d.notes = body.notes[:4000]
        if body.settings is not None:
            allowed = {
                k: v
                for k, v in body.settings.items()
                if k in ("robot_reserve_mb", "margin_mb", "maintenance_window", "labels")
            }
            d.settings = {**(d.settings or {}), **allowed}
        audit(db, p, "device.update", d.id, fields=[k for k, v in body.model_dump().items() if v is not None])
    return device_out(d)


@router.post("/{device_id}/deploy", status_code=201)
def deploy(
    device_id: str,
    body: DeployIn,
    p: Principal = Depends(require_role("operator")),
    db: DbSession = Depends(get_db),
):
    d = _dev(db, device_id)
    try:
        with write_txn(db):
            op = ops.create_operation(
                db,
                d,
                "deploy",
                {"release_id": body.release_id, "plan_id": body.plan_id, "bootstrap": body.bootstrap},
                created_by=p.user.email,
                deadline_s=body.deadline_s,
            )
            audit(db, p, "device.deploy", d.id, operation_id=op.id, release_id=body.release_id)
    except ops.OperationError as e:
        raise _op_err(e) from e
    return operation_out(op)


@router.post("/{device_id}/eval", status_code=201)
def run_eval(
    device_id: str,
    body: EvalIn,
    p: Principal = Depends(require_role("operator")),
    db: DbSession = Depends(get_db),
):
    d = _dev(db, device_id)
    try:
        with write_txn(db):
            op = ops.create_operation(
                db, d, "eval", {"plan_id": body.plan_id, "baseline": body.baseline}, created_by=p.user.email
            )
            audit(db, p, "device.eval", d.id, operation_id=op.id, plan_id=body.plan_id)
    except ops.OperationError as e:
        raise _op_err(e) from e
    return operation_out(op)


@router.post("/{device_id}/restore", status_code=201)
def restore(
    device_id: str,
    body: ReasonIn,
    p: Principal = Depends(require_role("operator")),
    db: DbSession = Depends(get_db),
):
    d = _dev(db, device_id)
    try:
        with write_txn(db):
            op = ops.create_operation(db, d, "recover", {"reason": body.reason}, created_by=p.user.email)
            audit(db, p, "device.restore", d.id, operation_id=op.id)
    except ops.OperationError as e:
        raise _op_err(e) from e
    return operation_out(op)


@router.post("/{device_id}/health", status_code=201)
def health_probe(
    device_id: str, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    d = _dev(db, device_id)
    try:
        with write_txn(db):
            op = ops.create_operation(db, d, "health", {}, created_by=p.user.email)
    except ops.OperationError as e:
        raise _op_err(e) from e
    return operation_out(op)


@router.post("/{device_id}/revoke-credential")
def revoke_credential(
    device_id: str,
    body: ReasonIn,
    p: Principal = Depends(require_role("admin")),
    db: DbSession = Depends(get_db),
):
    d = _dev(db, device_id)
    identity.revoke_device_credential(db, p, d, body.reason or "operator")
    return device_out(d)


@router.post("/{device_id}/retire")
def retire(device_id: str, p: Principal = Depends(require_role("admin")), db: DbSession = Depends(get_db)):
    d = _dev(db, device_id)
    with write_txn(db):
        d.retired_at = utcnow()
        if not d.credential_revoked_at:
            d.credential_revoked_at = utcnow()
            d.credential_revoked_reason = "retired"
        audit(db, p, "device.retire", d.id)
    return device_out(d)


@router.get("/{device_id}/operations")
def device_operations(
    device_id: str,
    limit: int = Query(50, ge=1, le=1000),
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    _dev(db, device_id)
    rows = db.scalars(
        select(Operation)
        .where(Operation.device_id == device_id)
        .order_by(Operation.created_at.desc())
        .limit(limit)
    )
    return [operation_out(o) for o in rows]


@router.get("/{device_id}/reports")
def device_reports(
    device_id: str,
    limit: int = Query(50, ge=1, le=1000),
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    _dev(db, device_id)
    rows = db.scalars(
        select(Report).where(Report.device_id == device_id).order_by(Report.id.desc()).limit(limit)
    )
    return [
        {
            "seq": r.seq,
            "boot_id": r.boot_id,
            "kind": r.kind,
            "source_ts": iso(r.source_ts),
            "receipt_ts": iso(r.receipt_ts),
            "applied": r.applied,
            "body": r.body,
        }
        for r in rows
    ]


@router.get("/{device_id}/telemetry")
def device_telemetry(
    device_id: str,
    limit: int = Query(500, ge=1, le=5000),
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    _dev(db, device_id)
    rows = db.scalars(
        select(TelemetrySample)
        .where(TelemetrySample.device_id == device_id)
        .order_by(TelemetrySample.ts.desc())
        .limit(limit)
    )
    return [
        {
            "ts": iso(t.ts),
            "mem_total_mb": t.mem_total_mb,
            "mem_available_mb": t.mem_available_mb,
            "cpu_pct": t.cpu_pct,
            "gpu_pct": t.gpu_pct,
            "power_w": t.power_w,
            "temp_max_c": t.temp_max_c,
            "disk_free_mb": t.disk_free_mb,
            "runtime_state": t.runtime_state,
            "clock_confidence": t.clock_confidence,
            "extra": t.extra,
        }  # fmt: skip
        for t in reversed(list(rows))
    ]


@router.get("/{device_id}/logs")
def device_logs(
    device_id: str,
    limit: int = Query(200, ge=1, le=2000),
    operation_id: str | None = None,
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    _dev(db, device_id)
    q = select(LogEntry).where(LogEntry.device_id == device_id)
    if operation_id:
        q = q.where(LogEntry.operation_id == operation_id)
    rows = db.scalars(q.order_by(LogEntry.id.desc()).limit(limit))
    return [
        {
            "ts": iso(e.ts),
            "level": e.level,
            "source": e.source,
            "message": e.message,
            "operation_id": e.operation_id,
            "trace_id": e.trace_id,
            "attrs": e.attrs,
        }
        for e in reversed(list(rows))
    ]


@router.get("/{device_id}/spans")
def device_spans(
    device_id: str,
    limit: int = Query(500, ge=1, le=5000),
    operation_id: str | None = None,
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    _dev(db, device_id)
    q = select(Span).where(Span.device_id == device_id)
    if operation_id:
        q = q.where(Span.operation_id == operation_id)
    rows = db.scalars(q.order_by(Span.id.desc()).limit(limit))
    return [
        {
            "trace_id": s.trace_id,
            "span_id": s.span_id,
            "parent_span_id": s.parent_span_id,
            "name": s.name,
            "kind": s.kind,
            "status": s.status,
            "start_ts": iso(s.start_ts),
            "duration_ms": s.duration_ms,
            "attrs": s.attrs,
            "operation_id": s.operation_id,
        }
        for s in reversed(list(rows))
    ]


@router.get("/{device_id}/failures")
def device_failures(
    device_id: str,
    limit: int = Query(100, ge=1, le=1000),
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    _dev(db, device_id)
    rows = db.scalars(
        select(Failure).where(Failure.device_id == device_id).order_by(Failure.ts.desc()).limit(limit)
    )
    return [
        {
            "id": f.id,
            "ts": iso(f.ts),
            "code": f.code,
            "stage": f.stage,
            "message": f.message,
            "details": f.details,
            "operation_id": f.operation_id,
            "release_id": f.release_id,
            "trace_id": f.trace_id,
            "simulated": f.simulated,
        }
        for f in rows
    ]


@router.get("/{device_id}/evals")
def device_evals(
    device_id: str,
    limit: int = Query(50, ge=1, le=1000),
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    from ..serialize import eval_out

    _dev(db, device_id)
    rows = db.scalars(
        select(EvalResult)
        .where(EvalResult.device_id == device_id)
        .order_by(EvalResult.created_at.desc())
        .limit(limit)
    )
    return [eval_out(e) for e in rows]
