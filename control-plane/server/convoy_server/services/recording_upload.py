"""Device-authenticated JSON recording ingestion with bounded disk use.

Publish complete, verified server-created journals atomically. Partial uploads
resume after interruption; model execution never waits on recording transfer.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager

from fastapi import HTTPException

from ..config import get_settings
from . import replay


@contextmanager
def locked_store():
    root = get_settings().data_dir / "recordings"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / ".upload.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield root


def paths(root, episode):
    # IDs come only from authorized database rows, never uploaded paths.
    return root / (episode.id + ".partial"), root / (episode.id + ".sqlite3")


def upload(episode, sequence, payload):
    steps = episode.summary.get("steps")
    if (episode.state not in {"completed", "failed", "cancelled"} or not episode.identity
            or type(steps) is not int or not 1 <= steps <= replay.MAX_STEPS
            or not 0 <= sequence < steps):
        raise HTTPException(409, "Episode has no matching terminal recording")
    if not isinstance(payload, dict) or set(payload) != {"request", "result", "outcome"}:
        raise HTTPException(422, "Expected one recorded command")
    try:
        values = tuple(json.dumps(payload[k], sort_keys=True, separators=(",", ":"), allow_nan=False)
                       for k in ("request", "result", "outcome"))
        if any(len(v.encode()) > replay.MAX_ROW_BYTES for v in values):
            raise ValueError("oversized row")
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid recorded command") from None
    with locked_store() as root:
        partial, final = paths(root, episode)
        path = final if final.exists() else partial
        quota = int(os.environ.get("CONVOY_RECORDING_QUOTA_BYTES", str(512 * 1024**2)))
        if not path.exists() and sum(p.stat().st_size for p in root.iterdir() if p.is_file()) + 65536 > quota:
            raise HTTPException(507, "Recording storage limit reached")
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY,identity_json TEXT,state TEXT,report_json TEXT);
                CREATE TABLE IF NOT EXISTS commands(mission_id TEXT,sequence INTEGER,request_json TEXT,
                    result_json TEXT,observation_json TEXT,state TEXT,PRIMARY KEY(mission_id,sequence));
            """)
            existing = db.execute("SELECT request_json,result_json,observation_json FROM commands WHERE mission_id=? AND sequence=?",
                                  (episode.mission_id, sequence)).fetchone()
            if existing:
                if tuple(existing) != values:
                    raise HTTPException(409, "Recording command is immutable")
                return {"sequence": sequence, "stored": True, "published": final.exists()}
            if final.exists():
                raise HTTPException(409, "Published recording is immutable")
            used = sum(p.stat().st_size for p in root.iterdir() if p.is_file())
            # Account conservatively for page/journal overhead as well as JSON.
            if used + 2 * sum(len(v.encode()) for v in values) + 65536 > quota:
                raise HTTPException(507, "Recording storage limit reached")
            db.execute("INSERT OR IGNORE INTO missions VALUES (?,?,?,?)", (
                episode.mission_id, json.dumps(episode.identity), episode.state,
                json.dumps({"state": episode.state, "summary": episode.summary})))
            db.execute("INSERT INTO commands VALUES (?,?,?,?,?,?)", (episode.mission_id, sequence, *values, "applied"))
            try:
                request, _, outcome = replay.command(db, episode, sequence)
                replay.image(request["observation"])
                replay.image(outcome["observation"])
                for before, after in ((sequence - 1, sequence), (sequence, sequence + 1)):
                    if before < 0 or after >= steps:
                        continue
                    present = db.execute("SELECT count(*) FROM commands WHERE sequence IN (?,?)", (before, after)).fetchone()[0]
                    if present == 2:
                        previous = replay.command(db, episode, before)[2]["observation"]
                        following = replay.command(db, episode, after)[0]["observation"]
                        if previous != following:
                            raise ValueError("observation discontinuity")
            except (ValueError, TypeError, KeyError, AttributeError):
                raise HTTPException(422, "Command does not match episode evidence") from None
        os.chmod(path, 0o600)
    return {"sequence": sequence, "stored": True, "published": False}


def publish(episode):
    with locked_store() as root:
        partial, final = paths(root, episode)
        if final.exists():
            return replay.manifest(episode, str(final))
        if not partial.exists():
            raise HTTPException(409, "Recording upload is incomplete")
        try:
            result = replay.manifest(episode, str(partial))
        except HTTPException:
            raise HTTPException(409, "Recording upload is incomplete or inconsistent") from None
        os.replace(partial, final)
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return result
