"""Explicit navigation associations; never compile or deploy a saved UI document."""
from __future__ import annotations

import json

from convoy_contracts.execution import canonical_digest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal
from ..ids import new_id
from ..platform_models import Application
from ..workspace_models import WorkspaceConfigurationLink as Link
from ..workspace_models import WorkspaceDocument
from . import platform


def document_for(db: Session, p: Principal) -> WorkspaceDocument | None:
    return db.scalar(select(WorkspaceDocument).where(
        WorkspaceDocument.owner_user_id == p.user.id, WorkspaceDocument.name == "configurations"))


def configuration(document: WorkspaceDocument, config_id: str) -> dict:
    body = json.loads(document.body)
    if document.schema_version != 1 or body.get("schemaVersion") != 1 or not isinstance(body.get("configurations"), list):
        raise HTTPException(422, "saved workspace configuration format is unsupported")
    matches = [item for item in body["configurations"] if isinstance(item, dict) and item.get("id") == config_id]
    if not matches:
        raise HTTPException(404, "saved configuration not found")
    if len(matches) != 1 or not isinstance(matches[0].get("name"), str) or not 1 <= len(matches[0]["name"].strip()) <= 120:
        raise HTTPException(422, "saved configuration identity is ambiguous or invalid")
    return matches[0]


def out(db: Session, p: Principal, row: Link, document: WorkspaceDocument) -> dict:
    app = platform.resource_for(db, Application, row.application_id, p)
    project = platform.project_for(db, app.project_id, p)
    state = "unchanged"
    try:
        current = configuration(document, row.configuration_id)
        if canonical_digest(current) != row.source_digest:
            state = "changed"
    except HTTPException as error:
        state = "missing" if error.status_code == 404 else "unsupported"
    return {"id": row.id, "configuration_id": row.configuration_id,
            "source_name": row.source_name, "source_revision": row.source_revision,
            "document_revision": document.revision, "source_state": state,
            "application": platform.application_out(app), "project_name": project.name}


def list_links(db: Session, p: Principal, config_id: str | None, application_id: str | None) -> list[dict]:
    if application_id:
        platform.resource_for(db, Application, application_id, p)
    document = document_for(db, p)
    if document is None:
        return []
    query = select(Link).where(Link.document_id == document.id)
    query = query.where(Link.configuration_id == config_id) if config_id else query.where(Link.application_id == application_id)
    return [out(db, p, row, document) for row in db.scalars(query.order_by(Link.created_at, Link.id))]


def save(db: Session, p: Principal, data: dict) -> dict:
    platform.resource_for(db, Application, data["application_id"], p)
    document = document_for(db, p)
    if document is None:
        raise HTTPException(404, "save a workspace configuration before linking it")
    if document.revision != data["document_revision"]:
        raise HTTPException(409, "workspace changed; reload and review it before linking")
    source = configuration(document, data["configuration_id"])
    old = db.scalar(select(Link).where(Link.document_id == document.id, Link.configuration_id == data["configuration_id"]))
    if (old.id if old else None) != data["expected_link_id"]:
        raise HTTPException(409, "configuration link changed; reload before replacing it")
    if old:
        db.delete(old)
        db.flush()
    row = Link(id=new_id("wcl"), document_id=document.id, configuration_id=data["configuration_id"],
               application_id=data["application_id"], source_revision=document.revision,
               source_digest=canonical_digest(source), source_name=source["name"].strip())
    db.add(row)
    db.flush()
    return out(db, p, row, document)


def remove(db: Session, p: Principal, link_id: str) -> dict:
    document = document_for(db, p)
    row = db.get(Link, link_id)
    if document is None or row is None or row.document_id != document.id:
        raise HTTPException(404, "configuration link not found")
    platform.resource_for(db, Application, row.application_id, p)
    db.delete(row)
    return {"id": link_id, "deleted": True}
