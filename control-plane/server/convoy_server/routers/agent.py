from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from ..config import get_settings
from ..db import get_db
from ..ids import iso, utcnow
from ..schemas import EnrollIn
from ..services import identity

router = APIRouter(prefix="/api/agent/v1", tags=["agent"])


@router.post("/enroll")
def enroll(body: EnrollIn, request: Request, db: DbSession = Depends(get_db)):
    s = get_settings()
    try:
        identity.throttle_check(
            db, f"enroll-ip:{request.client.host if request.client else 'unknown'}", s.enroll_throttle, 3600
        )
        res = identity.enroll_device(
            db,
            token=body.enrollment_token,
            request_id=body.request_id,
            secret_hash=body.secret_hash.lower(),
            name=body.name,
            hardware=body.hardware,
            agent_version=body.agent_version,
            simulated=body.simulated,
            profile_id=body.profile_id,
        )
    except identity.IdentityError as e:
        raise HTTPException(e.status, str(e)) from e
    return {
        **res,
        "server_time": iso(utcnow()),
        "poll_interval_s": s.heartbeat_interval_s,
        "protocol_version": 1,
    }


from pydantic import BaseModel, Field  # noqa: E402

from ..auth import current_device  # noqa: E402
from ..models import Device, Operation  # noqa: E402
from ..services import operations as ops  # noqa: E402
from ..validation import reject_invalid_json_values  # noqa: E402


class ReportIn(BaseModel):
    seq: int = Field(ge=1)
    boot_id: str | None = Field(default=None, max_length=64)
    kind: str = Field(default="heartbeat", max_length=16)
    source_ts: str | None = Field(default=None, max_length=40)
    agent_version: str | None = Field(default=None, max_length=32)
    observed: dict = Field(default_factory=dict)
    hardware: dict | None = None
    telemetry: dict | None = None
    time_confidence: str | None = Field(default=None, max_length=16)
    challenge: str | None = Field(default=None, max_length=64)
    live_nonce: str | None = Field(default=None, max_length=64)


class GrantIn(BaseModel):
    nonce: str = Field(min_length=8, max_length=64)
    boot_id: str = Field(min_length=1, max_length=64)
    seq: int = Field(ge=1)
    active_release_id: str | None = None
    recovery_release_id: str | None = None
    release_digest: str | None = None
    plan_digest: str | None = None
    artifact_sha256: str | None = None


class OutcomeIn(BaseModel):
    status: str
    grant_id: str | None = None
    grant_consumed_seq: int | None = Field(default=None, ge=1)
    trace_id: str | None = Field(default=None, max_length=64)
    progress: dict | None = None
    result: dict | None = None
    failure: dict | None = None
    evidence: dict | None = None
    seq: int | None = Field(default=None, ge=1)


def _op_for(db: DbSession, dev: Device, op_id: str) -> Operation:
    op = db.get(Operation, op_id)
    if not op or op.device_id != dev.id:
        raise HTTPException(404, "operation not found")  # foreign device: not found, never a hint
    return op


@router.post("/report")
def report(body: ReportIn, dev: Device = Depends(current_device), db: DbSession = Depends(get_db)):
    data = body.model_dump()
    reject_invalid_json_values(data)
    from ..handlerlog import HandlerTimer

    seq = data.get("seq") if isinstance(data.get("seq"), int) else None
    with HandlerTimer("report", dev.id, seq=seq) as h:
        try:
            res = ops.apply_report(db, dev, data)  # returns after its write transaction committed
        except ops.OperationError as e:
            raise HTTPException(e.status, str(e)) from e
        h.done(applied=bool(res.get("applied")))
    return res


@router.post("/operations/{op_id}/grant")
def grant(op_id: str, body: GrantIn, dev: Device = Depends(current_device), db: DbSession = Depends(get_db)):
    op = _op_for(db, dev, op_id)
    try:
        return ops.issue_grant(db, dev, op, body.model_dump())
    except ops.OperationError as e:
        raise HTTPException(e.status, str(e)) from e


@router.post("/operations/{op_id}/outcome")
def outcome(
    op_id: str, body: OutcomeIn, dev: Device = Depends(current_device), db: DbSession = Depends(get_db)
):
    op = _op_for(db, dev, op_id)
    data = body.model_dump(exclude_none=True)
    reject_invalid_json_values(data)
    try:
        return ops.record_outcome(db, dev, op, data)
    except ops.OperationError as e:
        raise HTTPException(e.status, str(e)) from e
