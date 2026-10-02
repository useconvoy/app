"""Compile editable configuration inputs into authoritative immutable releases."""
from convoy_contracts.execution import canonical_digest
from convoy_contracts.registered import REGISTERED_PROFILE, validate_robot_binding
from fastapi import HTTPException

from ..platform_models import Application
from ..robot_registry_models import RobotProfile
from . import platform

REFERENCE_RUNTIME = "convoy-joint-target-reference-v1"


def compile_manifest(db, principal, project_id, data):
    profile = platform.resource_for(db, RobotProfile, data["profile_id"], principal)
    if profile.project_id != project_id:
        raise HTTPException(404, "robot profile not found in project")
    spec = profile.spec
    model = next((m for m in spec["simulations"] if m["engine"] == "mujoco"), None)
    if (model is None or spec["command_interface"] != "joint-position"
            or spec.get("execution_profile") not in (None, REGISTERED_PROFILE)):
        raise HTTPException(422, "configuration requires a supported MuJoCo joint-position profile")
    joints = [j for j in spec["joints"] if j["kind"] != "fixed"]
    if not joints or any(j.get("lower") is None or j.get("upper") is None for j in joints):
        raise HTTPException(422, "configuration requires bounded actuated joints")
    targets = data["targets"]
    if set(targets) != {j["name"] for j in joints}:
        raise HTTPException(422, "provide exactly one target for every actuated joint")
    positions = [targets[j["name"]] for j in joints]
    policy = data["policy"]
    if policy["kind"] == "reference":
        policy = {"runtime": REFERENCE_RUNTIME,
                  "artifact_sha256": canonical_digest({"target_joint_positions": positions})}
    else:
        if policy["runtime"] == REFERENCE_RUNTIME:
            raise HTTPException(422, "use the reference policy option for the joint-target runtime")
        policy = {"runtime": policy["runtime"], "artifact_sha256": policy["artifact_sha256"]}
    manifest = {
        "schema_version": 3, "profile": REGISTERED_PROFILE, "policy": policy,
        "environment": {"engine": "mujoco", "version": model["engine_version"],
                        "robot_profile_sha256": profile.digest, "asset_sha256": model["asset"]["sha256"]},
        "interface": {"joint_names": [j["name"] for j in joints], "command_interface": "joint-position",
                      "action_bounds": [[j["lower"], j["upper"]] for j in joints],
                      "control_rate_hz": spec["control_rate_hz"]},
        "task": {"instruction": data["instruction"], "target_joint_positions": positions,
                 "position_tolerance": data["position_tolerance"], "velocity_tolerance": data["velocity_tolerance"]},
        "execution": {key: value for key, value in data["execution"].items() if value is not None},
    }
    try:
        validate_robot_binding(manifest, profile.digest, spec, "mujoco")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return manifest


def create(db, principal, data):
    platform.project_for(db, data["project_id"], principal)
    # Compile before creating either record; the enclosing mutation transaction is atomic.
    manifest = compile_manifest(db, principal, data["project_id"], data["configuration"])
    application = platform.create_application(db, principal, {"project_id": data["project_id"], "name": data["name"]})
    release = platform.create_release(db, principal, application["id"], {"manifest": manifest})
    return {"application": application, "release": release}


def revise(db, principal, application_id, data):
    application = platform.resource_for(db, Application, application_id, principal)
    manifest = compile_manifest(db, principal, application.project_id, data)
    release = platform.create_release(db, principal, application.id, {"manifest": manifest})
    return {"application": platform.application_out(application), "release": release}
