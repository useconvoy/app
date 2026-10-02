"""Project registration reuses enrollment; computer discovery is not a mechanical robot scan."""
import math
import shlex

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..auth import assert_live_principal
from ..config import get_settings
from ..db import write_txn
from ..models import Device, EnrollmentToken
from ..serialize import device_out, enrollment_out
from ..services import identity, platform
from .platform import Database, Id, Input, Name, PrincipalRead, PrincipalWrite

router = APIRouter(tags=["robot connection setup"])


class SetupIn(Input):
    project_id: Id
    name: Name
    simulated: bool = False


def computer(device):
    hardware = device.hardware or {}
    reported = {}
    for key in ("arch", "os", "kernel", "python", "gpu_name", "compute_capability", "jetson_model", "l4t_release", "cuda_version"):
        value = hardware.get(key)
        reported[key] = value[:160] if isinstance(value, str) else None
    for key in ("cpu_count", "mem_total_mb", "swap_total_mb"):
        value = hardware.get(key)
        reported[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None
    reported["synthetic"] = hardware.get("simulated") is True
    return {**device_out(device, brief=True), "hardware": reported}


def owned_enrollment(db, p, enrollment_id):
    row = db.get(EnrollmentToken, enrollment_id)
    if row is None or row.created_by != p.user.id:
        raise HTTPException(404, "connection setup not found")
    return row


def status(db, row):
    device = db.get(Device, row.device_id) if row.device_id else None
    return {"enrollment": enrollment_out(row),
            "device": computer(device) if device and not device.retired_at and not device.credential_revoked_at else None}


@router.post("/api/v1/robot-connections/enrollments", status_code=201)
def create_setup(body: SetupIn, p: PrincipalWrite, db: Database):
    platform.project_for(db, body.project_id, p)
    try:
        identity.throttle_check(db, f"robot-setup:{p.user.id}", 30, 3600)
        with write_txn(db):
            platform.project_for(db, body.project_id, p)
            row, token = identity.create_enrollment(db, p, label=body.name, group_name=f"project-{body.project_id}",
                                                  simulated=body.simulated, ttl_s=900, rebind_device_id=None)
            db.flush()
            directory = f"./convoy-connections/{row.id}"
            prefix = ["convoy-agent", "--data-dir", directory]
            command = [*prefix, "enroll", "--server", get_settings().public_url, "--token", token, "--name", body.name]
            if body.simulated:
                command += ["--simulate", "--host-inventory"]
            # Plaintext setup material is returned once; never persisted in an idempotency receipt.
            result = {**status(db, row), "command": shlex.join(command), "data_dir": directory,
                      "run_command": shlex.join([*prefix, "run", "--no-robot-sim"])}
        return result
    except identity.IdentityError as error:
        raise HTTPException(error.status, str(error), headers={"Retry-After": str(error.retry_after)} if error.retry_after is not None else None) from error


@router.get("/api/v1/robot-connections/enrollments/{enrollment_id}")
def get_setup(enrollment_id: str, p: PrincipalRead, db: Database):
    return status(db, owned_enrollment(db, p, enrollment_id))


@router.post("/api/v1/robot-connections/enrollments/{enrollment_id}/cancel")
def cancel_setup(enrollment_id: str, p: PrincipalWrite, db: Database):
    with write_txn(db):
        assert_live_principal(db, p, "operator")
        row = owned_enrollment(db, p, enrollment_id)
        if row.consumed_at:
            raise HTTPException(409, "connection is already enrolled; cancelling setup does not revoke a computer")
        identity.revoke_enrollment(db, p, row)
    return status(db, row)


@router.get("/api/v1/robot-connections/{device_id}")
def get_connection(device_id: str, p: PrincipalRead, db: Database):
    owned = db.scalar(select(EnrollmentToken.id).where(EnrollmentToken.device_id == device_id,
        EnrollmentToken.created_by == p.user.id, EnrollmentToken.consumed_at.is_not(None)).limit(1))
    device = db.get(Device, device_id) if owned else None
    if not device or device.retired_at or device.credential_revoked_at:
        raise HTTPException(404, "connection not found")
    return computer(device)
