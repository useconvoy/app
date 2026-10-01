"""Owner-scoped workspace documents: each user's own workspace content, never fleet control."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from ..auth import Principal, require_role
from ..services import workspace_documents as service
from .platform import Database, IdempotencyKey, Input

router = APIRouter(tags=["workspace documents"])
# Any authenticated user (viewer and up) owns their documents. Browser sessions still need the
# X-Convoy-Client header on writes: require_role enforces it for every non-GET request.
Owner = Annotated[Principal, Depends(require_role("viewer"))]
DocumentName = Annotated[str, Path(pattern=service.NAME_PATTERN)]


class DocumentIn(Input):
    schema_version: int = Field(strict=True, ge=1, le=service.MAX_SCHEMA_VERSION)
    body: dict[str, Any]


@router.get("/api/v1/workspace-documents")
def list_documents(p: Owner, db: Database):
    return {"items": service.list_documents(db, p)}


# Document responses skip the generic encoder walk: the body is already plain, validated JSON.
@router.get("/api/v1/workspace-documents/{name}")
def get_document(name: DocumentName, p: Owner, db: Database):
    return JSONResponse(service.get_document(db, p, name))


@router.put("/api/v1/workspace-documents/{name}")
def put_document(
    name: DocumentName, document: DocumentIn, request: Request, p: Owner, db: Database, key: IdempotencyKey = None
):
    return JSONResponse(
        service.put_document(db, p, request.url.path, key, name, document.schema_version, document.body)
    )


@router.delete("/api/v1/workspace-documents/{name}", status_code=204)
def delete_document(name: DocumentName, request: Request, p: Owner, db: Database, key: IdempotencyKey = None):
    service.delete_document(db, p, request.url.path, key, name)
    return Response(status_code=204)
