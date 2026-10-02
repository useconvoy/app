"""A simulator readiness check is bound to its exact model, device binding and request."""
from datetime import timedelta

from convoy_contracts.execution import canonical_digest
from fastapi import HTTPException
from sqlalchemy import select

from ..ids import aware, iso, new_id, utcnow
from ..models import Device
from ..platform_models import Robot
from ..robot_registry_models import RobotProfile, RobotQualification, RobotRegistration
from . import platform


def latest(db, robot_id):
    return db.scalar(select(RobotQualification).where(RobotQualification.robot_id == robot_id)
                     .order_by(RobotQualification.generation.desc()).limit(1))


def out(db, row):
    if row is None:
        return None
    robot = db.get(Robot, row.robot_id)
    device = db.get(Device, robot.device_id)
    stale = not device or device.retired_at or device.credential_revoked_at or device.binding_epoch != row.binding_epoch
    state = "stale" if stale else "expired" if row.state == "requested" and aware(row.expires_at) <= utcnow() else row.state
    return {"id": row.id, "robot_id": row.robot_id, "profile_id": row.profile_id,
            "profile_digest": row.profile_digest, "binding_epoch": row.binding_epoch,
            "generation": row.generation, "engine": row.engine, "state": state,
            "report": row.report, "created_at": iso(row.created_at), "expires_at": iso(row.expires_at),
            "completed_at": iso(row.completed_at),
            "scope": "Simulator model and controller checks; not policy timing or physical dynamics validation."}


def request_check(db, principal, robot_id):
    platform.dispatch_allowed(db)
    robot = platform.resource_for(db, Robot, robot_id, principal)
    registration = db.get(RobotRegistration, robot.id)
    device = db.get(Device, robot.device_id)
    if not registration or registration.kind != "simulated":
        raise HTTPException(409, "verify a simulated instance of this robot")
    if device.retired_at or device.credential_revoked_at:
        raise HTTPException(409, "robot connection is retired or revoked")
    profile = db.get(RobotProfile, registration.profile_id)
    previous = latest(db, robot.id)
    if previous and out(db, previous)["state"] == "requested":
        raise HTTPException(409, "simulator verification is already requested")
    row = RobotQualification(
        id=new_id("rqc"), project_id=robot.project_id, robot_id=robot.id, profile_id=profile.id,
        profile_digest=profile.digest, binding_epoch=device.binding_epoch,
        generation=previous.generation + 1 if previous else 1, engine=registration.simulation_engine,
        expires_at=utcnow() + timedelta(minutes=10),
    )
    db.add(row)
    db.flush()
    return out(db, row)


def device_registration(db, device):
    robot = db.scalar(select(Robot).where(Robot.device_id == device.id))
    registration = db.get(RobotRegistration, robot.id) if robot else None
    if not registration:
        return None
    return robot, registration


def desired(db, device):
    pair = device_registration(db, device)
    if pair is None:
        return {"robot": None, "qualification": None}
    robot, registration = pair
    profile = db.get(RobotProfile, registration.profile_id)
    return {"robot": platform.robot_out(robot, db=db),
            "profile": {"id": profile.id, "digest": profile.digest, "spec": profile.spec},
            "qualification": out(db, latest(db, robot.id))}


def report_check(db, device, qualification_id, body):
    row = db.get(RobotQualification, qualification_id)
    if row is None:
        raise HTTPException(404, "verification request not found")
    robot = db.get(Robot, row.robot_id)
    if robot.device_id != device.id:
        raise HTTPException(404, "verification request not found")
    if row.binding_epoch != device.binding_epoch or body["binding_epoch"] != row.binding_epoch:
        raise HTTPException(409, "verification belongs to a different device binding")
    if body["profile_digest"] != row.profile_digest:
        raise HTTPException(409, "verification profile changed")
    digest = canonical_digest(body)
    if row.report_digest:
        if row.report_digest != digest:
            raise HTTPException(409, "verification report is immutable")
        return out(db, row)
    if latest(db, robot.id).id != row.id or aware(row.expires_at) <= utcnow():
        raise HTTPException(409, "verification request expired or was superseded")
    profile = db.get(RobotProfile, row.profile_id)
    spec = profile.spec
    model = next(m for m in spec["simulations"] if m["engine"] == row.engine)
    evidence = body["evidence"]
    if body["state"] == "passed":
        required_checks = {"asset-digest", "model-load", "joint-contract", "controller-contract", "physics-step"}
        expected_joints = [j["name"] for j in spec["joints"] if j["kind"] != "fixed"]
        if (body["asset_sha256"] != model["asset"]["sha256"]
                or evidence["engine_version"] != model["engine_version"]
                or evidence["joint_names"] != expected_joints
                or evidence["command_interface"] != spec["command_interface"]
                or not required_checks.issubset(evidence["checks"])
                or evidence["steps"] < 1 or evidence["sim_seconds"] <= 0 or evidence["wall_seconds"] <= 0):
            raise HTTPException(422, "passing report does not satisfy the pinned simulation contract")
        if evidence["camera_names"] != [s["name"] for s in spec["sensors"] if s["kind"] == "camera"]:
            raise HTTPException(422, "passing report does not cover the declared cameras")
        if evidence["camera_names"] and "camera-render" not in evidence["checks"]:
            raise HTTPException(422, "passing report must render the declared cameras")
    row.state, row.report, row.report_digest, row.completed_at = body["state"], body, digest, utcnow()
    db.flush()
    return out(db, row)
