from __future__ import annotations

import argparse
import getpass
import json
import sys

from .config import get_settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="convoy-server")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser(
        "serve", help="run the API (and the in-process worker when CONVOY_SCHEDULER_INPROCESS=1)"
    )
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--reload", action="store_true")
    sub.add_parser("worker", help="run the fenced scheduler/rollout/backup worker")
    sub.add_parser("doctor", help="print database and configuration status")
    dm = sub.add_parser(
        "db-mode",
        help="offline journal-mode transition of the database FILE (api and worker must be stopped)",
    )
    dm.add_argument("mode", choices=["wal", "delete"])
    sa = sub.add_parser(
        "sqlite-attest", help="print the native SQLite this interpreter uses (image build check)"
    )
    sa.add_argument("--expect", default=None, help="fail unless sqlite_version equals this")
    sa.add_argument("--expect-source-id", default=None, help="fail unless SQL sqlite_source_id() equals this")
    sa.add_argument("--require-wal-safe", action="store_true")
    sa.add_argument(
        "--expect-library-prefix",
        default=None,
        help="fail unless the library file the interpreter mapped starts with this path (loader precedence)",
    )
    sa.add_argument(
        "--expect-base-prefix",
        default=None,
        help="fail unless sys.base_prefix equals this (the image's own CPython)",
    )
    u = sub.add_parser("create-user", help="create or update a user")
    u.add_argument("email")
    u.add_argument("--role", default="admin")
    u.add_argument("--name", default="")
    u.add_argument("--password", default=None)
    b = sub.add_parser("backup", help="take a consistent backup now")
    b.add_argument("--out", default=None)
    r = sub.add_parser("restore", help="restore a backup file into the data dir and enter quarantine")
    r.add_argument("file")
    r.add_argument("--yes", action="store_true")
    sub.add_parser("seed-sim", help="seed simulator fixtures (requires CONVOY_SIMULATOR=1)")
    ra = sub.add_parser(
        "recover-admin",
        help="after a restore: set a NEW password and enable one admin (dispatch stays paused)",
    )
    ra.add_argument("email")
    ra.add_argument("--password", default=None)
    q = sub.add_parser(
        "quarantine", help="enter restore quarantine manually (invalidates all credentials, pauses dispatch)"
    )
    q.add_argument("--reason", default="manual")
    m = sub.add_parser("migrate", help="apply the schema migration (api and worker must be stopped)")
    m.add_argument("--dry-run", action="store_true")
    sub.add_parser(
        "verify-artifacts",
        help="check every referenced runtime artifact archive exists with the recorded sha256",
    )
    sub.add_parser(
        "worker-health",
        help="exit 0 when a live scheduler lease exists (container healthcheck for the worker)",
    )
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from .db import init_engine
        from .migrations import SchemaError

        try:
            init_engine()  # R63: refuse explicitly on an incompatible schema or journal mode
        except (SchemaError, RuntimeError) as e:  # JournalModeError is a RuntimeError
            print(f"error: {e}", file=sys.stderr)
            return 3

        uvicorn.run(
            "convoy_server.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload
        )
        return 0
    if args.cmd == "worker":
        import logging

        from .db import init_engine
        from .services.worker import run_forever

        # the API process configures logging in create_app; the worker process has no app factory, so
        # without this the root logger stays at WARNING and the worker's INFO lines (lease start, backup
        # success with its id and timings) are dropped while only warnings reach stderr
        logging.basicConfig(level=get_settings().log_level)
        try:
            init_engine()
        except RuntimeError as e:  # schema or journal-mode refusal: explicit, before any tick
            print(f"error: {e}", file=sys.stderr)
            return 3
        return run_forever()
    if args.cmd == "sqlite-attest":
        from .db import sqlite_attestation

        att = sqlite_attestation()
        print(json.dumps(att, indent=2))
        if not att["consistent"]:
            print("error: sqlite3 module version and SQL sqlite_version() disagree", file=sys.stderr)
            return 1
        if args.expect and att["sqlite_version"] != args.expect:
            print(f"error: expected SQLite {args.expect}, got {att['sqlite_version']}", file=sys.stderr)
            return 1
        if args.expect_source_id and att["sqlite_source_id"] != args.expect_source_id:
            print(
                f"error: expected source id {args.expect_source_id!r}, got {att['sqlite_source_id']!r}",
                file=sys.stderr,
            )
            return 1
        if args.require_wal_safe and not att["wal_safe_library"]:
            print("error: this SQLite does not carry the WAL-reset fix", file=sys.stderr)
            return 1
        if args.expect_library_prefix:
            lib = att["library_path"]
            if lib is None:
                print(
                    "error: no separate libsqlite3 is mapped (statically linked extension module?); "
                    f"expected a library under {args.expect_library_prefix}",
                    file=sys.stderr,
                )
                return 1
            if not lib.startswith(args.expect_library_prefix):
                print(
                    f"error: the interpreter mapped {lib}, not a library under {args.expect_library_prefix} "
                    f"(ld.so.cache resolves libsqlite3.so.0 first to {att['ld_cache_first']})",
                    file=sys.stderr,
                )
                return 1
        if args.expect_base_prefix and att["base_prefix"] != args.expect_base_prefix:
            print(
                f"error: interpreter base prefix is {att['base_prefix']!r} (executable {att['interpreter']}), "
                f"expected {args.expect_base_prefix!r}",
                file=sys.stderr,
            )
            return 1
        return 0
    if args.cmd == "db-mode":
        from .db import change_journal_mode

        res = change_journal_mode(get_settings(), args.mode)
        print(json.dumps(res, indent=2, default=str))
        return 0 if res.get("ok") else 1
    if args.cmd == "doctor":
        from .db import JournalModeError, db_status, init_engine, sqlite_attestation

        try:
            init_engine()
        except JournalModeError as e:
            print(json.dumps({"ok": False, "error": str(e), "sqlite": sqlite_attestation()}, indent=2))
            return 3
        st = get_settings()
        print(
            json.dumps(
                {
                    "db": {**db_status(), "attestation": sqlite_attestation()},
                    "data_dir": str(st.data_dir),
                    "simulator": st.simulator,
                    "public_url": st.public_url,
                },
                indent=2,
            )
        )
        return 0
    if args.cmd == "create-user":
        from sqlalchemy import select

        from .db import init_engine, session_scope, write_txn
        from .ids import new_id
        from .models import User
        from .security import hash_password

        init_engine()
        pw = args.password or getpass.getpass("password: ")
        with session_scope() as db:
            with write_txn(db):
                row = db.scalar(select(User).where(User.email == args.email.lower()))
                if row:
                    row.password_hash = hash_password(pw)
                    row.role = args.role
                    print("updated", row.id)
                else:
                    row = User(
                        id=new_id("usr"),
                        email=args.email.lower(),
                        name=args.name,
                        role=args.role,
                        password_hash=hash_password(pw),
                    )
                    db.add(row)
                    print("created", row.id)
        return 0
    if args.cmd == "backup":
        from .db import init_engine, session_scope
        from .services.backup import BackupError, take_backup

        try:
            init_engine()
            with session_scope() as db:
                print(json.dumps(take_backup(db, args.out), indent=2))
        except (BackupError, RuntimeError) as e:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
            return 1
        return 0
    if args.cmd == "restore":
        from .services.backup import restore_backup

        res = restore_backup(args.file, confirm=args.yes)
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1  # R66: shell recovery must stop on failure
    if args.cmd == "migrate":
        from .migrations import SchemaError, migrate_database

        try:
            res = migrate_database(get_settings(), dry_run=args.dry_run)
        except SchemaError as e:
            res = {"ok": False, "error": str(e)}
        print(json.dumps(res, indent=2, default=str))
        return 0 if res.get("ok") else 1
    if args.cmd == "verify-artifacts":
        from .db import init_engine, session_scope
        from .services.catalog import verify_artifacts

        init_engine()
        with session_scope() as db:
            res = verify_artifacts(db, get_settings())
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1
    if args.cmd == "worker-health":
        from .services.worker import worker_health

        res = worker_health(get_settings())
        print(json.dumps(res))
        return 0 if res.get("ok") else 1
    if args.cmd == "recover-admin":
        from .db import init_engine, session_scope
        from .services.identity import IdentityError, recover_admin

        init_engine()
        pw = args.password or getpass.getpass("new password: ")
        with session_scope() as db:
            try:
                u = recover_admin(db, args.email, pw)
            except IdentityError as e:
                print("error:", e)
                return 2
            print(
                "recovered admin",
                u.id,
                u.email,
                "- dispatch remains paused until quarantine is lifted in Settings",
            )
        return 0
    if args.cmd == "quarantine":
        from .db import init_engine, session_scope
        from .services.identity import enter_quarantine

        init_engine()
        with session_scope() as db:
            enter_quarantine(db, args.reason)
        print("quarantine entered")
        return 0
    if args.cmd == "seed-sim":
        from .db import init_engine, session_scope
        from .services.seed import seed_simulator

        init_engine()
        with session_scope() as db:
            print(json.dumps(seed_simulator(db, get_settings()), indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
