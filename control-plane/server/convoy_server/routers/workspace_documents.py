"""Owner-scoped workspace documents: each user's own workspace content, never fleet control."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from ..auth import Principal, require_role
from ..services import workspace_documents as service
from .platform import Database, IdempotencyKey

router = APIRouter(tags=["workspace documents"])
# Any authenticated user (viewer and up) owns their documents. Browser sessions still need the
# X-Convoy-Client header on writes: require_role enforces it for every non-GET request.
Owner = Annotated[Principal, Depends(require_role("viewer"))]
DocumentName = Annotated[str, Path(pattern=service.NAME_PATTERN)]
IfMatch = Annotated[str | None, Header(alias="If-Match")]
IfNoneMatch = Annotated[str | None, Header(alias="If-None-Match")]


@router.get("/api/v1/workspace-documents")
def list_documents(p: Owner, db: Database):
    return {"items": service.list_documents(db, p)}


# Document responses skip the generic encoder walk: the body is already plain, validated JSON.
@router.get("/api/v1/workspace-documents/{name}")
def get_document(name: DocumentName, p: Owner, db: Database):
    return JSONResponse(service.get_document(db, p, name))


@router.put(
    "/api/v1/workspace-documents/{name}",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": service.DocumentIn.model_json_schema()}},
        }
    },
)
async def put_document(
    name: DocumentName,
    request: Request,
    p: Owner,
    db: Database,
    key: IdempotencyKey = None,
    if_match: IfMatch = None,
    if_none_match: IfNoneMatch = None,
):
    # No body parameter: FastAPI reads nothing before the dependencies have authenticated the caller and
    # checked the role and client header. The headers are checked and the write is counted next; only
    # then is the body read (ChatBodyLimit bounds it as it arrives) and decoded, off the event loop.
    key, precondition = service.check_put_headers(
        key, if_match, if_none_match, request.headers.get("content-type")
    )
    await run_in_threadpool(service.admit_write, db, p)
    try:
        raw = await request.body()
    except ClientDisconnect:
        raise HTTPException(400, "request body incomplete") from None
    meta = await run_in_threadpool(
        service.put_document, db, p, request.url.path, key, name, precondition, raw
    )
    return JSONResponse(meta)


@router.delete("/api/v1/workspace-documents/{name}", status_code=204)
def delete_document(
    name: DocumentName,
    request: Request,
    p: Owner,
    db: Database,
    key: IdempotencyKey = None,
    if_match: IfMatch = None,
):
    service.delete_document(db, p, request.url.path, key, name, if_match)
    return Response(status_code=204)
