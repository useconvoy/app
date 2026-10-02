"""Application lifecycle for one installation and one qualified simulation profile.

Project ownership scopes these new APIs. Legacy installation-wide APIs still require a
separate tenancy migration before this server can be offered as a multi-tenant service.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from convoy_contracts.execution import (
    canonical_digest,
    sign_grant,
    validate_identity,
)
from convoy_contracts.pairing import (
    PAIRED_PROFILE,
    action_manifest,
    release_profiles,
    sign_planner_grant,
    validate_release_manifest,
)
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, assert_live_principal, audit
from ..config import get_settings
from ..db import write_txn
from ..ids import aware, iso, new_id, utcnow
from ..models import Device, EnrollmentToken, Installation
from ..platform_models import (
    Application,
    ApplicationRelease,
    Deployment,
    Episode,
    Mission,
    MutationReceipt,
    Project,
    Robot,
)
from ..validation import reject_invalid_json_values

TERMINAL = ("completed", "failed", "cancelled")


def project_for(db: Session, project_id: str, principal: Principal) -> Project:
    row = db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_user_id == principal.user.id)
    )
    if row is None:
        raise HTTPException(404, "project not found")
    return row


def resource_for(db: Session, model: Any, resource_id: str, principal: Principal):
    row = db.get(model, resource_id)
    if row is None:
        raise HTTPException(404, "resource not found")
    project_for(db, row.project_id, principal)
    return row


def device_robot(db: Session, robot_id: str, device: Device) -> Robot:
    robot = db.scalar(select(Robot).where(Robot.id == robot_id, Robot.device_id == device.id))
    if robot is None:
        raise HTTPException(404, "robot not found")
    if not device.simulated or not get_settings().simulator or robot.profile not in release_profiles():
        raise HTTPException(409, "M1 execution supports the configured simulator profile only")
    return robot


def dispatch_allowed(db: Session) -> None:
    installation = db.get(Installation, 1)
    if installation and (installation.quarantined_at or installation.dispatch_paused_at):
        raise HTTPException(409, "installation dispatch is paused")


def require_idempotency_key(key: str | None) -> str:
    if not key or len(key) > 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise HTTPException(422, "Idempotency-Key must contain 1–128 printable non-space characters")
    return key


def previous_receipt(
    db: Session, principal: Principal, route: str, key: str, digest: str
) -> MutationReceipt | None:
    """The committed receipt for this owner, route and key; a different payload is a conflict."""
    previous = db.scalar(
        select(MutationReceipt).where(
            MutationReceipt.owner_user_id == principal.user.id,
            MutationReceipt.route == route,
            MutationReceipt.key == key,
        )
    )
    if previous is not None and previous.payload_digest != digest:
        raise HTTPException(409, "Idempotency-Key was already used with a different payload")
    return previous


def record_receipt(
    db: Session, principal: Principal, route: str, key: str, digest: str, response: dict
) -> None:
    db.add(
        MutationReceipt(
            id=new_id("rcp"),
            owner_user_id=principal.user.id,
            route=route,
            key=key,
            payload_digest=digest,
            response=response,
        )
    )


def mutate(
    db: Session, principal: Principal, route: str, key: str | None, payload: dict, action: Callable[[], dict]
) -> dict:
    """One durable transaction for the mutation and its idempotent response."""
    key = require_idempotency_key(key)
    reject_invalid_json_values(payload)
    digest = canonical_digest(payload)
    with write_txn(db):
        assert_live_principal(db, principal, "operator")
        previous = previous_receipt(db, principal, route, key, digest)
        if previous is not None:
            return previous.response
        response = action()
        record_receipt(db, principal, route, key, digest, response)
        audit(db, principal, "platform.mutate", response.get("id"), route=route)
        return response


def project_out(row: Project) -> dict:
    return {"id": row.id, "name": row.name, "created_at": iso(row.created_at)}


def robot_out(row: Robot, *, db: Session, evaluation_id: str | None = None) -> dict:
    from ..robot_registry_models import FleetMember, RobotRegistration

    device = db.get(Device, row.device_id)
    registration = db.get(RobotRegistration, row.id)
    member = db.get(FleetMember, row.id)
    from . import robot_qualification

    qualification = robot_qualification.out(db, robot_qualification.latest(db, row.id)) if registration else None
    return {
        "id": row.id,
        "project_id": row.project_id,
        "device_id": row.device_id,
        "name": row.name,
        "profile": row.profile,
        "generation": row.generation,
        "evaluation_id": evaluation_id,
        "simulated": bool(device and device.simulated),
        "profile_id": registration.profile_id if registration else None,
        "source_robot_id": registration.source_robot_id if registration else None,
        "simulation_engine": registration.simulation_engine if registration else None,
        "fleet_id": member.fleet_id if member else None,
        "qualification": qualification,
        "created_at": iso(row.created_at),
    }


def application_out(row: Application) -> dict:
    return {"id": row.id, "project_id": row.project_id, "name": row.name, "created_at": iso(row.created_at)}


def release_out(row: ApplicationRelease) -> dict:
    return {
        "id": row.id,
        "application_id": row.application_id,
        "digest": row.digest,
        "manifest": row.manifest,
        "created_at": iso(row.created_at),
    }


def deployment_out(row: Deployment) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "robot_id": row.robot_id,
        "release_id": row.release_id,
        "generation": row.generation,
        "state": row.state,
        "detail": row.detail,
        "observed_at": iso(row.observed_at),
        "created_at": iso(row.created_at),
    }


def mission_out(row: Mission) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "robot_id": row.robot_id,
        "deployment_id": row.deployment_id,
        "release_id": row.release_id,
        "release_digest": row.release_digest,
        "generation": row.generation,
        "seed": row.seed,
        "expires_at": aware(row.expires_at).timestamp(),
        "state": row.state,
        "detail": row.detail,
        "identity": row.identity,
        "episode_id": row.episode_id,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }


def episode_out(row: Episode) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "mission_id": row.mission_id,
        "release_digest": row.release_digest,
        "state": row.state,
        "detail": row.detail,
        "summary": row.summary,
        "identity": row.identity,
        "created_at": iso(row.created_at),
    }


def create_project(db: Session, p: Principal, data: dict) -> dict:
    row = Project(id=new_id("prj"), owner_user_id=p.user.id, name=data["name"])
    db.add(row)
    db.flush()
    return project_out(row)


def create_robot(db: Session, p: Principal, data: dict) -> dict:
    project_for(db, data["project_id"], p)
    device = db.get(Device, data["device_id"])
    if device is None or device.retired_at:
        raise HTTPException(404, "device not found")
    owned_enrollment = db.scalar(
        select(EnrollmentToken.id).where(
            EnrollmentToken.device_id == device.id,
            EnrollmentToken.created_by == p.user.id,
            EnrollmentToken.consumed_at.is_not(None),
        )
    )
    if owned_enrollment is None and p.user.role != "admin":
        raise HTTPException(404, "device not found")
    if not device.simulated or not get_settings().simulator or data["profile"] not in release_profiles():
        raise HTTPException(409, "M1 supports a simulated device and the MetaWorld profile only")
    if db.scalar(select(Robot.id).where(Robot.device_id == device.id)):
        raise HTTPException(409, "device is already attached to a robot")
    row = Robot(id=new_id("rob"), **data)
    db.add(row)
    db.flush()
    return robot_out(row, db=db)


def create_application(db: Session, p: Principal, data: dict) -> dict:
    project_for(db, data["project_id"], p)
    row = Application(id=new_id("app"), **data)
    db.add(row)
    db.flush()
    return application_out(row)


def create_release(db: Session, p: Principal, app_id: str, data: dict) -> dict:
    app = resource_for(db, Application, app_id, p)
    try:
        manifest = validate_release_manifest(data["manifest"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    digest = canonical_digest(manifest)
    row = db.scalar(
        select(ApplicationRelease).where(
            ApplicationRelease.application_id == app.id,
            ApplicationRelease.digest == digest,
        )
    )
    if row is None:
        row = ApplicationRelease(id=new_id("apr"), application_id=app.id, digest=digest, manifest=manifest)
        db.add(row)
        db.flush()
    return release_out(row)


def unresolved_mission(db: Session, robot: Robot) -> Mission | None:
    return db.scalar(select(Mission).where(Mission.robot_id == robot.id, Mission.state.not_in(TERMINAL)))


def create_deployment(db: Session, p: Principal, data: dict, *, evaluation_id: str | None = None) -> dict:
    from .evaluations import require_available, require_promotion
    from .robot_qualification import require_execution

    dispatch_allowed(db)
    robot = resource_for(db, Robot, data["robot_id"], p)
    require_execution(db, robot)
    require_available(db, robot.id, evaluation_id)
    if robot.generation != data["expected_generation"]:
        raise HTTPException(409, "robot generation changed")
    if unresolved_mission(db, robot):
        raise HTTPException(409, "robot has an active or unresolved mission")
    release = db.get(ApplicationRelease, data["release_id"])
    app = db.get(Application, release.application_id) if release else None
    if app is None or app.project_id != robot.project_id:
        raise HTTPException(404, "release not found in robot project")
    if require_execution(db, robot, release) and robot.profile == "custom-unqualified":
        # Upgrade an earlier registry-only record only after an explicitly requested
        # deployment proves its exact profile/asset/interface and current verification.
        from convoy_contracts.registered import REGISTERED_PROFILE

        robot.profile = REGISTERED_PROFILE
    if release.manifest["profile"] != robot.profile:
        raise HTTPException(409, "release profile does not match robot")
    if robot.profile == PAIRED_PROFILE and evaluation_id is None:
        # An evaluation is admitted by the API before its DB-only job creates
        # this deployment. That trusted internal path must not need signing keys.
        validate_execution_signing(paired=True)
    if evaluation_id is None:
        require_promotion(db, release)
    robot.generation += 1
    row = Deployment(
        id=new_id("dep"),
        project_id=robot.project_id,
        robot_id=robot.id,
        release_id=release.id,
        generation=robot.generation,
    )
    db.add(row)
    db.flush()
    return deployment_out(row)


def create_mission(db: Session, p: Principal, robot_id: str, data: dict, *, evaluation_id: str | None = None) -> dict:
    from .evaluations import require_available, require_promotion
    from .robot_qualification import require_execution

    dispatch_allowed(db)
    robot = resource_for(db, Robot, robot_id, p)
    require_execution(db, robot)
    require_available(db, robot.id, evaluation_id)
    if robot.generation != data["expected_generation"]:
        raise HTTPException(409, "robot generation changed")
    if unresolved_mission(db, robot):
        raise HTTPException(409, "robot has an active or unresolved mission")
    deployment = db.get(Deployment, data["deployment_id"])
    if not deployment or deployment.robot_id != robot.id:
        raise HTTPException(404, "deployment not found")
    if deployment.generation != robot.generation or deployment.state != "ready":
        raise HTTPException(409, "current deployment is not ready")
    release = db.get(ApplicationRelease, deployment.release_id)
    require_execution(db, robot, release)
    if evaluation_id is None:
        require_promotion(db, release)
    ttl = min(data["ttl_s"], action_manifest(release.manifest)["execution"]["mission_timeout_s"])
    row = Mission(
        id=new_id("mis"),
        project_id=robot.project_id,
        robot_id=robot.id,
        deployment_id=deployment.id,
        release_id=release.id,
        release_digest=release.digest,
        generation=robot.generation,
        seed=data["seed"],
        expires_at=utcnow() + timedelta(seconds=ttl),
    )
    db.add(row)
    db.flush()
    return mission_out(row)


def cancel_mission(db: Session, p: Principal, mission_id: str, data: dict) -> dict:
    row = resource_for(db, Mission, mission_id, p)
    if row.state not in TERMINAL and row.state != "cancel_requested":
        row.state = "cancel_requested"
        row.detail = data["reason"]
        row.updated_at = utcnow()
    return mission_out(row)


def expire_unclaimed_mission(db: Session, mission: Mission | None) -> bool:
    """Called under write_txn: expiry may settle only authority never granted."""
    if (
        mission is None
        or mission.state != "requested"
        or mission.identity is not None
        or mission.execution_started
        or aware(mission.expires_at) > utcnow()
    ):
        return False
    finish(db, mission, {
        "state": "failed",
        "identity": None,
        "detail": "authorization expired before claim",
        "summary": {"reason": "authorization_expired"},
    })
    return True


def desired(db: Session, robot: Robot) -> dict:
    from .evaluations import active_for_robot

    deployment = db.scalar(
        select(Deployment).where(Deployment.robot_id == robot.id, Deployment.generation == robot.generation)
    )
    mission = db.scalar(
        select(Mission).where(Mission.robot_id == robot.id).order_by(Mission.created_at.desc())
    )
    # Polling must release an expired, never-claimed request even when component
    # readiness is blocked and the coordinator cannot reach the claim endpoint.
    expire_unclaimed_mission(db, mission)
    deployed = None
    if deployment:
        deployed = deployment_out(deployment)
        deployed["release"] = release_out(db.get(ApplicationRelease, deployment.release_id))
    evaluation = active_for_robot(db, robot.id)
    return {
        "robot": robot_out(robot, db=db, evaluation_id=evaluation.id if evaluation else None),
        "deployment": deployed,
        "mission": mission_out(mission) if mission else None,
    }


def report_deployment(db: Session, robot: Robot, deployment_id: str, data: dict) -> dict:
    from .robot_qualification import require_execution

    row = db.get(Deployment, deployment_id)
    if row is None or row.robot_id != robot.id:
        raise HTTPException(404, "deployment not found")
    release = db.get(ApplicationRelease, row.release_id)
    if data["state"] == "ready":
        require_execution(db, robot, release)
    if (
        row.generation != robot.generation
        or data["generation"] != row.generation
        or data["release_digest"] != release.digest
    ):
        raise HTTPException(409, "stale deployment generation or release digest")
    pending = unresolved_mission(db, robot)
    if data["state"] == "ready" and pending:
        if row.state == "ready" and row.detail == data["detail"]:
            return deployment_out(row)
        if not (
            pending.state == "requested"
            and pending.identity is None
            and not pending.execution_started
            and pending.deployment_id == row.id
            and pending.generation == row.generation
            and pending.release_digest == release.digest
        ):
            raise HTTPException(409, "new ready acknowledgement requires an idle or unclaimed queued robot")
    row.state = data["state"]
    row.detail = data["detail"]
    row.observed_at = utcnow()
    return deployment_out(row)


def mission_for_robot(db: Session, robot: Robot, mission_id: str) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None or mission.robot_id != robot.id:
        raise HTTPException(404, "mission not found")
    return mission


def finish(db: Session, mission: Mission, data: dict) -> Episode:
    row = Episode(
        id=new_id("epi"),
        project_id=mission.project_id,
        mission_id=mission.id,
        release_digest=mission.release_digest,
        state=data["state"],
        detail=data.get("detail", ""),
        summary=data.get("summary", {}),
        identity=mission.identity,
    )
    db.add(row)
    mission.state = data["state"]
    mission.detail = data.get("detail", "")
    mission.updated_at = utcnow()
    mission.episode_id = row.id
    mission.terminal_digest = canonical_digest(data)
    db.flush()
    return row


def claim(db: Session, robot: Robot, device: Device, mission_id: str, data: dict) -> dict:
    from .robot_qualification import require_execution

    dispatch_allowed(db)
    mission = mission_for_robot(db, robot, mission_id)
    if mission.state in TERMINAL or mission.state == "cancel_requested":
        raise HTTPException(409, "mission is terminal or cancellation was requested")
    if aware(mission.expires_at) <= utcnow():
        if expire_unclaimed_mission(db, mission):
            return {"expired": True}
        raise HTTPException(
            410, "mission authorization expired; reconcile local execution and report outcome"
        )
    deployment = db.get(Deployment, mission.deployment_id)
    release = db.get(ApplicationRelease, mission.release_id)
    require_execution(db, robot, release)
    if (
        robot.generation != mission.generation
        or deployment.generation != robot.generation
        or deployment.state != "ready"
        or release.digest != mission.release_digest
    ):
        raise HTTPException(409, "mission deployment is no longer ready")
    identity = {
        "robot_id": robot.id,
        "device_id": device.id,
        "mission_id": mission.id,
        "release_digest": mission.release_digest,
        **data,
    }
    try:
        validate_identity(identity)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if mission.identity is not None:
        if mission.identity != identity or mission.state == "unknown":
            raise HTTPException(409, "mission already claimed; explicit execution recovery is required")
        return claim_out(mission, release)
    if mission.state != "requested":
        raise HTTPException(409, "mission cannot be claimed in its current state")
    validate_execution_signing(paired=release.manifest["profile"] == PAIRED_PROFILE)
    mission.grant = _sign_execution_grant(identity, "action", aware(mission.expires_at).timestamp())
    mission.identity = identity
    mission.state = "starting"
    mission.updated_at = utcnow()
    return claim_out(mission, release)


def _configured_signer():
    """Only an explicit file enables asymmetric mode; never fall back from it."""
    settings = get_settings()
    if settings.execution_signing_keys_file is None:
        return None
    if settings.execution_secret is not None or settings.planner_execution_secret is not None:
        raise HTTPException(503, "execution signing configuration is unavailable")
    try:
        # The optional crypto dependency is unnecessary for legacy HMAC installs.
        from convoy_contracts.grants import SigningKeys

        return SigningKeys(settings.execution_signing_keys_file)
    except (ImportError, OSError, TypeError, ValueError):
        raise HTTPException(503, "execution signing configuration is unavailable") from None


def validate_execution_signing(*, paired: bool = False) -> None:
    signer = _configured_signer()
    if signer is not None:
        try:
            signer.verification_document("action")
            if paired:
                signer.verification_document("planner")
        except (OSError, TypeError, ValueError):
            raise HTTPException(503, "execution signing configuration is unavailable") from None
        return
    settings = get_settings()
    if not settings.execution_secret or len(settings.execution_secret.encode()) < 32:
        raise HTTPException(503, "execution signing is not configured")
    planner = settings.planner_execution_secret
    if paired and (not planner or len(planner.encode()) < 32 or planner == settings.execution_secret):
        raise HTTPException(503, "distinct planner execution signing is not configured")


def action_verification_document() -> dict:
    """Export only public action keys to authenticated simulators; HMAC is never exported."""
    signer = _configured_signer()
    if signer is None:
        raise HTTPException(503, "automatic worker setup requires public-key execution signing")
    try:
        return signer.verification_document("action")
    except (OSError, TypeError, ValueError):
        raise HTTPException(503, "execution signing configuration is unavailable") from None


def _sign_execution_grant(identity: dict, purpose: str, expires_at: float) -> str:
    signer = _configured_signer()
    if signer is not None:
        try:
            return signer.sign(identity, purpose, expires_at)
        except (OSError, TypeError, ValueError):
            raise HTTPException(503, "execution signing configuration is unavailable") from None
    validate_execution_signing(paired=purpose == "planner")
    settings = get_settings()
    if purpose == "action":
        return sign_grant(identity, settings.execution_secret, expires_at)
    return sign_planner_grant(identity, settings.planner_execution_secret, expires_at)


def claim_out(mission: Mission, release: ApplicationRelease) -> dict:
    # Reclaims retain their original persisted action token and authorization
    # deadline, while configured key removal/conflict still fails closed.
    validate_execution_signing(paired=release.manifest["profile"] == PAIRED_PROFILE)
    result = {"mission": mission_out(mission), "identity": mission.identity, "grant": mission.grant}
    if release.manifest["profile"] == PAIRED_PROFILE:
        result["planner_grant"] = _sign_execution_grant(
            mission.identity, "planner", aware(mission.expires_at).timestamp(),
        )
    return result


def report_mission(db: Session, robot: Robot, mission_id: str, data: dict) -> dict:
    mission = mission_for_robot(db, robot, mission_id)
    reject_invalid_json_values(data)
    if len(json.dumps(data.get("summary", {}), allow_nan=False).encode()) > 64 * 1024:
        raise HTTPException(422, "summary exceeds 64 KiB")
    identity = data["identity"]
    if identity is not None:
        try:
            validate_identity(identity)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if identity != mission.identity:
        raise HTTPException(409, "report identity does not match claimed mission")
    if mission.state in TERMINAL:
        if mission.terminal_digest != canonical_digest(data):
            raise HTTPException(409, "terminal outcome is immutable")
        return {"mission": mission_out(mission), "episode": episode_out(db.get(Episode, mission.episode_id))}
    state = data["state"]
    if identity is None:
        if mission.state != "cancel_requested" or state != "cancelled":
            raise HTTPException(409, "only unclaimed cancellation can be acknowledged without identity")
    elif state == "running":
        if mission.state not in ("starting", "running", "cancel_requested"):
            raise HTTPException(409, "mission cannot enter running from its current state")
        if aware(mission.expires_at) <= utcnow():
            raise HTTPException(410, "mission authorization expired")
        mission.execution_started = True
        if mission.state != "cancel_requested":
            mission.state = "running"
        mission.updated_at = utcnow()
        return {"mission": mission_out(mission), "episode": None}
    elif state == "completed" and not mission.execution_started:
        raise HTTPException(409, "running acknowledgement is required before completion")
    elif mission.state not in ("starting", "running", "cancel_requested", "unknown"):
        raise HTTPException(409, "invalid mission report transition")
    if state == "unknown":
        mission.state = "unknown"
        mission.detail = data["detail"]
        mission.updated_at = utcnow()
        return {"mission": mission_out(mission), "episode": None}
    episode = finish(db, mission, data)
    return {"mission": mission_out(mission), "episode": episode_out(episode)}
