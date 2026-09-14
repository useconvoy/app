"""Device journal: SQLite (DELETE journal, synchronous=FULL) holding identity, intent, operation
state, pins and the three evidence lanes. Every state transition is one committed transaction so a
crash at any point leaves a recoverable record."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

LANES = ("critical", "usage", "telemetry")
LANE_QUOTA_BYTES = {"critical": 32 * 1024 * 1024, "usage": 32 * 1024 * 1024, "telemetry": 192 * 1024 * 1024}
SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operation (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, payload TEXT NOT NULL, generation INTEGER NOT NULL,
  stage TEXT NOT NULL, grant_json TEXT, grant_consumed_seq INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
  started_monotonic REAL, detail TEXT NOT NULL DEFAULT '{}', terminal INTEGER NOT NULL DEFAULT 0,
  outcome TEXT, outcome_acked INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pins (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, size INTEGER NOT NULL, role TEXT NOT NULL, release_id TEXT, pinned_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS lane (lane TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, bytes INTEGER NOT NULL, created_at REAL NOT NULL, PRIMARY KEY (lane, seq));
CREATE TABLE IF NOT EXISTS lane_cursor (lane TEXT PRIMARY KEY, next_seq INTEGER NOT NULL, committed_seq INTEGER NOT NULL, bytes INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS loss (lane TEXT NOT NULL, from_seq INTEGER NOT NULL, to_seq INTEGER NOT NULL, reason TEXT NOT NULL, reported INTEGER NOT NULL DEFAULT 0);
"""


class StorageError(RuntimeError):
    """Terminal journal storage failure; the agent must stop rather than continue with unknown state."""


class StorageBusy(StorageError):
    """A bounded transaction could not take the journal lock in time; nothing was written."""


class Journal:
    def __init__(self, path: str | os.PathLike[str]):
        self.broken: str | None = None
        self.closed = False
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False, timeout=5)
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        for lane in LANES:
            self.conn.execute(
                "INSERT OR IGNORE INTO lane_cursor(lane, next_seq, committed_seq, bytes) VALUES (?, 1, 0, 0)",
                (lane,),
            )

    # ---- transactions ----
    class _Txn:
        """BEGIN IMMEDIATE / COMMIT with the lock released on every failure path (R27). A COMMIT failure
        is rolled back before the lock is released; if cleanup itself fails the journal is marked
        broken and every later transaction raises StorageError instead of deadlocking."""

        def __init__(self, j: Journal, lock_timeout_s: float | None = None):
            self.j = j
            self.lock_timeout_s = lock_timeout_s

        def __enter__(self):
            if self.lock_timeout_s is None:
                self.j._lock.acquire()
            elif not self.j._lock.acquire(timeout=max(0.0, self.lock_timeout_s)):
                # a bounded caller (shutdown's final accounting) never queues behind another writer's
                # transaction without limit: it is told the journal is busy and nothing is written
                raise StorageBusy(f"journal lock busy for {self.lock_timeout_s:.3f} s")
            if self.j.broken or self.j.closed:
                self.j._lock.release()
                raise StorageError(self.j.broken or "journal closed")
            try:
                self.j.conn.execute("BEGIN IMMEDIATE")
            except Exception as e:
                self.j._lock.release()
                raise StorageError(f"BEGIN failed: {e}") from e
            return self.j.conn

        def __exit__(self, et, ev, tb):
            try:
                if et is None:
                    try:
                        self.j.conn.execute("COMMIT")
                    except Exception as e:
                        try:
                            self.j.conn.execute("ROLLBACK")
                        except Exception as e2:
                            self.j.broken = f"commit failed ({e}) and rollback failed ({e2})"
                        raise StorageError(f"COMMIT failed: {e}") from e
                else:
                    try:
                        self.j.conn.execute("ROLLBACK")
                    except Exception as e2:
                        self.j.broken = f"rollback failed: {e2}"
            finally:
                self.j._lock.release()
            return False

    def txn(self, lock_timeout_s: float | None = None) -> Journal._Txn:
        return Journal._Txn(self, lock_timeout_s)

    # ---- kv ----
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any, conn: sqlite3.Connection | None = None) -> None:
        c = conn or self.conn
        with self._lock:
            if conn is None and (self.broken or self.closed):
                raise StorageError(
                    self.broken or "journal closed"
                )  # a broken/closed journal persists nothing
            c.execute(
                "INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def set_many(self, items: dict[str, Any]) -> None:
        with self.txn() as c:
            for k, v in items.items():
                self.set(k, v, c)

    def next_seq(self) -> int:
        """Device-wide persistent report sequence."""
        with self.txn() as c:
            cur = self.get("report_seq", 0) + 1
            self.set("report_seq", cur, c)
        return cur

    # ---- operation ----
    def current_operation(self) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM operation WHERE terminal=0 ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return self._op(row)

    def operation(self, op_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM operation WHERE id=?", (op_id,)).fetchone()
        return self._op(row)

    def unacked_terminal(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM operation WHERE terminal=1 AND outcome_acked=0 ORDER BY created_at"
            ).fetchall()
        return [self._op(r) for r in rows]

    def _op(self, row) -> dict[str, Any] | None:
        if row is None:
            return None
        cols = [
            "id",
            "type",
            "payload",
            "generation",
            "stage",
            "grant_json",
            "grant_consumed_seq",
            "attempts",
            "started_monotonic",
            "detail",
            "terminal",
            "outcome",
            "outcome_acked",
            "created_at",
            "updated_at",
        ]
        d = dict(zip(cols, row, strict=False))
        d["payload"] = json.loads(d["payload"])
        d["grant"] = json.loads(d["grant_json"]) if d["grant_json"] else None
        d["detail"] = json.loads(d["detail"] or "{}")
        d["outcome"] = json.loads(d["outcome"]) if d["outcome"] else None
        return d

    def begin_operation(self, op: dict[str, Any], *, local: bool = False) -> None:
        """Idempotent insert (a resumed operation keeps its row). `local=True` marks a device-originated
        recovery row whose outcome is never posted to the server (outcome_acked starts at 1)."""
        now = time.time()
        with self.txn() as c:
            c.execute(
                "INSERT OR IGNORE INTO operation(id,type,payload,generation,stage,attempts,detail,outcome_acked,created_at,updated_at) VALUES (?,?,?,?,?,0,'{}',?,?,?)",
                (
                    op["id"],
                    op["type"],
                    json.dumps(op["payload"]),
                    int(op["generation"]),
                    "Staging",
                    1 if local else 0,
                    now,
                    now,
                ),
            )

    def set_stage(
        self,
        op_id: str,
        stage: str,
        *,
        detail: dict[str, Any] | None = None,
        grant: dict[str, Any] | None = None,
        grant_consumed_seq: int | None = None,
        started_monotonic: float | None = None,
        bump_attempts: bool = False,
        kv_updates: dict[str, Any] | None = None,
    ) -> None:
        """One committed transaction: the stage transition, its detail and any kv pointers that must be
        observed together (R38: cutover intent + recovery identity are never split across commits)."""
        with self.txn() as c:
            row = c.execute("SELECT detail, attempts FROM operation WHERE id=?", (op_id,)).fetchone()
            if row is None:
                raise KeyError(op_id)
            d = json.loads(row[0] or "{}")
            if detail:
                d.update(detail)
            sets = ["stage=?", "detail=?", "updated_at=?"]
            args: list[Any] = [stage, json.dumps(d), time.time()]
            if grant is not None:
                sets.append("grant_json=?")
                args.append(json.dumps(grant))
            if grant_consumed_seq is not None:
                sets.append("grant_consumed_seq=?")
                args.append(grant_consumed_seq)
            if started_monotonic is not None:
                sets.append("started_monotonic=?")
                args.append(started_monotonic)
            if bump_attempts:
                sets.append("attempts=?")
                args.append(int(row[1]) + 1)
            args.append(op_id)
            c.execute(f"UPDATE operation SET {', '.join(sets)} WHERE id=?", args)
            for k, v in (kv_updates or {}).items():
                self.set(k, v, c)

    def finish_operation(
        self,
        op_id: str,
        stage: str,
        outcome: dict[str, Any],
        kv_updates: dict[str, Any] | None = None,
        *,
        acked: bool = False,
    ) -> None:
        """Terminal commit. `outcome` is the COMPLETE immutable wire outcome (R33): it is replayed to the
        server unchanged until acknowledged. `acked=True` marks a local-only terminal row (never posted)."""
        with self.txn() as c:
            c.execute(
                "UPDATE operation SET stage=?, terminal=1, outcome=?, outcome_acked=MAX(outcome_acked, ?), updated_at=? WHERE id=?",
                (stage, json.dumps(outcome), 1 if acked else 0, time.time(), op_id),
            )
            for k, v in (kv_updates or {}).items():
                self.set(k, v, c)

    def mark_acked(self, op_id: str) -> None:
        with self.txn() as c:
            c.execute("UPDATE operation SET outcome_acked=1 WHERE id=?", (op_id,))

    # ---- pins ----
    def pin(self, path: str, sha256: str, size: int, role: str, release_id: str | None) -> None:
        with self.txn() as c:
            c.execute(
                "INSERT OR REPLACE INTO pins(path,sha256,size,role,release_id,pinned_at) VALUES (?,?,?,?,?,?)",
                (path, sha256, size, role, release_id, time.time()),
            )

    def pins(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute("SELECT path,sha256,size,role,release_id,pinned_at FROM pins").fetchall()
        return [
            dict(zip(["path", "sha256", "size", "role", "release_id", "pinned_at"], r, strict=False))
            for r in rows
        ]

    def unpin_release(self, release_id: str, keep_roles: tuple[str, ...] = ()) -> list[str]:
        with self.txn() as c:
            rows = c.execute("SELECT path, role FROM pins WHERE release_id=?", (release_id,)).fetchall()
            removed = [path for path, role in rows if role not in keep_roles]
            for path in removed:
                c.execute("DELETE FROM pins WHERE path=?", (path,))
        return removed

    # ---- lanes (spool) ----
    def append_and_set(
        self,
        lane: str,
        kind: str,
        body: dict[str, Any],
        key: str,
        value: Any,
        *,
        lock_timeout_s: float | None = None,
    ) -> int | None:
        """Append a record and update a kv watermark in ONE transaction (usage emission + consumed
        watermark): a crash can never leave the record without its watermark or vice versa. With
        `lock_timeout_s` the journal lock is taken with that bound (StorageBusy when another writer holds
        it past it; nothing written)."""
        with self.txn(lock_timeout_s) as c:
            seq = self._append_in(c, lane, kind, body)
            c.execute(
                "INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            return seq

    def append(self, lane: str, kind: str, body: dict[str, Any]) -> int | None:
        """Append a record; enforce the lane quota by dropping the oldest *uncommitted-but-oldest*
        telemetry records and recording an explicit loss range. Critical lane never drops silently:
        when full it records the loss and refuses (returns None)."""
        with self.txn() as c:
            return self._append_in(c, lane, kind, body)

    def _append_in(self, c: sqlite3.Connection, lane: str, kind: str, body: dict[str, Any]) -> int | None:
        data = json.dumps(body, separators=(",", ":"))
        nbytes = len(data)
        cur = c.execute("SELECT next_seq, bytes FROM lane_cursor WHERE lane=?", (lane,)).fetchone()
        seq, total = int(cur[0]), int(cur[1])
        quota = LANE_QUOTA_BYTES[lane]
        if total + nbytes > quota:
            if lane == "critical":
                c.execute(
                    "INSERT INTO loss(lane,from_seq,to_seq,reason) VALUES (?,?,?,?)",
                    (lane, seq, seq, "critical_lane_full"),
                )
                c.execute("UPDATE lane_cursor SET next_seq=? WHERE lane=?", (seq + 1, lane))
                return None
            # evict oldest records until it fits; record the evicted range
            rows = c.execute("SELECT seq, bytes FROM lane WHERE lane=? ORDER BY seq", (lane,)).fetchall()
            freed = 0
            first = last = None
            for s_, b in rows:
                if total - freed + nbytes <= quota:
                    break
                freed += int(b)
                first = s_ if first is None else first
                last = s_
            if first is not None:
                c.execute("DELETE FROM lane WHERE lane=? AND seq BETWEEN ? AND ?", (lane, first, last))
                c.execute(
                    "INSERT INTO loss(lane,from_seq,to_seq,reason) VALUES (?,?,?,?)",
                    (lane, first, last, "quota_evicted"),
                )
                total -= freed
        c.execute(
            "INSERT INTO lane(lane,seq,kind,body,bytes,created_at) VALUES (?,?,?,?,?,?)",
            (lane, seq, kind, data, nbytes, time.time()),
        )
        c.execute("UPDATE lane_cursor SET next_seq=?, bytes=? WHERE lane=?", (seq + 1, total + nbytes, lane))
        return seq

    def pending(self, lane: str, limit: int = 200) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self._lock:
            cur = self.conn.execute("SELECT committed_seq FROM lane_cursor WHERE lane=?", (lane,)).fetchone()
            rows = self.conn.execute(
                "SELECT seq, kind, body FROM lane WHERE lane=? AND seq>? ORDER BY seq LIMIT ?",
                (lane, int(cur[0]), limit),
            ).fetchall()
            losses = self.conn.execute(
                "SELECT rowid, from_seq, to_seq, reason FROM loss WHERE lane=? AND reported=0", (lane,)
            ).fetchall()
        return [{"seq": r[0], "kind": r[1], "body": json.loads(r[2])} for r in rows], [
            {"rowid": x[0], "from_seq": x[1], "to_seq": x[2], "reason": x[3]} for x in losses
        ]

    def commit_lane(self, lane: str, committed_seq: int, loss_rowids: list[int]) -> None:
        """Delete only records the server durably acknowledged; never move the cursor backwards. A
        submitted loss row is retired only when the server's acknowledged frontier actually covers its
        span (partial ACKs keep the rest pending, R48 follow-up)."""
        with self.txn() as c:
            cur = int(c.execute("SELECT committed_seq FROM lane_cursor WHERE lane=?", (lane,)).fetchone()[0])
            new = max(cur, int(committed_seq))
            freed = c.execute(
                "SELECT COALESCE(SUM(bytes),0) FROM lane WHERE lane=? AND seq<=?", (lane, new)
            ).fetchone()[0]
            c.execute("DELETE FROM lane WHERE lane=? AND seq<=?", (lane, new))
            c.execute(
                "UPDATE lane_cursor SET committed_seq=?, bytes=MAX(0, bytes-?) WHERE lane=?",
                (new, int(freed), lane),
            )
            for rid in loss_rowids:
                c.execute("UPDATE loss SET reported=1 WHERE rowid=? AND to_seq<=?", (rid, int(committed_seq)))

    def declare_loss(self, lane: str, from_seq: int, to_seq: int, reason: str) -> int | None:
        """Declare a span the device can no longer supply (e.g. history acknowledged by a server that
        was later restored to an older snapshot). Idempotent by construction: one pending declaration
        per lane and reason, replaced by the exact current span; never touches sequences or records."""
        if to_seq < from_seq:
            return None
        with self.txn() as c:
            existing = c.execute(
                "SELECT rowid FROM loss WHERE lane=? AND reason=? AND reported=0 AND from_seq=? AND to_seq=?",
                (lane, reason, int(from_seq), int(to_seq)),
            ).fetchone()
            if existing:
                return int(existing[0])
            # drop only pending same-reason declarations that OVERLAP this span (a re-declaration of a
            # changed span replaces the stale one); disjoint chunks of one large gap are all kept
            c.execute(
                "DELETE FROM loss WHERE lane=? AND reason=? AND reported=0 AND NOT (to_seq<? OR from_seq>?)",
                (lane, reason, int(from_seq), int(to_seq)),
            )
            cur = c.execute(
                "INSERT INTO loss(lane,from_seq,to_seq,reason) VALUES (?,?,?,?)",
                (lane, int(from_seq), int(to_seq), reason[:64]),
            )
            return int(cur.lastrowid)

    def drop_record(self, lane: str, seq: int, reason: str) -> None:
        """Explicit, accountable disposition of ONE retained record the server refuses outright (422):
        the record is deleted and its sequence declared as loss with the server's reason, so the lane can
        progress and the gap is visible in coverage; never silent, never a cursor jump."""
        with self.txn() as c:
            row = c.execute("SELECT bytes FROM lane WHERE lane=? AND seq=?", (lane, int(seq))).fetchone()
            if row is None:
                return
            c.execute("DELETE FROM lane WHERE lane=? AND seq=?", (lane, int(seq)))
            c.execute("UPDATE lane_cursor SET bytes=MAX(0, bytes-?) WHERE lane=?", (int(row[0]), lane))
            c.execute(
                "INSERT INTO loss(lane,from_seq,to_seq,reason) VALUES (?,?,?,?)",
                (lane, int(seq), int(seq), reason[:64]),
            )

    def loss_covers(self, lane: str, seq: int) -> str | None:
        """Reason of a declared loss covering `seq` (reported or pending), else None."""
        with self._lock:
            row = self.conn.execute(
                "SELECT reason FROM loss WHERE lane=? AND from_seq<=? AND to_seq>=? LIMIT 1",
                (lane, int(seq), int(seq)),
            ).fetchone()
        return str(row[0]) if row else None

    def lane_status(self) -> dict[str, Any]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT lane, next_seq, committed_seq, bytes FROM lane_cursor"
            ).fetchall()
        return {
            r[0]: {"next_seq": r[1], "committed_seq": r[2], "bytes": r[3], "quota": LANE_QUOTA_BYTES[r[0]]}
            for r in rows
        }

    def close(self) -> None:
        """After close every transaction raises StorageError (never a bare sqlite error from a late
        writer such as a gateway callback that outlived the shutdown drain)."""
        with self._lock:
            self.closed = True
            self.conn.close()
