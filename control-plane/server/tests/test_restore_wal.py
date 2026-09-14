"""Backup correction, commit C: WAL-aware offline restore, rollback and migration copies.

A registered backup is a self-contained DELETE-mode file. Restoring it into a WAL installation must:
fold the live database's committed WAL frames into its bare file BEFORE that file is copied aside (the
previous installation keeps every committed page even after a crash left a WAL behind), convert the
staged, quarantined snapshot to the configured mode, publish it atomically, and leave no sidecar of the
OLD database beside the new file (a leftover WAL is self-consistent and would be applied to the
replacement). WAL mechanics run with the WAL-reset guard bypassed by explicit monkeypatch.

Fail-closed correction (review of 354ca78): the Convoy lock excludes Convoy processes only. A foreign
read-only SQLite connection holding an older snapshot makes the TRUNCATE checkpoint return busy with
committed frames still outstanding; restore and the migration copy must then refuse and leave the live
file and both sidecars untouched (a checkpoint may legally copy some pages first: what is preserved is
every committed logical row, the file identity and the sidecars, never "no byte changed")."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from convoy_server import config, db
from convoy_server.app import create_app
from convoy_server.db import session_scope
from convoy_server.services import backup as bk
from helpers import enrolled_agent, heartbeat


def _wal_stack(settings, monkeypatch):
    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: True)
    s = replace(settings, sqlite_wal=True)
    config.set_settings(s)
    db.reset_engine()
    return create_app(s, start_scheduler=False), s


def _rows(path: Path, sql: str) -> int:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def _crash_leave_wal(live: Path, rows: int) -> Path:
    """Commit rows in WAL mode and leave them ONLY in the -wal (as a crashed process would): the WAL
    is copied aside before the last connection checkpoints on close, then put back."""
    c = sqlite3.connect(str(live), isolation_level=None, timeout=5)
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE IF NOT EXISTS _crash(n INTEGER)")
    for i in range(rows):
        c.execute("INSERT INTO _crash VALUES (?)", (i,))
    wal = Path(str(live) + "-wal")
    assert wal.stat().st_size > 0
    keep = live.with_name("kept.wal")
    shutil.copy2(wal, keep)
    c.close()  # checkpoints and removes the WAL: the main file now has the rows ...
    # ... so reproduce the crash state exactly: main file WITHOUT them, WAL with them
    main_only = sqlite3.connect(str(live), isolation_level=None)
    main_only.execute("PRAGMA journal_mode=DELETE")  # temporarily, to drop the rows from the main file
    main_only.execute("DROP TABLE _crash")
    main_only.execute("PRAGMA journal_mode=WAL")
    main_only.close()
    shutil.copy2(keep, wal)  # a WAL whose frames the main file has not absorbed
    keep.unlink()
    return wal


@pytest.mark.timeout(120)
def test_restore_into_a_wal_installation_after_an_ordinary_shutdown(settings, monkeypatch, tmp_path):
    app, s = _wal_stack(settings, monkeypatch)
    from conftest import login
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        admin = login(c)
        a = enrolled_agent(app, admin, name="wal-dev")
        heartbeat(a)
        with session_scope() as sess:
            b = bk.take_backup(sess, str(tmp_path / "snap.db"))  # self-contained DELETE-mode file
        heartbeat(a)  # a report AFTER the backup: present live, absent from the snapshot
    live = Path(db.get_engine().url.database)
    db.reset_engine()  # ordinary shutdown: every connection closed, the WAL checkpointed on close
    assert db.file_journal_mode(live) == "wal" and _rows(live, "SELECT count(*) FROM reports") == 2
    assert db.file_journal_mode(Path(b["path"])) == "delete"
    res = bk.restore_backup(b["path"], confirm=True)
    assert res["ok"], res
    assert res["journal_mode"] == "wal"  # the published file is in the CONFIGURED mode
    assert not Path(str(live) + "-wal").exists() and not Path(str(live) + "-shm").exists()
    assert db.file_journal_mode(live) == "wal"
    assert _rows(live, "SELECT count(*) FROM reports") == 1  # the snapshot, not the later report
    prev = Path(res["previous_db"])
    assert prev.exists() and _rows(prev, "SELECT count(*) FROM reports") == 2  # previous installation intact
    # the WAL-configured service starts on the restored file and it is quarantined
    eng = db.init_engine(s)
    try:
        from convoy_server.models import Installation
        from sqlalchemy import select

        with session_scope() as sess:
            inst = sess.scalar(select(Installation))
            assert inst is not None and inst.quarantined_at is not None
        with eng.connect() as conn:
            from sqlalchemy import text

            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA quick_check")).scalar() == "ok"
    finally:
        db.reset_engine()
        config.set_settings(settings)


@pytest.mark.timeout(120)
def test_restore_after_a_crash_left_wal_keeps_committed_frames_in_the_previous_copy(
    settings, monkeypatch, tmp_path
):
    app, s = _wal_stack(settings, monkeypatch)
    with session_scope() as sess:
        b = bk.take_backup(sess, str(tmp_path / "snap.db"))
    live = Path(db.get_engine().url.database)
    db.reset_engine()
    wal = _crash_leave_wal(live, 7)
    assert wal.exists() and wal.stat().st_size > 0
    assert _rows(live, "SELECT count(*) FROM sqlite_master WHERE name='_crash'") == 1  # visible via the WAL
    res = bk.restore_backup(b["path"], confirm=True)
    assert res["ok"], res
    assert res["previous_wal_checkpoint"]["busy"] == 0
    prev = Path(res["previous_db"])
    assert _rows(prev, "SELECT count(*) FROM _crash") == 7  # the committed frames were folded in first
    assert db.file_journal_mode(prev) == "wal"
    assert not Path(str(live) + "-wal").exists()  # the old WAL never meets the restored file
    assert _rows(live, "SELECT count(*) FROM sqlite_master WHERE name='_crash'") == 0  # snapshot state
    c = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        c.close()
    config.set_settings(settings)


def test_restore_into_a_delete_installation_is_unchanged_and_refuses_wal_on_an_unqualified_library(
    settings, monkeypatch, tmp_path
):
    # DELETE stays DELETE (no sidecars, no mode change), covered end to end elsewhere; here the guard:
    # a WAL intent on an unqualified library is refused before anything is published
    create_app(settings, start_scheduler=False)
    with session_scope() as sess:
        b = bk.take_backup(sess, str(tmp_path / "snap.db"))
    live = Path(db.get_engine().url.database)
    db.reset_engine()
    res = bk.restore_backup(b["path"], confirm=True)
    assert res["ok"] and res["journal_mode"] == "delete" and res["previous_wal_checkpoint"] is None
    assert not Path(str(live) + "-wal").exists()
    db.reset_engine()
    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: False)
    config.set_settings(replace(settings, sqlite_wal=True))
    before = live.read_bytes()[:100]
    with pytest.raises(db.JournalModeError):
        bk.restore_backup(b["path"], confirm=True)
    assert live.read_bytes()[:100] == before  # nothing published
    assert not list(live.parent.glob("*.restore-staging*"))
    config.set_settings(settings)
    db.reset_engine()


def test_migration_copy_folds_a_crash_left_wal_first(settings, monkeypatch):
    app, s = _wal_stack(settings, monkeypatch)
    live = Path(db.get_engine().url.database)
    db.reset_engine()
    _crash_leave_wal(live, 3)
    res = bk._settle_live_wal(live)
    assert res is not None and res["busy"] == 0
    assert not Path(str(live) + "-wal").exists()
    assert _rows(live, "SELECT count(*) FROM _crash") == 3  # in the main file now
    from convoy_server.migrations import migrate_database

    m = migrate_database(s)
    assert m["ok"], m
    config.set_settings(settings)


class _ForeignReader:
    """An independent read-only connection holding an older snapshot; it never takes Convoy's lock."""

    def __init__(self, live: Path):
        self.c = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
        self.c.execute("BEGIN")
        self.count = self.c.execute("SELECT count(*) FROM reports").fetchone()[0]

    def release(self):
        self.c.rollback()
        self.c.close()


def _commit_late_rows(live: Path, rows: int) -> None:
    """A writer commits AFTER the reader's snapshot: those frames sit in the WAL past the reader's mark."""
    w = sqlite3.connect(str(live), isolation_level=None, timeout=5)
    try:
        w.execute("PRAGMA wal_autocheckpoint=0")
        w.execute("CREATE TABLE IF NOT EXISTS _late(n INTEGER)")
        for i in range(rows):
            w.execute("INSERT INTO _late VALUES (?)", (i,))
    finally:
        w.close()


def _logical_state(path: Path) -> dict:
    """What a fresh reader sees: schema, user_version and every table's row count (main file + WAL)."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        schema = c.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
        ).fetchall()
        tables = [r[1] for r in schema if r[0] == "table"]
        rows = {t: c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        return {
            "schema": schema,
            "user_version": c.execute("PRAGMA user_version").fetchone()[0],
            "rows": rows,
        }
    finally:
        c.close()


def _file_identity(path: Path) -> dict:
    st = path.stat()
    wal, shm = Path(str(path) + "-wal"), Path(str(path) + "-shm")
    return {
        "inode": (st.st_dev, st.st_ino),
        "wal_bytes": wal.stat().st_size if wal.exists() else None,
        "shm_exists": shm.exists(),
    }


@pytest.mark.timeout(120)
def test_restore_refuses_while_a_foreign_reader_holds_an_older_snapshot_and_preserves_everything(
    settings, monkeypatch, tmp_path
):
    app, s = _wal_stack(settings, monkeypatch)
    with session_scope() as sess:
        b = bk.take_backup(sess, str(tmp_path / "snap.db"))
    live = Path(db.get_engine().url.database)
    db.reset_engine()
    monkeypatch.setattr(bk, "LIVE_WAL_BUSY_S", 0.5)  # the checkpoint waits this long for the reader
    reader = _ForeignReader(live)
    _commit_late_rows(live, 5)  # committed, but only in the WAL beyond the reader's mark
    before_state, before_id = _logical_state(live), _file_identity(live)
    assert before_state["rows"]["_late"] == 5 and before_id["wal_bytes"] > 0
    res = bk.restore_backup(b["path"], confirm=True)
    assert res["ok"] is False and res.get("live_untouched") is True, res
    assert "checkpoint incomplete" in res["error"] and "busy=1" in res["error"], res["error"]
    # complete preservation: same file (not replaced), sidecars present, every committed row readable
    after_id = _file_identity(live)
    assert after_id["inode"] == before_id["inode"] and after_id["shm_exists"]
    assert after_id["wal_bytes"] is not None and after_id["wal_bytes"] >= before_id["wal_bytes"]
    assert _logical_state(live) == before_state
    assert db.file_journal_mode(live) == "wal"
    assert not list(live.parent.glob("*.pre-restore-*")) and not list(live.parent.glob("*.restore-staging*"))
    assert reader.c.execute("SELECT count(*) FROM reports").fetchone()[0] == reader.count  # snapshot intact
    # the reader lets go of its snapshot but keeps the database open (idle): the checkpoint completes,
    # yet the sidecars survive the close, so the restore still refuses rather than unlink them under it
    reader.release()
    idle = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    idle.execute("SELECT count(*) FROM reports").fetchone()
    res_idle = bk.restore_backup(b["path"], confirm=True)
    assert res_idle["ok"] is False and "still holds the database open" in res_idle["error"], res_idle
    assert _file_identity(live)["inode"] == before_id["inode"] and _logical_state(live) == before_state
    assert Path(str(live) + "-shm").exists()
    idle.close()
    # every foreign connection gone: the same restore now succeeds and the previous copy has the rows
    res2 = bk.restore_backup(b["path"], confirm=True)
    assert res2["ok"], res2
    assert res2["previous_wal_checkpoint"]["busy"] == 0 and res2["previous_wal_checkpoint"]["complete"]
    assert not Path(str(live) + "-wal").exists()  # checked before any reader could recreate an empty one
    prev = Path(res2["previous_db"])
    assert _rows(prev, "SELECT count(*) FROM _late") == 5
    assert _rows(live, "SELECT count(*) FROM sqlite_master WHERE name='_late'") == 0  # snapshot state
    config.set_settings(settings)


@pytest.mark.timeout(120)
def test_migration_copy_refuses_while_a_foreign_reader_holds_an_older_snapshot(settings, monkeypatch):
    from convoy_server.migrations import migrate_database

    app, s = _wal_stack(settings, monkeypatch)
    live = Path(db.get_engine().url.database)
    db.reset_engine()
    # an additive upgrade candidate: a table the current schema carries is missing
    c = sqlite3.connect(str(live), isolation_level=None)
    c.execute("DROP TABLE usage_unknown_intervals")
    c.close()
    monkeypatch.setattr(bk, "LIVE_WAL_BUSY_S", 0.5)
    reader = _ForeignReader(live)
    _commit_late_rows(live, 3)
    before_state, before_id = _logical_state(live), _file_identity(live)
    assert "usage_unknown_intervals" not in before_state["rows"]
    m = migrate_database(s)
    assert m["ok"] is False and m.get("live_untouched") is True, m
    assert "checkpoint incomplete" in m["error"] and m["plan"]["add_tables"] == ["usage_unknown_intervals"]
    assert _file_identity(live)["inode"] == before_id["inode"] and _logical_state(live) == before_state
    assert not list(live.parent.glob("*.pre-migrate-*"))  # no copy was taken from an incomplete file
    reader.release()
    m2 = migrate_database(s)
    assert m2["ok"] and "usage_unknown_intervals" in _logical_state(live)["rows"], m2
    assert _rows(Path(m2["pre_migration_copy"]), "SELECT count(*) FROM _late") == 3  # complete copy
    config.set_settings(settings)
