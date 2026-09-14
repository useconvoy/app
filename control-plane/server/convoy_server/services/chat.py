"""Bounded, transient chat relay. Never stores prompt or response in the fleet database.

Workers share a private SQLite file on Linux shared memory. Other platforms use the private
system temporary directory (not guaranteed memory-backed). A request is claimed once: a lost
claim reply or agent crash expires honestly and is never redelivered for generation.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from ..config import get_settings

MAX_ROWS = 1024
MAX_ACTIVE = 64
MAX_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 200 * 1024
REQUEST_TTL = 120
RESULT_TTL = 300
TOMBSTONE_TTL = 86400


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _path() -> Path:
    settings = get_settings()
    namespace = hashlib.sha256(
        (str(settings.data_dir.resolve()) + "\0" + settings.db_url).encode()
    ).hexdigest()[:24]
    base = Path("/dev/shm") if Path("/dev/shm").is_dir() else Path(tempfile.gettempdir())
    directory = base / f"convoy-chat-{os.getuid()}-{namespace}"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise HTTPException(503, "chat relay private directory is unavailable")
    path = directory / "relay.sqlite3"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise HTTPException(503, "chat relay private file is unavailable")
    finally:
        os.close(fd)
    return path


@contextmanager
def transaction():
    try:
        c = sqlite3.connect(_path(), timeout=5, isolation_level=None)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "chat relay private storage is unavailable") from exc
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA secure_delete=ON")
        c.execute("PRAGMA journal_mode=DELETE")
        c.execute("PRAGMA synchronous=FULL")
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("BEGIN IMMEDIATE")
        c.execute("""CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, device_id TEXT NOT NULL, user_id TEXT NOT NULL,
            binding_epoch INTEGER NOT NULL, restore_scope TEXT NOT NULL, release_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
            expires REAL NOT NULL, forget REAL NOT NULL, payload TEXT, claim_token TEXT,
            result TEXT, error TEXT
        )""")
        now = time.time()
        c.execute("DELETE FROM requests WHERE forget < ?", (now,))
        c.execute(
            """UPDATE requests SET status='expired', payload=NULL, result=NULL, claim_token=NULL,
                  error='request_expired'
                  WHERE expires < ?""",
            (now,),
        )
        yield c
        c.commit()
    except sqlite3.Error as e:
        c.rollback()
        raise HTTPException(503, "chat relay is temporarily unavailable") from e
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def output(row) -> dict:
    result = json.loads(row["result"]) if row["result"] else {}
    error = result.get("error")
    if row["error"]:
        error = {
            "code": row["error"],
            "message": ERRORS.get(row["error"], "Chat request could not complete."),
        }
    return {
        "id": row["id"],
        "device_id": row["device_id"],
        "release_id": row["release_id"],
        "status": row["status"],
        "created_at": iso(row["created"]),
        "expires_at": iso(row["expires"]),
        "content": result.get("content"),
        "finish_reason": result.get("finish_reason"),
        "usage": result.get("usage"),
        "metrics": result.get("metrics"),
        "trace_id": result.get("trace_id"),
        "error": error,
    }


ERRORS = {
    "request_expired": "Request expired. It will not be run again automatically.",
    "device_changed": "Device identity or active release changed before this request could complete.",
    "device_unavailable": "Device is not ready for production chat.",
    "context_length_exceeded": "Conversation exceeds the active model context window. Start a new chat.",
    "timeout": "The local model did not finish within the inference deadline.",
    "runtime_error": "The local model could not complete this request.",
    "unavailable": "The local production gateway is unavailable.",
    "invalid_request_error": "The local model rejected the request limits.",
    "internal_error": "The local model could not complete this request.",
    "output_too_large": "The local model response exceeded the relay size limit.",
}


def create(c, *, request_id, device, user_id, scope, body):
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    row = c.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
    if row:
        if row["user_id"] != user_id or row["device_id"] != device.id:
            raise HTTPException(404, "chat request not found")
        if row["binding_epoch"] != device.binding_epoch or row["restore_scope"] != scope:
            raise HTTPException(
                409, "chat request belongs to an earlier device binding or control plane restore"
            )
        if row["fingerprint"] != fingerprint:
            raise HTTPException(409, "request_id was already used with different content")
        return output(row)
    now = time.time()
    count, active, size = c.execute("""SELECT COUNT(*), COALESCE(SUM(status IN ('queued','running')),0),
        COALESCE(SUM(COALESCE(LENGTH(CAST(payload AS BLOB)),0)+COALESCE(LENGTH(CAST(result AS BLOB)),0)),0)
        FROM requests""").fetchone()
    if (
        count >= MAX_ROWS
        or active >= MAX_ACTIVE
        or size + (active + 1) * MAX_RESULT_BYTES + len(encoded.encode()) > MAX_BYTES
    ):
        raise HTTPException(
            429, "chat relay capacity reached; retry after retained requests expire (up to 24 hours)"
        )
    if c.execute(
        "SELECT 1 FROM requests WHERE device_id=? AND status IN ('queued','running')", (device.id,)
    ).fetchone():
        raise HTTPException(409, "device already has a chat request in progress")
    c.execute(
        """INSERT INTO requests
        (id,device_id,user_id,binding_epoch,restore_scope,release_id,fingerprint,status,created,expires,forget,payload)
        VALUES (?,?,?,?,?,?,?,'queued',?,?,?,?)""",
        (
            request_id,
            device.id,
            user_id,
            device.binding_epoch,
            scope,
            body["expected_release_id"],
            fingerprint,
            now,
            now + REQUEST_TTL,
            now + TOMBSTONE_TTL,
            encoded,
        ),
    )
    return output(c.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone())


def fetch(c, request_id, device, scope, user_id, admin=False):
    row = c.execute("SELECT * FROM requests WHERE id=? AND device_id=?", (request_id, device.id)).fetchone()
    if not row or (row["user_id"] != user_id and not admin):
        raise HTTPException(404, "chat request not found")
    if row["binding_epoch"] != device.binding_epoch or row["restore_scope"] != scope:
        raise HTTPException(409, "chat request belongs to an earlier device binding or control plane restore")
    return output(row)


def claim(c, device, scope, release_id):
    row = c.execute(
        "SELECT * FROM requests WHERE device_id=? AND status='queued' ORDER BY created LIMIT 1", (device.id,)
    ).fetchone()
    if not row:
        return None
    if (
        row["binding_epoch"] != device.binding_epoch
        or row["restore_scope"] != scope
        or row["release_id"] != release_id
    ):
        c.execute(
            "UPDATE requests SET status='failed',payload=NULL,error='device_changed' WHERE id=?", (row["id"],)
        )
        return None
    token = secrets.token_urlsafe(32)
    c.execute(
        "UPDATE requests SET status='running',claim_token=?,payload=NULL WHERE id=?", (token, row["id"])
    )
    payload = json.loads(row["payload"])
    return {
        "id": row["id"],
        "expected_release_id": row["release_id"],
        "messages": payload["messages"],
        "max_tokens": payload["max_tokens"],
        "expires_at": iso(row["expires"]),
        "remaining_s": max(0, row["expires"] - time.time()),
        "claim_token": token,
    }


def finish(c, device, scope, request_id, body):
    row = c.execute("SELECT * FROM requests WHERE id=? AND device_id=?", (request_id, device.id)).fetchone()
    if (
        not row
        or not row["claim_token"]
        or not secrets.compare_digest(row["claim_token"], body["claim_token"])
    ):
        raise HTTPException(404, "chat claim not found or expired")
    if (
        row["binding_epoch"] != device.binding_epoch
        or row["restore_scope"] != scope
        or row["release_id"] != body["release_id"]
    ):
        raise HTTPException(409, "chat device binding changed")
    result = {k: body.get(k) for k in ("content", "usage", "metrics", "trace_id", "finish_reason")}
    code = body.get("error_code")
    result["error"] = {"code": code, "message": ERRORS.get(code, ERRORS["internal_error"])} if code else None
    encoded = json.dumps(result, separators=(",", ":"))
    if len(encoded.encode()) > MAX_RESULT_BYTES:
        raise HTTPException(413, "chat result exceeds relay size limit")
    if row["status"] in ("succeeded", "failed"):
        if row["result"] != encoded or row["status"] != body["status"]:
            raise HTTPException(409, "chat result is already terminal")
        return {"ok": True}
    if row["status"] != "running":
        raise HTTPException(409, "chat request is no longer running")
    c.execute(
        "UPDATE requests SET status=?,result=?,expires=? WHERE id=?",
        (
            body["status"],
            encoded,
            time.time() + RESULT_TTL,
            request_id,
        ),
    )
    return {"ok": True}
