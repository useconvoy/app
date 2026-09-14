"""Device-facing read endpoints for immutable catalog objects."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DbSession

from ..auth import current_device
from ..db import get_db
from ..models import Device, EvalSet, Plan, Release, RuntimeArtifact
from ..services import catalog as cat

router = APIRouter(prefix="/api/agent/v1", tags=["agent"])


@router.get("/releases/{rel_id}")
def release_manifest(rel_id: str, dev: Device = Depends(current_device), db: DbSession = Depends(get_db)):
    r = db.get(Release, rel_id)
    if not r or r.simulated != dev.simulated:
        raise HTTPException(404, "not found")
    art = db.get(RuntimeArtifact, r.runtime_artifact_id) if r.runtime_artifact_id else None
    return cat.manifest(r, art)


@router.get("/plans/{plan_id}")
def plan_manifest(plan_id: str, dev: Device = Depends(current_device), db: DbSession = Depends(get_db)):
    p = db.get(Plan, plan_id)
    if not p or p.simulated != dev.simulated:
        raise HTTPException(404, "not found")
    es = db.get(EvalSet, p.eval_set_id)
    return cat.plan_manifest(p, es)
