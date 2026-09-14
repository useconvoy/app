from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, require_role
from ..db import get_db
from ..ids import iso
from ..models import ApiToken, AuditLog, User
from ..schemas import ApiTokenCreate, UserCreate, UserUpdate
from ..serialize import token_out, user_out
from ..services import identity

router = APIRouter(prefix="/api/v1", tags=["users"])


def _err(e: identity.IdentityError) -> HTTPException:
    return HTTPException(e.status, str(e))


@router.get("/users")
def list_users(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.created_at))]


@router.post("/users", status_code=201)
def create_user(
    body: UserCreate, p: Principal = Depends(require_role("admin")), db: DbSession = Depends(get_db)
):
    try:
        return user_out(identity.create_user(db, p, body.email, body.name, body.role, body.password))
    except identity.IdentityError as e:
        raise _err(e) from e


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdate,
    p: Principal = Depends(require_role("admin")),
    db: DbSession = Depends(get_db),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    try:
        return user_out(identity.update_user(db, p, u, body.model_dump()))
    except identity.IdentityError as e:
        raise _err(e) from e


@router.get("/tokens")
def list_tokens(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    q = select(ApiToken).where(ApiToken.user_id == p.user.id).order_by(ApiToken.created_at.desc())
    return [token_out(t) for t in db.scalars(q)]


@router.post("/tokens", status_code=201)
def create_token(
    body: ApiTokenCreate, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)
):
    row, tok = identity.create_api_token(db, p, body.name, body.expires_in_days)
    return {**token_out(row), "token": tok}


@router.delete("/tokens/{token_id}")
def revoke_token(
    token_id: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)
):
    row = db.get(ApiToken, token_id)
    if not row or (row.user_id != p.user.id and p.user.role != "admin"):
        raise HTTPException(404, "token not found")
    identity.revoke_api_token(db, p, row)
    return {"ok": True}


@router.get("/audit")
def list_audit(
    limit: int = Query(200, ge=1, le=2000),
    p: Principal = Depends(require_role("admin")),
    db: DbSession = Depends(get_db),
):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    return [
        {
            "id": a.id,
            "ts": iso(a.ts),
            "actor_email": a.actor_email,
            "action": a.action,
            "target": a.target,
            "details": a.details,
        }
        for a in rows
    ]
