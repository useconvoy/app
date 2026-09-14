from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, audit, require_role
from ..db import get_db, write_txn
from ..models import Rollout
from ..services import rollouts as ro

router = APIRouter(prefix="/api/v1/rollouts", tags=["rollouts"])


class TargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_ids: list[str] | None = Field(default=None, max_length=64)
    group: str | None = Field(default=None, max_length=64)


class RolloutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    plan_id: str = Field(min_length=1, max_length=32)
    target: TargetIn
    canary_device_ids: list[str] | None = Field(default=None, max_length=64)


def _get(db: DbSession, rid: str) -> Rollout:
    r = db.get(Rollout, rid)
    if not r:
        raise HTTPException(404, "rollout not found")
    return r


def _err(e: ro.RolloutError) -> HTTPException:
    return HTTPException(e.status, str(e))


@router.get("")
def list_rollouts(
    status: str | None = None, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)
):
    q = select(Rollout).order_by(Rollout.created_at.desc())
    if status:
        q = q.where(Rollout.status.in_(status.split(",")))
    return [ro.rollout_out(r) for r in db.scalars(q.limit(200))]


@router.post("", status_code=201)
def create_rollout(
    body: RolloutIn, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    try:
        with write_txn(db):
            r = ro.create_rollout(db, body.model_dump(), p.user.email)
            audit(db, p, "rollout.create", r.id, plan_id=body.plan_id, targets=len(r.targets))
    except ro.RolloutError as e:
        raise _err(e) from e
    return ro.rollout_out(r)


@router.get("/{rid}")
def get_rollout(rid: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    r = _get(db, rid)
    try:
        ro.advance(db, r)  # idempotent refresh so the view is current even between worker ticks
    except Exception:
        pass
    return ro.rollout_out(_get(db, rid))


def _action(name: str, fn):
    def route(
        rid: str, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
    ) -> Any:
        r = _get(db, rid)
        try:
            out = fn(db, r, p.user.email)
            with write_txn(db):
                audit(db, p, f"rollout.{name}", r.id)
        except ro.RolloutError as e:
            raise _err(e) from e
        return out if isinstance(out, dict) and "rollout" in out else ro.rollout_out(_get(db, rid))

    route.__name__ = f"rollout_{name}"
    return route


router.post("/{rid}/start")(_action("start", ro.start))
router.post("/{rid}/promote")(_action("promote", ro.promote))
router.post("/{rid}/pause")(_action("pause", ro.pause))
router.post("/{rid}/resume")(_action("resume", ro.resume))
router.post("/{rid}/abort")(_action("abort", ro.abort))
router.post("/{rid}/restore")(_action("restore", ro.restore))
