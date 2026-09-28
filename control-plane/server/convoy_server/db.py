"""Database engine and transaction discipline.

SQLite is opened in driver autocommit mode (isolation_level=None); SQLAlchemy therefore never emits an
implicit BEGIN. Reads run statement-by-statement. Every write happens inside `write_txn(db)`, which
issues `BEGIN IMMEDIATE` (taking the RESERVED lock up front so a transaction can never deadlock on a
read-to-write upgrade), retries a bounded number of times on SQLITE_BUSY, and commits or rolls back.
Synchronous is FULL (acknowledged durability). The journal mode is a property of the database FILE,
never forced by a connection: services verify at start that the file's mode equals the configured
intent (`CONVOY_SQLITE_WAL`, default DELETE) and refuse to start otherwise, and the only mode
transitions are the explicit offline `convoy-server db-mode` command. WAL is accepted only on a
library that carries the WAL-reset fix (3.44.6+, 3.50.7+, 3.51.3+), attested by `sqlite_attestation()`.
"""

from __future__ import annotations

import logging
import os
import random
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings

log = logging.getLogger("convoy.db")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_lock: Any = None  # shared advisory lock on <db>.lock while this process has the engine open (R51)


class DbLocked(RuntimeError):
    """Another process holds the database lock in a conflicting mode."""


def acquire_db_lock(settings: Settings, *, exclusive: bool) -> Any:
    """Advisory flock on `<database file>.lock`. Services hold it SHARED for their lifetime; restore and
    migration take it EXCLUSIVE (non-blocking) so they can never overwrite a database that an api or
    worker process still has open. Returns the open lock file (close() releases). In-memory: no lock."""
    import fcntl

    from .migrations import db_file_path
    from .postgres import enabled

    if enabled(settings):
        return None  # PostgreSQL ownership is transactional, never a local file lock.

    path = db_file_path(settings)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    f = open(lock_path, "a+")
    try:
        fcntl.flock(f.fileno(), (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except OSError as e:
        f.close()
        mode = (
            "exclusively (a restore or migration is running)"
            if not exclusive
            else "(an api or worker process still has it open); stop them first"
        )
        raise DbLocked(f"database {path} is locked {mode}") from e
    return f


BUSY_RETRIES = 6
BUSY_BASE_MS = 40
BUSY_MAX_MS = 800
WAL_SAFE_VERSIONS = ((3, 44, 6), (3, 50, 7), (3, 51, 3))


def sqlite_wal_is_safe(version: tuple[int, int, int] | None = None) -> bool:
    """True only for releases that carry the WAL-reset race fix (3.51.3, or the 3.44.6 / 3.50.7
    backports) and anything newer."""
    v = version or sqlite3.sqlite_version_info
    for major, minor, patch in WAL_SAFE_VERSIONS:
        if v[0] == major and v[1] == minor and v[2] >= patch:
            return True
    if v[:2] > (3, 51):
        return True
    return False


class JournalModeError(RuntimeError):
    """The database file's journal mode does not match the configured intent, or the intent cannot be
    honoured by this SQLite library. Services refuse to start; `convoy-server db-mode` is the remedy."""


def intended_journal_mode(settings: Settings | None = None) -> str:
    return "wal" if (settings or get_settings()).sqlite_wal else "delete"


def _sqlite_library_path() -> str | None:
    """Which native library the running interpreter's `sqlite3` module mapped (Linux)."""
    try:
        with open("/proc/self/maps") as f:
            for line in f:
                if "libsqlite3" in line or "_sqlite3" in line:
                    path = line.split()[-1]
                    if "libsqlite3" in path:
                        return path
    except OSError:
        pass
    return None


def _ld_cache_first(soname: str) -> str | None:
    """Where the dynamic loader's cache resolves `soname` FIRST (`ldconfig -p`), if available. Evidence
    for the image's loader precedence; the mapped `library_path` is what actually counts."""
    import shutil
    import subprocess

    exe = shutil.which("ldconfig") or ("/sbin/ldconfig" if os.path.exists("/sbin/ldconfig") else None)
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "-p"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] == soname and "=>" in parts:
            return parts[-1]
    return None


def sqlite_attestation() -> dict[str, Any]:
    """Facts about the ONE native SQLite every accessor in this process uses (stdlib `sqlite3`, hence
    SQLAlchemy, the worker, the CLI, restore and migration): version as the module reports it, the
    version and source id the engine itself answers, the compile options, the mapped library file
    and whether WAL is accepted on it. `expect_version` checks belong to the caller (image build)."""
    import _sqlite3

    c = sqlite3.connect(":memory:")
    try:
        sql_version = c.execute("SELECT sqlite_version()").fetchone()[0]
        source_id = c.execute("SELECT sqlite_source_id()").fetchone()[0]
        opts = [r[0] for r in c.execute("PRAGMA compile_options")]
    finally:
        c.close()
    import sys

    library_path = _sqlite_library_path()
    return {
        "sqlite_version": sqlite3.sqlite_version,
        "sql_sqlite_version": sql_version,
        "sqlite_source_id": source_id,
        "consistent": sql_version == sqlite3.sqlite_version,
        "module": getattr(_sqlite3, "__file__", None),
        "library_path": library_path,
        # None when the extension module carries its own statically linked SQLite (nothing separate is
        # mapped), or off Linux; the image build requires a mapped file under /usr/local/lib
        "library_mapped": library_path is not None,
        "ld_cache_first": _ld_cache_first("libsqlite3.so.0"),
        "interpreter": sys.executable,
        "base_prefix": sys.base_prefix,
        "compile_options": opts,
        "wal_safe_library": sqlite_wal_is_safe(),
        "wal_safe_versions": [".".join(map(str, v)) + "+" for v in WAL_SAFE_VERSIONS],
        "threadsafety": sqlite3.threadsafety,
    }


def file_journal_mode(path: Any) -> str:
    """The journal mode recorded in the file itself, read without changing it."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        c.close()


def preflight_journal_library(settings: Settings) -> str:
    """The library half of the startup contract, needing no file: a WAL intent on a library without
    the WAL-reset fix is refused. Runs before a fresh database could be created."""
    intent = intended_journal_mode(settings)
    if intent == "wal" and not sqlite_wal_is_safe():
        raise JournalModeError(
            f"CONVOY_SQLITE_WAL=1 but SQLite {sqlite3.sqlite_version} does not carry the WAL-reset fix "
            f"(needs one of {WAL_SAFE_VERSIONS} or newer); refusing to start on an unqualified library"
        )
    return intent


def verify_journal_mode(settings: Settings, *, path: Any = None) -> dict[str, Any]:
    """Startup contract: the file's mode must equal the intent, and a WAL intent needs a qualified
    library. Raises JournalModeError with the remedy; opens the file read-only and never changes it."""
    from .migrations import db_file_path

    p = path if path is not None else db_file_path(settings)
    intent = intended_journal_mode(settings)
    if p is None:
        return {"intended": intent, "actual": "memory", "ok": True}
    preflight_journal_library(settings)
    actual = file_journal_mode(p)
    if actual != intent:
        raise JournalModeError(
            f"database {p} is in journal_mode={actual} but this service is configured for {intent} "
            f"(CONVOY_SQLITE_WAL={'1' if intent == 'wal' else '0'}); a connection never changes the mode. "
            f"Stop every api/worker process and run `convoy-server db-mode {intent}` (or set the "
            f"configuration to match the file), then start again"
        )
    return {"intended": intent, "actual": actual, "ok": True}


def _configure_sqlite(dbapi_conn, _record) -> None:
    dbapi_conn.isolation_level = None  # driver autocommit; we manage BEGIN IMMEDIATE ourselves
    cur = dbapi_conn.cursor()
    # No `PRAGMA journal_mode=...` here: the mode belongs to the file (see the module docstring). A
    # connection that forced its own preference would silently convert a WAL database back to DELETE
    # the moment it got exclusive access (exactly what an old-image accessor must not do).
    cur.execute("PRAGMA synchronous=FULL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.close()


def make_engine(settings: Settings) -> Engine:
    url = settings.db_url
    from sqlalchemy.engine import make_url

    from .postgres import enabled

    if enabled(settings):
        parsed = make_url(url)
        if parsed.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise RuntimeError("PostgreSQL uses the qualified psycopg driver")
        return create_engine(
            parsed.set(drivername="postgresql+psycopg"),
            connect_args={
                "connect_timeout": 5,
                "application_name": "convoy",
                "options": "-c timezone=UTC -c lock_timeout=5000 -c statement_timeout=30000",
            },
            isolation_level="READ COMMITTED",
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_timeout=5,
        )
    if not url.startswith("sqlite"):
        raise RuntimeError("supported database backends are SQLite and PostgreSQL")
    if ":memory:" not in url:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 5},
        pool_pre_ping=True,
        poolclass=None,
    )
    event.listen(engine, "connect", _configure_sqlite)
    return engine


def init_engine(settings: Settings | None = None) -> Engine:
    global _engine, _SessionLocal, _lock
    settings = settings or get_settings()
    from .migrations import db_file_path, ensure_schema
    from .postgres import enabled, require_schema

    if enabled(settings):
        try:
            _engine = make_engine(settings)
            require_schema(_engine)
            _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
            return _engine
        except Exception:
            reset_engine()
            raise

    if _lock is None:
        _lock = acquire_db_lock(settings, exclusive=False)
    path = db_file_path(settings)
    fresh = path is not None and (not path.exists() or path.stat().st_size == 0)
    try:
        # Preflight BEFORE any connection exists: a WAL intent needs a qualified library (also for a
        # file this service is about to create), and an existing file's mode must equal the intent.
        # Refusal therefore happens before the schema could be created, upgraded or stamped: an
        # existing database keeps its sqlite_schema, user_version, rows and bytes; a fresh path stays
        # absent. Nothing below runs against a database this service must not touch.
        if path is not None:
            if fresh:
                preflight_journal_library(settings)
            else:
                verify_journal_mode(settings, path=path)
        _engine = make_engine(settings)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
        ensure_schema(_engine, allow_rebuild=False)
        if path is not None:
            if fresh:
                # a database this service creates starts in the configured mode (the only place a
                # service sets the mode; an existing file is verified, never changed). Pooled
                # connections opened during schema creation predate the switch: drop them.
                set_file_journal_mode(path, intended_journal_mode(settings))
                _engine.dispose()
            verify_journal_mode(settings, path=path)  # the invariant holds after creation too
    except Exception:
        reset_engine()
        raise
    with _engine.connect() as c:
        mode = c.execute(text("PRAGMA journal_mode")).scalar()
        sync = c.execute(text("PRAGMA synchronous")).scalar()
    log.info("sqlite %s journal_mode=%s synchronous=%s", sqlite3.sqlite_version, mode, sync)
    return _engine


def set_file_journal_mode(path: Any, mode: str) -> dict[str, Any]:
    """Change the FILE's journal mode on a private connection: to WAL only on a qualified library; to
    DELETE after a TRUNCATE checkpoint so the main file holds every committed page and no -wal/-shm
    is left beside it. The caller holds the exclusive ownership this needs (fresh file at creation,
    or the exclusive data-directory lock in `db-mode`)."""
    mode = mode.lower()
    if mode not in ("wal", "delete"):
        raise JournalModeError(f"unsupported journal mode {mode!r}")
    if mode == "wal" and not sqlite_wal_is_safe():
        raise JournalModeError(
            f"refusing journal_mode=wal on SQLite {sqlite3.sqlite_version}: the WAL-reset fix "
            f"(3.44.6+, 3.50.7+, 3.51.3+) is not present"
        )
    before = file_journal_mode(path)
    c = sqlite3.connect(str(path), isolation_level=None, timeout=5)
    try:
        checkpoint = None
        if before == "wal":
            checkpoint = list(c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
        got = str(c.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]).lower()
        c.execute("PRAGMA synchronous=FULL")
        if got != mode:
            raise JournalModeError(
                f"journal_mode={mode} not applied (file answers {got}); is another process using it?"
            )
    finally:
        c.close()
    after = file_journal_mode(path)
    if after != mode:
        raise JournalModeError(f"journal_mode={mode} did not persist (file answers {after})")
    sidecars = {}
    from pathlib import Path as _P

    for suffix in ("-wal", "-shm"):
        sc = _P(str(path) + suffix)
        sidecars[suffix] = sc.stat().st_size if sc.exists() else None
    return {"before": before, "after": after, "checkpoint": checkpoint, "sidecars": sidecars}


def change_journal_mode(settings: Settings, mode: str) -> dict[str, Any]:
    """`convoy-server db-mode <mode>`: the deliberate, offline mode transition. Takes the exclusive
    data-directory lock (refuses while any api/worker holds it), converts the file, verifies, and
    reports what the configuration must say for services to start again. Never touches backups."""
    from .migrations import db_file_path
    from .postgres import enabled

    if enabled(settings):
        return {"ok": False, "error": "db-mode applies only to SQLite; PostgreSQL has no SQLite journal mode"}

    path = db_file_path(settings)
    if path is None or not path.exists():
        return {"ok": False, "error": f"database not found at {path}"}
    try:
        lock = acquire_db_lock(settings, exclusive=True)
    except DbLocked as e:
        return {"ok": False, "error": str(e)}
    try:
        res = set_file_journal_mode(path, mode)
    except JournalModeError as e:
        return {"ok": False, "error": str(e), "sqlite_version": sqlite3.sqlite_version}
    finally:
        lock.close()
    return {
        "ok": True,
        "path": str(path),
        "sqlite_version": sqlite3.sqlite_version,
        **res,
        "configure": f"CONVOY_SQLITE_WAL={'1' if mode.lower() == 'wal' else '0'} for api AND worker before starting them",
    }


def db_status() -> dict:
    eng = get_engine()
    if eng.dialect.name == "postgresql":
        from .postgres import status

        return status(eng)
    with eng.connect() as c:
        mode = c.execute(text("PRAGMA journal_mode")).scalar()
        status = {
            "sqlite_version": sqlite3.sqlite_version,
            "journal_mode": mode,
            "synchronous": c.execute(text("PRAGMA synchronous")).scalar(),
            "wal_safe_library": sqlite_wal_is_safe(),
            "intended_journal_mode": intended_journal_mode(),
        }
    status["journal_mode_matches"] = str(mode).lower() == status["intended_journal_mode"] or mode == "memory"
    return status


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal


class WriteConflict(Exception):
    """Raised when a bounded write transaction cannot acquire its database lock."""


@contextmanager
def write_txn(db: Session) -> Iterator[Session]:
    """Scoped write transaction: SQLite BEGIN IMMEDIATE or PostgreSQL advisory lock.
    Nested use inside an open write transaction is a no-op (joins the outer transaction)."""
    if db.info.get("in_write_txn"):
        yield db
        return
    db.rollback()  # discard any implicit read state
    if db.get_bind().dialect.name == "postgresql":
        from .postgres import WRITE_LOCK

        try:
            # Preserve SQLite's existing write serialization across API/worker
            # processes. READ COMMITTED gives fresh reads after the lock wait.
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WRITE_LOCK})
            db.info["in_write_txn"] = True
            yield db
            db.flush()
            db.commit()
        except OperationalError as error:
            db.rollback()
            if getattr(error.orig, "sqlstate", None) in {"55P03", "40001", "40P01"}:
                raise WriteConflict(
                    "database write contention; retry the complete idempotent request"
                ) from error
            raise
        except BaseException:
            db.rollback()
            raise
        finally:
            db.info["in_write_txn"] = False
        return
    for attempt in range(BUSY_RETRIES):
        try:
            db.execute(text("BEGIN IMMEDIATE"))
            break
        except OperationalError as e:
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            db.rollback()
            if attempt == BUSY_RETRIES - 1:
                raise WriteConflict("database busy after retries") from e
            delay = min(BUSY_MAX_MS, BUSY_BASE_MS * (2**attempt)) * (0.5 + random.random())
            time.sleep(delay / 1000.0)
    db.info["in_write_txn"] = True
    try:
        yield db
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.info["in_write_txn"] = False


@contextmanager
def session_scope() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
    finally:
        s.close()


def reset_engine() -> None:
    global _engine, _SessionLocal, _lock
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
    if _lock is not None:
        _lock.close()
        _lock = None
