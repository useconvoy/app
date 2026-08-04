"""Temporal payload codec — client-side encryption of every payload.

All Temporal payloads are encrypted (per-stack keys) before leaving workers;
combined with claim-checking, Temporal holds only ciphertext and refs. The
codec must stay ON in every environment including compose — there is no
plaintext fallback and no way to construct the runtime data converter without
a key.
"""

import base64
import dataclasses
import os
from collections.abc import Sequence

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from temporalio.api.common.v1 import Payload
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import DataConverter, PayloadCodec

ENCODING = b"binary/encrypted"
_NONCE_SIZE = 12
# The stack injects CONVOY_CODEC_KEY_B64 (from the per-stack secret); the
# older CONVOY_CODEC_KEY spelling remains a fallback for local tooling.
CODEC_KEY_ENV = "CONVOY_CODEC_KEY_B64"
CODEC_KEY_ENV_LEGACY = "CONVOY_CODEC_KEY"


class EncryptionCodec(PayloadCodec):
    """AES-256-GCM codec: every payload is encrypted, whole, on encode."""

    def __init__(self, key: bytes, key_id: str = "convoy-stack-key") -> None:
        if len(key) != 32:
            raise ValueError("payload codec requires a 32-byte AES-256 key")
        self._aesgcm = AESGCM(key)
        self._key_id = key_id.encode()

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        return [self._encrypt(p) for p in payloads]

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        return [self._decrypt(p) for p in payloads]

    def _encrypt(self, payload: Payload) -> Payload:
        nonce = os.urandom(_NONCE_SIZE)
        data = nonce + self._aesgcm.encrypt(nonce, payload.SerializeToString(), None)
        return Payload(
            metadata={"encoding": ENCODING, "encryption-key-id": self._key_id},
            data=data,
        )

    def _decrypt(self, payload: Payload) -> Payload:
        if payload.metadata.get("encoding") != ENCODING:
            # Payload produced outside our codec (e.g. Temporal server internals).
            return payload
        data = payload.data
        plaintext = self._aesgcm.decrypt(data[:_NONCE_SIZE], data[_NONCE_SIZE:], None)
        decoded = Payload()
        decoded.ParseFromString(plaintext)
        return decoded


def runtime_data_converter(key: bytes, key_id: str = "convoy-stack-key") -> DataConverter:
    """The one data converter every client and worker uses: Pydantic + encryption."""
    return dataclasses.replace(pydantic_data_converter, payload_codec=EncryptionCodec(key, key_id))


def codec_key_from_env() -> bytes:
    """Load the stack codec key (base64, 32 bytes). Fails hard if absent — the
    codec is never optional, in any environment including compose."""
    raw = os.environ.get(CODEC_KEY_ENV) or os.environ.get(CODEC_KEY_ENV_LEGACY)
    if not raw:
        raise RuntimeError(
            f"{CODEC_KEY_ENV} is not set - the payload codec is mandatory in every "
            "environment including compose"
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(f"{CODEC_KEY_ENV} must decode to 32 bytes")
    return key
