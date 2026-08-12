"""Secrets: backends, not a vault.

The row in the `secrets` table is a *reference*; only a backend can turn it
into a value, and the only callers of `reveal` are the gateway (credential
injection) and the fill sidecar lease endpoint. Nothing in console_api ever
reads a value back — write-only is enforced by this module having exactly one
read path, not by convention in each route.
"""

from __future__ import annotations

import abc
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from ..db.tables import AuditLog, Secret


class SecretsBackend(abc.ABC):
    name: str = ""

    @abc.abstractmethod
    def store(self, row: Secret, value: str) -> None:
        """Write the value into the backend, updating `row` in place
        (ciphertext for builtin, backend_ref for external)."""

    @abc.abstractmethod
    def reveal(self, row: Secret) -> str: ...


class SecretsService:
    """The one place secret values pass through. In-memory reveal cache with
    short TTL; invalidated on rotation by key_version bump."""

    def __init__(self, session_factory, backends: Dict[str, SecretsBackend], cache_ttl_s: int = 300) -> None:
        self._sf = session_factory
        self._backends = backends
        self._ttl = cache_ttl_s
        self._cache: Dict[str, tuple] = {}  # secret_id → (key_version, expires_at, value)

    def _backend(self, name: str) -> SecretsBackend:
        try:
            return self._backends[name]
        except KeyError:
            raise ValueError("unknown secrets backend: %s" % name)

    def create(self, organization_id: str, name: str, value: str, backend: str = "builtin",
               created_by: Optional[str] = None) -> str:
        row = Secret(id="sec_" + uuid.uuid4().hex[:20], organization_id=organization_id,
                     name=name, backend=backend, key_version=1, created_by=created_by)
        self._backend(backend).store(row, value)
        with self._sf() as session:
            session.add(row)
            session.add(AuditLog(organization_id=organization_id, actor_user_id=created_by,
                                 action="secret.create", subject_type="secret", subject_id=row.id))
            session.commit()
        return row.id

    def rotate(self, secret_id: str, value: str, actor: Optional[str] = None) -> None:
        with self._sf() as session:
            row = session.get(Secret, secret_id)
            if row is None:
                raise KeyError(secret_id)
            row.key_version += 1
            row.rotated_at = datetime.now(timezone.utc)
            self._backend(row.backend).store(row, value)
            session.add(AuditLog(organization_id=row.organization_id, actor_user_id=actor,
                                 action="secret.rotate", subject_type="secret", subject_id=row.id))
            session.commit()
        self._cache.pop(secret_id, None)

    def reveal(self, secret_id: str) -> str:
        """Gateway/sidecar only. Never expose through console_api."""
        now = datetime.now(timezone.utc).timestamp()
        with self._sf() as session:
            row = session.get(Secret, secret_id)
            if row is None:
                raise KeyError(secret_id)
            cached = self._cache.get(secret_id)
            if cached and cached[0] == row.key_version and cached[1] > now:
                return cached[2]
            value = self._backend(row.backend).reveal(row)
        self._cache[secret_id] = (row.key_version, now + self._ttl, value)
        return value
