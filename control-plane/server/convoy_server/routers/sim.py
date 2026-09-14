"""Simulator-only endpoints: fixture blobs (device auth) and seeding (operator)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, current_device, require_role
from ..config import get_settings
from ..db import get_db
from ..models import Device, RuntimeArtifact
from ..modelsource import blob_path

router = APIRouter(prefix="/api", tags=["sim"])


@router.get("/sim/blobs/{sha}")
def sim_blob(sha: str, dev: Device = Depends(current_device)):
    s = get_settings()
    if not s.simulator:
        raise HTTPException(404, "not found")
    try:
        p = blob_path(s, sha)
    except ValueError as e:
        raise HTTPException(404, "not found") from e
    if not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="application/octet-stream")


@router.get("/agent/v1/runtime-artifacts/{artifact_id}/archive")
def artifact_archive(
    artifact_id: str, dev: Device = Depends(current_device), db: DbSession = Depends(get_db)
):
    s = get_settings()
    a = db.get(RuntimeArtifact, artifact_id)
    if not a or a.storage != "server":
        raise HTTPException(404, "not found")
    if a.scope != "fleet" and a.scope != f"device:{dev.id}":
        raise HTTPException(404, "not found")
    p = blob_path(s, a.archive_sha256, "runtime")
    if not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="application/gzip")


@router.post("/v1/sim/seed")
def seed(p: Principal = Depends(require_role("admin")), db: DbSession = Depends(get_db)):
    s = get_settings()
    if not s.simulator:
        raise HTTPException(409, "simulator mode is off")
    from ..services.seed import seed_simulator

    return seed_simulator(db, s)
