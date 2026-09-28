"""SQLite runtime contract (backup correction, commit A): one attested native library for every
accessor, the journal mode as a property of the FILE that no connection changes, an explicit offline
mode transition, and safe refusal on an unqualified library or a mismatching configuration.

WAL mechanics are exercised on the developer interpreter's SQLite with the WAL-reset guard bypassed by
an explicit monkeypatch (the guard concerns a race under concurrent load, not the mechanics tested
here); the guard itself is tested separately. Whether the deployed image's library is qualified is an
attestation of that image (`convoy-server sqlite-attest`), never of this interpreter."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from convoy_server import config, db
from sqlalchemy import text


def _rows(path: Path, force_delete: bool = False) -> list[int]:
    """Read every row the way an OLD-image accessor would (it forces DELETE on connect)."""
    c = sqlite3.connect(str(path), isolation_level=None)
    try:
        if force_delete:
            c.execute("PRAGMA journal_mode=DELETE")
        return [r[0] for r in c.execute("SELECT n FROM t ORDER BY n")]
    finally:
        c.close()


def _make_db(path: Path, mode: str, n: int = 5) -> None:
    c = sqlite3.connect(str(path), isolation_level=None)
    try:
        c.execute("CREATE TABLE t(n INTEGER PRIMARY KEY)")
        c.execute(f"PRAGMA journal_mode={mode}")
        for i in range(1, n + 1):
            c.execute("INSERT INTO t VALUES (?)", (i,))
    finally:
        c.close()


def _evidence(path: Path) -> dict:
    """Everything a refused startup must leave alone: schema text, user_version, logical rows, the main
    file's bytes and the sidecars."""
    import hashlib

    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        schema = c.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
        ).fetchall()
        rows = {
            r[1]: c.execute(f'SELECT count(*) FROM "{r[1]}"').fetchone()[0] for r in schema if r[0] == "table"
        }
        uv = c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()
    sidecars = {
        sfx: (Path(str(path) + sfx).stat().st_size if Path(str(path) + sfx).exists() else None)
        for sfx in ("-wal", "-shm", "-journal")
    }
    return {
        "schema": schema,
        "rows": rows,
        "user_version": uv,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
        "sidecars": sidecars,
    }


def test_attestation_reports_one_consistent_native_library():
    att = db.sqlite_attestation()
    assert att["consistent"] and att["sqlite_version"] == att["sql_sqlite_version"] == sqlite3.sqlite_version
    assert (
        isinstance(att["sqlite_source_id"], str) and len(att["sqlite_source_id"].split()) >= 3
    )  # date time hash
    assert isinstance(att["compile_options"], list) and att["compile_options"]
    assert att["wal_safe_library"] == db.sqlite_wal_is_safe()
    assert "3.51.3+" in att["wal_safe_versions"] and "3.44.6+" in att["wal_safe_versions"]
    # the CLI form the image build and CI run
    out = subprocess.run(
        [sys.executable, "-m", "convoy_server.cli", "sqlite-attest"], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["sqlite_version"] == sqlite3.sqlite_version
    bad = subprocess.run(
        [sys.executable, "-m", "convoy_server.cli", "sqlite-attest", "--expect", "0.0.0"],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1 and "expected SQLite 0.0.0" in bad.stderr
    # loader-path attestation (root's ARM64 build of 354ca78: the interpreter mapped the distribution
    # 3.46.1 because ld.so.conf.d order put /lib/aarch64-linux-gnu before /usr/local/lib): the mapped
    # library file and the interpreter's base prefix are facts, and the image build asserts both
    assert att["interpreter"] and att["base_prefix"] == sys.base_prefix
    assert att["library_mapped"] is (att["library_path"] is not None)
    if att["library_path"] is not None:  # Linux with a dynamically linked _sqlite3 (this container)
        assert att["library_path"].startswith("/") and "libsqlite3" in att["library_path"]
        prefix = att["library_path"].rsplit("/", 1)[0] + "/"
        ok = subprocess.run(
            [
                sys.executable,
                "-m",
                "convoy_server.cli",
                "sqlite-attest",
                "--expect-library-prefix",
                prefix,
                "--expect-base-prefix",
                sys.base_prefix,
            ],
            capture_output=True,
            text=True,
        )
        assert ok.returncode == 0, ok.stderr
        wrong = subprocess.run(
            [
                sys.executable,
                "-m",
                "convoy_server.cli",
                "sqlite-attest",
                "--expect-library-prefix",
                "/nonexistent/pinned/",
            ],
            capture_output=True,
            text=True,
        )
        assert wrong.returncode == 1 and "not a library under /nonexistent/pinned/" in wrong.stderr
        assert "ld.so.cache resolves" in wrong.stderr
    wrong_py = subprocess.run(
        [
            sys.executable,
            "-m",
            "convoy_server.cli",
            "sqlite-attest",
            "--expect-base-prefix",
            "/nonexistent/python",
        ],
        capture_output=True,
        text=True,
    )
    assert wrong_py.returncode == 1 and "base prefix" in wrong_py.stderr


def test_wal_safety_guard_names_exactly_the_fixed_releases():
    assert (
        db.sqlite_wal_is_safe((3, 51, 3))
        and db.sqlite_wal_is_safe((3, 51, 4))
        and db.sqlite_wal_is_safe((3, 52, 0))
    )
    assert db.sqlite_wal_is_safe((3, 44, 6)) and db.sqlite_wal_is_safe((3, 50, 7))
    for v in ((3, 46, 1), (3, 45, 1), (3, 51, 2), (3, 50, 6), (3, 44, 5), (3, 49, 9)):
        assert not db.sqlite_wal_is_safe(v), v


def test_a_service_never_changes_the_file_mode_and_refuses_a_mismatch(tmp_path, settings, monkeypatch):
    # a WAL file (created with the guard bypassed) opened by a service configured for DELETE
    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: True)
    path = settings.data_dir / "convoy.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    _make_db(path, "wal")
    assert db.file_journal_mode(path) == "wal"
    db.reset_engine()
    before = _evidence(path)
    with pytest.raises(db.JournalModeError) as ei:
        db.init_engine(replace(settings, sqlite_wal=False))
    assert "journal_mode=wal" in str(ei.value) and "db-mode delete" in str(ei.value)
    assert db.file_journal_mode(path) == "wal"  # the refusal changed nothing
    # ... and not only the mode: no schema was created or stamped, no row, no byte, no sidecar changed
    assert _evidence(path) == before and before["user_version"] == 0 and list(before["rows"]) == ["t"]
    db.reset_engine()
    # and the other way round: a DELETE file under a WAL intent is refused, never converted
    _make_db(settings.data_dir / "other.db", "delete")
    s2 = replace(
        settings, database_url=f"sqlite:///{(settings.data_dir / 'other.db').as_posix()}", sqlite_wal=True
    )
    before2 = _evidence(settings.data_dir / "other.db")
    with pytest.raises(db.JournalModeError) as ei2:
        db.init_engine(s2)
    assert "journal_mode=delete" in str(ei2.value) and "db-mode wal" in str(ei2.value)
    assert db.file_journal_mode(settings.data_dir / "other.db") == "delete"
    assert _evidence(settings.data_dir / "other.db") == before2
    db.reset_engine()


def test_mode_mismatch_refusal_precedes_schema_work_on_an_additive_upgrade_candidate(settings, monkeypatch):
    """Review of 354ca78: startup used to ensure the schema (create, add tables, stamp user_version)
    BEFORE verifying the journal intent, so a mismatched start mutated a database it then refused to
    serve. The preflight now runs before any connection: an existing additive-upgrade candidate keeps its
    missing table, old user_version, rows and bytes across the refusal, and the same candidate upgrades
    normally once the configuration matches the file."""
    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: True)
    path = settings.data_dir / "convoy.db"
    # a real Convoy database at the current schema, created in DELETE mode ...
    db.init_engine(replace(settings, sqlite_wal=False))
    db.reset_engine()
    # ... turned into an additive upgrade candidate (a schema-3 table missing, stamped one version back)
    c = sqlite3.connect(str(path), isolation_level=None)
    c.execute("DROP TABLE usage_unknown_intervals")
    c.execute("PRAGMA user_version=2")
    c.close()
    assert db.change_journal_mode(replace(settings, sqlite_wal=True), "wal")["ok"]  # the file is WAL now
    before = _evidence(path)
    assert before["user_version"] == 2 and "usage_unknown_intervals" not in before["rows"]
    # a service configured for DELETE refuses; the candidate is untouched in every respect
    with pytest.raises(db.JournalModeError) as ei:
        db.init_engine(replace(settings, sqlite_wal=False))
    assert "journal_mode=wal" in str(ei.value)
    after = _evidence(path)
    assert after == before, {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    db.reset_engine()
    # positive control: the matching configuration applies exactly that additive upgrade
    eng = db.init_engine(replace(settings, sqlite_wal=True))
    try:
        upgraded = _evidence(path)
        from convoy_server.migrations import SCHEMA_VERSION

        assert upgraded["user_version"] == SCHEMA_VERSION and "usage_unknown_intervals" in upgraded["rows"]
        with eng.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    finally:
        db.reset_engine()


def test_fresh_wal_intent_on_an_unqualified_library_creates_no_file(settings, monkeypatch):
    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: False)
    path = settings.data_dir / "convoy.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    assert not path.exists()
    with pytest.raises(db.JournalModeError) as ei:
        db.init_engine(replace(settings, sqlite_wal=True))
    assert "WAL-reset fix" in str(ei.value)
    assert not path.exists()  # refused before the file could be created or stamped
    for sfx in ("-wal", "-shm", "-journal"):
        assert not Path(str(path) + sfx).exists()
    db.reset_engine()
    # control: the same path starts fine under the DELETE intent on this library
    db.init_engine(replace(settings, sqlite_wal=False))
    assert path.exists() and db.file_journal_mode(path) == "delete"
    db.reset_engine()


def test_wal_intent_is_refused_on_an_unqualified_library(tmp_path, settings, monkeypatch):
    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: False)
    path = settings.data_dir / "convoy.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    _make_db(path, "delete")
    with pytest.raises(db.JournalModeError) as ei:
        db.init_engine(replace(settings, sqlite_wal=True))
    assert "WAL-reset fix" in str(ei.value)
    db.reset_engine()
    res = db.change_journal_mode(replace(settings, sqlite_wal=True), "wal")
    assert res["ok"] is False and "WAL-reset fix" in res["error"]
    assert db.file_journal_mode(path) == "delete"


def test_fresh_database_starts_in_the_configured_mode_and_every_accessor_sees_wal_full(settings, monkeypatch):
    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: True)
    s = replace(settings, sqlite_wal=True)
    config.set_settings(s)
    db.reset_engine()
    eng = db.init_engine(s)
    try:
        path = settings.data_dir / "convoy.db"
        assert db.file_journal_mode(path) == "wal"
        with eng.connect() as c:  # SQLAlchemy accessor (api, worker, migration)
            assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert int(c.execute(text("PRAGMA synchronous")).scalar()) == 2  # FULL
        raw = sqlite3.connect(str(path))  # raw stdlib accessor (backup source, health, CLI)
        try:
            assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            raw.close()
        st = db.db_status()
        assert (
            st["journal_mode"] == "wal"
            and st["intended_journal_mode"] == "wal"
            and st["journal_mode_matches"]
        )
    finally:
        db.reset_engine()
        config.set_settings(settings)


def test_db_mode_transitions_offline_checkpoint_first_and_an_old_accessor_reads_everything(
    settings, monkeypatch
):
    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: True)
    path = settings.data_dir / "convoy.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    _make_db(path, "delete", n=3)
    # delete -> wal
    res = db.change_journal_mode(replace(settings, sqlite_wal=True), "wal")
    assert res["ok"] and res["before"] == "delete" and res["after"] == "wal", res
    assert "CONVOY_SQLITE_WAL=1" in res["configure"]
    # commit rows in WAL mode and leave frames un-checkpointed (an open reader holds the WAL)
    w = sqlite3.connect(str(path), isolation_level=None)
    w.execute("PRAGMA wal_autocheckpoint=0")
    for i in (4, 5, 6):
        w.execute("INSERT INTO t VALUES (?)", (i,))
    assert Path(str(path) + "-wal").stat().st_size > 0
    w.close()
    # wal -> delete: TRUNCATE checkpoint moves every committed page into the main file, sidecars go
    res = db.change_journal_mode(replace(settings, sqlite_wal=False), "delete")
    assert res["ok"] and res["before"] == "wal" and res["after"] == "delete", res
    assert res["checkpoint"] is not None and res["checkpoint"][0] == 0  # not busy
    assert not Path(str(path) + "-wal").exists() and not Path(str(path) + "-shm").exists()
    assert db.file_journal_mode(path) == "delete"
    # an old-image accessor (forces DELETE on connect) sees all six committed rows
    assert _rows(path, force_delete=True) == [1, 2, 3, 4, 5, 6]
    # the transition is refused while a service holds the shared lock
    db.reset_engine()
    db.init_engine(replace(settings, sqlite_wal=False))
    try:
        busy = db.change_journal_mode(replace(settings, sqlite_wal=True), "wal")
        assert busy["ok"] is False and "locked" in busy["error"]
        assert db.file_journal_mode(path) == "delete"
    finally:
        db.reset_engine()


def test_doctor_and_entrypoints_refuse_a_mode_mismatch_explicitly(settings, monkeypatch):
    import os

    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: True)
    path = settings.data_dir / "convoy.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    _make_db(path, "wal")
    env = {**os.environ, "CONVOY_DATA_DIR": str(settings.data_dir), "CONVOY_SQLITE_WAL": "0"}
    for cmd in (["doctor"], ["worker"]):
        out = subprocess.run(
            [sys.executable, "-m", "convoy_server.cli", *cmd],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert out.returncode == 3, (cmd, out.stdout, out.stderr)
        assert "journal_mode=wal" in (out.stdout + out.stderr) and "db-mode delete" in (
            out.stdout + out.stderr
        )
    assert db.file_journal_mode(path) == "wal"  # refusals never touch the file
