"""Upload a pinned model; only its assigned simulator may download it."""
import asyncio

from convoy_contracts.assets import ROBOT_ASSET_MAX_BYTES
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..auth import assert_live_principal, audit
from ..config import get_settings
from ..db import write_txn
from ..robot_registry_models import RobotProfile
from ..services import platform, robot_assets, robot_qualification
from .platform import Database, DeviceIdentity, PrincipalRead, PrincipalWrite
from .robot_qualification import device_write

router = APIRouter(tags=["robot model assets"])


@router.get("/api/v1/robot-profiles/{profile_id}/simulation-assets")
def list_assets(profile_id: str, p: PrincipalRead, db: Database):
    profile = platform.resource_for(db, RobotProfile, profile_id, p)
    return [robot_assets.status(get_settings(), profile, model) for model in profile.spec["simulations"]]


@router.post("/api/v1/robot-profiles/{profile_id}/simulation-assets/{engine}", status_code=201)
async def upload(profile_id: str, engine: str, request: Request, p: PrincipalWrite, db: Database):
    profile = platform.resource_for(db, RobotProfile, profile_id, p)
    model = robot_assets.model_for(profile, engine)
    if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
        raise HTTPException(415, "send model bytes as application/octet-stream")
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > ROBOT_ASSET_MAX_BYTES):
        raise HTTPException(413, "robot model exceeds the 16 MiB upload limit")
    settings = get_settings()
    with robot_assets.receive(settings, profile, model) as sink:
        async def consume():
            async for chunk in request.stream():
                sink.write(chunk)
        try:
            await asyncio.wait_for(consume(), timeout=60)
        except TimeoutError as exc:
            raise HTTPException(408, "robot model upload timed out") from exc
        with write_txn(db):
            assert_live_principal(db, p, "operator")
            platform.resource_for(db, RobotProfile, profile.id, p)
            sink.publish()
            audit(db, p, "robot-model.upload", profile.id, engine=engine, sha256=model["asset"]["sha256"], size=sink.size)
    return robot_assets.status(settings, profile, model)


@router.get("/api/agent/v1/robot-assets/{profile_id}/{engine}")
def download(profile_id: str, engine: str, device: DeviceIdentity, db: Database):
    def admitted(fresh):
        pair = robot_qualification.device_registration(db, fresh)
        if (not fresh.simulated or not pair or pair[1].kind != "simulated" or pair[1].profile_id != profile_id
                or pair[1].simulation_engine != engine):
            raise HTTPException(404, "robot model not found")
        profile = db.get(RobotProfile, profile_id)
        path = robot_assets.path_for(get_settings(), profile, robot_assets.model_for(profile, engine))
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, "robot model has not been uploaded")
        return path
    path = device_write(db, device, admitted)
    return FileResponse(path, media_type="application/octet-stream", headers={"Cache-Control": "private, no-store"})
