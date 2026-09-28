"""Project-scoped repeatable simulation evaluation and release qualification."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy import select

from ..evaluation_models import EvaluationGate, EvaluationRun, EvaluationSuite
from ..platform_models import Application
from ..services import evaluations as service
from ..services import platform
from .platform import CancelIn, Database, Id, IdempotencyKey, Input, Name, PrincipalRead, PrincipalWrite

router = APIRouter(tags=["release evaluations"])
Seed = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]


class SuiteIn(Input):
    name: Name
    reference_release_id: Id
    seeds: list[Seed] = Field(min_length=1, max_length=20)
    min_successes: int = Field(strict=True, ge=1, le=20)


class RunIn(Input):
    suite_id: Id
    release_id: Id
    robot_id: Id


class GateIn(Input):
    suite_id: Id
    expected_generation: int = Field(strict=True, ge=0)


@router.post("/api/v1/applications/{application_id}/evaluation-suites", status_code=201)
def create_suite(
    application_id: str,
    body: SuiteIn,
    request: Request,
    p: PrincipalWrite,
    db: Database,
    key: IdempotencyKey = None,
):
    data = body.model_dump()
    return platform.mutate(
        db, p, request.url.path, key, data, lambda: service.create_suite(db, p, application_id, data)
    )


@router.get("/api/v1/applications/{application_id}/evaluation-suites")
def list_suites(application_id: str, p: PrincipalRead, db: Database):
    platform.resource_for(db, Application, application_id, p)
    rows = db.scalars(
        select(EvaluationSuite)
        .where(EvaluationSuite.application_id == application_id)
        .order_by(EvaluationSuite.created_at.desc())
        .limit(200)
    )
    return [service.suite_out(row) for row in rows]


@router.get("/api/v1/evaluation-suites/{suite_id}")
def get_suite(suite_id: str, p: PrincipalRead, db: Database):
    return service.suite_out(platform.resource_for(db, EvaluationSuite, suite_id, p))


@router.post("/api/v1/evaluations", status_code=201)
def create_run(body: RunIn, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None):
    data = body.model_dump()
    return platform.mutate(db, p, request.url.path, key, data, lambda: service.create_run(db, p, data))


@router.get("/api/v1/evaluations")
def list_runs(project_id: Id, p: PrincipalRead, db: Database):
    platform.project_for(db, project_id, p)
    rows = db.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.project_id == project_id)
        .order_by(EvaluationRun.created_at.desc())
        .limit(200)
    )
    return [service.run_out(db, row) for row in rows]


@router.get("/api/v1/evaluations/{run_id}")
def get_run(run_id: str, p: PrincipalRead, db: Database, baseline_id: Id | None = None):
    if baseline_id:
        return service.compare(db, p, run_id, baseline_id)
    return service.run_out(db, platform.resource_for(db, EvaluationRun, run_id, p))


@router.post("/api/v1/evaluations/{run_id}/cancel")
def cancel_run(
    run_id: str, body: CancelIn, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None
):
    data = body.model_dump()
    return platform.mutate(
        db, p, request.url.path, key, data, lambda: service.cancel_run(db, p, run_id, data)
    )


@router.post("/api/v1/evaluations/{run_id}/promote", status_code=201)
def promote(
    run_id: str, body: Input, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None
):
    return platform.mutate(db, p, request.url.path, key, {}, lambda: service.promote(db, p, run_id))


@router.post("/api/v1/applications/{application_id}/evaluation-gate")
def set_gate(
    application_id: str,
    body: GateIn,
    request: Request,
    p: PrincipalWrite,
    db: Database,
    key: IdempotencyKey = None,
):
    data = body.model_dump()
    return platform.mutate(
        db, p, request.url.path, key, data, lambda: service.set_gate(db, p, application_id, data)
    )


@router.get("/api/v1/applications/{application_id}/evaluation-gate")
def get_gate(application_id: str, p: PrincipalRead, db: Database):
    platform.resource_for(db, Application, application_id, p)
    gate = db.get(EvaluationGate, application_id)
    if gate is None:
        raise HTTPException(404, "no evaluation gate configured")
    return {"application_id": application_id, "suite_id": gate.suite_id, "generation": gate.generation}
