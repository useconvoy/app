"""Worker process/thread: fenced scheduler ticks, grant expiry, deadlines, rollouts, occurrences,
retention and the nightly backup. Any FenceLost aborts the tick; the next tick re-acquires."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..config import get_settings
from ..db import session_scope
from ..ids import utcnow
from . import operations as ops
from . import rollouts as rollouts_svc
from .scheduler import FenceLost, Worker, finalize_occurrences, tick_schedules

log = logging.getLogger("convoy.worker")

# The nightly backup runs as ONE single-flight background task per worker (BackupTask) with its own
# session and connections, so the tick thread keeps scheduling, coordinating maintenance and renewing
# the 30 s lease while a multi-gigabyte copy proceeds. Budgets (measured 2026-09-13 on the live
# 2,157,518,848-byte / 526,738-page database at ~147 MiB/s copy throughput: ~14 s for the copy alone,
# plus hashing and fsync of the same bytes):
# - BACKUP_ATTEMPT_S bounds the whole attempt (copy, hash, fsync, fenced publication): 300 s, about
#   20x the measured copy, so a slow disk defers instead of running forever;
# - BACKUP_SNAPSHOT_S bounds how long a WAL snapshot may be held (WAL cannot be reset meanwhile, so WAL
#   grows and checkpoints wait for that long at most): 120 s, about 8x the measured copy;
# - BACKUP_SOURCE_LOCK_S bounds the writer COMMIT pause under a rollback (DELETE) journal: 3 s, below the
#   API's 5 s busy timeout. At the current size a DELETE source therefore defers honestly; completion
#   needs the qualified WAL runtime (db-mode wal on the pinned library).
# All bounds are checked between 256-page steps and hash chunks, so one in-flight step or I/O can
# overrun them; a deferred attempt is retried the same UTC day with bounded backoff, never every tick.
BACKUP_ATTEMPT_S = 300.0
BACKUP_SNAPSHOT_S = 120.0
BACKUP_SOURCE_LOCK_S = 3.0
BACKUP_RETRY_MIN_S = 60.0
BACKUP_RETRY_MAX_S = 900.0
BACKUP_JOIN_S = 10.0  # bounded wait for the task at worker stop (cancellation lands at the next step)


def run_tick(worker: Worker) -> dict[str, Any]:
    out: dict[str, Any] = {"owner": worker.owner}
    with session_scope() as db:
        if not worker.acquire(db):
            return {**out, "leader": False}
        out["leader"] = True
        try:
            out["fired"] = tick_schedules(db, worker)
            out["grants_expired"] = ops.expire_grants(db, worker)
            out["deadlines"] = ops.sweep_deadlines(db, worker)
            out["rollouts"] = rollouts_svc.advance_all(db, worker)
            out["occurrences"] = finalize_occurrences(db, worker)
        except FenceLost as e:
            log.warning("fence lost: %s", e)
            out["fence_lost"] = True
            out["leader"] = False  # R53 follow-up: no maintenance on a lost lease
            worker.fence = 0
    return out


class BackupTask(threading.Thread):
    """One backup attempt on its own thread and session. It captures the leader's identity as an
    immutable FenceToken at start: the attempt publishes only through that token's fenced transaction,
    so a task whose worker lost the lease (or re-acquired it under a new fence) can neither publish,
    register nor prune. Cancellation is owned by the WorkerThread (leader loss, stop); the result is
    collected by the worker on a later tick. Daemon: an unfinished copy never keeps the process alive."""

    def __init__(self, token: Any, *, attempt_s: float, snapshot_s: float, source_lock_s: float):
        super().__init__(daemon=True, name="convoy-backup")
        self.token = token
        self.attempt_s, self.snapshot_s, self.source_lock_s = attempt_s, snapshot_s, source_lock_s
        self.stop = threading.Event()
        self.started_mono = time.monotonic()
        self.result: dict[str, Any] | None = None
        self.deferred: Any = None  # BackupDeferred
        self.fence_lost: Any = None  # FenceLost
        self.error: BaseException | None = None

    def run(self) -> None:
        from .backup import BackupDeferred, take_backup

        try:
            with session_scope() as db:
                self.result = take_backup(
                    db,
                    None,
                    worker=self.token,
                    budget_s=self.attempt_s,
                    snapshot_s=self.snapshot_s,
                    source_lock_s=self.source_lock_s,
                    stop=self.stop,
                )
        except BackupDeferred as e:
            self.deferred = e
        except FenceLost as e:
            self.fence_lost = e
        except BaseException as e:  # noqa: BLE001 - collected by the worker, never lost
            self.error = e

    def cancel(self) -> None:
        self.stop.set()


class WorkerThread(threading.Thread):
    def __init__(self, interval_s: float | None = None):
        super().__init__(daemon=True, name="convoy-worker")
        self.interval_s = interval_s or get_settings().scheduler_interval_s
        self.worker = Worker()
        self._halt = threading.Event()
        self.last: dict[str, Any] = {}
        self._last_backup_day: str | None = None
        self._last_retention: float = 0.0
        self._backup_retry_at: float = 0.0
        self._backup_backoff_s: float = BACKUP_RETRY_MIN_S
        self._backup_task: BackupTask | None = None
        self.backup_attempt_s = BACKUP_ATTEMPT_S
        self.backup_snapshot_s = BACKUP_SNAPSHOT_S
        self.backup_source_lock_s = BACKUP_SOURCE_LOCK_S
        self.backup_after_hour = 2  # one backup per UTC day, once this hour has begun
        self.last_backup: dict[str, Any] | None = None

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                self.last = run_tick(self.worker)
                self._maintenance()
            except Exception:
                log.exception("worker tick failed")
            self._halt.wait(self.interval_s)
        task = self._backup_task
        if task is not None and task.is_alive():
            task.cancel()
            task.join(timeout=BACKUP_JOIN_S)
            if task.is_alive():
                log.warning("backup task did not stop within %.0f s; left to process exit", BACKUP_JOIN_S)

    def _collect_backup(self, day: str) -> None:
        """Result of a finished task (called on the tick thread); the task never publishes after a
        lost fence, so a stale result here is at most a log line."""
        task = self._backup_task
        if task is None or task.is_alive():
            return
        self._backup_task = None
        if task.result is not None:
            r = task.result
            self._last_backup_day = day
            self._backup_backoff_s = BACKUP_RETRY_MIN_S
            self.last_backup = {"ok": True, **r}
            log.info(
                "backup: SUCCESS id=%s created_at=%s size=%d sha256=%s source_mode=%s snapshot_held_s=%s duration_ms=%s",
                r["id"], r["created_at"], r["size"], r["sha256"], r.get("source_mode"),
                r.get("source_lock_held_s"), r["duration_ms"],
            )  # fmt: skip
        elif task.deferred is not None:
            e = task.deferred
            self._last_backup_day = None
            self._backup_retry_at = time.monotonic() + self._backup_backoff_s
            self.last_backup = {"ok": False, **e.details, "retry_in_s": self._backup_backoff_s}
            self.last["backup_deferred"] = self.last_backup
            log.warning("backup deferred (retry in %.0f s): %s", self._backup_backoff_s, e)
            self._backup_backoff_s = min(self._backup_backoff_s * 2, BACKUP_RETRY_MAX_S)
        elif task.fence_lost is not None:
            self._last_backup_day = None  # a new leader (maybe this worker, re-fenced) tries again
            self.last_backup = {"ok": False, "reason": "fence_lost", "error": str(task.fence_lost)}
            log.warning("backup skipped: %s", task.fence_lost)
        else:
            self._last_backup_day = day  # any other failure counts as this day's attempt (no storm)
            self.last_backup = {"ok": False, "reason": "error", "error": repr(task.error)}
            log.error("backup failed: %r", task.error)

    def _maintenance(self) -> None:
        day = utcnow().strftime("%Y-%m-%d")
        self._collect_backup(day)
        if not self.last.get("leader"):
            task = self._backup_task
            if task is not None and task.is_alive():
                task.cancel()  # a stalled ex-leader holds no snapshot and could not publish anyway
            return
        now = time.monotonic()
        if now - self._last_retention > 3600:
            self._last_retention = now
            from .evidence import apply_retention

            with session_scope() as db:
                try:
                    log.info("retention: %s", apply_retention(db, self.worker))
                except FenceLost as e:
                    log.warning("retention skipped: %s", e)
                    self.last["leader"] = False
                    return
        task = self._backup_task
        if (
            (task is None or not task.is_alive())  # single flight within this worker
            and self._last_backup_day != day
            and utcnow().hour >= self.backup_after_hour
            and now >= self._backup_retry_at
            and get_settings().db_url.startswith("sqlite")
            and ":memory:" not in get_settings().db_url
        ):
            # the task captures the CURRENT identity; a later fence change makes its publication fail
            self._backup_task = BackupTask(
                self.worker.token_snapshot(),
                attempt_s=self.backup_attempt_s,
                snapshot_s=self.backup_snapshot_s,
                source_lock_s=self.backup_source_lock_s,
            )
            self._backup_task.start()
            log.info("backup: task started under %s", self._backup_task.token)

    def stop(self) -> None:
        self._halt.set()
        task = self._backup_task
        if task is not None:
            task.cancel()


def worker_health(settings: Any) -> dict[str, Any]:
    """R73: container healthcheck for the worker service. Healthy when the shared database holds a
    scheduler lease that is still unexpired (a live leader refreshes it on every fenced transaction)."""
    import sqlite3
    from datetime import datetime, timezone

    from ..migrations import db_file_path

    path = db_file_path(settings)
    if path is None or not path.exists():
        return {"ok": False, "error": f"database not found at {path}"}
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = c.execute(
                "SELECT owner, fence, expires_at FROM scheduler_leases WHERE name='scheduler'"
            ).fetchone()
        finally:
            c.close()
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}
    if row is None:
        return {"ok": False, "error": "no scheduler lease yet"}
    exp = row[2]
    try:
        exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return {"ok": False, "error": f"unparseable lease expiry {exp!r}"}
    live = exp_dt > datetime.now(timezone.utc)
    return {"ok": live, "owner": row[0], "fence": row[1], "expires_at": str(exp)}


def run_forever(interval_s: float | None = None) -> int:
    """Process entrypoint (`convoy-server worker`). SIGTERM/SIGINT stop the tick loop, wait (bounded) for
    an in-flight tick to finish and exit 0, so a container stop never needs SIGKILL."""
    import signal

    t = WorkerThread(interval_s)
    stop = threading.Event()

    def handler(signum, frame):  # pragma: no cover - exercised as a subprocess in tests
        log.info("signal %s received: stopping the worker", signum)
        t.stop()
        stop.set()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    log.info("worker starting as %s", t.worker.owner)
    t.start()
    try:
        while not stop.is_set():
            stop.wait(60)
    except KeyboardInterrupt:
        t.stop()
    t.join(timeout=30)
    if t.is_alive():
        log.warning("worker tick did not finish within 30 s; exiting anyway")
        return 1
    return 0
