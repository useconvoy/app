"""Durable intent and outcome records; restart never replays a robot command."""

from __future__ import annotations

import fcntl
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ExecutionJournal:
    def __init__(self, directory: Path, robot_id: str, device_id: str):
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = (directory / "executor.lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("another coordinator owns this executor") from None
        self.db = None
        try:
            self.db = sqlite3.connect(directory / "execution.sqlite3")
            self.db.row_factory = sqlite3.Row
            # The coordinator is the only writer and transactions are short.
            # Avoid WAL and its extra qualification surface on device SQLite.
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS missions (
                    id TEXT PRIMARY KEY, identity_json TEXT, state TEXT NOT NULL,
                    report_json TEXT, reported INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS commands (
                    mission_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    request_id TEXT NOT NULL UNIQUE, request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL, state TEXT NOT NULL,
                    observation_json TEXT, PRIMARY KEY(mission_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS plans (
                    mission_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    request_json TEXT NOT NULL, result_json TEXT,
                    state TEXT NOT NULL
                );
            """)
            with self.db:
                binding = encode({"robot_id": robot_id, "device_id": device_id})
                old = self.db.execute("SELECT value FROM meta WHERE key='binding'").fetchone()
                if old and old[0] != binding:
                    raise ValueError("journal belongs to another robot or enrolled device")
                self.db.execute("INSERT OR IGNORE INTO meta VALUES ('binding', ?)", (binding,))
                previous = self.db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()
                self.epoch = int(previous[0]) + 1 if previous else 1
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('epoch', ?)", (str(self.epoch),))
            self.incarnation = str(uuid.uuid4())
            boot_path = Path("/proc/sys/kernel/random/boot_id")
            self.boot_id = boot_path.read_text().strip() if boot_path.exists() else "session-" + str(uuid.uuid4())
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        try:
            if self.db is not None:
                self.db.close()
        finally:
            if not self.lock.closed:
                fcntl.flock(self.lock, fcntl.LOCK_UN)
                self.lock.close()

    def identity(self, mission: dict, device_id: str, robot_id: str) -> dict:
        return {
            "mission_id": mission["id"], "device_id": device_id, "robot_id": robot_id,
            "release_digest": mission["release_digest"], "boot_id": self.boot_id,
            "incarnation": self.incarnation, "authority_epoch": self.epoch,
        }

    def prepare(self, mission_id: str, identity: dict | None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO missions(id, identity_json, state) VALUES (?, ?, 'prepared')",
                (mission_id, encode(identity)),
            )

    def get(self, mission_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
        return dict(row) if row else None

    def mark_running(self, mission_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE missions SET state='running' WHERE id=?", (mission_id,))

    def finish(self, mission_id: str, payload: dict) -> None:
        with self.db:
            row = self.get(mission_id)
            if row is None:
                raise ValueError("mission was not prepared")
            if row["report_json"] is not None:
                if row["report_json"] != encode(payload):
                    raise ValueError("cannot replace a terminal report")
                return
            self.db.execute(
                "UPDATE missions SET state=?, report_json=? WHERE id=?",
                (payload["state"], encode(payload), mission_id),
            )

    def acknowledge(self, mission_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE missions SET reported=1 WHERE id=?", (mission_id,))

    def pending(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM missions WHERE report_json IS NOT NULL AND reported=0 ORDER BY rowid"
        )]

    def recover(self) -> None:
        """Fence prior process work, including a claim whose response may have been lost."""
        for row in self.db.execute("SELECT * FROM missions WHERE report_json IS NULL").fetchall():
            identity = json.loads(row["identity_json"])
            self.finish(row["id"], {
                "identity": identity,
                "state": "unknown" if identity else "cancelled",
                "detail": "coordinator restarted; prior execution is not replayed",
                "summary": {"recovered_after_restart": True, "execution_mode": "lockstep_offline"},
            })

    def intend(self, request: dict, result: dict) -> None:
        """This transaction commits before calling adapter.step."""
        with self.db:
            self.db.execute(
                "INSERT INTO commands VALUES (?, ?, ?, ?, ?, 'intended', NULL)",
                (request["identity"]["mission_id"], request["sequence"], request["request_id"],
                 encode(request), encode(result)),
            )

    def request_plan(self, request: dict) -> None:
        with self.db:
            self.db.execute("INSERT INTO plans VALUES (?, ?, ?, NULL, 'requested')", (
                request["identity"]["mission_id"], request["request_id"], encode(request),
            ))

    def record_plan(self, request: dict, result: dict, *, accepted: bool) -> None:
        with self.db:
            changed = self.db.execute(
                "UPDATE plans SET result_json=?, state=? WHERE request_id=? AND request_json=? AND state='requested'",
                (encode(result), "accepted" if accepted else "declined", request["request_id"], encode(request)),
            )
            if changed.rowcount != 1:
                raise ValueError("planner decision does not match an outstanding durable request")

    def command_outcome(self, request: dict, state: str, observation: dict | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE commands SET state=?, observation_json=? WHERE request_id=? AND state='intended'",
                (state, encode(observation), request["request_id"]),
            )

    def command_count(self, mission_id: str) -> int:
        return self.db.execute("SELECT count(*) FROM commands WHERE mission_id=?", (mission_id,)).fetchone()[0]
