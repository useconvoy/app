"""Users, API tokens, throttles, enrollment (hash commitment + rebind), revocation, quarantine."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, assert_live_principal, audit
from ..config import get_settings
from ..db import write_txn
from ..ids import aware, new_id, utcnow
from ..models import ApiToken, AuditLog, Device, EnrollmentToken, Installation, Session, Throttle, User
from ..security import (
    INVALID_HASH_PREFIX,
    ROLES,
    hash_password,
    hash_token,
    make_api_token,
    make_enrollment_token,
)

HEX64 = 64


class IdentityError(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


# ---- throttles (persisted, no Redis) ----
def throttle_check(db: DbSession, key: str, limit: int, window_s: int) -> None:
    """Counts an attempt inside its own write transaction; raises 429 when over the limit."""
    now = utcnow()
    with write_txn(db):
        row = db.get(Throttle, key)
        if row is None:
            db.add(Throttle(key=key, window_start=now, count=1))
            return
        if (now - aware(row.window_start)).total_seconds() > window_s:
            row.window_start = now
            row.count = 1
            return
        row.count += 1
        if row.count > limit:
            raise IdentityError("too many attempts; try again later", 429)


# ---- users ----
def create_user(
    db: DbSession, actor: Principal | None, email: str, name: str, role: str, password: str
) -> User:
    if role not in ROLES:
        raise IdentityError(f"role must be one of {ROLES}")
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise IdentityError("email already exists", 409)
    u = User(id=new_id("usr"), email=email, name=name, role=role, password_hash=hash_password(password))
    with write_txn(db):
        if actor is not None:
            assert_live_principal(db, actor, "admin")
        db.add(u)
        audit(db, actor, "user.create", u.id, email=email, role=role)
    return u


def _enabled_admins_excluding(db: DbSession, user_id: str) -> int:
    return len(
        db.scalars(
            select(User.id).where(User.role == "admin", User.disabled.is_(False), User.id != user_id)
        ).all()
    )


def update_user(db: DbSession, actor: Principal, u: User, data: dict[str, Any]) -> User:
    with write_txn(db):
        # R4: re-validate the actor inside the transaction and keep at least one enabled admin
        assert_live_principal(db, actor, "admin")
        db.refresh(u)
        if data.get("disabled") is False and u.password_reset_required and not data.get("password"):
            raise IdentityError(
                "this account's credential was invalidated by a restore; set a new password to enable it", 409
            )
        removing_admin = (
            u.role == "admin"
            and not u.disabled
            and (data.get("disabled") is True or (data.get("role") is not None and data["role"] != "admin"))
        )
        if removing_admin and _enabled_admins_excluding(db, u.id) == 0:
            raise IdentityError("cannot remove the last enabled admin", 409)
        if data.get("role") is not None:
            if data["role"] not in ROLES:
                raise IdentityError("bad role")
            if u.id == actor.user.id and data["role"] != "admin":
                raise IdentityError("cannot demote yourself", 409)
            u.role = data["role"]
        if data.get("name") is not None:
            u.name = data["name"]
        if data.get("disabled") is not None:
            if u.id == actor.user.id and data["disabled"]:
                raise IdentityError("cannot disable yourself", 409)
            u.disabled = bool(data["disabled"])
            if u.disabled:
                _revoke_user_sessions(db, u.id)
        if data.get("password"):
            u.password_hash = hash_password(data["password"])
            u.password_reset_required = False
            _revoke_user_sessions(db, u.id)
        audit(db, actor, "user.update", u.id, fields=sorted(k for k, v in data.items() if v is not None))
    return u


def _revoke_user_sessions(db: DbSession, user_id: str) -> None:
    now = utcnow()
    db.execute(
        update(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None)).values(revoked_at=now)
    )
    db.execute(
        update(ApiToken)
        .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def create_api_token(
    db: DbSession, actor: Principal, name: str, expires_in_days: int | None
) -> tuple[ApiToken, str]:
    tok, prefix, h = make_api_token()
    row = ApiToken(
        id=new_id("tok"),
        user_id=actor.user.id,
        name=name,
        prefix=prefix,
        token_hash=h,
        expires_at=(utcnow() + timedelta(days=expires_in_days)) if expires_in_days else None,
    )
    with write_txn(db):
        assert_live_principal(db, actor, "viewer")
        db.add(row)
        audit(db, actor, "token.create", row.id, name=name)
    return row, tok


def revoke_api_token(db: DbSession, actor: Principal, row: ApiToken) -> None:
    with write_txn(db):
        row.revoked_at = utcnow()
        audit(db, actor, "token.revoke", row.id)


# ---- enrollment ----
def create_enrollment(
    db: DbSession,
    actor: Principal,
    *,
    label: str,
    group_name: str,
    simulated: bool,
    ttl_s: int | None,
    rebind_device_id: str | None,
) -> tuple[EnrollmentToken, str]:
    s = get_settings()
    if simulated and not s.simulator:
        raise IdentityError("simulated enrollment requires CONVOY_SIMULATOR=1", 409)
    if rebind_device_id:
        dev = db.get(Device, rebind_device_id)
        if not dev or dev.retired_at:
            raise IdentityError("rebind target device not found", 404)
        simulated = dev.simulated
        group_name = dev.group_name
    tok, h = make_enrollment_token()
    row = EnrollmentToken(
        id=new_id("enr"),
        token_hash=h,
        label=label,
        group_name=group_name,
        simulated=simulated,
        rebind_device_id=rebind_device_id,
        created_by=actor.user.id,
        expires_at=utcnow() + timedelta(seconds=ttl_s or s.enrollment_ttl_s),
    )
    with write_txn(db):
        assert_live_principal(db, actor, "operator")
        db.add(row)
        audit(
            db,
            actor,
            "enrollment.create",
            row.id,
            label=label,
            group=group_name,
            simulated=simulated,
            rebind=rebind_device_id,
        )
    return row, tok


def revoke_enrollment(db: DbSession, actor: Principal, row: EnrollmentToken) -> None:
    with write_txn(db):
        row.revoked_at = utcnow()
        audit(db, actor, "enrollment.revoke", row.id)


def enroll_device(
    db: DbSession,
    *,
    token: str,
    request_id: str,
    secret_hash: str,
    name: str,
    hardware: dict[str, Any],
    agent_version: str,
    simulated: bool,
    profile_id: str | None,
) -> dict[str, Any]:
    """One-use claim consumed in the same transaction as device creation (or rebinding).
    Lost-response retry: same token + request_id + secret_hash returns the same device id.
    Any other use of a consumed token is rejected; no new credential is ever exposed on replay."""
    if len(secret_hash) != HEX64 or any(c not in "0123456789abcdef" for c in secret_hash):
        raise IdentityError("secret_hash must be 64 lowercase hex chars")
    if not request_id or len(request_id) > 64:
        raise IdentityError("request_id required (<=64 chars)")
    now = utcnow()
    h = hash_token(token)
    with write_txn(db):
        row = db.scalar(select(EnrollmentToken).where(EnrollmentToken.token_hash == h))
        if not row or row.revoked_at:
            raise IdentityError("enrollment token unknown or revoked", 401)
        if row.consumed_at:
            if (
                row.consumed_request_id == request_id
                and row.consumed_secret_hash == secret_hash
                and row.device_id
            ):
                return {"device_id": row.device_id, "replay": True}
            raise IdentityError("enrollment token already consumed", 409)
        if aware(row.expires_at) < now:
            raise IdentityError("enrollment token expired", 410)
        if bool(simulated) != bool(row.simulated):
            raise IdentityError("simulated flag does not match the enrollment token", 409)
        if row.rebind_device_id:
            dev = db.get(Device, row.rebind_device_id)
            if not dev or dev.retired_at:
                raise IdentityError("rebind target missing", 409)
            dev.credential_hash = secret_hash
            dev.credential_issued_at = now
            dev.credential_revoked_at = None
            dev.credential_revoked_reason = ""
            dev.rebound_at = now
            # new credential = new liveness epoch (first report after rebinding is live; sequences restart)
            dev.live_nonce = None
            dev.live_nonce_issued_at = None
            dev.live_seq = 0
            dev.live_at = None
            dev.last_report_seq = 0
            dev.binding_epoch = (dev.binding_epoch or 1) + 1
            dev.agent_version = agent_version or dev.agent_version
            if hardware:
                dev.hardware = hardware
            device_id = dev.id
        else:
            device_id = new_id("dev")
            dev = Device(
                id=device_id,
                name=name or row.label or device_id,
                group_name=row.group_name,
                profile_id=profile_id or ("simulated-host" if row.simulated else "jetson-orin-nano-8gb"),
                simulated=row.simulated,
                credential_hash=secret_hash,
                credential_issued_at=now,
                agent_version=agent_version,
                hardware=hardware or {},
                settings={},
            )
            db.add(dev)
        row.consumed_at = now
        row.consumed_request_id = request_id
        row.consumed_secret_hash = secret_hash
        row.device_id = device_id
        audit(
            db, None, "device.enroll", device_id, rebind=bool(row.rebind_device_id), simulated=row.simulated
        )
    return {"device_id": device_id, "replay": False}


def revoke_device_credential(db: DbSession, actor: Principal | None, dev: Device, reason: str) -> None:
    with write_txn(db):
        dev.credential_revoked_at = utcnow()
        dev.credential_revoked_reason = reason[:64]
        audit(db, actor, "device.credential.revoke", dev.id, reason=reason)


# ---- quarantine (after restore) ----
def enter_quarantine(db: DbSession, reason: str, restored_from: dict[str, Any] | None = None) -> Installation:
    """Invalidate every human session, API token and device credential; pause dispatch."""
    now = utcnow()
    with write_txn(db):
        inst = db.get(Installation, 1)
        if inst is None:
            inst = Installation(id=1)
            db.add(inst)
        inst.quarantined_at = now
        inst.quarantine_reason = reason
        inst.dispatch_paused_at = now
        inst.dispatch_pause_reason = "quarantine"
        inst.restored_from = restored_from or {}
        db.execute(update(Session).where(Session.revoked_at.is_(None)).values(revoked_at=now))
        db.execute(update(ApiToken).where(ApiToken.revoked_at.is_(None)).values(revoked_at=now))
        db.execute(
            update(Device)
            .where(Device.credential_revoked_at.is_(None), Device.retired_at.is_(None))
            .values(credential_revoked_at=now, credential_revoked_reason="quarantine")
        )
        # R3: every outstanding enrollment/rebind capability from the snapshot is revoked too
        db.execute(
            update(EnrollmentToken)
            .where(EnrollmentToken.consumed_at.is_(None), EnrollmentToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        # R12: restored password hashes may predate resets/disables: disable every user AND make the
        # restored hashes unverifiable, so ordinary re-enabling can never reactivate an old credential
        for u in db.scalars(select(User)):
            u.disabled = True
            u.password_reset_required = True
            if not u.password_hash.startswith(INVALID_HASH_PREFIX):
                u.password_hash = (
                    INVALID_HASH_PREFIX + hashlib.sha256(u.password_hash.encode()).hexdigest()[:16]
                )
        db.add(AuditLog(action="installation.quarantine", details={"reason": reason}))
    return inst


def lift_quarantine(db: DbSession, actor: Principal) -> Installation:
    with write_txn(db):
        assert_live_principal(db, actor, "admin")
        inst = db.get(Installation, 1)
        if inst is None or inst.quarantined_at is None:
            raise IdentityError("not in quarantine", 409)
        pending = db.scalars(
            select(Device).where(Device.retired_at.is_(None), Device.credential_revoked_at.is_not(None))
        ).all()
        inst.quarantined_at = None
        inst.quarantine_reason = ""
        if inst.dispatch_pause_reason == "quarantine":
            inst.dispatch_paused_at = None
            inst.dispatch_pause_reason = ""
        audit(db, actor, "installation.quarantine.lift", None, devices_still_unbound=[d.id for d in pending])
    return inst


def set_dispatch_paused(
    db: DbSession, actor: Principal, paused: bool, reason: str = "operator"
) -> Installation:
    with write_txn(db):
        assert_live_principal(db, actor, "admin")
        inst = db.get(Installation, 1) or Installation(id=1)
        db.add(inst)
        inst.dispatch_paused_at = utcnow() if paused else None
        inst.dispatch_pause_reason = reason if paused else ""
        audit(db, actor, "dispatch.pause" if paused else "dispatch.resume", None, reason=reason)
    return inst


def recover_admin(db: DbSession, email: str, new_password: str) -> User:
    """Server-console recovery after a restore: set a NEW password and enable exactly this administrator.
    Dispatch stays paused (quarantine is not lifted here). Other users stay disabled until they get fresh
    credentials from this admin."""
    if len(new_password) < 12:
        raise IdentityError("recovery password must be at least 12 characters")
    email = email.strip().lower()
    with write_txn(db):
        inst = db.get(Installation, 1)
        if inst is None or inst.quarantined_at is None:
            raise IdentityError("recover-admin is only valid during restore quarantine", 409)
        u = db.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(
                id=new_id("usr"),
                email=email,
                name="Recovery admin",
                role="admin",
                password_hash=hash_password(new_password),
                disabled=False,
            )
            db.add(u)
        else:
            u.password_hash = hash_password(new_password)
            u.role = "admin"
            u.disabled = False
            u.password_reset_required = False
            _revoke_user_sessions(db, u.id)
        db.add(AuditLog(action="installation.recover_admin", target=u.id, details={"email": email}))
    return u
