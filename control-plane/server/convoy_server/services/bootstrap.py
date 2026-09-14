"""First-start bootstrap: installation row, admin user from env, simulator seed."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..config import Settings
from ..db import write_txn
from ..ids import new_id
from ..models import Installation, User
from ..security import hash_password

log = logging.getLogger("convoy.bootstrap")


def bootstrap(db: DbSession, settings: Settings) -> None:
    with write_txn(db):
        inst = db.get(Installation, 1)
        if inst is None:
            inst = Installation(id=1)
            db.add(inst)
        if inst.quarantined_at is not None:
            log.warning(
                "installation is in restore quarantine; bootstrap admin creation skipped (use `convoy-server recover-admin`)"
            )
        elif settings.bootstrap_admin_email and settings.bootstrap_admin_password:
            email = settings.bootstrap_admin_email.strip().lower()
            if not db.scalar(select(User).where(User.email == email)):
                db.add(
                    User(
                        id=new_id("usr"),
                        email=email,
                        name="Admin",
                        role="admin",
                        password_hash=hash_password(settings.bootstrap_admin_password),
                    )
                )
                log.info("bootstrap admin created: %s", email)
    if settings.simulator and settings.seed_simulator:
        try:
            from .seed import seed_simulator

            seed_simulator(db, settings)
        except ModuleNotFoundError:
            pass
