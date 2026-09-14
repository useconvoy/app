from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, audit, require_role
from ..db import get_db, write_txn
from ..ids import utcnow
from ..models import Occurrence, Schedule
from ..services import scheduler as sch

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


class ScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(max_length=16)
    cron: str = Field(min_length=9, max_length=64)
    timezone: str = Field(default="UTC", max_length=64)
    target: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    missed_policy: str = "skip"
    catchup_age_s: int = Field(default=3600, ge=0, le=7 * 86400)
    window_s: int = Field(default=600, ge=60, le=86400)
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=255)
    cron: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)
    target: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    missed_policy: str | None = None
    catchup_age_s: int | None = Field(default=None, ge=0, le=7 * 86400)
    window_s: int | None = Field(default=None, ge=60, le=86400)
    enabled: bool | None = None


def _get(db: DbSession, sid: str) -> Schedule:
    s = db.get(Schedule, sid)
    if not s:
        raise HTTPException(404, "schedule not found")
    return s


@router.get("")
def list_schedules(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    return [sch.schedule_out(s) for s in db.scalars(select(Schedule).order_by(Schedule.created_at.desc()))]


@router.post("", status_code=201)
def create_schedule(
    body: ScheduleIn, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    try:
        with write_txn(db):
            s = sch.create_schedule(db, body.model_dump(), p.user.id)
            audit(db, p, "schedule.create", s.id, kind=body.kind, cron=body.cron, tz=body.timezone)
    except sch.ScheduleError as e:
        raise HTTPException(e.status, str(e)) from e
    return sch.schedule_out(s)


@router.get("/{sid}")
def get_schedule(sid: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    return sch.schedule_out(_get(db, sid))


@router.patch("/{sid}")
def update_schedule(
    sid: str,
    body: ScheduleUpdate,
    p: Principal = Depends(require_role("operator")),
    db: DbSession = Depends(get_db),
):
    s = _get(db, sid)
    try:
        with write_txn(db):
            sch.update_schedule(db, s, body.model_dump(exclude_unset=True))
            audit(db, p, "schedule.update", s.id, revision=s.revision)
    except sch.ScheduleError as e:
        raise HTTPException(e.status, str(e)) from e
    return sch.schedule_out(s)


@router.post("/{sid}/pause")
def pause(sid: str, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)):
    s = _get(db, sid)
    with write_txn(db):
        s.paused_at = utcnow()
        s.pause_reason = "operator"
        audit(db, p, "schedule.pause", s.id)
    return sch.schedule_out(s)


@router.post("/{sid}/resume")
def resume(sid: str, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)):
    s = _get(db, sid)
    with write_txn(db):
        s.paused_at = None
        s.pause_reason = ""
        s.next_run_at, s.next_civil = sch.compute_next(s.cron, s.timezone, utcnow())
        audit(db, p, "schedule.resume", s.id)
    return sch.schedule_out(s)


@router.get("/{sid}/preview")
def preview(sid: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    return {"next": sch.preview(_get(db, sid))}


@router.get("/{sid}/occurrences")
def occurrences(sid: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    _get(db, sid)
    return [
        sch.occurrence_out(o)
        for o in db.scalars(
            select(Occurrence)
            .where(Occurrence.schedule_id == sid)
            .order_by(Occurrence.dispatched_at.desc())
            .limit(200)
        )
    ]
