from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, audit, require_role
from ..db import get_db, write_txn
from ..models import Operation
from ..serialize import operation_out
from ..services import operations as ops
from ..services.operations import unsettled_predicate

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


class ReasonIn(BaseModel):
    reason: str = ""


@router.get("")
def list_operations(
    status: str | None = None, type: str | None = None, device_id: str | None = None, rollout_id: str | None = None, limit: int = Query(100, ge=1, le=1000),
    unsettled: bool = False,
    p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db),
):  # fmt: skip
    q = select(Operation)
    if unsettled:  # the overview's drill-down: operations that currently hold their device
        q = q.where(unsettled_predicate())
    if status:
        q = q.where(Operation.status.in_(status.split(",")))
    if type:
        q = q.where(Operation.type == type)
    if device_id:
        q = q.where(Operation.device_id == device_id)
    if rollout_id:
        q = q.where(Operation.rollout_id == rollout_id)
    rows = db.scalars(q.order_by(Operation.created_at.desc()).limit(limit))
    return [operation_out(o) for o in rows]


@router.get("/{op_id}")
def get_operation(
    op_id: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)
):
    op = db.get(Operation, op_id)
    if not op:
        raise HTTPException(404, "operation not found")
    return operation_out(op)


@router.post("/{op_id}/cancel")
def cancel(op_id: str, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)):
    op = db.get(Operation, op_id)
    if not op:
        raise HTTPException(404, "operation not found")
    try:
        ops.request_cancel(db, op, p.user.email)
        with write_txn(db):
            audit(db, p, "operation.cancel", op.id, status=op.status)
    except ops.OperationError as e:
        raise HTTPException(e.status, str(e)) from e
    return {
        **operation_out(op),
        "note": "cancellation requested; if a grant was already consumed, activation may be in progress"
        if op.status != "cancelled"
        else "cancelled before grant",
    }


@router.post("/{op_id}/abandon")
def abandon(
    op_id: str, body: ReasonIn, p: Principal = Depends(require_role("admin")), db: DbSession = Depends(get_db)
):
    op = db.get(Operation, op_id)
    if not op:
        raise HTTPException(404, "operation not found")
    try:
        ops.abandon(db, op, p.user.email, body.reason)
        with write_txn(db):
            audit(db, p, "operation.abandon", op.id, reason=body.reason)
    except ops.OperationError as e:
        raise HTTPException(e.status, str(e)) from e
    return {
        **operation_out(op),
        "note": "abandoned (unconfirmed): the device reservation stays blocked until the device reconciles",
    }
