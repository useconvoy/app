"""Immutable profile-scoped model files; receiving bytes does not verify a simulator."""
from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from convoy_contracts.assets import ROBOT_ASSET_MAX_BYTES
from fastapi import HTTPException


def model_for(profile, engine):
    model = next((m for m in profile.spec["simulations"] if m["engine"] == engine), None)
    if model is None:
        raise HTTPException(404, "simulation model not found in profile")
    return model


def path_for(settings, profile, model):
    return settings.artifacts_dir / "robot-models" / profile.id / model["asset"]["sha256"]


def status(settings, profile, model):
    path = path_for(settings, profile, model)
    present = path.is_file() and not path.is_symlink()
    return {"engine": model["engine"], "sha256": model["asset"]["sha256"], "format": model["asset"]["format"],
            "stored": present, "size_bytes": path.stat().st_size if present else None,
            "max_upload_bytes": ROBOT_ASSET_MAX_BYTES}


@contextmanager
def receive(settings, profile, model):
    """One uploader across API processes sharing this volume; bounded temporary and stored bytes."""
    root = settings.artifacts_dir / "robot-models"
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".upload.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HTTPException(429, "another robot asset upload is in progress", headers={"Retry-After": "2"}) from exc
        # Only unpublished files from an interrupted exclusive owner can remain here.
        for abandoned in root.glob(".upload-*"):
            abandoned.unlink()
        used = sum(p.stat().st_size for p in root.glob("*/*") if p.is_file())
        target = path_for(settings, profile, model)
        if target.is_file() and not target.is_symlink():
            used -= target.stat().st_size  # replace identical content without charging it twice
        fd, name = tempfile.mkstemp(prefix=".upload-", dir=root)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                sink = Receiver(stream, temporary, target, model["asset"]["sha256"], used,
                                settings.robot_asset_quota_bytes)
                yield sink
        finally:
            temporary.unlink(missing_ok=True)


class Receiver:
    def __init__(self, stream, temporary, target, digest, used, quota):
        self.stream, self.temporary, self.target = stream, temporary, target
        self.digest, self.used, self.quota = digest, used, quota
        self.hash = hashlib.sha256()
        self.size = 0

    def write(self, chunk):
        self.size += len(chunk)
        if self.size > ROBOT_ASSET_MAX_BYTES:
            raise HTTPException(413, "robot model exceeds the 16 MiB upload limit")
        if self.used + self.size > self.quota:
            raise HTTPException(507, "robot model storage is full")
        self.hash.update(chunk)
        self.stream.write(chunk)

    def publish(self):
        if not self.size or self.hash.hexdigest() != self.digest:
            raise HTTPException(422, "model bytes do not match the profile SHA-256; select the pinned file or create a new profile revision")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.temporary, self.target)
        directory = os.open(self.target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
