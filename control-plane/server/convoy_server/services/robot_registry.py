"""Project-owned registration, immutable profiles and explicit simulation lineage."""
from __future__ import annotations

from convoy_contracts.execution import canonical_digest
from convoy_contracts.registered import REGISTERED_PROFILE
from fastapi import HTTPException
from sqlalchemy import select

from ..ids import iso, new_id
from ..models import Device, EnrollmentToken
from ..platform_models import Project, Robot
from ..robot_profiles import simulation_readiness
from ..robot_registry_models import Fleet, FleetMember, RobotProfile, RobotRegistration
from . import platform


def lock_project(db, project_id, principal):
    platform.project_for(db, project_id, principal)
    db.execute(select(Project.id).where(Project.id == project_id).with_for_update()).first()


def profile_out(row):
    return {"id": row.id, "project_id": row.project_id, "name": row.name,
            "revision": row.revision, "digest": row.digest, "spec": row.spec,
            "simulation": simulation_readiness(row.spec), "created_at": iso(row.created_at)}


def create_profile(db, principal, data):
    lock_project(db, data["project_id"], principal)
    latest = db.scalar(select(RobotProfile).where(
        RobotProfile.project_id == data["project_id"], RobotProfile.name == data["name"],
    ).order_by(RobotProfile.revision.desc()).limit(1))
    if data["expected_revision"] != (latest.revision if latest else 0):
        raise HTTPException(409, "profile revision changed")
    row = RobotProfile(id=new_id("rpf"), project_id=data["project_id"], name=data["name"],
                       revision=data["expected_revision"] + 1,
                       digest=canonical_digest(data["spec"]), spec=data["spec"])
    db.add(row)
    db.flush()
    return profile_out(row)


def owned_device(db, principal, device_id):
    device = db.get(Device, device_id)
    if device is None or device.retired_at or device.credential_revoked_at:
        raise HTTPException(404, "device not found")
    enrollment = db.scalar(select(EnrollmentToken.id).where(
        EnrollmentToken.device_id == device.id, EnrollmentToken.created_by == principal.user.id,
        EnrollmentToken.consumed_at.is_not(None),
    ))
    if enrollment is None:
        raise HTTPException(404, "device not found")
    if db.scalar(select(Robot.id).where(Robot.device_id == device.id)):
        raise HTTPException(409, "device is already attached to a robot")
    return device


def register_robot(db, principal, data):
    lock_project(db, data["project_id"], principal)
    profile = platform.resource_for(db, RobotProfile, data["profile_id"], principal)
    if profile.project_id != data["project_id"]:
        raise HTTPException(404, "profile not found in project")
    device = owned_device(db, principal, data["device_id"])
    if device.simulated != (data["kind"] == "simulated"):
        raise HTTPException(409, "device kind does not match robot kind")
    engine = data["simulation_engine"]
    if data["kind"] == "physical" and engine is not None:
        raise HTTPException(422, "physical registration cannot select a simulation engine")
    if data["kind"] == "simulated" and engine not in [m["engine"] for m in profile.spec["simulations"]]:
        raise HTTPException(409, "profile has no model for this simulation engine")
    source_id = data["source_robot_id"]
    if source_id:
        source = platform.resource_for(db, Robot, source_id, principal)
        registration = db.get(RobotRegistration, source.id)
        if source.project_id != profile.project_id or not registration or registration.kind != "physical":
            raise HTTPException(409, "simulation source must be a physical robot in this project")
        if data["kind"] != "simulated" or registration.profile_id != profile.id:
            raise HTTPException(409, "simulation must reference the physical robot's exact profile revision")
    fleet_id = data["fleet_id"]
    if fleet_id:
        fleet = platform.resource_for(db, Fleet, fleet_id, principal)
        if fleet.project_id != profile.project_id:
            raise HTTPException(404, "fleet not found in project")
    default_profile = REGISTERED_PROFILE if profile.spec["command_interface"] == "joint-position" else "custom-unqualified"
    row = Robot(id=new_id("rob"), project_id=profile.project_id, device_id=device.id,
                name=data["name"], profile=profile.spec["execution_profile"] or default_profile)
    db.add(row)
    db.flush()
    db.add(RobotRegistration(robot_id=row.id, profile_id=profile.id, kind=data["kind"],
                             source_robot_id=source_id, simulation_engine=engine))
    if fleet_id:
        db.add(FleetMember(robot_id=row.id, fleet_id=fleet_id))
    db.flush()
    return platform.robot_out(row, db=db)


def fleet_out(db, row):
    return {"id": row.id, "project_id": row.project_id, "name": row.name,
            "robot_ids": list(db.scalars(select(FleetMember.robot_id).where(
                FleetMember.fleet_id == row.id).order_by(FleetMember.robot_id))),
            "created_at": iso(row.created_at)}


def create_fleet(db, principal, data):
    lock_project(db, data["project_id"], principal)
    row = Fleet(id=new_id("flt"), **data)
    db.add(row)
    db.flush()
    return fleet_out(db, row)


def assign_fleet(db, principal, fleet_id, data):
    fleet = platform.resource_for(db, Fleet, fleet_id, principal)
    lock_project(db, fleet.project_id, principal)
    # Validate the entire batch before changing membership. Expected membership prevents a stale
    # browser from silently moving a robot someone else assigned in the meantime.
    for assignment in data["assignments"]:
        robot = platform.resource_for(db, Robot, assignment["robot_id"], principal)
        if robot.project_id != fleet.project_id:
            raise HTTPException(404, "robot not found in fleet project")
        member = db.get(FleetMember, robot.id)
        if (member.fleet_id if member else None) != assignment["expected_fleet_id"]:
            raise HTTPException(409, "robot fleet membership changed")
    for assignment in data["assignments"]:
        member = db.get(FleetMember, assignment["robot_id"])
        if member:
            member.fleet_id = fleet.id
        else:
            db.add(FleetMember(robot_id=assignment["robot_id"], fleet_id=fleet.id))
    db.flush()
    return fleet_out(db, fleet)
