"""Disposable development authority; never replace existing installation keys."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def development_grants(output: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Create one fresh signer and public documents in a private setup directory.

    The owning harness distributes each file to its intended process. This
    same-user fixture verifies configuration, not operating-system isolation.
    """
    from convoy_contracts.grants import SigningKeys
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    def write(path, value):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    private, public = output / "api-keys", output / "verification"
    private.mkdir(mode=0o700)
    public.mkdir(mode=0o700)
    active, keys = {}, []
    for purpose in ("action", "planner"):
        kid = purpose + "-" + uuid.uuid4().hex
        active[purpose] = kid
        pem = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ).decode("ascii")
        keys.append({"kid": kid, "purpose": purpose, "audience": "convoy-development-" + purpose,
                     "private_key_pem": pem})
    signing_path = private / "execution.json"
    write(signing_path, {"schema_version": 1, "issuer": "convoy-development-api", "active": active, "keys": keys})
    signer = SigningKeys(signing_path)
    verifier_env = {}
    for purpose in ("action", "planner"):
        path = public / (purpose + ".json")
        write(path, signer.verification_document(purpose))
        verifier_env[f"CONVOY_{purpose.upper()}_VERIFICATION_KEYS_FILE"] = str(path)
    return {"CONVOY_EXECUTION_SIGNING_KEYS_FILE": str(signing_path)}, verifier_env
