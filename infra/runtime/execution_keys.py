"""Prepare role-scoped execution keys before a container starts serving requests.

AWS wrappers materialize their one injected JSON document into a private file.
The local bootstrap initializes a persistent pair once; it never rotates keys or
repairs a partial installation while an old mission might remain authorized.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import uuid
from pathlib import Path

from convoy_contracts.grants import MAX_FILE_BYTES, GrantVerifier, SigningKeys

JSON_ENV = {
    "api": "CONVOY_EXECUTION_SIGNING_JSON",
    "action": "CONVOY_ACTION_VERIFICATION_JSON",
    "planner": "CONVOY_PLANNER_VERIFICATION_JSON",
}
FILE_ENV = {
    "api": "CONVOY_EXECUTION_SIGNING_KEYS_FILE",
    "action": "CONVOY_ACTION_VERIFICATION_KEYS_FILE",
    "planner": "CONVOY_PLANNER_VERIFICATION_KEYS_FILE",
}
LEGACY_ENV = {"CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET"}
EXECUTION_ENV = set(JSON_ENV.values()) | set(FILE_ENV.values()) | LEGACY_ENV


def _directory(path: Path) -> Path:
    path = Path(path).absolute()
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("execution key directory must be private and owned by this process user")
    return path


def _create(path: Path, raw: bytes, mode: int) -> None:
    if not 0 < len(raw) <= MAX_FILE_BYTES:
        raise ValueError("execution key document exceeds its bound")
    # Initial publication only: never truncate, follow a symlink or replace an
    # existing key. No serving process is started until initialization succeeds.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def materialize_execution_keys(role: str, directory: Path) -> dict[str, str]:
    """Consume one role's injected document; return its validated file setting.

    The caller supplies a fresh mode-0700 temporary directory and retains it
    across exec. The container filesystem owns its lifetime, not a context manager.
    Credentials are never part of an exception, command argument or return value.
    """
    try:
        if role not in JSON_ENV:
            raise ValueError("unsupported execution role")
        name = JSON_ENV[role]
        raw = os.environ.pop(name, None)
        if raw is None or EXECUTION_ENV.intersection(os.environ):
            raise ValueError("missing or conflicting execution key configuration")
        directory = _directory(directory)
        path = directory / "keys.json"
        _create(path, raw.encode("utf-8"), 0o600)
        if role == "api":
            SigningKeys(path)
        else:
            GrantVerifier(path, purpose=role)
        return {FILE_ENV[role]: str(path)}
    except (OSError, ValueError, TypeError, UnicodeError):
        # Retain malformed files inside the private task directory for bounded
        # diagnosis; never overwrite or attempt to recover them automatically.
        raise ValueError("execution key configuration unavailable for this process role") from None


def _encode(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def initialize(signing_file: Path, action_verification_file: Path, issuer: str,
               *, require_existing: bool = False) -> None:
    """Initialize a local installation once, or validate its unchanged key pair."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        signing = _directory(signing_file.parent) / signing_file.name
        public = _directory(action_verification_file.parent) / action_verification_file.name
        if signing == public or not isinstance(issuer, str) or not issuer:
            raise ValueError("invalid local execution key configuration")
        # lexists includes dangling symlinks; none may be replaced by bootstrap.
        present = [os.path.lexists(path) for path in (signing, public)]
        if any(present):
            if not all(present):
                raise ValueError("partial execution key installation")
            signer = SigningKeys(signing)
            verifier = GrantVerifier(public, purpose="action")
            expected = signer.verification_document("action")
            with public.open("rb") as stream:
                observed = stream.read(MAX_FILE_BYTES + 1)
            if (verifier.issuer != issuer or len(observed) > MAX_FILE_BYTES
                    or json.loads(observed) != expected):
                raise ValueError("execution key documents do not match")
            return
        if require_existing:
            raise ValueError("execution keys missing from an initialized installation")
        keys, active = [], {}
        for purpose in ("action", "planner"):
            kid = purpose + "-" + uuid.uuid4().hex
            active[purpose] = kid
            key = Ed25519PrivateKey.generate()
            keys.append({"kid": kid, "purpose": purpose, "audience": issuer + ":" + purpose,
                         "private_key_pem": key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii")})
        _create(signing, _encode({"schema_version": 1, "issuer": issuer, "active": active, "keys": keys}), 0o600)
        signer = SigningKeys(signing)
        _create(public, _encode(signer.verification_document("action")), 0o444)
        GrantVerifier(public, purpose="action")
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ValueError("execution key initialization failed; preserve existing files and inspect the installation") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("initialize")
    init.add_argument("--signing-file", type=Path, required=True)
    init.add_argument("--action-verification-file", type=Path, required=True)
    init.add_argument("--issuer", required=True)
    init.add_argument("--require-existing", action="store_true")
    args = parser.parse_args()
    try:
        initialize(args.signing_file, args.action_verification_file, args.issuer,
                   require_existing=args.require_existing)
    except ValueError as error:
        parser.error(str(error))
    print("Execution key pair verified; no key material emitted.")


if __name__ == "__main__":
    main()
