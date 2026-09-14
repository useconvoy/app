"""Authentication and authorization dependencies.

Rules: auth helpers never commit route-unrelated mutations on the route session. Role checks happen
server-side on every route via `require_role`. Browser sessions must add the `X-Convoy-Client: web`
header on non-GET requests (CSRF: SameSite=Lax cookie + custom header)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .config import get_settings
from .db import get_db, session_scope, write_txn
from .ids import aware, new_id, utcnow
from .models import ApiToken, AuditLog, Device, Installation, Session, User
from .redaction import redact
from .security import (
    device_secret_from_credential,
    hash_token,
    make_session_token,
    parse_device_credential,
    role_at_least,
)

SESSION_COOKIE = "convoy_session"
CLIENT_HEADER = "x-convoy-client"


@dataclass
class Principal:
    user: User
    via: str  # session | token
    token_id: str | None = None


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return None


def load_principal(request: Request, db: DbSession) -> Principal | None:
    now = utcnow()
    tok = _bearer(request)
    if tok and tok.startswith("cva_"):
        row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(tok)))
        if not row or row.revoked_at or (row.expires_at and aware(row.expires_at) < now):
            return None
        user = db.get(User, row.user_id)
        if not user or user.disabled:
            return None
        _touch_token(row.id, now)
        return Principal(user=user, via="token", token_id=row.id)
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        row = db.scalar(select(Session).where(Session.token_hash == hash_token(cookie)))
        if not row or row.revoked_at or aware(row.expires_at) < now:
            return None
        user = db.get(User, row.user_id)
        if not user or user.disabled:
            return None
        return Principal(user=user, via="session", token_id=row.id)
    return None


def _touch_token(token_id: str, now) -> None:
    """Best-effort last_used update in its own short transaction, never on the route session."""
    try:
        with session_scope() as s2:
            row = s2.get(ApiToken, token_id)
            if row and (row.last_used_at is None or (now - aware(row.last_used_at)) > timedelta(minutes=5)):
                with write_txn(s2):
                    row.last_used_at = now
    except Exception:  # pragma: no cover - never fail auth on bookkeeping
        pass


def current_principal(request: Request, db: DbSession = Depends(get_db)) -> Principal:
    p = load_principal(request, db)
    if p is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    return p


def optional_principal(request: Request, db: DbSession = Depends(get_db)) -> Principal | None:
    return load_principal(request, db)


def require_role(role: str) -> Callable[..., Principal]:
    def dep(request: Request, p: Principal = Depends(current_principal)) -> Principal:
        if not role_at_least(p.user.role, role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role {role}")
        if request.method not in ("GET", "HEAD", "OPTIONS") and p.via == "session":
            if request.headers.get(CLIENT_HEADER) != "web":
                raise HTTPException(status.HTTP_403_FORBIDDEN, "missing X-Convoy-Client header")
        return p

    return dep


def start_session(db: DbSession, user: User, response: Response, verified_hash: str | None = None) -> str:
    """R5: the session is created only if, inside the write transaction, the user is still enabled and
    still has the exact password hash that was verified."""
    s = get_settings()
    tok, h = make_session_token()
    with write_txn(db):
        fresh = db.get(User, user.id)
        if fresh is not None:
            db.refresh(fresh)
        if (
            fresh is None
            or fresh.disabled
            or (verified_hash is not None and fresh.password_hash != verified_hash)
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "credentials changed; sign in again")
        db.add(
            Session(
                id=new_id("ses"),
                user_id=user.id,
                token_hash=h,
                expires_at=utcnow() + timedelta(seconds=s.session_ttl_s),
            )
        )
    response.set_cookie(
        SESSION_COOKIE,
        tok,
        max_age=s.session_ttl_s,
        httponly=True,
        samesite="lax",
        secure=s.secure_cookies,
        path="/",
    )
    return tok


def end_session(db: DbSession, request: Request, response: Response) -> None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        row = db.scalar(select(Session).where(Session.token_hash == hash_token(cookie)))
        if row:
            with write_txn(db):
                row.revoked_at = utcnow()
    response.delete_cookie(SESSION_COOKIE, path="/")


def assert_live_principal(db: DbSession, p: Principal, role: str = "viewer") -> User:
    """R13: inside a write transaction, re-read the user AND the exact authenticating credential
    (session or API token). Credential issuance and recovery controls call this so a principal revoked
    mid-flight can never mint replacements."""
    now = utcnow()
    user = db.get(User, p.user.id)
    if user is not None:
        db.refresh(user)
    if user is None or user.disabled or not role_at_least(user.role, role):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authorization no longer valid")
    if p.via == "session":
        row = db.get(Session, p.token_id) if p.token_id else None
        if row is not None:
            db.refresh(row)
        if row is None or row.revoked_at or aware(row.expires_at) < now or row.user_id != user.id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session no longer valid")
    else:
        row = db.get(ApiToken, p.token_id) if p.token_id else None
        if row is not None:
            db.refresh(row)
        if (
            row is None
            or row.revoked_at
            or (row.expires_at and aware(row.expires_at) < now)
            or row.user_id != user.id
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token no longer valid")
    return user


def audit(db: DbSession, actor: Principal | None, action: str, target: str | None = None, **details) -> None:
    """Adds an audit row to the caller's open write transaction (does not commit)."""
    db.add(
        AuditLog(
            actor_id=actor.user.id if actor else None,
            actor_email=actor.user.email if actor else None,
            action=action,
            target=target,
            details=redact(details),
        )
    )


def installation(db: DbSession) -> Installation:
    row = db.get(Installation, 1)
    if row is None:
        with write_txn(db):
            row = db.get(Installation, 1)
            if row is None:
                row = Installation(id=1)
                db.add(row)
    return row


def current_device(request: Request, db: DbSession = Depends(get_db)) -> Device:
    tok = _bearer(request)
    if not tok or not tok.startswith("cvd_"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "device credential required")
    device_id = parse_device_credential(tok)
    if not device_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "malformed device credential")
    dev = db.get(Device, device_id)
    if not dev or not dev.credential_hash or dev.credential_revoked_at or dev.retired_at:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "device credential revoked or unknown")
    secret = device_secret_from_credential(tok)
    if not secret or dev.credential_hash != hash_token(secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid device credential")
    # the admitted binding: every device-facing write re-validates it inside its own transaction
    dev.__dict__["_admitted"] = {
        "credential_hash": dev.credential_hash,
        "binding_epoch": dev.binding_epoch or 1,
    }
    return dev


class StaleBinding(Exception):
    """The device credential/binding admitted at request start is no longer the current one."""


def assert_admitted_binding(db: DbSession, dev: Device) -> Device:
    """Call inside a write transaction (restore/rebind race): re-read the device row and refuse to
    mutate anything if its credential or binding epoch changed since the request was admitted."""
    admitted = dev.__dict__.get("_admitted")
    fresh = db.get(Device, dev.id)
    db.refresh(fresh)
    if (
        fresh is None
        or fresh.credential_revoked_at
        or fresh.retired_at
        or (
            admitted
            and (
                fresh.credential_hash != admitted["credential_hash"]
                or (fresh.binding_epoch or 1) != admitted["binding_epoch"]
            )
        )
    ):
        raise StaleBinding("device credential or binding changed during the request")
    return fresh
