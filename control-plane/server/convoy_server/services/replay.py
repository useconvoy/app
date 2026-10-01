"""Read-only recording adapter for locally mounted coordinator journals.

Authorization is performed by the episode route before this adapter is called.
Paths are operator configured, never accepted from the browser. Remote deployments
must mount a consistent journal snapshot; this is not an artifact upload service.
"""
from __future__ import annotations

import base64
import json
import math
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from ..config import get_settings

MAX_STEPS = 2048
MAX_ROW_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 768 * 1024


def unavailable():
    return HTTPException(404, "Recording is not available for this episode")


def decode(value):
    if not isinstance(value, str) or len(value.encode()) > MAX_ROW_BYTES:
        raise ValueError("invalid recording row")
    return json.loads(value)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@contextmanager
def recording(episode, location=None):
    connection = None
    try:
        identity = episode.identity
        if not isinstance(identity, dict) or identity.get("mission_id") != episode.mission_id or identity.get("release_digest") != episode.release_digest:
            raise ValueError("missing episode identity")
        journals = json.loads(os.environ.get("CONVOY_REPLAY_JOURNALS", "{}"))
        if location is None:
            uploaded = get_settings().data_dir / "recordings" / (episode.id + ".sqlite3")
            location = str(uploaded) if uploaded.is_file() else (
                journals.get(identity.get("robot_id")) or os.environ.get("CONVOY_REPLAY_JOURNAL"))
        if not isinstance(location, str) or not Path(location).is_absolute():
            raise unavailable()
        connection = sqlite3.connect(Path(location).as_uri() + "?mode=ro", uri=True, timeout=1)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        row = connection.execute("SELECT identity_json, state, report_json FROM missions WHERE id=?", (episode.mission_id,)).fetchone()
        if not row or decode(row[0]) != identity or row[1] not in {"completed", "failed", "cancelled"}:
            raise ValueError("recording identity or state mismatch")
        report = decode(row[2])
        if report.get("summary") != episode.summary or report.get("state") != episode.state:
            raise ValueError("recording report mismatch")
        steps = episode.summary.get("steps")
        if type(steps) is not int or not 1 <= steps <= MAX_STEPS:
            raise unavailable()
        count, low, high = connection.execute("SELECT count(*),min(sequence),max(sequence) FROM commands WHERE mission_id=? AND state='applied'", (episode.mission_id,)).fetchone()
        if (count, low, high) != (steps, 0, steps - 1):
            raise ValueError("incomplete recording")
        yield connection, steps
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error, OSError):
        raise unavailable() from None
    finally:
        if connection is not None:
            connection.close()


def command(connection, episode, sequence):
    row = connection.execute(
        "SELECT request_json,result_json,observation_json FROM commands WHERE mission_id=? AND sequence=? AND state='applied'",
        (episode.mission_id, sequence),
    ).fetchone()
    if not row:
        raise ValueError("missing command")
    request, result, outcome = map(decode, row)
    if request.get("identity") != episode.identity or result.get("identity") != episode.identity or request.get("sequence") != sequence or result.get("sequence") != sequence:
        raise ValueError("command identity mismatch")
    if request.get("request_id") != result.get("request_id") or request.get("observation_id") != result.get("observation_id"):
        raise ValueError("command correlation mismatch")
    action = result.get("action")
    if not isinstance(action, list) or len(action) != 4 or not all(finite(v) and -1 <= v <= 1 for v in action):
        raise ValueError("invalid action")
    if not finite(result.get("policy_duration_ms")) or not finite(outcome.get("reward")) or type(outcome.get("success")) is not bool:
        raise ValueError("invalid measurements")
    return request, result, outcome


def image(observation):
    value = observation.get("image_png_base64")
    if not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 4:
        raise ValueError("missing or oversized image")
    raw = base64.b64decode(value, validate=True)
    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("invalid image")
    return value


def manifest(episode, location=None):
    with recording(episode, location) as (connection, steps):
        previous = None
        for sequence in range(steps):
            request, _, outcome = command(connection, episode, sequence)
            if previous is not None and previous != request.get("observation"):
                raise ValueError("observation discontinuity")
            image(request["observation"])
            image(outcome["observation"])
            previous = outcome["observation"]
        summary = episode.summary
        proposal = summary.get("planner_result") or {}
        return {
            "episode_id": episode.id, "mission_id": episode.mission_id,
            "release_digest": episode.release_digest, "steps": steps,
            "skill": proposal.get("decision", {}).get("skill_id"),
            "planner_ms": proposal.get("planner_duration_ms"),
            "wall_seconds": summary.get("wall_duration_s"),
            "sim_seconds": summary.get("simulated_duration_s"),
            "source": "Recorded coordinator camera observations and applied actions",
        }


def frame(episode, index):
    with recording(episode) as (connection, steps):
        if not 0 <= index <= steps:
            raise unavailable()
        request, result, outcome = command(connection, episode, max(0, index - 1))
        return {
            "index": index,
            "image_png_base64": image(request["observation"] if index == 0 else outcome["observation"]),
            "action": None if index == 0 else result["action"],
            "reward": None if index == 0 else outcome["reward"],
            "success": None if index == 0 else outcome["success"],
            "policy_ms": None if index == 0 else result["policy_duration_ms"],
        }
