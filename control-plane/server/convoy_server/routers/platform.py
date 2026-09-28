"""Versioned robot application APIs alongside the existing text model APIs."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, StaleBinding, assert_admitted_binding, current_device, require_role
from ..db import get_db, write_txn
from ..models import Device
from ..platform_models import Application, ApplicationRelease, Deployment, Episode, Mission, Project, Robot
from ..services import platform as service

router = APIRouter(tags=["application lifecycle"])
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Id = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
PrincipalRead = Annotated[Principal, Depends(require_role("viewer"))]
PrincipalWrite = Annotated[Principal, Depends(require_role("operator"))]
Database = Annotated[Session, Depends(get_db)]
DeviceIdentity = Annotated[Device, Depends(current_device)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectIn(Input):
    name: Name


class ApplicationIn(ProjectIn):
    project_id: Id


class RobotIn(ApplicationIn):
    device_id: Id
    profile: Annotated[str, StringConstraints(min_length=1, max_length=80)]


class ReleaseIn(Input):
    manifest: dict


class DeploymentIn(Input):
    robot_id: Id
    release_id: Id
    expected_generation: int = Field(strict=True, ge=0)


class MissionIn(Input):
    deployment_id: Id
    expected_generation: int = Field(strict=True, ge=1)
    seed: int = Field(strict=True, ge=0, le=2**32 - 1)
    ttl_s: int = Field(strict=True, ge=1, le=300)


class CancelIn(Input):
    reason: str = Field(default="", max_length=1000)


class DeploymentReportIn(Input):
    generation: int = Field(strict=True, ge=1)
    state: Literal["ready", "blocked"]
    release_digest: Digest
    detail: str = Field(default="", max_length=1000)


class ClaimIn(Input):
    boot_id: Id
    incarnation: Id
    authority_epoch: int = Field(strict=True, ge=1)


class MissionReportIn(Input):
    identity: dict | None
    state: Literal["running", "completed", "failed", "cancelled", "unknown"]
    detail: str = Field(default="", max_length=1000)
    summary: dict = Field(default_factory=dict)


@router.post("/api/v1/projects", status_code=201)
def create_project(
    body: ProjectIn, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None
):
    data = body.model_dump()
    return service.mutate(db, p, request.url.path, key, data, lambda: service.create_project(db, p, data))


@router.get("/api/v1/projects")
def list_projects(p: PrincipalRead, db: Database):
    rows = db.scalars(
        select(Project)
        .where(Project.owner_user_id == p.user.id)
        .order_by(Project.created_at.desc())
        .limit(200)
    )
    return [service.project_out(row) for row in rows]


@router.post("/api/v1/robots", status_code=201)
def create_robot(
    body: RobotIn, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None
):
    data = body.model_dump()
    return service.mutate(db, p, request.url.path, key, data, lambda: service.create_robot(db, p, data))


@router.get("/api/v1/robots")
def list_robots(project_id: Id, p: PrincipalRead, db: Database):
    from ..evaluation_models import EvaluationRun
    from ..services.evaluations import TERMINAL

    service.project_for(db, project_id, p)
    reservations = dict(db.execute(select(EvaluationRun.robot_id, EvaluationRun.id).where(
        EvaluationRun.project_id == project_id, EvaluationRun.state.not_in(TERMINAL),
    )).all())
    rows = db.scalars(
        select(Robot).where(Robot.project_id == project_id).order_by(Robot.created_at.desc()).limit(200)
    )
    return [service.robot_out(row, evaluation_id=reservations.get(row.id)) for row in rows]


@router.post("/api/v1/applications", status_code=201)
def create_application(
    body: ApplicationIn, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None
):
    data = body.model_dump()
    return service.mutate(db, p, request.url.path, key, data, lambda: service.create_application(db, p, data))


@router.get("/api/v1/applications")
def list_applications(project_id: Id, p: PrincipalRead, db: Database):
    service.project_for(db, project_id, p)
    rows = db.scalars(
        select(Application)
        .where(Application.project_id == project_id)
        .order_by(Application.created_at.desc())
        .limit(200)
    )
    return [service.application_out(row) for row in rows]


@router.post("/api/v1/applications/{application_id}/releases", status_code=201)
def create_release(
    application_id: str,
    body: ReleaseIn,
    request: Request,
    p: PrincipalWrite,
    db: Database,
    key: IdempotencyKey = None,
):
    data = body.model_dump()
    return service.mutate(
        db, p, request.url.path, key, data, lambda: service.create_release(db, p, application_id, data)
    )


@router.get("/api/v1/applications/{application_id}/releases")
def list_releases(application_id: str, p: PrincipalRead, db: Database):
    service.resource_for(db, Application, application_id, p)
    rows = db.scalars(
        select(ApplicationRelease)
        .where(ApplicationRelease.application_id == application_id)
        .order_by(ApplicationRelease.created_at.desc())
        .limit(200)
    )
    return [service.release_out(row) for row in rows]


@router.post("/api/v1/deployments", status_code=201)
def create_deployment(
    body: DeploymentIn, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None
):
    data = body.model_dump()
    return service.mutate(db, p, request.url.path, key, data, lambda: service.create_deployment(db, p, data))


@router.get("/api/v1/deployments/{deployment_id}")
def get_deployment(deployment_id: str, p: PrincipalRead, db: Database):
    return service.deployment_out(service.resource_for(db, Deployment, deployment_id, p))


@router.get("/api/v1/deployments")
def list_deployments(
    project_id: Id, p: PrincipalRead, db: Database,
    robot_id: str | None = Query(default=None, max_length=64),
):
    service.project_for(db, project_id, p)
    statement = select(Deployment).where(Deployment.project_id == project_id)
    if robot_id is not None:
        robot = service.resource_for(db, Robot, robot_id, p)
        if robot.project_id != project_id:
            raise HTTPException(404, "robot not found in project")
        statement = statement.where(Deployment.robot_id == robot_id)
    return [service.deployment_out(row) for row in db.scalars(
        statement.order_by(Deployment.created_at.desc()).limit(200)
    )]


@router.post("/api/v1/robots/{robot_id}/missions", status_code=201)
def create_mission(
    robot_id: str,
    body: MissionIn,
    request: Request,
    p: PrincipalWrite,
    db: Database,
    key: IdempotencyKey = None,
):
    data = body.model_dump()
    return service.mutate(
        db, p, request.url.path, key, data, lambda: service.create_mission(db, p, robot_id, data)
    )


@router.get("/api/v1/missions/{mission_id}")
def get_mission(mission_id: str, p: PrincipalRead, db: Database):
    return service.mission_out(service.resource_for(db, Mission, mission_id, p))


@router.get("/api/v1/missions")
def list_missions(
    project_id: Id, p: PrincipalRead, db: Database, limit: int = Query(default=100, ge=1, le=200)
):
    service.project_for(db, project_id, p)
    rows = db.scalars(
        select(Mission)
        .where(Mission.project_id == project_id)
        .order_by(Mission.created_at.desc())
        .limit(limit)
    )
    return [service.mission_out(row) for row in rows]


@router.post("/api/v1/missions/{mission_id}/cancel")
def cancel_mission(
    mission_id: str,
    body: CancelIn,
    request: Request,
    p: PrincipalWrite,
    db: Database,
    key: IdempotencyKey = None,
):
    data = body.model_dump()
    return service.mutate(
        db, p, request.url.path, key, data, lambda: service.cancel_mission(db, p, mission_id, data)
    )


@router.get("/api/v1/episodes/{episode_id}")
def get_episode(episode_id: str, p: PrincipalRead, db: Database):
    return service.episode_out(service.resource_for(db, Episode, episode_id, p))


@router.get("/api/v1/episodes")
def list_episodes(mission_id: Id, p: PrincipalRead, db: Database):
    service.resource_for(db, Mission, mission_id, p)
    return [
        service.episode_out(row)
        for row in db.scalars(select(Episode).where(Episode.mission_id == mission_id))
    ]


@router.get("/api/agent/v1/robots/{robot_id}/desired")
def desired(robot_id: str, device: DeviceIdentity, db: Database):
    # Reconciliation may expire an unclaimed request. Serialize it with claim and
    # revalidate the admitted device before making that durable state transition.
    return _device_write(db, device, robot_id, lambda robot: service.desired(db, robot))


def _device_write(db: Session, device: Device, robot_id: str, action):
    try:
        with write_txn(db):
            fresh = assert_admitted_binding(db, device)
            return action(service.device_robot(db, robot_id, fresh))
    except StaleBinding as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/agent/v1/robots/{robot_id}/deployments/{deployment_id}/report")
def deployment_report(
    robot_id: str, deployment_id: str, body: DeploymentReportIn, device: DeviceIdentity, db: Database
):
    return _device_write(
        db,
        device,
        robot_id,
        lambda robot: service.report_deployment(db, robot, deployment_id, body.model_dump()),
    )


@router.post("/api/agent/v1/robots/{robot_id}/missions/{mission_id}/claim")
def claim(robot_id: str, mission_id: str, body: ClaimIn, device: DeviceIdentity, db: Database):
    result = _device_write(
        db, device, robot_id, lambda robot: service.claim(db, robot, device, mission_id, body.model_dump())
    )
    if result.get("expired"):
        # Report 410 only after the expired request's durable terminal outcome is committed.
        raise HTTPException(410, "mission authorization expired before claim")
    return result


@router.post("/api/agent/v1/robots/{robot_id}/missions/{mission_id}/report")
def mission_report(
    robot_id: str, mission_id: str, body: MissionReportIn, device: DeviceIdentity, db: Database
):
    return _device_write(
        db, device, robot_id, lambda robot: service.report_mission(db, robot, mission_id, body.model_dump())
    )
