"""Check a recorded episode against the platform's replay rules.

``validate_journal`` re-implements the invariants of
``control-plane/server/convoy_server/services/replay.py`` without importing the
server. ``platform_reader`` runs the server's own reader when the ``managed``
extra (convoy-server) is installed.
"""

from __future__ import annotations

import base64
import json
import math
import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

MAX_STEPS = 2048
MAX_IMAGE_BYTES = 768 * 1024


def _finite(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def load_episode(directory: Path) -> SimpleNamespace:
    record = json.loads((directory / "episode.json").read_text())
    return SimpleNamespace(**record)


def validate_journal(directory: Path) -> dict:
    directory = directory.resolve()
    episode = load_episode(directory)
    path = directory / "journal.sqlite3"
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        identity_json, state, report_json = db.execute(
            "SELECT identity_json, state, report_json FROM missions WHERE id=?", (episode.mission_id,)).fetchone()
        identity, report = json.loads(identity_json), json.loads(report_json)
        assert identity == episode.identity and identity["mission_id"] == episode.mission_id
        assert identity["release_digest"] == episode.release_digest
        assert state in {"completed", "failed", "cancelled"} and state == episode.state
        assert report["summary"] == episode.summary and report["state"] == episode.state
        steps = episode.summary["steps"]
        assert type(steps) is int and 1 <= steps <= MAX_STEPS
        count, low, high = db.execute("SELECT count(*), min(sequence), max(sequence) FROM commands "
                                      "WHERE mission_id=? AND state='applied'", (episode.mission_id,)).fetchone()
        assert (count, low, high) == (steps, 0, steps - 1), "incomplete recording"
        previous = None
        largest = 0
        for sequence in range(steps):
            request, result, outcome = map(json.loads, db.execute(
                "SELECT request_json, result_json, observation_json FROM commands WHERE mission_id=? AND sequence=?",
                (episode.mission_id, sequence)).fetchone())
            assert request["identity"] == identity == result["identity"]
            assert request["sequence"] == sequence == result["sequence"]
            assert request["request_id"] == result["request_id"]
            assert request["observation_id"] == result["observation_id"]
            action = result["action"]
            assert isinstance(action, list) and len(action) == 4 and all(_finite(v) and -1 <= v <= 1 for v in action)
            assert _finite(result["policy_duration_ms"]) and _finite(outcome["reward"])
            assert type(outcome["success"]) is bool
            if previous is not None:
                assert previous == request["observation"], f"observation discontinuity at {sequence}"
            for obs in (request["observation"], outcome["observation"]):
                raw = base64.b64decode(obs["image_png_base64"], validate=True)
                assert raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) <= MAX_IMAGE_BYTES
                largest = max(largest, len(raw))
            previous = outcome["observation"]
    finally:
        db.close()
    return {"steps": steps, "largest_png_bytes": largest, "bytes": path.stat().st_size}


def platform_reader(directory: Path, frames: tuple[int, ...] = (0, 1)) -> dict:
    """Run convoy_server's replay manifest and frame readers on the recording."""
    from convoy_server import config
    from convoy_server.services import replay

    episode = load_episode(directory)
    path = str((directory / "journal.sqlite3").resolve())
    manifest = replay.manifest(episode, path)
    previous_env = os.environ.get("CONVOY_REPLAY_JOURNAL")
    previous_settings = config._settings
    with tempfile.TemporaryDirectory() as empty:
        os.environ["CONVOY_REPLAY_JOURNAL"] = path
        config.set_settings(config.Settings(data_dir=Path(empty)))
        try:
            steps = manifest["steps"]
            read = [replay.frame(episode, i) for i in sorted({*frames, steps // 2, steps})]
        finally:
            config._settings = previous_settings
            if previous_env is None:
                os.environ.pop("CONVOY_REPLAY_JOURNAL", None)
            else:
                os.environ["CONVOY_REPLAY_JOURNAL"] = previous_env
    return {"manifest": manifest, "frames": [{k: v for k, v in f.items() if k != "image_png_base64"} for f in read]}
