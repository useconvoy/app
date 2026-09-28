"""PostgreSQL fleet storage; device journals remain SQLite.

The first backend preserves the existing single-writer transaction contract with
a database-scoped advisory lock. This is a compatibility boundary, not a claim
of parallel write throughput. Migrations are explicit and never run at startup.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine, make_url

WRITE_LOCK = 4849917224464821577  # stable within each PostgreSQL database


def enabled(settings) -> bool:
    return make_url(settings.db_url).get_backend_name() == "postgresql"


def migration_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("postgres_migrations")))
    return config


def schema_status(connection) -> dict:
    config = migration_config()
    current = MigrationContext.configure(connection).get_current_revision()
    expected = ScriptDirectory.from_config(config).get_current_head()
    return {"revision": current, "expected_revision": expected, "compatible": current == expected}


def require_schema(engine: Engine) -> dict:
    from .migrations import SchemaError

    with engine.connect() as connection:
        status = schema_status(connection)
    if not status["compatible"]:
        raise SchemaError(
            "PostgreSQL schema is absent or incompatible; stop API/worker services and run "
            "`convoy-server migrate` with this installation's DATABASE_URL"
        )
    return status


def migrate(settings, *, dry_run: bool = False) -> dict:
    from .db import make_engine
    from .migrations import SchemaError

    engine = make_engine(settings)
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WRITE_LOCK})
            status = schema_status(connection)
            tables = set(inspect(connection).get_table_names())
            if status["revision"] is None and tables - {"alembic_version"}:
                raise SchemaError(
                    "Refusing to stamp an existing unversioned PostgreSQL schema; use a fresh database"
                )
            config = migration_config()
            script = ScriptDirectory.from_config(config)
            try:
                if status["revision"]:
                    script.get_revision(status["revision"])
            except CommandError as error:
                raise SchemaError("PostgreSQL schema revision is unknown to this server") from error
            if not dry_run:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            return {
                "ok": True,
                "backend": "postgresql",
                "dry_run": dry_run,
                "from_revision": status["revision"],
                "target_revision": status["expected_revision"],
            }
    finally:
        engine.dispose()


def status(engine: Engine) -> dict:
    with engine.connect() as connection:
        return {
            "backend": "postgresql",
            "server_version": connection.execute(text("SHOW server_version")).scalar(),
            "schema": schema_status(connection),
            "write_concurrency": "serialized_advisory_lock",
            "backup": {"mode": "external", "managed_by_convoy": False},
        }
