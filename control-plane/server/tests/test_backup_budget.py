"""Bounded nightly backup (worker): a SQLite online backup restarts from page zero whenever another
connection writes to the source, and a rollback-journal writer's commit lock answers BUSY, so on a busy
fleet the copy can make no net progress. The worker runs the backup on its tick thread, where every
second of copying is a second the scheduler lease is not refreshed. These tests pin the bound: a
deferred attempt publishes and registers nothing, removes only its own staging file, the lease keeps
renewing afterwards, stop cancels the copy, and the worker retries later with bounded backoff."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from convoy_server.db import session_scope
from convoy_server.services import backup as bk
from convoy_server.services.scheduler import Worker
from convoy_server.services.worker import (
    BACKUP_ATTEMPT_S,
    BACKUP_SOURCE_LOCK_S,
    run_tick,
    worker_health,
)


class _ApiWriterLoop:
    """Real API write requests (user creation) through the app while a backup runs: each one is a
    BEGIN IMMEDIATE + COMMIT under the API's 5 s SQLite busy timeout. A pinned snapshot pauses the COMMIT;
    write_txn retries BEGIN but a COMMIT that waits past the busy timeout fails outright, which is why
    the source lock budget must stay below it."""

    def __init__(self, admin):
        self.admin = admin
        self.stop = threading.Event()
        self.results: list[tuple[int, float]] = []  # (status, seconds)
        self.windows: list[tuple[float, float]] = []  # (request start, request end), monotonic
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        from conftest import WEB

        try:
            i = 0
            while not self.stop.is_set():
                i += 1
                t0 = time.monotonic()
                r = self.admin.post(
                    "/api/v1/users",
                    json={"email": f"writer-{i}@example.com", "role": "viewer", "password": "password-123"},
                    headers=WEB,
                )
                now = time.monotonic()
                self.results.append((r.status_code, now - t0))
                self.windows.append((t0, now))
                time.sleep(0.01)
        except BaseException as e:
            self.error = e

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=15)
        assert not self.thread.is_alive() and self.error is None, self.error


# sqlite's default busy handler polls at most every 100 ms once past its short initial ramp, so a
# COMMIT that was waiting on the snapshot completes within one poll of the release; the rest of this
# tolerance is thread scheduling on a loaded CI runner. It is a lock-release bound, not disk slack.
LOCK_RELEASE_TOLERANCE_S = 0.5


class _WriterLoop:
    """A foreign connection committing as fast as it can, like the API under device reports. Records
    every commit's start and end (monotonic) so a test can attribute each wait to the snapshot window
    the backup reports (`snapshot_pinned_mono`/`snapshot_released_mono`) rather than to the disk: the
    connection runs with synchronous=OFF, so a COMMIT's duration is lock waiting plus a page write,
    never an fsync competing with the backup's own staging fsync (CI 354ca78 measured a 1.66 s writer
    commit against a 0.145 s hold: disk contention outside the window, which a slack term would only
    have hidden)."""

    def __init__(self, live, gap_s=0.002):
        self.live = live
        self.gap_s = (
            gap_s  # reporters commit at intervals; a gapless loop starves every other BEGIN IMMEDIATE
        )
        self.stop = threading.Event()
        self.commits: list[float] = []  # monotonic time of each commit
        self.stalls: list[float] = []  # seconds each COMMIT waited
        self.windows: list[tuple[float, float]] = []  # (commit start, commit end) per commit
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        c = sqlite3.connect(str(self.live), timeout=5)
        try:
            c.execute("PRAGMA synchronous=OFF")  # measure lock waiting, not this connection's fsyncs
            c.execute("CREATE TABLE IF NOT EXISTS _writer(x)")
            c.commit()
            while not self.stop.is_set():
                c.execute("INSERT INTO _writer VALUES (1)")
                t0 = time.monotonic()
                c.commit()
                now = time.monotonic()
                self.stalls.append(now - t0)
                self.commits.append(now)
                self.windows.append((t0, now))
                time.sleep(self.gap_s)
        except BaseException as e:  # surfaced by the test, never swallowed
            self.error = e
        finally:
            c.close()

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.commits and time.monotonic() < deadline:
            time.sleep(0.01)
        assert self.commits, self.error
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=10)
        assert not self.thread.is_alive()
        assert self.error is None, self.error

    def commits_after(self, t):
        return sum(1 for c in self.commits if c > t)


def _staging_files(tmp_path):
    return sorted(p.name for p in tmp_path.rglob(".*.staging-*"))


def _grow_live_db(live, rows=1500):
    c = sqlite3.connect(str(live), timeout=5)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS _bulk(x BLOB)")
        c.executemany("INSERT INTO _bulk VALUES (?)", [(b"x" * 4000,)] * rows)
        c.commit()
    finally:
        c.close()


def _journal_mode(live):
    c = sqlite3.connect(str(live))
    try:
        return c.execute("PRAGMA journal_mode").fetchone()[0].lower()
    finally:
        c.close()


def _assert_nothing_published_and_lease_live(admin, settings, tmp_path, out, w):
    assert not out.exists()
    assert _staging_files(tmp_path) == []
    assert admin.get("/api/v1/admin/backups").json() == []
    assert worker_health(settings)["ok"] is True  # the lease survived the bounded attempt
    assert run_tick(w)["leader"] is True  # and the same worker keeps renewing it


def test_unpinned_writer_induced_restarts_expire_the_budget_and_publish_nothing(
    app, admin, settings, tmp_path
):
    # budget without source_lock_s: the copy is NOT pinned, so a foreign commit restarts it (this is the
    # failure mode the pinned worker path below exists to remove; the counter is kept as evidence)
    live = bk.live_db_path()
    _grow_live_db(live)  # several 256-page steps
    w = Worker("w-budget")
    with session_scope() as db:
        assert w.acquire(db)
    writer = sqlite3.connect(str(live), timeout=5)
    writer.execute("CREATE TABLE IF NOT EXISTS _restart(x)")
    writer.commit()

    def commit_after_every_step(status, remaining, total):
        # a foreign connection commits between steps: sqlite must restart the copy on the next step
        writer.execute("INSERT INTO _restart VALUES (1)")
        writer.commit()

    out = tmp_path / "never.db"
    t0 = time.monotonic()
    try:
        with session_scope() as db:
            with pytest.raises(bk.BackupDeferred) as ei:
                bk.take_backup(db, str(out), worker=w, budget_s=0.6, _step_hook=commit_after_every_step)
    finally:
        writer.close()
    elapsed = time.monotonic() - t0
    e = ei.value
    assert e.reason == "budget" and e.phase == "copy"
    assert e.details["steps"] > 1 and e.details["restarts"] >= 1, e.details
    assert e.details["remaining"] is not None and e.details["total"] > bk.BACKUP_STEP_PAGES
    assert elapsed < 0.6 + 3.0, elapsed  # bounded by the budget plus one in-flight step, not by the copy
    assert isinstance(e, bk.BackupError)  # still an error for callers that do not retry
    _assert_nothing_published_and_lease_live(admin, settings, tmp_path, out, w)


def test_unpinned_busy_source_lock_expires_the_budget_without_a_five_second_wait_per_step(
    app, admin, settings, tmp_path
):
    live = bk.live_db_path()
    if _journal_mode(live) == "wal":
        pytest.skip("BUSY on the source only arises under a rollback journal (WAL readers never block)")
    _grow_live_db(live, rows=300)
    w = Worker("w-busy")
    with session_scope() as db:
        assert w.acquire(db)
    blocker = sqlite3.connect(str(live), timeout=5)
    seen: list[int] = []

    def lock_source_after_first_step(status, remaining, total):
        seen.append(status)
        if len(seen) == 1:
            blocker.execute("BEGIN EXCLUSIVE")  # a writer's commit lock: subsequent steps answer BUSY

    out = tmp_path / "busy.db"
    t0 = time.monotonic()
    try:
        with session_scope() as db:
            with pytest.raises(bk.BackupDeferred) as ei:
                bk.take_backup(db, str(out), worker=w, budget_s=0.7, _step_hook=lock_source_after_first_step)
    finally:
        blocker.rollback()
        blocker.close()
    elapsed = time.monotonic() - t0
    e = ei.value
    assert e.reason == "budget" and e.phase == "copy"
    assert e.details["busy_steps"] >= 1 and bk._SQLITE_BUSY in seen, (e.details, seen)
    assert elapsed < 0.7 + 3.0, elapsed  # each BUSY step waits BACKUP_SOURCE_BUSY_S, not sqlite's 5 s
    _assert_nothing_published_and_lease_live(admin, settings, tmp_path, out, w)


@pytest.mark.parametrize("phase", ["hash", "publish"])
def test_budget_is_also_checked_during_hash_and_before_publication(
    app, admin, settings, tmp_path, monkeypatch, phase
):
    w = Worker("w-late")
    with session_scope() as db:
        assert w.acquire(db)
    clock = {"t": 1000.0}
    out = tmp_path / f"late-{phase}.db"
    with monkeypatch.context() as m:  # scoped: the API calls below hash session tokens with sha256
        if phase == "hash":
            real = bk.hashlib.sha256

            class LateHash:
                def __init__(self, data=b""):
                    self._h = real(data)

                def update(self, b):
                    clock["t"] += 100.0  # the copy finished in time; hashing did not
                    self._h.update(b)

                def hexdigest(self):
                    return self._h.hexdigest()

            m.setattr(bk.hashlib, "sha256", LateHash)
        else:
            real_new_id = bk.new_id

            def late_new_id(prefix, *a, **k):
                if prefix == "bak":  # hashed and about to publish
                    clock["t"] += 100.0
                return real_new_id(prefix, *a, **k)

            m.setattr(bk, "new_id", late_new_id)
        with session_scope() as db:
            with pytest.raises(bk.BackupDeferred) as ei:
                bk.take_backup(
                    db, str(out), worker=w, budget_s=10.0, source_lock_s=3.0, _clock=lambda: clock["t"]
                )
    assert ei.value.reason == "budget" and ei.value.phase == phase
    assert ei.value.details["limit"] == "total"  # the source lock was already released: not its budget
    assert ei.value.details["remaining"] == 0  # the copy itself had completed
    # the reported hold is the frozen copy-phase duration (the fake clock did not move during the copy),
    # not the +100 s the clock advanced after release while hashing/publishing
    assert ei.value.details["source_lock_held_s"] == 0.0
    _assert_nothing_published_and_lease_live(admin, settings, tmp_path, out, w)


def test_stop_event_cancels_the_copy_at_the_next_step(app, admin, settings, tmp_path):
    _grow_live_db(bk.live_db_path())
    w = Worker("w-stop")
    with session_scope() as db:
        assert w.acquire(db)
    halt = threading.Event()
    steps: list[int] = []

    def stop_after_first_step(status, remaining, total):
        steps.append(remaining)
        halt.set()

    out = tmp_path / "stopped.db"
    with session_scope() as db:
        with pytest.raises(bk.BackupDeferred) as ei:
            bk.take_backup(
                db,
                str(out),
                worker=w,
                budget_s=30.0,
                source_lock_s=3.0,
                stop=halt,
                _step_hook=stop_after_first_step,
            )
    assert ei.value.reason == "stopped" and ei.value.phase == "copy"
    assert ei.value.details["source_lock_held_s"] is not None  # cancelled while pinned; lock released
    assert len(steps) == 1 and steps[0] > 0  # cancelled at the first checkpoint, copy unfinished
    _assert_nothing_published_and_lease_live(admin, settings, tmp_path, out, w)


def test_pinned_snapshot_completes_under_an_active_writer_without_restarts(app, admin, settings, tmp_path):
    live = bk.live_db_path()
    _grow_live_db(live, rows=3000)  # ~12 MiB: dozens of 256-page steps, each an opportunity to restart
    w = Worker("w-pin")
    with session_scope() as db:
        assert w.acquire(db)
    out = tmp_path / "pinned.db"
    with _WriterLoop(live) as wl, _ApiWriterLoop(admin) as api:
        before = len(wl.commits)
        t0 = time.monotonic()
        with session_scope() as db:
            r = bk.take_backup(db, str(out), worker=w, budget_s=BACKUP_ATTEMPT_S, source_lock_s=3.0)
        t1 = time.monotonic()
        time.sleep(0.3)
        resumed = wl.commits_after(t1)
        final = len(wl.commits)
    assert r["restarts"] == 0, r  # a writer committed throughout, the pinned copy never went back to page 0
    assert 0 <= r["source_lock_held_s"] <= t1 - t0 <= BACKUP_ATTEMPT_S
    assert final > before and resumed > 0  # the writer was paused at most for the copy, then went on
    # interval attribution against the backup's own source-lock window (same monotonic clock)
    pinned, released = r["snapshot_pinned_mono"], r["snapshot_released_mono"]
    assert t0 <= pinned < released <= t1 and round(released - pinned, 3) == r["source_lock_held_s"]
    assert r["source_mode"] == "delete"
    # 1. the DELETE-mode snapshot really held the source lock: no foreign COMMIT completed inside it
    assert not [w for w in wl.windows if pinned < w[1] < released], (pinned, released)
    # 2. the writer was mid-commit during the hold, and each such commit completed within one
    #    busy-handler poll of the release: the pause was the hold, nothing longer
    paused = [w for w in wl.windows if w[0] < released and w[1] > pinned]
    assert paused, (pinned, released, wl.windows[:3])
    assert max(end - released for _, end in paused) <= LOCK_RELEASE_TOLERANCE_S, (paused, released)
    # 3. commits outside the window waited on nothing long-lived (this connection never fsyncs)
    outside = [end - start for start, end in wl.windows if not (start < released and end > pinned)]
    assert outside and max(outside) <= LOCK_RELEASE_TOLERANCE_S, sorted(outside)[-3:]
    # real API writes kept succeeding: never past the 5 s busy timeout; a request that overlapped the
    # hold took at most the hold plus what an unpaused request costs (password hashing, FULL fsyncs)
    assert api.results and {st for st, _ in api.results} == {201}, api.results[:5]
    assert max(t for _, t in api.results) < 5.0
    unpaused = [end - start for start, end in api.windows if not (start < released and end > pinned)]
    overlapped = [end - start for start, end in api.windows if start < released and end > pinned]
    assert unpaused, api.windows[:3]
    for dur in overlapped:
        assert dur <= r["source_lock_held_s"] + max(unpaused) + LOCK_RELEASE_TOLERANCE_S, (
            dur, r["source_lock_held_s"], max(unpaused),
        )  # fmt: skip
    assert not _staging_files(tmp_path)
    chk = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert chk.execute("SELECT count(*) FROM _bulk").fetchone()[0] == 3000
        n = chk.execute("SELECT count(*) FROM _writer").fetchone()[0]
    finally:
        chk.close()
    assert 0 < n <= final  # a consistent committed state from inside the window, never a torn copy
    rows = admin.get("/api/v1/admin/backups").json()
    assert [x["id"] for x in rows] == [r["id"]] and rows[0]["sha256"] == r["sha256"]
    assert worker_health(settings)["ok"] is True


def test_pinned_lock_budget_defers_and_frees_the_writer_promptly(app, admin, settings, tmp_path):
    live = bk.live_db_path()
    _grow_live_db(live, rows=3000)
    w = Worker("w-pin-defer")
    with session_scope() as db:
        assert w.acquire(db)
    out = tmp_path / "pinned-deferred.db"
    # The budget must expire while the copy is UNFINISHED, whatever the library's copy speed (root's
    # pinned 3.51.3 on ARM64 finished all 3153 pages inside 10 ms): the first copy step, which leaves
    # remaining > 0, is held past the source-lock budget inside its callback, so the check that follows
    # that very step is the one that defers. Same production budget code, same bound, no benchmark.
    steps: list[tuple[int, int]] = []

    def outlast_budget_on_first_step(status, remaining, total):
        steps.append((remaining, total))
        if len(steps) == 1:
            time.sleep(0.05)  # > source_lock_s while the snapshot is still pinned

    with _WriterLoop(live) as wl:
        with session_scope() as db:
            with pytest.raises(bk.BackupDeferred) as ei:
                bk.take_backup(
                    db,
                    str(out),
                    worker=w,
                    budget_s=BACKUP_ATTEMPT_S,
                    source_lock_s=0.01,
                    _step_hook=outlast_budget_on_first_step,
                )
        t1 = time.monotonic()
        time.sleep(0.5)
        resumed = wl.commits_after(t1)
    e = ei.value
    assert e.reason == "budget" and e.phase == "copy" and e.details["limit"] == "source_lock"
    assert e.details["source_lock_s"] == 0.01 and e.details["restarts"] == 0
    assert e.details["remaining"] > 0  # unfinished copy
    assert len(steps) == 1 and steps[0][0] > 0 and e.details["remaining"] == steps[0][0], (steps, e.details)
    assert resumed > 5, (resumed, wl.stalls[-5:])  # the writer was released as soon as the copy stopped
    assert max(wl.stalls) < 2.0, max(wl.stalls)
    _assert_nothing_published_and_lease_live(admin, settings, tmp_path, out, w)


def test_source_lock_is_released_before_hash_and_publication(app, admin, settings, tmp_path, monkeypatch):
    # a write from a foreign connection with a short busy timeout succeeds while take_backup hashes:
    # under a rollback journal that is only possible once the pinned read transaction is gone (under WAL
    # the write would succeed anyway; the fenced publication below is the second half of the proof)
    live = bk.live_db_path()
    w = Worker("w-release")
    with session_scope() as db:
        assert w.acquire(db)
    real = bk.hashlib.sha256
    wrote: list[float] = []
    failed: list[BaseException] = []

    def independent_writer():
        c = sqlite3.connect(str(live), timeout=0.5)
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("CREATE TABLE IF NOT EXISTS _during_hash(x)")
            t0 = time.monotonic()
            c.commit()  # would wait for the SHARED lock (then fail at 0.5 s) if the snapshot were still pinned
            wrote.append(time.monotonic() - t0)
        except BaseException as e:
            failed.append(e)
        finally:
            c.close()

    class PausesHashForAWriter:
        def __init__(self, data=b""):
            self._h = real(data)

        def update(self, b):
            if not wrote and not failed:
                t = threading.Thread(target=independent_writer)
                t.start()
                t.join(timeout=5)  # hashing stays paused until the independent commit has happened
                assert not t.is_alive()
            self._h.update(b)

        def hexdigest(self):
            return self._h.hexdigest()

    out = tmp_path / "released.db"
    with monkeypatch.context() as m:
        m.setattr(bk.hashlib, "sha256", PausesHashForAWriter)
        with session_scope() as db:
            r = bk.take_backup(db, str(out), worker=w, budget_s=BACKUP_ATTEMPT_S, source_lock_s=3.0)
    assert not failed, failed
    assert wrote and wrote[0] < 0.5, wrote  # committed without waiting on the backup's source lock
    assert out.exists() and r["source_lock_held_s"] is not None
    assert [x["id"] for x in admin.get("/api/v1/admin/backups").json()] == [r["id"]]
    chk = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:  # the snapshot predates the write made during hashing
        names = {x[0] for x in chk.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        chk.close()
    assert "_during_hash" not in names


@pytest.mark.parametrize("hold_s,expect", [(0.5, "success"), (2.0, "deferred")])
def test_pin_waits_briefly_for_a_committing_writer(
    app, admin, settings, tmp_path, monkeypatch, hold_s, expect
):
    live = bk.live_db_path()
    if _journal_mode(live) == "wal":
        pytest.skip("a committing writer only blocks the pin under a rollback journal")
    w = Worker("w-pin-busy")
    with session_scope() as db:
        assert w.acquire(db)
    released = threading.Event()

    def hold_exclusive():
        c = sqlite3.connect(str(live), timeout=5)
        try:
            c.execute("BEGIN EXCLUSIVE")  # a writer mid-commit: SHARED cannot be taken until it ends
            time.sleep(hold_s)
            c.rollback()
        finally:
            c.close()
            released.set()

    real_is_convoy_db = bk.is_convoy_db
    holder = threading.Thread(target=hold_exclusive, daemon=True)

    def start_holder_after_fence(path):
        ok = real_is_convoy_db(path)  # runs after the early fence refresh and before the pin
        holder.start()
        time.sleep(0.1)
        return ok

    monkeypatch.setattr(bk, "is_convoy_db", start_holder_after_fence)
    out = tmp_path / f"pin-{expect}.db"
    t0 = time.monotonic()
    try:
        with session_scope() as db:
            if expect == "success":
                r = bk.take_backup(db, str(out), worker=w, budget_s=1.5, source_lock_s=3.0)
                assert r["busy_steps"] >= 1 and r["restarts"] == 0, r
                assert out.exists()
            else:
                with pytest.raises(bk.BackupDeferred) as ei:
                    bk.take_backup(db, str(out), worker=w, budget_s=0.6, source_lock_s=3.0)
                e = ei.value
                assert e.reason == "budget" and e.phase == "pin" and e.details["limit"] == "total"
                assert e.details["busy_steps"] >= 1 and e.details["source_lock_held_s"] is None
                assert time.monotonic() - t0 < 0.6 + 1.0  # bounded BUSY retries, never sqlite's 5 s wait
    finally:
        holder.join(timeout=10)
        assert released.is_set()
    if expect == "deferred":
        _assert_nothing_published_and_lease_live(admin, settings, tmp_path, out, w)


def test_unbounded_and_bounded_success_paths_are_unchanged(app, admin, settings, tmp_path):
    _grow_live_db(bk.live_db_path(), rows=200)
    w = Worker("w-ok")
    with session_scope() as db:
        assert w.acquire(db)
        a = bk.take_backup(db, str(tmp_path / "a.db"))  # operator path: no worker, no budget
        b = bk.take_backup(
            db,
            str(tmp_path / "b.db"),
            worker=w,
            budget_s=BACKUP_ATTEMPT_S,
            source_lock_s=BACKUP_SOURCE_LOCK_S,
            stop=threading.Event(),
        )
    assert a["restarts"] == 0 and a["source_lock_held_s"] is None  # operator path: not pinned
    assert b["restarts"] == 0 and 0 <= b["source_lock_held_s"] <= BACKUP_SOURCE_LOCK_S
    for r, name in ((a, "a.db"), (b, "b.db")):
        p = tmp_path / name
        assert p.exists() and r["size"] == p.stat().st_size > 0 and r["path"] == str(p)
        assert bk.is_convoy_db(p)
        chk = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            assert chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert chk.execute("SELECT count(*) FROM _bulk").fetchone()[0] == 200
        finally:
            chk.close()
    assert _staging_files(tmp_path) == []
    assert {x["id"] for x in admin.get("/api/v1/admin/backups").json()} == {a["id"], b["id"]}
    assert worker_health(settings)["ok"] is True


@pytest.mark.parametrize("arrival", ["stop", "deadline"])
def test_stop_or_deadline_arriving_while_publication_waits_for_the_write_lock_still_wins(
    app, admin, settings, tmp_path, arrival
):
    """Review of 354ca78: the publish check ran before BEGIN IMMEDIATE, which may wait (bounded retries)
    for another writer; a stop or the total deadline arriving during that wait used to let the backup
    publish and register anyway. The decision is now taken again INSIDE the acquired transaction. Barrier:
    a foreign writer holds the write lock until the backup has reached the publish phase and the
    cancellation has been raised; the recheck is then observed after the lock was released."""
    live = bk.live_db_path()
    w = Worker(f"w-late-{arrival}")
    with session_scope() as db:
        assert w.acquire(db)
    halt = threading.Event()
    clock = {"t": 1000.0, "lock": threading.Lock()}

    def fake_clock():
        with clock["lock"]:
            return clock["t"]

    phases: list[tuple[str, float]] = []
    at_publish = threading.Event()
    # the foreign writer takes the write lock once the copy is done (hash phase): earlier it would
    # also block the attempt's own fenced pre-check, which is not the wait under test
    holder = sqlite3.connect(str(live), timeout=5, check_same_thread=False)
    held = {"v": False}

    def phase_hook(phase):
        phases.append((phase, time.monotonic()))
        if phase == "hash" and not held["v"]:
            held["v"] = True
            holder.execute("BEGIN IMMEDIATE")  # the write lock every publication needs
        if phase == "publish":
            at_publish.set()

    out = tmp_path / f"late-{arrival}.db"
    result: dict = {}

    def run():
        try:
            with session_scope() as db:
                bk.take_backup(
                    db, str(out), worker=w, budget_s=10.0, source_lock_s=3.0, stop=halt,
                    _clock=fake_clock, _phase_hook=phase_hook,
                )  # fmt: skip
            result["published"] = True
        except BaseException as e:  # surfaced below
            result["error"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    assert at_publish.wait(30), (
        phases,
        result,
    )  # hashed, about to publish: BEGIN IMMEDIATE finds the lock held
    assert held["v"]
    time.sleep(0.2)  # let it actually reach the lock wait
    if arrival == "stop":
        halt.set()
    else:
        with clock["lock"]:
            clock["t"] += 100.0  # the total budget expired while waiting
    released_at = time.monotonic()
    holder.commit()  # the barrier drops only AFTER the cancellation exists
    holder.close()
    th.join(30)
    assert not th.is_alive()
    e = result.get("error")
    assert isinstance(e, bk.BackupDeferred), result
    assert e.phase == "publish-acquired" and e.reason == ("stopped" if arrival == "stop" else "budget"), (
        e.details
    )
    if arrival == "deadline":
        assert e.details["limit"] == "total"
    acquired = [t for ph, t in phases if ph == "publish-acquired"]
    assert acquired and acquired[0] >= released_at, (phases, released_at)  # rechecked inside the acquired txn
    assert [ph for ph, _ in phases].count("publish") == 1
    _assert_nothing_published_and_lease_live(admin, settings, tmp_path, out, w)
    with session_scope() as db:
        from convoy_server.models import Backup

        assert db.query(Backup).count() == 0
