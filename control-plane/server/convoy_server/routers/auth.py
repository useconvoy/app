from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, current_principal, end_session, installation, start_session
from ..config import get_settings
from ..db import get_db
from ..ids import iso
from ..models import User
from ..schemas import LoginIn
from ..security import verify_password
from ..serialize import user_out
from ..services.identity import IdentityError, throttle_check

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: DbSession = Depends(get_db)):
    s = get_settings()
    email = body.email.strip().lower()
    try:
        throttle_check(db, f"login:{email}", s.login_throttle, 900)
        throttle_check(
            db, f"login-ip:{request.client.host if request.client else 'unknown'}", s.login_throttle * 5, 900
        )
    except IdentityError as e:
        raise HTTPException(e.status, str(e)) from e
    user = db.scalar(select(User).where(User.email == email))
    if not user or user.disabled:
        raise HTTPException(401, "invalid email or password")
    verified_hash = user.password_hash
    if not verify_password(body.password, verified_hash):
        raise HTTPException(401, "invalid email or password")
    start_session(db, user, response, verified_hash=verified_hash)
    return {"user": user_out(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)):
    end_session(db, request, response)
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(current_principal), db: DbSession = Depends(get_db)):
    inst = installation(db)
    s = get_settings()
    return {
        "user": user_out(p.user),
        "via": p.via,
        "installation": {
            "simulator": s.simulator,
            "quarantined_at": iso(inst.quarantined_at),
            "quarantine_reason": inst.quarantine_reason,
            "dispatch_paused_at": iso(inst.dispatch_paused_at),
            "dispatch_pause_reason": inst.dispatch_pause_reason,
            "public_url": s.public_url,
        },
    }
