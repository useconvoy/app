"""Builtin backend: envelope encryption with a local master key.

Each secret gets a fresh Fernet data-encryption key (DEK); the DEK is wrapped
by the master key (KEK). Dev/white-glove: KEK from CONVOY_MASTER_KEY (or a
key file). The KMS slot later replaces only `wrap`/`unwrap` — ciphertext at
rest never changes shape. This exists because OAuth tokens must live
somewhere; customer-owned backends (1Password, AWS SM, Vault) are the
preferred story and plug into the same SecretsBackend interface.
"""

from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet

from ..db.tables import Secret
from .backend import SecretsBackend


class MasterKey:
    def __init__(self, key: Optional[bytes] = None) -> None:
        raw = key or os.environ.get("CONVOY_MASTER_KEY", "").encode() or None
        if raw is None:
            raise RuntimeError(
                "CONVOY_MASTER_KEY is not set. Generate one with "
                "`convoy-environments generate-master-key` and export it."
            )
        self._fernet = Fernet(raw)

    @staticmethod
    def generate() -> str:
        return Fernet.generate_key().decode()

    def wrap(self, dek: bytes) -> bytes:
        return self._fernet.encrypt(dek)

    def unwrap(self, wrapped: bytes) -> bytes:
        return self._fernet.decrypt(wrapped)


class BuiltinBackend(SecretsBackend):
    name = "builtin"

    def __init__(self, master_key: MasterKey) -> None:
        self._mk = master_key

    def store(self, row: Secret, value: str) -> None:
        dek = Fernet.generate_key()
        row.ciphertext = Fernet(dek).encrypt(value.encode("utf-8"))
        row.dek_wrapped = self._mk.wrap(dek)
        row.backend_ref = None

    def reveal(self, row: Secret) -> str:
        if not row.ciphertext or not row.dek_wrapped:
            raise ValueError("secret %s has no builtin material" % row.id)
        dek = self._mk.unwrap(row.dek_wrapped)
        return Fernet(dek).decrypt(row.ciphertext).decode("utf-8")
