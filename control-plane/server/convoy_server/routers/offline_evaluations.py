"""Owner-scoped offline evaluations: episodes recorded outside the hosted runner, imported unsigned."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from ..auth import Principal, require_role
from ..services import offline_evaluations as service
from .platform import Database

router = APIRouter(tags=["offline evaluations"])
# Reads: the owner at any role. Writes: the owner with the operator role (re-checked in the write).
Reader = Annotated[Principal, Depends(require_role("viewer"))]
Writer = Annotated[Principal, Depends(require_role("operator"))]
EvaluationId = Annotated[str, Path(pattern=service.EVALUATION_ID)]
EpisodeId = Annotated[str, Path(pattern=service.EPISODE_ID)]
Index = Annotated[int, Path(ge=0, le=service.MAX_STEPS)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
ROOT = "/api/v1/offline-evaluations"


def _body(model) -> dict:
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": model.model_json_schema()}}}}


async def _read(request: Request) -> bytes:
    # ChatBodyLimit bounds the body (size and deadline) while it is read here, after authentication.
    try:
        return await request.body()
    except ClientDisconnect:
        raise HTTPException(400, "request body incomplete") from None


@router.get(ROOT)
def list_evaluations(p: Reader, db: Database):
    return {"items": service.list_evaluations(db, p)}


@router.post(ROOT, status_code=201, openapi_extra=_body(service.EvaluationIn))
async def create_evaluation(request: Request, p: Writer, db: Database, key: IdempotencyKey = None):
    # No body parameter: nothing is read before the dependencies authenticate the caller and check the
    # role and client header; the headers and the write budget come next, then the body.
    key = service.check_write_headers(key, request.headers.get("content-type"))
    await run_in_threadpool(service.admit_write, db, p)
    raw = await _read(request)
    created = await run_in_threadpool(service.create_evaluation, db, p, request.url.path, key, raw)
    return JSONResponse(created, status_code=201)


@router.get(ROOT + "/{evaluation_id}")
def get_evaluation(evaluation_id: EvaluationId, p: Reader, db: Database):
    return JSONResponse(service.get_evaluation(db, p, evaluation_id))


@router.delete(ROOT + "/{evaluation_id}", status_code=204)
def delete_evaluation(evaluation_id: EvaluationId, request: Request, p: Writer, db: Database, key: IdempotencyKey = None):
    service.delete_evaluation(db, p, request.url.path, key, evaluation_id)
    return Response(status_code=204)


@router.post(ROOT + "/{evaluation_id}/episodes", status_code=201, openapi_extra=_body(service.EpisodeIn))
async def upload_episode(
    evaluation_id: EvaluationId, request: Request, p: Writer, db: Database, key: IdempotencyKey = None
):
    # Up to MAX_EPISODE_BODY of frames: refused before it is read unless the caller is authenticated, may
    # write, owns the evaluation and is within the write budget. Decoding runs off the event loop.
    key = service.check_write_headers(key, request.headers.get("content-type"))
    await run_in_threadpool(service.owned_evaluation, db, p, evaluation_id)
    await run_in_threadpool(service.admit_write, db, p)
    raw = await _read(request)
    episode, created = await run_in_threadpool(
        service.upload_episode, db, p, request.url.path, key, evaluation_id, raw
    )
    return JSONResponse(episode, status_code=201 if created else 200)


@router.delete(ROOT + "/{evaluation_id}/episodes/{episode_id}", status_code=204)
def delete_episode(
    evaluation_id: EvaluationId, episode_id: EpisodeId, request: Request, p: Writer, db: Database,
    key: IdempotencyKey = None,
):
    service.delete_episode(db, p, request.url.path, key, evaluation_id, episode_id)
    return Response(status_code=204)


# The replay endpoints answer in the hosted replay shape (`GET /api/v1/episodes/{id}/replay[/frames/{i}]`).
@router.get(ROOT + "/{evaluation_id}/episodes/{episode_id}/replay")
def get_replay(evaluation_id: EvaluationId, episode_id: EpisodeId, p: Reader, db: Database):
    return JSONResponse(service.replay_manifest(db, p, evaluation_id, episode_id))


@router.get(ROOT + "/{evaluation_id}/episodes/{episode_id}/replay/frames/{index}")
def get_replay_frame(evaluation_id: EvaluationId, episode_id: EpisodeId, index: Index, p: Reader, db: Database):
    return JSONResponse(service.replay_frame(db, p, evaluation_id, episode_id, index))
