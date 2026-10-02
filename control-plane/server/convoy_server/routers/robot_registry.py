"""Registration and profile APIs share the platform ownership/idempotency boundary."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, model_validator
from sqlalchemy import select

from ..platform_models import Robot
from ..robot_profiles import RobotProfileSpec
from ..robot_registry_models import Fleet, RobotProfile
from ..services import platform
from ..services import robot_registry as service
from .platform import Database, Id, IdempotencyKey, Input, Name, PrincipalRead, PrincipalWrite

router = APIRouter(tags=["robot registry"])


class ProfileIn(Input):
    project_id: Id
    name: Name
    expected_revision: int = Field(strict=True, ge=0)
    spec: RobotProfileSpec


class RegistrationIn(Input):
    project_id: Id
    name: Name
    device_id: Id
    profile_id: Id
    kind: Literal["physical", "simulated"]
    simulation_engine: Literal["mujoco", "isaac"] | None = None
    source_robot_id: Id | None = None
    fleet_id: Id | None = None


class FleetIn(Input):
    project_id: Id
    name: Name


class Assignment(Input):
    robot_id: Id
    expected_fleet_id: Id | None


class AssignmentsIn(Input):
    assignments: list[Assignment] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique(self):
        if len({a.robot_id for a in self.assignments}) != len(self.assignments):
            raise ValueError("duplicate robot assignment")
        return self


@router.post("/api/v1/robot-profiles", status_code=201)
def create_profile(body: ProfileIn, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None):
    data = body.model_dump()
    return platform.mutate(db, p, request.url.path, key, data, lambda: service.create_profile(db, p, data))


@router.get("/api/v1/robot-profiles")
def list_profiles(project_id: Id, p: PrincipalRead, db: Database):
    platform.project_for(db, project_id, p)
    return [service.profile_out(r) for r in db.scalars(select(RobotProfile).where(
        RobotProfile.project_id == project_id).order_by(RobotProfile.created_at.desc()).limit(200))]


@router.get("/api/v1/robot-profiles/{profile_id}")
def get_profile(profile_id: str, p: PrincipalRead, db: Database):
    return service.profile_out(platform.resource_for(db, RobotProfile, profile_id, p))


@router.post("/api/v1/robot-registrations", status_code=201)
def register_robot(body: RegistrationIn, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None):
    data = body.model_dump()
    return platform.mutate(db, p, request.url.path, key, data, lambda: service.register_robot(db, p, data))


@router.get("/api/v1/robots/{robot_id}")
def get_robot(robot_id: str, p: PrincipalRead, db: Database):
    return platform.robot_out(platform.resource_for(db, Robot, robot_id, p), db=db)


@router.post("/api/v1/fleets", status_code=201)
def create_fleet(body: FleetIn, request: Request, p: PrincipalWrite, db: Database, key: IdempotencyKey = None):
    data = body.model_dump()
    return platform.mutate(db, p, request.url.path, key, data, lambda: service.create_fleet(db, p, data))


@router.get("/api/v1/fleets")
def list_fleets(project_id: Id, p: PrincipalRead, db: Database):
    platform.project_for(db, project_id, p)
    return [service.fleet_out(db, r) for r in db.scalars(select(Fleet).where(
        Fleet.project_id == project_id).order_by(Fleet.created_at.desc()).limit(200))]


@router.post("/api/v1/fleets/{fleet_id}/members")
def assign_members(fleet_id: str, body: AssignmentsIn, request: Request, p: PrincipalWrite, db: Database,
                   key: IdempotencyKey = None):
    data = body.model_dump()
    return platform.mutate(db, p, request.url.path, key, data, lambda: service.assign_fleet(db, p, fleet_id, data))


@router.post("/api/v1/fleets/{fleet_id}/members/{robot_id}/remove")
def remove_member(fleet_id: str, robot_id: str, request: Request, p: PrincipalWrite, db: Database,
                  key: IdempotencyKey = None):
    from ..robot_registry_models import FleetMember

    def action():
        fleet = platform.resource_for(db, Fleet, fleet_id, p)
        service.lock_project(db, fleet.project_id, p)
        robot = platform.resource_for(db, Robot, robot_id, p)
        member = db.get(FleetMember, robot.id)
        if robot.project_id != fleet.project_id or not member or member.fleet_id != fleet.id:
            raise HTTPException(409, "robot is no longer in this fleet")
        db.delete(member)
        db.flush()
        return service.fleet_out(db, fleet)

    return platform.mutate(db, p, request.url.path, key, {}, action)


@router.get("/api/v1/robot-connections")
def list_connections(p: PrincipalRead, db: Database):
    from ..models import Device, EnrollmentToken
    from ..serialize import device_out

    owned = select(EnrollmentToken.device_id).where(
        EnrollmentToken.created_by == p.user.id, EnrollmentToken.consumed_at.is_not(None),
    )
    rows = db.scalars(select(Device).where(
        Device.id.in_(owned), Device.retired_at.is_(None), Device.credential_revoked_at.is_(None),
        Device.id.not_in(select(Robot.device_id)),
    ).order_by(Device.name).limit(200))
    return [device_out(row, brief=True) for row in rows]
