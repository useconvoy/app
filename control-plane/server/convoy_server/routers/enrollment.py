from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, require_role
from ..config import get_settings
from ..db import get_db
from ..models import EnrollmentToken
from ..schemas import EnrollmentCreate
from ..serialize import enrollment_out
from ..services import identity

router = APIRouter(prefix="/api/v1/enrollments", tags=["enrollment"])


@router.get("")
def list_enrollments(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    rows = db.scalars(select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc()).limit(200))
    return [enrollment_out(e) for e in rows]


@router.post("", status_code=201)
def create_enrollment(
    body: EnrollmentCreate, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    try:
        row, tok = identity.create_enrollment(
            db,
            p,
            label=body.label,
            group_name=body.group_name,
            simulated=body.simulated,
            ttl_s=body.ttl_s,
            rebind_device_id=body.rebind_device_id,
        )
    except identity.IdentityError as e:
        raise HTTPException(e.status, str(e)) from e
    s = get_settings()
    return {
        **enrollment_out(row),
        "token": tok,
        "server_url": s.public_url,
        "command": f"convoy-agent enroll --server {s.public_url} --token {tok} --name <device-name>"
        + (" --simulate" if row.simulated else ""),
    }


@router.delete("/{enrollment_id}")
def revoke_enrollment(
    enrollment_id: str, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    row = db.get(EnrollmentToken, enrollment_id)
    if not row:
        raise HTTPException(404, "not found")
    identity.revoke_enrollment(db, p, row)
    return {"ok": True}
