"""Human-requested, device-reported checks; no uploaded evidence grants motor authority."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, FiniteFloat

from ..auth import StaleBinding, assert_admitted_binding
from ..db import write_txn
from ..platform_models import Robot
from ..services import platform
from ..services import robot_qualification as service
from .platform import (
    Database,
    DeviceIdentity,
    Digest,
    IdempotencyKey,
    Input,
    Name,
    PrincipalRead,
    PrincipalWrite,
)

router = APIRouter(tags=["simulation qualification"])


class Evidence(Input):
    engine_version: str = Field(default="", max_length=120)
    joint_names: list[Name] = Field(default_factory=list, max_length=128)
    camera_names: list[Name] = Field(default_factory=list, max_length=64)
    command_interface: str = Field(default="", max_length=80)
    checks: list[Name] = Field(default_factory=list, max_length=20)
    steps: int = Field(default=0, ge=0, le=1000, strict=True)
    sim_seconds: FiniteFloat = Field(default=0, ge=0)
    wall_seconds: FiniteFloat = Field(default=0, ge=0)
    max_joint_displacement: FiniteFloat = Field(default=0, ge=0)
    host: dict[str, str] = Field(default_factory=dict, max_length=12)


class ReportIn(Input):
    profile_digest: Digest
    binding_epoch: int = Field(ge=1, strict=True)
    asset_sha256: Digest | None = None
    state: Literal["passed", "failed"]
    detail: str = Field(default="", max_length=1000)
    evidence: Evidence


@router.post("/api/v1/robots/{robot_id}/qualification", status_code=201)
def request_check(robot_id: str, request: Request, p: PrincipalWrite, db: Database,
                  key: IdempotencyKey = None):
    return platform.mutate(db, p, request.url.path, key, {}, lambda: service.request_check(db, p, robot_id))


@router.get("/api/v1/robots/{robot_id}/qualification")
def get_check(robot_id: str, p: PrincipalRead, db: Database):
    robot = platform.resource_for(db, Robot, robot_id, p)
    return {"qualification": service.out(db, service.latest(db, robot.id))}


def device_write(db, device, action):
    try:
        with write_txn(db):
            fresh = assert_admitted_binding(db, device)
            return action(fresh)
    except StaleBinding as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/agent/v1/registry")
def registry(device: DeviceIdentity, db: Database):
    return device_write(db, device, lambda fresh: service.desired(db, fresh))


@router.get("/api/agent/v1/action-verification-keys")
def action_keys(device: DeviceIdentity, db: Database):
    def read(fresh):
        if not fresh.simulated:
            raise HTTPException(403, "automatic simulation worker setup requires a simulated device")
        return platform.action_verification_document()
    return device_write(db, device, read)


@router.post("/api/agent/v1/qualifications/{qualification_id}/report")
def report(qualification_id: str, body: ReportIn, device: DeviceIdentity, db: Database):
    return device_write(db, device, lambda fresh: service.report_check(db, fresh, qualification_id, body.model_dump()))
