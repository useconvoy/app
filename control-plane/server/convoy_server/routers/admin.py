from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, installation, require_role
from ..db import get_db
from ..ids import iso
from ..services import identity

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class PauseIn(BaseModel):
    paused: bool
    reason: str = "operator"


def _inst_out(inst) -> dict:
    return {
        "quarantined_at": iso(inst.quarantined_at),
        "quarantine_reason": inst.quarantine_reason,
        "dispatch_paused_at": iso(inst.dispatch_paused_at),
        "dispatch_pause_reason": inst.dispatch_pause_reason,
        "restored_from": inst.restored_from,
    }


@router.get("/installation")
def get_installation(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    return _inst_out(installation(db))


@router.post("/quarantine/lift")
def lift(p: Principal = Depends(require_role("admin")), db: DbSession = Depends(get_db)):
    try:
        return _inst_out(identity.lift_quarantine(db, p))
    except identity.IdentityError as e:
        raise HTTPException(e.status, str(e)) from e


@router.post("/dispatch")
def dispatch(body: PauseIn, p: Principal = Depends(require_role("admin")), db: DbSession = Depends(get_db)):
    return _inst_out(identity.set_dispatch_paused(db, p, body.paused, body.reason))
