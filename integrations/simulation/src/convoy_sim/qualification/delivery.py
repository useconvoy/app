"""Obtain only the registered model from the enrollment's authenticated control plane."""
from pathlib import Path
from urllib.parse import quote

from convoy_contracts.assets import ROBOT_ASSET_MAX_BYTES

from .assets import read_asset


def ensure_asset(control, profile, model, root):
    digest = model["asset"]["sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid pinned asset digest")
    path = Path(root) / digest
    try:
        read_asset(path, digest)
        return path
    except (ValueError, OSError):
        pass
    # Never fetch the profile's arbitrary URI. This endpoint enforces the device's exact assignment.
    control.download(f"/api/agent/v1/robot-assets/{quote(profile['id'], safe='')}/{quote(model['engine'], safe='')}",
                     path, digest, ROBOT_ASSET_MAX_BYTES)
    read_asset(path, digest)
    return path
