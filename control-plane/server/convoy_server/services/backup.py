"""Consistent backups with the SQLite Online Backup API; restore stages, quarantines, then publishes.

R52: the backup source and restore target are the CONFIGURED database (settings.db_url), which must
already exist and carry the Convoy schema; a missing source is an error, never created as a side effect.
R51: a restore copies the snapshot to a staging file, migrates and quarantines THE STAGING FILE, and only
then publishes it over the live database with an atomic rename. A crash before publication leaves the
live database untouched; a crash after publication leaves a database that is already quarantined.
Restore requires exclusive ownership: it refuses while any api/worker process holds the database lock."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session as DbSession

from ..config import get_settings
from ..db import DbLocked, acquire_db_lock, make_engine, write_txn
from ..ids import iso, new_id, utcnow
from ..migrations import db_file_path, ensure_schema
from ..models import Backup

REQUIRED_TABLES = ("installation", "devices", "users", "releases")
log = logging.getLogger("convoy.backup")


class BackupError(RuntimeError):
    pass


class LiveWalIncomplete(BackupError):
    """The live database's WAL could not be folded completely into its main file (a reader still holds
    an older snapshot, or new frames appeared). Nothing was unlinked or replaced."""


class BackupDeferred(BackupError):
    """A bounded backup attempt that was stopped before publication: the time budget ran out (`budget`)
    or the caller asked to stop (`stopped`). Nothing was published or registered; the attempt's own
    staging file is removed by the caller's cleanup. Distinct from BackupError so the worker can retry
    later instead of treating it as a failed day."""

    def __init__(self, reason: str, phase: str, **details: Any):
        self.reason = reason
        self.phase = phase
        self.details = {"reason": reason, "phase": phase, **details}
        super().__init__(
            f"backup {reason} during {phase}: " + ", ".join(f"{k}={v}" for k, v in details.items())
        )


# The source connection's busy handler wait per attempt (a writer committing under a rollback journal
# holds an EXCLUSIVE lock briefly); sqlite's own default of 5 s would let a single BUSY step eat most
# of a bounded budget. The copy loop sleeps `BACKUP_STEP_SLEEP_S` between BUSY/LOCKED retries.
BACKUP_SOURCE_BUSY_S = 0.2
LIVE_WAL_BUSY_S = 5.0  # how long an offline TRUNCATE checkpoint waits for a foreign reader to let go
BACKUP_STEP_SLEEP_S = 0.05
BACKUP_STEP_PAGES = 256
_SQLITE_BUSY, _SQLITE_LOCKED = 5, 6


def _open_ro(path: Path, timeout: float = 5.0) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)


def is_convoy_db(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        c = _open_ro(path)
    except sqlite3.Error:
        return False
    try:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        return False
    finally:
        c.close()
    return all(t in names for t in REQUIRED_TABLES)


def live_db_path() -> Path:
    p = db_file_path(get_settings())
    if p is None:
        raise BackupError("in-memory databases cannot be backed up or restored")
    return p


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def take_backup(
    db: DbSession,
    out: str | None,
    worker: Any = None,
    *,
    budget_s: float | None = None,
    source_lock_s: float | None = None,
    snapshot_s: float | None = None,
    stop: threading.Event | None = None,
    attempt_id: str | None = None,
    _step_hook: Callable[[int, int, int], None] | None = None,
    _phase_hook: Callable[[str], None] | None = None,
    _clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Snapshot -> hash under a private staging path -> ONE fenced transaction that publishes the file
    (atomic rename), registers it and prunes old backups. A stalled ex-leader can therefore only ever
    delete its own unpublished staging file; it can never overwrite a published backup, register a row
    or prune anything (R53 follow-up).

    `budget_s` bounds the whole attempt on the monotonic clock, measured from before the early fence
    refresh (so the fenced publication lands within `budget_s` of that refresh):
    the page-copy loop is checked after every step (BUSY/LOCKED steps included), the hash after every
    chunk and once more before the fenced publication. A SQLite online backup restarts from page zero
    whenever another connection writes to the source, so under continuous writes an attempt can make no
    net progress; the budget turns that into a `BackupDeferred` (nothing published, staging removed)
    instead of an unbounded stall of the caller. `stop` (an Event) cancels at the same checkpoints.
    Not bounded: a single page-copy step, an fsync, or any other uninterruptible I/O already in flight.

    Two DISTINCT hold budgets apply to the pinned snapshot, chosen by the source file's journal mode
    read on the source connection: `source_lock_s` under a rollback (DELETE) journal, where the pinned
    read transaction PAUSES writer COMMITs, and `snapshot_s` under WAL, where the snapshot blocks no
    writer but keeps the WAL from being reset (WAL growth and delayed checkpoints for its lifetime).
    Removing one never disables the pin: the snapshot is pinned whenever either budget is given.

    `source_lock_s` (worker attempts) pins the source snapshot: the read-only source connection opens an
    explicit read transaction (BEGIN + a real SELECT, which takes the SHARED lock) before the copy, so
    every copy step reuses that transaction and a writer's commit can no longer restart the copy. Under a
    rollback journal this is a bounded writer COMMIT pause, not a non-blocking snapshot: writers keep
    working but their COMMIT waits for the SHARED lock. The hold is bounded by `source_lock_s` as a
    callback-enforced budget checked between steps (kept below the API's 5 s busy timeout; one in-flight
    step or I/O can still overrun it, so it is an intended bound, not a guarantee) and released immediately after
    the copy, BEFORE the hash, the fsync and the fenced publication, which therefore never run while this
    attempt holds the source read lock (a write from this process while holding it would deadlock into
    a busy timeout). Under WAL the pinned snapshot blocks nobody. `_step_hook`/`_clock` are deterministic
    test injection points."""
    s = get_settings()
    t0 = _clock()
    deadline = None if budget_s is None else t0 + budget_s
    lock: dict[str, Any] = {
        "pinned_at": None, "released_at": None, "deadline": None, "held_s": None, "limit": None, "mode": None
    }  # fmt: skip
    progress = {"restarts": 0, "busy_steps": 0, "steps": 0, "remaining": None, "total": None}
    attempt = attempt_id or new_id("bka", 6)

    def check(phase: str) -> None:
        if _phase_hook is not None:
            _phase_hook(phase)
        now = _clock()
        if lock["held_s"] is not None:
            held = lock["held_s"]  # released: the frozen copy-hold, never the hash/publish time after it
        elif lock["pinned_at"] is not None:
            held = round(now - lock["pinned_at"], 3)  # still pinned (copy phase)
        else:
            held = None
        if stop is not None and stop.is_set():
            raise BackupDeferred(
                "stopped", phase, elapsed_s=round(now - t0, 3), source_lock_held_s=held, **progress
            )
        if lock["deadline"] is not None and now > lock["deadline"]:
            raise BackupDeferred(
                "budget",
                phase,
                limit=lock["limit"],
                source_mode=lock["mode"],
                source_lock_s=source_lock_s,
                snapshot_s=snapshot_s,
                source_lock_held_s=held,
                elapsed_s=round(now - t0, 3),
                attempt=attempt,
                **progress,
            )
        if deadline is not None and now > deadline:
            raise BackupDeferred(
                "budget",
                phase,
                limit="total",
                budget_s=budget_s,
                source_lock_held_s=held,
                elapsed_s=round(now - t0, 3),
                **progress,
            )

    def pin_source(src: sqlite3.Connection) -> None:
        # explicit read transaction that outlives every copy step; the SELECT is what takes the SHARED
        # lock (a deferred BEGIN alone locks nothing). BUSY here means a writer is committing right now:
        # retry briefly under the total budget rather than sqlite's 5 s default wait.
        while True:
            try:
                src.execute("BEGIN")
                src.execute("SELECT count(*) FROM sqlite_master").fetchone()
                break
            except sqlite3.OperationalError as e:
                if e.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
                if src.in_transaction:
                    src.rollback()
                progress["busy_steps"] += 1
                check("pin")
                time.sleep(BACKUP_STEP_SLEEP_S)
        mode = str(src.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        page_size = int(src.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(src.execute("PRAGMA page_count").fetchone()[0])
        lock["mode"], lock["page_size"], lock["page_count"] = mode, page_size, page_count
        lock["pinned_at"] = _clock()
        lock["pinned_wall"] = time.time()
        budget = snapshot_s if mode == "wal" else source_lock_s
        lock["limit"] = "snapshot" if mode == "wal" else "source_lock"
        lock["deadline"] = None if budget is None else lock["pinned_at"] + budget
        log.info(
            "backup %s: snapshot acquired utc=%s mono=%.3f source_mode=%s page_size=%d page_count=%d "
            "hold_limit=%s hold_budget_s=%s",
            attempt, iso(utcnow()), lock["pinned_at"], mode, page_size, page_count, lock["limit"], budget,
        )  # fmt: skip

    def release_source(src: sqlite3.Connection) -> None:
        """Actual release of the source read transaction (hash/fsync/publication come after it).
        Idempotent: the cleanup path calls it again after a normal release."""
        if lock.get("released"):
            return
        lock["released"] = True
        try:
            if src.in_transaction:
                src.rollback()
        finally:
            src.close()
            if lock["pinned_at"] is not None:
                lock["released_at"] = _clock()
                lock["held_s"] = round(lock["released_at"] - lock["pinned_at"], 3)
                log.info(
                    "backup %s: snapshot released utc=%s mono=%.3f held_s=%.3f steps=%d restarts=%d "
                    "busy_steps=%d remaining=%s",
                    attempt, iso(utcnow()), _clock(), lock["held_s"], progress["steps"],
                    progress["restarts"], progress["busy_steps"], progress["remaining"],
                )  # fmt: skip
            lock["deadline"] = None

    def guard(status: int, remaining: int, total: int) -> None:
        # called by sqlite3.Connection.backup after EVERY step, including ones that returned BUSY/LOCKED
        if _step_hook is not None:
            _step_hook(status, remaining, total)
        progress["steps"] += 1
        if status in (_SQLITE_BUSY, _SQLITE_LOCKED):
            progress["busy_steps"] += 1
        prev = progress["remaining"]
        if status not in (_SQLITE_BUSY, _SQLITE_LOCKED) and prev is not None and remaining >= prev:
            # an OK step always copies a full batch of pages, so no net progress means sqlite went back
            # to page zero because a concurrent writer modified the source (a restart every step keeps
            # `remaining` constant rather than growing)
            progress["restarts"] += 1
        progress["remaining"], progress["total"] = remaining, total
        check("copy")

    if worker is not None:
        with write_txn(db):
            worker.fenced(db)  # cheap early exit for a worker that already lost its lease
    src_path = live_db_path()
    if not is_convoy_db(src_path):
        raise BackupError(f"configured database {src_path} does not exist or is not a Convoy database")
    bdir = s.backup_dir or (src_path.parent / "backups")
    bdir.mkdir(parents=True, exist_ok=True)
    dest = Path(out) if out else bdir / f"convoy-{utcnow().strftime('%Y%m%dT%H%M%SZ')}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.with_name(f".{dest.name}.staging-{new_id('bk', 6)}")
    try:
        check("copy")
        src = _open_ro(src_path, timeout=BACKUP_SOURCE_BUSY_S if deadline is not None else 5.0)
        try:
            if source_lock_s is not None or snapshot_s is not None:
                pin_source(src)
            dst = sqlite3.connect(str(staging))
            try:
                src.backup(dst, pages=BACKUP_STEP_PAGES, progress=guard, sleep=BACKUP_STEP_SLEEP_S)
                release_source(src)  # frees writers first: nothing below may run under the source lock
                # the online backup copies the header's WAL flag: make the file self-contained and
                # mode-neutral (readable anywhere without sidecars, restorable into either mode)
                got = str(dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
                if got != "delete":
                    raise BackupError(
                        f"staging file could not be set to journal_mode=delete (answered {got})"
                    )
            finally:
                release_source(src)  # idempotent
                dst.close()
        finally:
            src.close()  # idempotent after release_source
        checkpoint = None
        if lock["mode"] == "wal" and worker is not None:
            # normal maintenance after a released snapshot: a PASSIVE checkpoint (never blocks anyone,
            # never waits on readers) so the WAL the snapshot held back can be reset; its result is the
            # post-copy recovery evidence (busy, log frames, checkpointed frames)
            try:
                from sqlalchemy import text as _text

                r = db.execute(_text("PRAGMA wal_checkpoint(PASSIVE)")).fetchone()
                checkpoint = {"busy": int(r[0]), "log_frames": int(r[1]), "checkpointed_frames": int(r[2])}
                db.commit()
            except Exception as e:  # pragma: no cover - evidence only, never a failure of the backup
                checkpoint = {"error": type(e).__name__}
            wal = Path(str(src_path) + "-wal")
            checkpoint["wal_bytes_after"] = wal.stat().st_size if wal.exists() else 0
            log.info("backup %s: post-copy PASSIVE checkpoint %s", attempt, checkpoint)
        h = hashlib.sha256()
        with open(staging, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
                check("hash")
            os.fsync(f.fileno())
        row = Backup(
            id=new_id("bak"),
            path=str(dest),
            size=staging.stat().st_size,
            sha256=h.hexdigest(),
            duration_ms=round((_clock() - t0) * 1000, 1),
        )
        check("publish")  # the source read lock is already released; the fenced write below cannot deadlock
        with write_txn(db):
            # BEGIN IMMEDIATE may have waited (bounded retries) for another writer: a stop or the total
            # deadline that arrived meanwhile must still win, so the decision is taken INSIDE the
            # acquired transaction, before anything is renamed or registered
            if worker is not None:
                worker.fenced(db)  # publication, registration and retention are one fenced unit
            check("publish-acquired")
            os.replace(staging, dest)
            _fsync_dir(dest.parent)
            db.add(row)
            files = sorted(bdir.glob("convoy-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[14:]:  # prune: keep the newest 14 local backups
                if old != dest:
                    old.unlink(missing_ok=True)
        log.info(
            "backup %s: success id=%s utc=%s path=%s size=%d sha256=%s source_mode=%s snapshot_held_s=%s "
            "total_s=%.3f pages=%s restarts=%d busy_steps=%d",
            attempt, row.id, iso(row.created_at), row.path, row.size, row.sha256, lock["mode"],
            lock["held_s"], _clock() - t0, lock.get("page_count"), progress["restarts"], progress["busy_steps"],
        )  # fmt: skip
    except BackupDeferred as e:
        log.warning("backup %s: deferred %s", attempt, e.details)
        raise
    finally:
        # only ever this attempt's own unpublished staging file (and its rollback journal if sqlite left
        # one behind after an aborted copy); never a published backup
        staging.unlink(missing_ok=True)
        staging.with_name(staging.name + "-journal").unlink(missing_ok=True)
    return {
        "id": row.id,
        "path": row.path,
        "size": row.size,
        "sha256": row.sha256,
        "created_at": iso(row.created_at),
        "duration_ms": row.duration_ms,
        "restarts": progress["restarts"],
        "busy_steps": progress["busy_steps"],
        "source_lock_held_s": lock["held_s"],
        "source_mode": lock["mode"],
        "page_size": lock.get("page_size"),
        "page_count": lock.get("page_count"),
        "snapshot_utc": None if lock.get("pinned_wall") is None else iso(_dt_from_wall(lock["pinned_wall"])),
        "snapshot_pinned_mono": lock["pinned_at"],  # `_clock` readings: the exact source-lock window
        "snapshot_released_mono": lock["released_at"],
        "checkpoint": checkpoint,
        "attempt": attempt,
    }


def _dt_from_wall(wall: float):
    import datetime as _dt

    return _dt.datetime.fromtimestamp(wall, tz=_dt.timezone.utc)


def _settle_live_wal(live: Path, *, busy_timeout_s: float | None = None) -> dict[str, Any] | None:
    """Under exclusive Convoy ownership: fold every committed WAL frame into the main file (TRUNCATE
    checkpoint) so a bare-file copy of the live database is complete, and leave no -wal/-shm behind.
    A crash-left WAL is recovered here exactly as SQLite would on the next open: committed frames are
    applied, uncommitted ones are not. No-op for a DELETE-mode file.

    Fails closed: the Convoy lock excludes Convoy processes only. An independent SQLite reader (a
    read-only shell, a monitoring script) holding an older snapshot need not take it, and then the
    checkpoint waits up to `busy_timeout_s`, may legally copy the frames below that reader's mark, and
    returns busy=1 with committed frames still outstanding in the WAL. Deleting the WAL then would drop
    committed state, so on any incomplete checkpoint (busy, log != checkpointed, or a WAL that carries
    frames again after the checkpoint) this raises LiveWalIncomplete and leaves the live file and both
    sidecars exactly as they are: nothing is unlinked, nothing is replaced. This helper never unlinks a
    sidecar itself: after a complete checkpoint SQLite deletes -wal and -shm when the last connection
    closes, so a sidecar that survives that point proves another connection still holds the database
    open (an idle foreign reader), and that is refused too rather than pulling the WAL index from under
    it."""
    from ..db import file_journal_mode

    if file_journal_mode(live) != "wal":
        return None
    wait = LIVE_WAL_BUSY_S if busy_timeout_s is None else busy_timeout_s
    wal = Path(str(live) + "-wal")
    c = sqlite3.connect(str(live), isolation_level=None, timeout=wait)
    try:
        r = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        res = {"busy": int(r[0]), "log_frames": int(r[1]), "checkpointed_frames": int(r[2])}
    finally:
        c.close()  # a complete checkpoint's WAL is deleted on the last close; a foreign reader keeps it
    complete = res["busy"] == 0 and res["log_frames"] == res["checkpointed_frames"]
    res["complete"] = complete
    shm = Path(str(live) + "-shm")
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    if not complete or wal_bytes > 0:
        raise LiveWalIncomplete(
            f"live database {live}: WAL checkpoint incomplete (busy={res['busy']}, log_frames="
            f"{res['log_frames']}, checkpointed_frames={res['checkpointed_frames']}, wal_bytes="
            f"{wal_bytes}): a reader outside Convoy still holds an older snapshot or new frames appeared; "
            f"the live file and its -wal/-shm were left untouched. Close other readers of the database "
            f"and retry"
        )
    if wal.exists() or shm.exists():
        raise LiveWalIncomplete(
            f"live database {live}: the checkpoint completed but -wal/-shm persisted after the last "
            f"Convoy connection closed, so another connection still holds the database open; the live "
            f"file and its sidecars were left untouched. Close other readers of the database and retry"
        )
    return res


def restore_backup(file: str, *, confirm: bool, _fault: str | None = None) -> dict[str, Any]:
    """Stage -> migrate -> quarantine -> publish. Requires --yes and exclusive database ownership.
    `_fault` is a deterministic crash-injection point for tests: "before_publish" | "after_publish".

    WAL-aware: the live database is checkpointed (TRUNCATE) under the exclusive lock BEFORE its bare
    file is copied aside, so the previous installation's copy holds every committed page even after a
    crash left a WAL behind; the staged snapshot (a self-contained DELETE-mode backup file) is converted
    to the configured journal mode after quarantine, so the service that starts next finds the mode it
    expects; and no stale -wal/-shm of the OLD database survives beside the published file (a leftover
    WAL is self-consistent and would otherwise be applied to the replacement)."""
    s = get_settings()
    src = Path(file)
    if not src.exists():
        return {"ok": False, "error": "backup file not found"}
    if not is_convoy_db(src):
        return {"ok": False, "error": "backup file is not a Convoy database"}
    chk = _open_ro(src)
    try:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        chk.close()
    if ok != "ok":
        return {"ok": False, "error": f"integrity_check: {ok}"}
    if not confirm:
        return {
            "ok": False,
            "error": "pass --yes to restore; this replaces the live database and enters quarantine (all sessions, tokens and device credentials are invalidated)",
        }
    live = live_db_path()
    live.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock = acquire_db_lock(s, exclusive=True)
    except DbLocked as e:
        return {"ok": False, "error": str(e)}
    staging = live.with_name(live.name + ".restore-staging")
    try:
        staging.unlink(missing_ok=True)
        shutil.copy2(src, staging)
        with open(staging, "rb+") as f:
            os.fsync(f.fileno())
        # quarantine the STAGED copy: nothing from the snapshot is live until it is already invalidated
        eng = make_engine(replace(s, database_url=f"sqlite:///{staging.as_posix()}"))
        try:
            ensure_schema(eng, allow_rebuild=True)
            from ..db import Session
            from .identity import enter_quarantine

            with Session(eng, expire_on_commit=False, autoflush=False) as db:
                enter_quarantine(
                    db,
                    f"restored from {src.name}",
                    {"file": str(src), "restored_at": iso(utcnow()), "previous_db": None},
                )
        finally:
            eng.dispose()
        # the staged snapshot takes the CONFIGURED mode now (a WAL intent on an unqualified library is
        # refused here, before anything is published)
        from ..db import intended_journal_mode, set_file_journal_mode

        mode_change = set_file_journal_mode(staging, intended_journal_mode(s))
        if _fault == "before_publish":
            raise RuntimeError("simulated crash before publication")
        aside = None
        live_wal = None
        if live.exists():
            try:
                live_wal = _settle_live_wal(live)  # committed WAL frames folded in; sidecars gone
            except LiveWalIncomplete as e:
                # fail closed BEFORE the aside copy and the publication: the live file and its sidecars
                # are exactly as they were, and nothing from the staged snapshot is live
                return {"ok": False, "error": str(e), "live_untouched": True}
            aside = live.with_name(f"{live.stem}.pre-restore-{utcnow().strftime('%Y%m%dT%H%M%SZ')}.db")
            shutil.copy2(live, aside)
            with open(aside, "rb+") as f:
                os.fsync(f.fileno())
        os.replace(staging, live)  # atomic publication
        for suffix in ("-wal", "-shm"):  # never let the old database's sidecars meet the new file
            Path(str(live) + suffix).unlink(missing_ok=True)
        _fsync_dir(live.parent)
        if _fault == "after_publish":
            raise RuntimeError("simulated crash after publication")
    finally:
        staging.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(staging) + suffix).unlink(missing_ok=True)
        lock.close()
        from ..db import reset_engine

        reset_engine()
    return {
        "ok": True,
        "restored_from": str(src),
        "previous_db": str(aside) if aside else None,
        "journal_mode": mode_change["after"],
        "previous_wal_checkpoint": live_wal,
        "next": "run `convoy-server recover-admin <email>` to set a NEW admin password; verify artifacts with `convoy-server verify-artifacts`; re-enroll devices with rebind tokens; lift quarantine in Settings",
    }
