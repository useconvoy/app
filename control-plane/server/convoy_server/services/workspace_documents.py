"""Owner-scoped workspace documents.

A document is its owner's own workspace content (for example the configurations view), not fleet
control: any authenticated user may keep up to MAX_DOCUMENTS named JSON objects. Every read and write is
scoped to the caller's account, and administrators cannot read or change another user's documents.

Writes are conditional. A document's `revision` is 1 when it is created and grows by one with every
replacement. A PUT carries exactly one precondition: `If-None-Match: *` creates a document that must not
exist yet, and `If-Match: "<revision>"` replaces exactly that revision. Anything else is refused (428
without a precondition, 412 when it does not hold), so a stale writer never overwrites newer content. A
DELETE may carry `If-Match` too.

The router authenticates the caller, checks these headers and counts the write against the owner's
budget (WRITE_LIMIT per WRITE_WINDOW_S) before it reads the body; only then is the body decoded. Writes
use the application lifecycle's durable idempotency receipts, whose digest covers the precondition,
and its audit log. A receipt keeps the document metadata only, and an account keeps its document
receipts for RECEIPT_TTL and at most MAX_RECEIPTS of them; a retry after that is decided by its
precondition. The stored body is the compact UTF-8 JSON counted against MAX_BODY_BYTES.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from convoy_contracts.execution import canonical_digest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, defer

from ..auth import Principal, assert_live_principal, audit
from ..db import write_txn
from ..ids import iso, new_id, utcnow
from ..platform_models import MutationReceipt
from ..workspace_models import WorkspaceDocument
from .identity import IdentityError, throttle_check
from .platform import previous_receipt, record_receipt, require_idempotency_key

NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
ROUTE_PREFIX = "/api/v1/workspace-documents/"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 16
MAX_DEPTH = 32
MAX_SCHEMA_VERSION = 2**31 - 1
WRITE_LIMIT = 60
WRITE_WINDOW_S = 600
RECEIPT_TTL = timedelta(hours=24)
MAX_RECEIPTS = 256
CREATE = "*"
REVISION = re.compile(r'"([1-9][0-9]{0,17})"')
JSON_CONTENT = re.compile(r"application/(?:[a-z0-9.+-]*\+)?json\s*(?:;.*)?", re.IGNORECASE)
_FOLD = bytes.maketrans(b"{}", b"[]")
_NOT_BRACKET = bytes(sorted(set(range(256)) - set(b"[]{}")))


class DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(strict=True, ge=1, le=MAX_SCHEMA_VERSION)
    body: dict[str, Any]


def check_put_headers(
    key: str | None, if_match: str | None, if_none_match: str | None, content_type: str | None
) -> tuple[str, str | int]:
    """A PUT's idempotency key and its precondition: CREATE, or the revision it replaces."""
    key = require_idempotency_key(key)
    if if_match is None and if_none_match is None:
        raise HTTPException(
            428,
            'workspace document writes need a precondition: If-None-Match: * to create the document, '
            'or If-Match: "<revision>" to replace that revision',
        )
    if if_match is not None and if_none_match is not None:
        raise HTTPException(422, "send either If-Match or If-None-Match, not both")
    if if_none_match is not None and if_none_match.strip() != CREATE:
        raise HTTPException(422, "If-None-Match accepts only *")
    precondition = CREATE if if_match is None else revision(if_match)
    if not JSON_CONTENT.fullmatch(content_type or ""):
        raise HTTPException(422, "the request body must be JSON (Content-Type: application/json)")
    return key, precondition


def revision(if_match: str) -> int:
    match = REVISION.fullmatch(if_match.strip())
    if match is None:
        raise HTTPException(422, 'If-Match must be one quoted document revision, for example "3"')
    return int(match.group(1))


def admit_write(db: Session, principal: Principal) -> None:
    """Counts a write against its owner's budget, before the request body is read."""
    try:
        throttle_check(db, f"workspace-documents:{principal.user.id}", WRITE_LIMIT, WRITE_WINDOW_S)
    except IdentityError as error:
        raise HTTPException(
            429,
            f"too many workspace document writes (at most {WRITE_LIMIT} per {WRITE_WINDOW_S // 60} minutes)",
            headers={"Retry-After": str(error.retry_after or WRITE_WINDOW_S)},
        ) from None


def nesting(data: bytes) -> int:
    """Container depth of compact JSON, the outermost container being level 1, counted to MAX_DEPTH + 1.

    Bytes-level passes instead of a walk over every value: with the escape pairs removed every remaining
    quote delimits a string, the strings are dropped (in slices, which bounds the pieces in memory), and
    each pass peels the innermost containers."""
    plain = data.replace(b"\\\\", b"").replace(b'\\"', b"")
    outside = bytearray()
    in_string = 0
    for start in range(0, len(plain), 1 << 16):
        pieces = plain[start : start + (1 << 16)].split(b'"')
        outside += b"".join(pieces[in_string::2])
        in_string = (in_string + len(pieces) - 1) % 2
    skeleton = bytes(outside).translate(_FOLD, _NOT_BRACKET)
    depth = 0
    while skeleton and depth <= MAX_DEPTH:
        depth += 1
        skeleton = skeleton.replace(b"[]", b"")
    return depth


def decode(raw: bytes) -> tuple[int, str, int, str]:
    """(schema_version, compact body, its UTF-8 size, payload digest) of a PUT body, or the refusal."""
    try:
        payload = json.loads(raw.decode())
    except UnicodeDecodeError as error:
        raise _invalid_json(error.start, "the request body is not UTF-8") from None
    except json.JSONDecodeError as error:
        raise _invalid_json(error.pos, error.msg) from None
    except RecursionError:
        raise HTTPException(422, f"body nesting exceeds {MAX_DEPTH} levels") from None
    try:
        document = DocumentIn.model_validate(payload)
    except ValidationError as error:
        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in error.errors(include_url=False)]
        ) from None
    try:
        text = json.dumps(document.body, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        data = text.encode()
    except UnicodeEncodeError:
        raise HTTPException(422, "body text must be valid Unicode") from None
    except ValueError:
        raise HTTPException(422, "non-finite numbers are not accepted; use null for unavailable values") from None
    except RecursionError:
        raise HTTPException(422, f"body nesting exceeds {MAX_DEPTH} levels") from None
    if len(data) > MAX_BODY_BYTES:
        raise HTTPException(413, f"workspace document body exceeds {MAX_BODY_BYTES} bytes as compact JSON")
    if nesting(data) > MAX_DEPTH:
        raise HTTPException(422, f"body nesting exceeds {MAX_DEPTH} levels")
    return document.schema_version, text, len(data), hashlib.sha256(data).hexdigest()


def _invalid_json(position: int, reason: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "json_invalid", "loc": ("body", position), "msg": "JSON decode error", "ctx": {"error": reason}}]
    )


def summary(row: WorkspaceDocument) -> dict:
    return {
        "name": row.name,
        "schema_version": row.schema_version,
        "revision": row.revision,
        "size_bytes": row.size_bytes,
        "updated_at": iso(row.updated_at),
    }


def _owned(db: Session, principal: Principal, name: str, *, with_body: bool = True):
    query = select(WorkspaceDocument).where(
        WorkspaceDocument.owner_user_id == principal.user.id, WorkspaceDocument.name == name
    )
    if not with_body:
        query = query.options(defer(WorkspaceDocument.body))
    return db.scalar(query)


def list_documents(db: Session, principal: Principal) -> list[dict]:
    rows = db.execute(
        select(
            WorkspaceDocument.name,
            WorkspaceDocument.schema_version,
            WorkspaceDocument.revision,
            WorkspaceDocument.size_bytes,
            WorkspaceDocument.updated_at,
        )
        .where(WorkspaceDocument.owner_user_id == principal.user.id)
        .order_by(WorkspaceDocument.name)
    )
    return [summary(row) for row in rows]


def get_document(db: Session, principal: Principal, name: str) -> dict:
    row = _owned(db, principal, name)
    if row is None:
        raise HTTPException(404, "workspace document not found")
    meta = summary(row)
    return {
        "name": meta["name"],
        "schema_version": meta["schema_version"],
        "revision": meta["revision"],
        "body": json.loads(row.body),
        "size_bytes": meta["size_bytes"],
        "updated_at": meta["updated_at"],
    }


def put_document(
    db: Session, principal: Principal, route: str, key: str, name: str, precondition: str | int, raw: bytes
) -> dict:
    """Create (precondition CREATE) or replace the named revision of the caller's document in one
    transaction with its receipt and audit row. Returns the document metadata, never the body."""
    schema_version, text, size, body_digest = decode(raw)
    digest = canonical_digest(
        {"schema_version": schema_version, "body_sha256": body_digest, "precondition": precondition}
    )
    with write_txn(db):
        assert_live_principal(db, principal)
        previous = previous_receipt(db, principal, route, key, digest)
        if previous is not None:
            return previous.response
        row = _owned(db, principal, name, with_body=False)
        now = utcnow()
        if precondition == CREATE:
            if row is not None:
                raise HTTPException(412, "workspace document already exists; replace it with If-Match and its revision")
            count = db.scalar(
                select(func.count())
                .select_from(WorkspaceDocument)
                .where(WorkspaceDocument.owner_user_id == principal.user.id)
            )
            if count >= MAX_DOCUMENTS:
                raise HTTPException(
                    409,
                    f"workspace document limit reached ({MAX_DOCUMENTS} per account); delete one first",
                )
            row = WorkspaceDocument(
                id=new_id("wsd"), owner_user_id=principal.user.id, name=name, revision=1, created_at=now
            )
            db.add(row)
        else:
            _require_revision(row, precondition)
            row.revision += 1
        row.schema_version = schema_version
        row.body = text
        row.size_bytes = size
        row.updated_at = now
        meta = summary(row)
        record_receipt(db, principal, route, key, digest, meta)
        audit(
            db,
            principal,
            "workspace_document.put",
            row.id,
            name=name,
            schema_version=schema_version,
            revision=row.revision,
            size_bytes=size,
            created=precondition == CREATE,
        )
        _prune_receipts(db, principal, now)
    return meta


def delete_document(
    db: Session, principal: Principal, route: str, key: str | None, name: str, if_match: str | None
) -> None:
    key = require_idempotency_key(key)
    expected = None if if_match is None else revision(if_match)
    admit_write(db, principal)
    digest = canonical_digest({"delete": True, "precondition": expected})
    with write_txn(db):
        assert_live_principal(db, principal)
        if previous_receipt(db, principal, route, key, digest) is not None:
            return
        row = _owned(db, principal, name, with_body=False)
        if row is None:
            raise HTTPException(404, "workspace document not found")
        if expected is not None:
            _require_revision(row, expected)
        record_receipt(db, principal, route, key, digest, {"name": name, "deleted": True})
        audit(db, principal, "workspace_document.delete", row.id, name=name, revision=row.revision)
        db.delete(row)
        _prune_receipts(db, principal, utcnow())


def _require_revision(row: WorkspaceDocument | None, expected: int) -> None:
    if row is None:
        raise HTTPException(412, "workspace document does not exist; create it with If-None-Match: *")
    if row.revision != expected:
        raise HTTPException(
            412, f"workspace document is at revision {row.revision}, not {expected}; read it again first"
        )


def _prune_receipts(db: Session, principal: Principal, now: datetime) -> None:
    """Keeps the account's document receipts younger than RECEIPT_TTL, and at most MAX_RECEIPTS."""
    db.flush()  # the receipt this write recorded counts among the newest
    owned = (
        MutationReceipt.owner_user_id == principal.user.id,
        MutationReceipt.route.startswith(ROUTE_PREFIX, autoescape=True),
    )
    newest = (
        select(MutationReceipt.id)
        .where(*owned)
        .order_by(MutationReceipt.created_at.desc(), MutationReceipt.id.desc())
        .limit(MAX_RECEIPTS)
    )
    db.execute(
        delete(MutationReceipt)
        .where(*owned, or_(MutationReceipt.created_at < now - RECEIPT_TTL, MutationReceipt.id.not_in(newest)))
        .execution_options(synchronize_session=False)
    )
