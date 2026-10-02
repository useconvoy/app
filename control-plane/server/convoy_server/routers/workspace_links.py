"""Associations between saved workspace specifications and project applications."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from ..services import platform, workspace_links
from .platform import Database, Id, IdempotencyKey, Input, PrincipalRead, PrincipalWrite

router = APIRouter(tags=["workspace configuration links"])
ROOT = "/api/v1/workspace-configuration-links"


class LinkIn(Input):
    configuration_id: Id
    application_id: Id
    document_revision: int = Field(strict=True, ge=1)
    expected_link_id: Id | None


@router.get(ROOT)
def list_links(p: PrincipalRead, db: Database, configuration_id: Id | None = None, application_id: Id | None = None):
    if bool(configuration_id) == bool(application_id):
        raise HTTPException(422, "specify exactly one configuration_id or application_id")
    return workspace_links.list_links(db, p, configuration_id, application_id)


@router.post(ROOT, status_code=201)
def save_link(body: LinkIn, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None):
    data = body.model_dump()
    return platform.mutate(db, p, request.url.path, key, data, lambda: workspace_links.save(db, p, data))


@router.post(ROOT + "/{link_id}/remove")
def remove_link(link_id: str, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None):
    return platform.mutate(db, p, request.url.path, key, {"id": link_id}, lambda: workspace_links.remove(db, p, link_id))
