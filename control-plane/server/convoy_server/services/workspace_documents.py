"""Owner-scoped workspace documents.

A document is its owner's own workspace content (for example the configurations view), not fleet
control: any authenticated user may keep up to MAX_DOCUMENTS named JSON objects. Every read and write is
scoped to the caller's account, and administrators cannot read or change another user's documents.

Writes use the application lifecycle's durable idempotency receipts and audit log. A receipt keeps the
document metadata only: a matching retry carries the identical body, so the original response is rebuilt
from it instead of storing up to MAX_BODY_BYTES again for every write.
"""

from __future__ import annotations

import json
from typing import Any

from convoy_contracts.execution import canonical_digest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from ..auth import Principal, assert_live_principal, audit
from ..db import write_txn
from ..ids import iso, new_id, utcnow
from ..workspace_models import WorkspaceDocument
from .platform import previous_receipt, record_receipt, require_idempotency_key

NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 16
MAX_DEPTH = 32
MAX_SCHEMA_VERSION = 2**31 - 1


def encoded_size(body: dict[str, Any]) -> int:
    """Bytes of the body as compact UTF-8 JSON, refusing what standard JSON cannot carry."""
    pending: list[tuple[Any, int]] = [(body, 1)]
    while pending:
        value, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise HTTPException(422, f"body nesting exceeds {MAX_DEPTH} levels")
        children = value.values() if isinstance(value, dict) else value
        pending.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
    try:
        size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
    except UnicodeEncodeError:
        raise HTTPException(422, "body text must be valid Unicode") from None
    except ValueError:
        raise HTTPException(422, "non-finite numbers are not accepted; use null for unavailable values") from None
    if size > MAX_BODY_BYTES:
        raise HTTPException(413, f"workspace document body exceeds {MAX_BODY_BYTES} bytes as compact JSON")
    return size


def summary(row: WorkspaceDocument) -> dict:
    return {
        "name": row.name,
        "schema_version": row.schema_version,
        "size_bytes": row.size_bytes,
        "updated_at": iso(row.updated_at),
    }


def document_out(meta: dict, body: dict[str, Any]) -> dict:
    return {
        "name": meta["name"],
        "schema_version": meta["schema_version"],
        "body": body,
        "size_bytes": meta["size_bytes"],
        "updated_at": meta["updated_at"],
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
    return document_out(summary(row), row.body)


def put_document(
    db: Session,
    principal: Principal,
    route: str,
    key: str | None,
    name: str,
    schema_version: int,
    body: dict[str, Any],
) -> dict:
    """Create or replace the caller's document in one transaction with its receipt and audit row."""
    key = require_idempotency_key(key)
    size = encoded_size(body)
    digest = canonical_digest({"schema_version": schema_version, "body": body})
    with write_txn(db):
        assert_live_principal(db, principal)
        previous = previous_receipt(db, principal, route, key, digest)
        if previous is not None:
            return document_out(previous.response, body)
        row = _owned(db, principal, name, with_body=False)
        created = row is None
        now = utcnow()
        if created:
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
                id=new_id("wsd"), owner_user_id=principal.user.id, name=name, created_at=now
            )
            db.add(row)
        row.schema_version = schema_version
        row.body = body
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
            size_bytes=size,
            created=created,
        )
    return document_out(meta, body)


def delete_document(db: Session, principal: Principal, route: str, key: str | None, name: str) -> None:
    key = require_idempotency_key(key)
    digest = canonical_digest({})
    with write_txn(db):
        assert_live_principal(db, principal)
        if previous_receipt(db, principal, route, key, digest) is not None:
            return
        row = _owned(db, principal, name, with_body=False)
        if row is None:
            raise HTTPException(404, "workspace document not found")
        record_receipt(db, principal, route, key, digest, {"name": name, "deleted": True})
        audit(db, principal, "workspace_document.delete", row.id, name=name)
        db.delete(row)
