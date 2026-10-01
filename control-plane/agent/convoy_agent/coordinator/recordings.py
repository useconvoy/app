"""Upload completed simulator recordings independently of the control loop.

Uses only the enrolled device credential and verified HTTPS. Resumable command
uploads cannot start/retry robot actions. Run beside the paired coordinator.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import signal
import sqlite3
import threading
from pathlib import Path

from convoy_agent.agent import AgentConfig

from .transport import JsonHTTP

log = logging.getLogger(__name__)


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def sync_recordings(journal, robot_id, device_id, control, progress_path, stop=None):
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.execute("PRAGMA query_only=ON")
        binding = json.loads(db.execute("SELECT value FROM meta WHERE key='binding'").fetchone()[0])
        if binding != {"robot_id": robot_id, "device_id": device_id}:
            raise ValueError("recording journal belongs to another device")
        missions = db.execute("SELECT id,report_json FROM missions WHERE reported=1 AND state IN ('completed','failed','cancelled')").fetchall()
        for mission_id, raw in missions:
            if stop and stop.is_set():
                return
            report = json.loads(raw)
            steps = report.get("summary", {}).get("steps", 0)
            if type(steps) is not int or not 1 <= steps <= 2048 or progress.get(mission_id) == "published":
                continue
            prefix = f"/api/agent/v1/robots/{robot_id}/missions/{mission_id}/recording"
            first = progress.get(mission_id, 0)
            for sequence in range(first, steps):
                if stop and stop.is_set():
                    return
                row = db.execute("SELECT request_json,result_json,observation_json FROM commands WHERE mission_id=? AND sequence=? AND state='applied'",
                                 (mission_id, sequence)).fetchone()
                if not row:
                    raise ValueError("incomplete local recording")
                payload = dict(zip(("request", "result", "outcome"), map(json.loads, row), strict=True))
                # Non-camera episodes still retain their ordinary outcome report.
                if "image_png_base64" not in payload["request"]["observation"]:
                    break
                control.post(prefix + f"/commands/{sequence}", payload)
                progress[mission_id] = sequence + 1
                save(progress_path, progress)
            else:
                control.post(prefix + "/publish", {})
                progress[mission_id] = "published"
                save(progress_path, progress)
                log.info("Published recording for %s (%s actions)", mission_id, steps)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    cfg = AgentConfig(args.data_dir)
    if not cfg.credential or not cfg.data.get("simulate"):
        parser.error("an enrolled simulator is required")
    logging.basicConfig(level=logging.INFO)
    root = args.data_dir.resolve() / "coordinator"
    root.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    with (root / "recording-upload.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        control = JsonHTTP(cfg.data["server"], cfg.credential, ca_file=cfg.data.get("ca_file"), timeout_s=20)
        while not stop.is_set():
            try:
                if (root / "execution.sqlite3").exists():
                    sync_recordings(root / "execution.sqlite3", args.robot_id, cfg.data["device_id"],
                                    control, root / "recording-upload.json", stop)
            except Exception as error:
                log.warning("Recording sync deferred (%s); original execution is unchanged", type(error).__name__)
                if args.once:
                    raise
            if args.once:
                return
            stop.wait(10)


if __name__ == "__main__":
    main()
