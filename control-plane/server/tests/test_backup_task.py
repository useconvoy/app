"""Backup correction, commit B: the nightly backup as ONE single-flight background task per worker,
under a frozen fence token, so the scheduler keeps ticking and renewing its lease while a large copy
proceeds; WAL snapshots with their own lifetime budget separate from the DELETE writer-pause bound;
mode-neutral, self-contained backup files; cleanup of only the attempt's own staging on every
cancellation path; a stale token that can neither publish, register nor prune.

WAL mechanics run on the developer interpreter with the WAL-reset guard bypassed by explicit
monkeypatch (mechanics, not the race the guard concerns); the qualified library is attested by the
image, never assumed here."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from convoy_server import config, db
from convoy_server.app import create_app
from convoy_server.db import session_scope
from convoy_server.models import Backup
from convoy_server.services import backup as bk
from convoy_server.services import worker as worker_mod
from convoy_server.services.scheduler import LEASE_TTL_S, FenceLost, Worker
from convoy_server.services.worker import BackupTask, WorkerThread, run_tick, worker_health
from sqlalchemy import select


def _grow(live: Path, rows: int, table: str = "_bulk") -> None:
    c = sqlite3.connect(str(live), timeout=5)
    try:
        c.execute(f"CREATE TABLE IF NOT EXISTS {table}(x BLOB)")
        c.executemany(f"INSERT INTO {table} VALUES (?)", [(b"x" * 4000,)] * rows)
        c.commit()
    finally:
        c.close()


def _count(path: Path, table: str = "_bulk") -> int:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


def _mode(path: Path) -> str:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return c.execute("PRAGMA journal_mode").fetchone()[0].lower()
    finally:
        c.close()


def _backups(settings):
    with session_scope() as s:
        return sorted((b.id, b.path, b.size, b.sha256) for b in s.scalars(select(Backup)))


def _staging(bdir: Path) -> list[str]:
    return sorted(p.name for p in bdir.glob(".*.staging-*"))


def _lease_expiry(settings):
    return worker_health(settings)["expires_at"]


def _worker(settings, **kw) -> WorkerThread:
    t = WorkerThread(interval_s=0.2)
    t._last_retention = time.monotonic()
    t.backup_after_hour = 0  # due now
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _wait(fn, timeout=30.0, every=0.05):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise AssertionError("timed out")


# ----------------------------------------------------------------------------- task lifecycle


@pytest.mark.timeout(120)
def test_scheduler_ticks_and_renews_its_lease_while_a_long_backup_runs_single_flight(wal_app, monkeypatch):
    # WAL: the pinned snapshot blocks no writer, so the tick thread's fenced renewals proceed during the
    # copy. Under DELETE the same snapshot pauses every writer, the worker's own renewal included, which
    # is why that mode keeps the 3 s bound (test_backup_budget) and cannot complete at the live size.
    app, settings = wal_app
    live = db.get_engine().url.database
    _grow(Path(live), 3000)  # ~12 MiB
    real = bk.take_backup
    ticks = {"n": 0}
    steps = {"n": 0}

    def slow_step(status, remaining, total):
        steps["n"] += 1
        time.sleep(0.25)  # 256 pages = 1 MiB per step: ~12 steps -> a copy of ~3 s, many ticks long

    def slow_backup(*a, **kw):
        return real(*a, _step_hook=slow_step, **kw)

    monkeypatch.setattr(bk, "take_backup", slow_backup)
    real_tick = worker_mod.run_tick

    def counting_tick(w):
        ticks["n"] += 1
        return real_tick(w)

    monkeypatch.setattr(worker_mod, "run_tick", counting_tick)
    t = _worker(settings)
    t.start()
    try:
        task = _wait(lambda: t._backup_task, 10)
        assert isinstance(task, BackupTask) and task.is_alive()
        first_task = task
        exp0 = _lease_expiry(settings)
        ticks0 = ticks["n"]
        time.sleep(1.2)
        assert t._backup_task is first_task and first_task.is_alive()  # single flight, still copying
        assert ticks["n"] > ticks0 + 3  # the tick thread kept scheduling during the copy
        assert worker_health(settings)["ok"] is True
        assert _lease_expiry(settings) > exp0  # and renewing the lease
        _wait(lambda: t.last_backup is not None, 60)
    finally:
        t.stop()
        t.join(timeout=30)
    assert t.last_backup and t.last_backup["ok"], t.last_backup
    assert t._last_backup_day is not None and t._backup_task is None
    rows = _backups(settings)
    assert len(rows) == 1 and rows[0][0] == t.last_backup["id"]
    assert Path(rows[0][1]).exists() and Path(rows[0][1]).stat().st_size == rows[0][2]
    assert _mode(Path(rows[0][1])) == "delete" and not Path(rows[0][1] + "-wal").exists()
    assert steps["n"] > 8


@pytest.mark.timeout(120)
def test_a_stale_fence_token_cannot_publish_register_or_prune(wal_app, monkeypatch):
    # WAL: the takeover commit lands while the task's snapshot is pinned (under DELETE that commit
    # would wait for the pinned reader, which is the writer pause the 3 s bound exists for)
    app, settings = wal_app
    live = Path(db.get_engine().url.database)
    _grow(live, 300)
    bdir = settings.backup_dir or live.parent / "backups"
    # a previous published backup that must survive untouched
    with session_scope() as s:
        prev = bk.take_backup(s, str(bdir / "convoy-20260101T000000Z.db"))
    w1 = Worker("w1")
    with session_scope() as s:
        assert w1.acquire(s)
    token = w1.token_snapshot()
    hold = threading.Event()
    taken_over = threading.Event()

    def pause_mid_copy(status, remaining, total):
        hold.set()
        taken_over.wait(30)  # the copy resumes only after another worker took the lease

    real = bk.take_backup
    monkeypatch.setattr(bk, "take_backup", lambda *a, **kw: real(*a, _step_hook=pause_mid_copy, **kw))
    task = BackupTask(token, attempt_s=60.0, snapshot_s=60.0, source_lock_s=60.0)
    task.start()
    hold.wait(10)
    # leader handoff while the copy is paused: the lease expires and w2 acquires with fence+1
    from datetime import timedelta

    from convoy_server.db import write_txn
    from convoy_server.ids import utcnow
    from convoy_server.models import SchedulerLease

    with session_scope() as s:
        with write_txn(s):
            s.get(SchedulerLease, "scheduler").expires_at = utcnow() - timedelta(seconds=1)
        w2 = Worker("w2")
        assert w2.acquire(s) and w2.fence == token.fence + 1
    taken_over.set()
    task.join(timeout=60)
    assert not task.is_alive()
    assert task.result is None and isinstance(task.fence_lost, FenceLost), (
        task.result,
        task.deferred,
        task.error,
    )
    # nothing published, registered or pruned; only the attempt's own staging is gone
    assert _backups(settings) == [(prev["id"], prev["path"], prev["size"], prev["sha256"])]
    assert Path(prev["path"]).exists() and _staging(bdir) == []
    assert sorted(p.name for p in bdir.glob("convoy-*.db")) == ["convoy-20260101T000000Z.db"]
    # the same worker re-acquiring under a NEW fence: the old token is still stale
    with session_scope() as s:
        with write_txn(s):
            s.get(SchedulerLease, "scheduler").expires_at = utcnow() - timedelta(seconds=1)
        assert w1.acquire(s) and w1.fence == token.fence + 2
        with write_txn(s):
            with pytest.raises(FenceLost):
                token.fenced(s)
            w1.token_snapshot().fenced(s)  # the fresh token works


@pytest.mark.timeout(120)
@pytest.mark.parametrize("path", ["cancel_copy", "deadline_copy", "disk_error", "cancel_hash"])
def test_every_abort_path_removes_only_its_own_staging_and_keeps_previous_backups(
    app, settings, monkeypatch, path
):
    live = Path(db.get_engine().url.database)
    _grow(live, 1500)
    bdir = settings.backup_dir or live.parent / "backups"
    with session_scope() as s:
        prev = bk.take_backup(s, str(bdir / "convoy-20260101T000000Z.db"))
    w = Worker("w")
    with session_scope() as s:
        assert w.acquire(s)
    token = w.token_snapshot()
    real = bk.take_backup
    task = BackupTask(token, attempt_s=60.0, snapshot_s=60.0, source_lock_s=60.0)
    if path == "cancel_copy":

        def hook(status, remaining, total):
            task.cancel()

        monkeypatch.setattr(bk, "take_backup", lambda *a, **kw: real(*a, _step_hook=hook, **kw))
    elif path == "deadline_copy":
        task.attempt_s = 0.001
    elif path == "disk_error":

        def boom(status, remaining, total):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(bk, "take_backup", lambda *a, **kw: real(*a, _step_hook=boom, **kw))
    else:  # cancel during hash: the clock jumps once the copy is done
        clock = {"t": 1000.0}
        real_sha = bk.hashlib.sha256

        class Late:
            def __init__(self, data=b""):
                self._h = real_sha(data)

            def update(self, b):
                task.cancel()
                self._h.update(b)

            def hexdigest(self):
                return self._h.hexdigest()

        monkeypatch.setattr(bk.hashlib, "sha256", Late)
        monkeypatch.setattr(bk, "take_backup", lambda *a, **kw: real(*a, _clock=lambda: clock["t"], **kw))
    task.start()
    task.join(timeout=60)
    assert not task.is_alive()
    assert task.result is None
    if path == "disk_error":
        assert isinstance(task.error, sqlite3.OperationalError)
    else:
        assert task.deferred is not None and task.deferred.reason in ("stopped", "budget"), task.deferred
        if path == "cancel_hash":
            assert task.deferred.phase == "hash" and task.deferred.details["remaining"] == 0
    assert _backups(settings) == [(prev["id"], prev["path"], prev["size"], prev["sha256"])]
    assert Path(prev["path"]).exists() and _staging(bdir) == []
    assert not list(bdir.glob("*-journal"))
    # the worker collects the outcome: a deferral retries with backoff, an error counts as the day
    t = _worker(settings)
    t._backup_task = task
    t.last = {"leader": True}
    t._maintenance()
    if path == "disk_error":
        assert t.last_backup["reason"] == "error" and t._last_backup_day is not None
    else:
        assert (
            t.last_backup["ok"] is False
            and t._last_backup_day is None
            and t._backup_retry_at > time.monotonic()
        )


@pytest.mark.timeout(120)
def test_worker_stop_cancels_the_running_task_and_joins_it_bounded(app, settings, monkeypatch):
    live = Path(db.get_engine().url.database)
    _grow(live, 3000)
    real = bk.take_backup
    monkeypatch.setattr(
        bk, "take_backup", lambda *a, **kw: real(*a, _step_hook=lambda *x: time.sleep(0.05), **kw)
    )
    t = _worker(settings)
    t.start()
    task = _wait(lambda: t._backup_task, 10)
    time.sleep(0.3)
    t0 = time.monotonic()
    t.stop()
    t.join(timeout=30)
    assert not t.is_alive() and not task.is_alive() and time.monotonic() - t0 < 5.0
    assert task.result is None and task.deferred is not None and task.deferred.reason == "stopped"
    bdir = settings.backup_dir or live.parent / "backups"
    assert _staging(bdir) == [] and _backups(settings) == []


@pytest.mark.timeout(120)
def test_leader_loss_cancels_the_task_and_a_later_worker_retries(app, settings, monkeypatch):
    live = Path(db.get_engine().url.database)
    _grow(live, 3000)
    real = bk.take_backup
    monkeypatch.setattr(
        bk, "take_backup", lambda *a, **kw: real(*a, _step_hook=lambda *x: time.sleep(0.05), **kw)
    )
    t = _worker(settings)
    t.start()
    try:
        task = _wait(lambda: t._backup_task, 10)
        # another worker takes the lease from under the running task
        from datetime import timedelta

        from convoy_server.db import write_txn
        from convoy_server.ids import utcnow
        from convoy_server.models import SchedulerLease

        with session_scope() as s:
            with write_txn(s):
                s.get(SchedulerLease, "scheduler").expires_at = utcnow() - timedelta(seconds=1)
            assert Worker("other").acquire(s)
        _wait(lambda: not task.is_alive(), 30)
        assert task.result is None
        assert (
            task.deferred is not None and task.deferred.reason == "stopped"
        ) or task.fence_lost is not None
        _wait(lambda: t._backup_task is None, 10)
        assert t._last_backup_day is None  # not counted: a leader tries again
    finally:
        t.stop()
        t.join(timeout=30)
    assert _backups(settings) == []


def test_worker_task_lifecycle_defers_with_backoff_then_succeeds(app, settings, monkeypatch):
    calls: list[dict] = []
    outcome = {"defer": True}

    def fake_backup(db_, out, worker=None, **kw):
        calls.append({"token": worker, **kw})
        if outcome["defer"]:
            raise bk.BackupDeferred("budget", "copy", limit="snapshot", elapsed_s=1.0, restarts=0)
        return {
            "id": "bak_x", "path": "/p", "size": 1, "sha256": "s", "created_at": "t", "duration_ms": 1.0,
            "restarts": 0, "busy_steps": 0, "source_lock_held_s": 0.1, "source_mode": "wal",
        }  # fmt: skip

    monkeypatch.setattr(bk, "take_backup", fake_backup)
    t = _worker(settings)
    with session_scope() as s:
        assert t.worker.acquire(s)
    t.last = {"leader": True}
    t._maintenance()  # starts the task
    task = t._backup_task
    assert task is not None
    task.join(10)
    assert calls[0]["budget_s"] == t.backup_attempt_s and calls[0]["snapshot_s"] == t.backup_snapshot_s
    assert calls[0]["source_lock_s"] == t.backup_source_lock_s and calls[0]["token"] is task.token
    assert task.token is not t.worker  # the mutable Worker is never handed to the task
    t.last = {"leader": True}
    t._maintenance()  # collects the deferral
    assert t.last_backup["ok"] is False and t.last_backup["limit"] == "snapshot"
    assert t._backup_task is None and t._last_backup_day is None and t._backup_backoff_s == 120.0
    t.last = {"leader": True}
    t._maintenance()  # inside the backoff window: no new task
    assert t._backup_task is None and len(calls) == 1
    t._backup_retry_at = 0.0
    outcome["defer"] = False
    t.last = {"leader": True}
    t._maintenance()
    t._backup_task.join(10)
    t.last = {"leader": True}
    t._maintenance()
    assert len(calls) == 2 and t.last_backup["ok"] and t.last_backup["id"] == "bak_x"
    assert t._last_backup_day is not None and t._backup_backoff_s == 60.0
    t.last = {"leader": True}
    t._maintenance()
    assert len(calls) == 2  # done for the day


# ----------------------------------------------------------------------------- WAL snapshot


@pytest.fixture()
def wal_app(settings, monkeypatch):
    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: True)
    s = replace(settings, sqlite_wal=True)
    config.set_settings(s)
    db.reset_engine()
    app = create_app(s, start_scheduler=False)
    yield app, s
    db.reset_engine()
    config.set_settings(settings)


@pytest.mark.timeout(120)
def test_wal_snapshot_isolates_commits_made_during_the_copy_and_checkpoints_after_release(
    wal_app, monkeypatch
):
    app, s = wal_app
    live = Path(db.get_engine().url.database)
    assert _mode(live) == "wal"
    _grow(live, 2000)
    w = Worker("w")
    with session_scope() as sess:
        assert w.acquire(sess)
    paused = threading.Event()
    resume = threading.Event()
    seen = {"first": True}

    def pause_once(status, remaining, total):
        if seen["first"]:
            seen["first"] = False
            paused.set()
            resume.wait(30)

    latencies: list[float] = []
    before = _count(live)

    def writer():
        c = sqlite3.connect(str(live), timeout=5)
        try:
            paused.wait(10)
            for _ in range(20):  # committed WHILE the snapshot is pinned: each acknowledged promptly
                c.execute("INSERT INTO _bulk VALUES (zeroblob(100))")
                t0 = time.monotonic()
                c.commit()
                latencies.append(time.monotonic() - t0)
        finally:
            c.close()
            resume.set()

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    bdir = s.backup_dir or live.parent / "backups"
    with session_scope() as sess:
        r = bk.take_backup(
            sess,
            str(bdir / "snap.db"),
            worker=w,
            budget_s=60.0,
            snapshot_s=30.0,
            source_lock_s=3.0,
            _step_hook=pause_once,
        )
    th.join(10)
    assert len(latencies) == 20 and max(latencies) < 1.0, latencies  # no writer pause under WAL
    assert r["source_mode"] == "wal" and r["restarts"] == 0
    assert r["source_lock_held_s"] is not None and r["source_lock_held_s"] < 30.0
    assert _count(live) == before + 20  # the live database has the commits ...
    assert _count(bdir / "snap.db") == before  # ... the backup is the pinned snapshot, no more, no less
    assert _mode(bdir / "snap.db") == "delete" and not (bdir / "snap.db-wal").exists()  # self-contained
    chk = sqlite3.connect(f"file:{bdir / 'snap.db'}?mode=ro", uri=True)
    try:
        assert chk.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        chk.close()
    # post-copy PASSIVE checkpoint evidence, and the WAL is reset once the snapshot no longer holds it
    assert r["checkpoint"] is not None and r["checkpoint"]["busy"] == 0, r["checkpoint"]
    assert isinstance(r["checkpoint"]["checkpointed_frames"], int)
    assert r["snapshot_utc"] and r["page_size"] == 4096 and r["page_count"] > 0
    # repeated backups keep succeeding and the WAL stays bounded (no reader holds it back)
    wal = Path(str(live) + "-wal")
    size_after_first = wal.stat().st_size if wal.exists() else 0
    for i in range(3):
        _grow(live, 50, table="_more")
        with session_scope() as sess:
            r2 = bk.take_backup(sess, str(bdir / f"snap{i}.db"), worker=w, budget_s=60.0, snapshot_s=30.0)
        assert r2["source_mode"] == "wal" and r2["checkpoint"]["busy"] == 0
        assert _count(bdir / f"snap{i}.db", "_more") == 50 * (i + 1)
    size_after = wal.stat().st_size if wal.exists() else 0
    assert size_after <= size_after_first + (1 << 20), (size_after_first, size_after)
    # (a WAL file keeps its high-water size on disk; boundedness shows as every frame checkpointed and
    # no growth across the repeated backups, not as a shrinking file)
    assert all(x["checkpoint"]["log_frames"] == x["checkpoint"]["checkpointed_frames"] for x in (r, r2)), (
        r["checkpoint"],
        r2["checkpoint"],
    )


@pytest.mark.timeout(120)
def test_wal_snapshot_budget_is_the_snapshot_limit_not_the_delete_pause(wal_app, monkeypatch):
    app, s = wal_app
    live = Path(db.get_engine().url.database)
    _grow(live, 3000)
    w = Worker("w")
    with session_scope() as sess:
        assert w.acquire(sess)
    bdir = s.backup_dir or live.parent / "backups"
    with session_scope() as sess:
        with pytest.raises(bk.BackupDeferred) as ei:
            bk.take_backup(
                sess, str(bdir / "x.db"), worker=w, budget_s=60.0, snapshot_s=0.01, source_lock_s=3.0,
                _step_hook=lambda *a: time.sleep(0.02),
            )  # fmt: skip
    e = ei.value
    assert e.details["limit"] == "snapshot" and e.details["source_mode"] == "wal"
    assert e.details["snapshot_s"] == 0.01 and e.details["restarts"] == 0
    assert _staging(bdir) == [] and not (bdir / "x.db").exists()
    # removing source_lock_s never disables the pin under WAL: the snapshot is still pinned
    with session_scope() as sess:
        r = bk.take_backup(sess, str(bdir / "y.db"), worker=w, budget_s=60.0, snapshot_s=30.0)
    assert r["source_lock_held_s"] is not None and r["source_mode"] == "wal" and r["restarts"] == 0


@pytest.mark.timeout(60)
def test_run_tick_never_blocks_on_the_task(app, settings, monkeypatch):
    """run_tick is the lease heartbeat: it must not wait for the backup task. Proof by synchronisation
    rather than by a stopwatch (CI f1c1306, Python 3.11: an unrelated 1.45 s tick under load false-failed
    a `< 1.0 s` bound): the task is held in its hash phase (after the source snapshot was released, so
    the DELETE-mode source lock, which pauses every COMMIT including the lease renewal for at most its
    separately bounded 3 s, is not what is measured here) on a barrier the test controls; every tick
    returns while the task is provably still held (the barrier has not been released, the task thread is
    alive and inside the phase), and only afterwards is the task let go. A generous hard fail-safe (a
    quarter of the lease) still catches a tick that really blocked on the task."""
    live = Path(db.get_engine().url.database)
    _grow(live, 3000)
    real = bk.take_backup
    inside = threading.Event()  # the task has entered its hash phase ...
    release = threading.Event()  # ... and stays there until the test says so

    def hold_phase(phase):
        if phase == "hash" and not inside.is_set():
            inside.set()
            release.wait(60)

    monkeypatch.setattr(bk, "take_backup", lambda *a, **kw: real(*a, _phase_hook=hold_phase, **kw))
    t = _worker(settings)
    with session_scope() as s_:
        assert t.worker.acquire(s_)
    t.last = {"leader": True}
    t._maintenance()
    task = t._backup_task
    assert task is not None and task.is_alive()
    try:
        assert inside.wait(30)  # copy done, snapshot released, the task is held mid-hash
        for _ in range(5):
            t0 = time.monotonic()
            out = run_tick(t.worker)
            took = time.monotonic() - t0
            assert out["leader"] is True, out
            # the tick returned while the task was still held: nothing in it waited for the task
            assert not release.is_set() and task.is_alive() and inside.is_set()
            assert took < LEASE_TTL_S / 4, took  # hard fail-safe only; the proof is the line above
        assert worker_health(settings)["ok"] is True
        assert LEASE_TTL_S == 30
    finally:
        release.set()
        task.cancel()
        task.join(10)
