"""Schema versioning and migration (R63).

`PRAGMA user_version` stamps the schema. Startup (`ensure_schema`) creates a fresh schema, accepts a
current one, applies ADDITIVE changes automatically (new tables, new nullable/defaulted columns, new
indexes) and REFUSES to start when a table rebuild is required (a unique constraint changed), naming the
command that performs it with the service stopped: `convoy-server migrate`. A database stamped by a
newer server is refused. Nothing is ever dropped; unknown columns in an older database are left alone.

Besides DDL, a version can carry DATA steps (`DATA_STEPS`): idempotent row transforms that run in the
same transaction as the DDL and the version stamp. A data step that has rows to touch is treated like
a rebuild: startup refuses and names `convoy-server migrate` (which keeps a pre-migration copy)."""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateIndex, CreateTable

from .config import Settings

log = logging.getLogger("convoy.migrations")
# Schema 3 also carries the ADDITIVE table `usage_unknown_intervals` (explicit unobserved intervals from
# schema-2 usage records). A v3 database that predates it gains the table at startup through the ordinary
# `plan_migration` -> `add_tables` path; no version bump or rebuild is involved.
# Schema 4 adds the platform application lifecycle; old text release records are unchanged.
# Schema 5 adds immutable evaluation suites, durable case jobs and release gates.
SCHEMA_VERSION = 5


class SchemaError(RuntimeError):
    pass


def db_file_path(settings: Settings) -> Path | None:
    url = settings.db_url
    if not url.startswith("sqlite"):
        raise SchemaError("Convoy MVP supports SQLite only")
    if ":memory:" in url:
        return None
    return (
        Path(url[len("sqlite:///") :]) if url.startswith("sqlite:///") else Path(url.split("sqlite:", 1)[1])
    )


def _literal(col: Any) -> str | None:
    """SQL literal for a column's Python-side scalar default (None when there is none)."""
    d = col.default
    if d is None or not getattr(d, "is_scalar", False):
        if d is not None and getattr(d, "is_callable", False):
            # SQLAlchemy wraps `default=dict`/`default=list` in a callable; the factory survives as
            # `__wrapped__` (or by name). A JSON column with such a default is backfilled with '{}'/'[]'.
            arg = getattr(d, "arg", None)
            factory = getattr(arg, "__wrapped__", None) or arg
            name = getattr(factory, "__name__", "")
            if factory is dict or name == "dict":
                return "'{}'"
            if factory is list or name == "list":
                return "'[]'"
        return None
    v = d.arg
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    if isinstance(v, (dict, list)):
        import json

        return "'" + json.dumps(v).replace("'", "''") + "'"
    return None


# ---- data steps -------------------------------------------------------------------------------
# Schema 3: persisted usage_daily rows written under `active_minutes` / `online_minutes` /
# `connected_minutes` (and the interim `legacy_*` names) came from unversioned producers whose meaning
# changed over time (process elapsed vs loosely bounded contact time; runtime loaded vs inference busy).
# They cannot be attributed after the fact, so they are preserved under explicit MIXED names and never
# summed with the measured schema-2 populations (`inference_minutes`, `contact_minutes`, ...), which
# use names no unversioned producer ever wrote. Collisions are summed (they are all "unattributable
# minutes" for that device-day); the step is idempotent.
USAGE_MIXED_RENAMES: tuple[tuple[str, str], ...] = (
    ("active_minutes", "mixed_active_minutes"),
    ("legacy_active_minutes", "mixed_active_minutes"),
    ("online_minutes", "mixed_online_minutes"),
    ("legacy_online_minutes", "mixed_online_minutes"),
    ("connected_minutes", "mixed_online_minutes"),  # only unversioned producers ever wrote it
)


def _usage_mixed_rows(conn: Any) -> int:
    names = ", ".join(f"'{s}'" for s, _ in USAGE_MIXED_RENAMES)
    return int(
        conn.execute(text(f"SELECT COUNT(*) FROM usage_daily WHERE metric IN ({names})")).scalar() or 0
    )


def _usage_mixed_apply(conn: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for src, dst in USAGE_MIXED_RENAMES:
        p = {"src": src, "dst": dst}
        merged = conn.execute(
            text(
                "UPDATE usage_daily SET value = COALESCE(value, 0) + ("
                " SELECT COALESCE(SUM(o.value), 0) FROM usage_daily o"
                " WHERE o.day = usage_daily.day AND o.device_id = usage_daily.device_id AND o.metric = :src)"
                " WHERE metric = :dst AND EXISTS ("
                " SELECT 1 FROM usage_daily o"
                " WHERE o.day = usage_daily.day AND o.device_id = usage_daily.device_id AND o.metric = :src)"
            ),
            p,
        ).rowcount
        conn.execute(
            text(
                "DELETE FROM usage_daily WHERE metric = :src AND EXISTS ("
                " SELECT 1 FROM usage_daily m"
                " WHERE m.day = usage_daily.day AND m.device_id = usage_daily.device_id AND m.metric = :dst)"
            ),
            p,
        )
        renamed = conn.execute(text("UPDATE usage_daily SET metric = :dst WHERE metric = :src"), p).rowcount
        out[src] = {"merged_into_existing": int(merged), "renamed": int(renamed)}
    return out


# {version: [(name, table, count_rows(conn) -> int, apply(conn) -> summary)]}
DATA_STEPS: dict[int, list[tuple[str, str, Any, Any]]] = {
    3: [("usage_daily_mixed_populations", "usage_daily", _usage_mixed_rows, _usage_mixed_apply)],
}


def _model_tables() -> dict[str, Any]:
    from . import models

    return dict(models.Base.metadata.tables)


def _unique_sets(engine: Engine, table: str) -> list[set[str]]:
    out: list[set[str]] = []
    with engine.connect() as c:
        for row in c.execute(text(f'PRAGMA index_list("{table}")')).mappings():
            if int(row["unique"]) != 1:
                continue
            cols = {r["name"] for r in c.execute(text(f'PRAGMA index_info("{row["name"]}")')).mappings()}
            out.append(cols)
    return out


def user_version(engine: Engine) -> int:
    with engine.connect() as c:
        return int(c.execute(text("PRAGMA user_version")).scalar() or 0)


def plan_migration(engine: Engine) -> dict[str, Any]:
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    plan: dict[str, Any] = {
        "user_version": user_version(engine),
        "target_version": SCHEMA_VERSION,
        "add_tables": [],
        "add_columns": [],
        "rebuild": [],
        "add_indexes": [],
    }
    tables = _model_tables()
    if not existing:
        plan["fresh"] = True
        return plan
    for name, table in tables.items():
        if name not in existing:
            plan["add_tables"].append(name)
            continue
        have = {c["name"] for c in insp.get_columns(name)}
        for col in table.columns:
            if col.name not in have:
                lit = _literal(col)
                if not col.nullable and lit is None:
                    ok_types = ("DATETIME",)
                    if str(col.type).upper() in ok_types:
                        lit = None  # timestamps without a scalar default are added nullable
                    else:
                        raise SchemaError(f"cannot add NOT NULL column {name}.{col.name} without a default")
                plan["add_columns"].append({"table": name, "column": col.name, "default": lit})
        wanted = [
            set(c.name for c in uc.columns)
            for uc in table.constraints
            if uc.__class__.__name__ == "UniqueConstraint"
        ]
        for cols in wanted:
            if cols not in _unique_sets(engine, name):
                plan["rebuild"].append(name)
                break
        existing_idx = {i["name"] for i in insp.get_indexes(name)}
        for idx in table.indexes:
            if idx.name not in existing_idx:
                plan["add_indexes"].append(idx.name)
    plan["rebuild"] = sorted(set(plan["rebuild"]))
    plan["needs_rebuild"] = bool(plan["rebuild"])
    plan["data_steps"] = []
    with engine.connect() as conn:
        for ver in sorted(DATA_STEPS):
            if ver <= plan["user_version"]:
                continue
            for name, table, count, _apply in DATA_STEPS[ver]:
                rows = count(conn) if table in existing else 0
                plan["data_steps"].append({"version": ver, "name": name, "table": table, "rows": rows})
    plan["needs_data_migration"] = any(s["rows"] for s in plan["data_steps"])
    plan["empty"] = not (
        plan["add_tables"]
        or plan["add_columns"]
        or plan["rebuild"]
        or plan["add_indexes"]
        or plan["needs_data_migration"]
    )
    return plan


def _rebuild_table(conn: Any, engine: Engine, name: str) -> None:
    """CREATE the model table under a temporary name, copy the common columns (filling new NOT NULL
    columns with their scalar defaults), drop the old table and rename. Runs inside the caller's txn."""
    table = _model_tables()[name]
    ddl = str(CreateTable(table).compile(engine)).strip()
    tmp = f"{name}__convoy_new"
    assert ddl.upper().startswith("CREATE TABLE"), ddl[:40]
    head, _, rest = ddl.partition("(")
    ddl = f'CREATE TABLE "{tmp}" (' + rest
    conn.execute(text(ddl))
    old_cols = [r["name"] for r in conn.execute(text(f'PRAGMA table_info("{name}")')).mappings()]
    target_cols, select_exprs = [], []
    for col in table.columns:
        if col.name in old_cols:
            target_cols.append(f'"{col.name}"')
            select_exprs.append(f'"{col.name}"')
        else:
            lit = _literal(col)
            if lit is None and not col.nullable:
                raise SchemaError(f"rebuild of {name}: no default for new NOT NULL column {col.name}")
            if lit is not None:
                target_cols.append(f'"{col.name}"')
                select_exprs.append(lit)
    conn.execute(
        text(f'INSERT INTO "{tmp}" ({", ".join(target_cols)}) SELECT {", ".join(select_exprs)} FROM "{name}"')
    )
    conn.execute(text(f'DROP TABLE "{name}"'))
    conn.execute(text(f'ALTER TABLE "{tmp}" RENAME TO "{name}"'))
    for idx in table.indexes:
        conn.execute(text(str(CreateIndex(idx, if_not_exists=True).compile(engine))))


def apply_migration(engine: Engine, plan: dict[str, Any], *, allow_rebuild: bool) -> dict[str, Any]:
    from . import models

    tables = _model_tables()
    if plan.get("fresh"):
        models.Base.metadata.create_all(engine)
        _stamp(engine)
        return {"created": sorted(tables), "user_version": SCHEMA_VERSION}
    if (plan.get("needs_rebuild") or plan.get("needs_data_migration")) and not allow_rebuild:
        pending = [f"{s['name']} ({s['rows']} rows)" for s in plan.get("data_steps", []) if s["rows"]]
        raise SchemaError(
            f"schema migration required (tables to rebuild: {plan['rebuild']}, data steps: {pending}, "
            f"user_version {plan['user_version']} -> {SCHEMA_VERSION}); stop the api and worker and run "
            "`convoy-server migrate`"
        )
    applied: dict[str, Any] = {
        "add_tables": [],
        "add_columns": [],
        "rebuild": [],
        "add_indexes": [],
        "data_steps": {},
    }
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text("BEGIN IMMEDIATE"))
        try:
            for name in plan["add_tables"]:
                conn.execute(text(str(CreateTable(tables[name]).compile(engine))))
                for idx in tables[name].indexes:
                    conn.execute(text(str(CreateIndex(idx, if_not_exists=True).compile(engine))))
                applied["add_tables"].append(name)
            for ac in plan["add_columns"]:
                if ac["table"] in plan["rebuild"]:
                    continue  # the rebuild carries it
                col = tables[ac["table"]].columns[ac["column"]]
                typ = col.type.compile(engine.dialect)
                extra = (
                    f" NOT NULL DEFAULT {ac['default']}"
                    if (ac["default"] is not None and not col.nullable)
                    else (f" DEFAULT {ac['default']}" if ac["default"] is not None else "")
                )
                conn.execute(text(f'ALTER TABLE "{ac["table"]}" ADD COLUMN "{ac["column"]}" {typ}{extra}'))
                applied["add_columns"].append(f"{ac['table']}.{ac['column']}")
            for name in plan["rebuild"]:
                _rebuild_table(conn, engine, name)
                applied["rebuild"].append(name)
            for table in tables.values():
                for idx in table.indexes:
                    if idx.name in plan["add_indexes"]:
                        conn.execute(text(str(CreateIndex(idx, if_not_exists=True).compile(engine))))
                        applied["add_indexes"].append(idx.name)
            for step in plan.get("data_steps", []):
                for name, _table, _count, apply in DATA_STEPS[step["version"]]:
                    if name == step["name"]:
                        applied["data_steps"][name] = apply(conn)  # idempotent; the DDL above precedes it
            conn.execute(text(f"PRAGMA user_version={SCHEMA_VERSION}"))
            conn.execute(text("COMMIT"))
        except Exception:
            conn.execute(text("ROLLBACK"))
            raise
        finally:
            conn.execute(text("PRAGMA foreign_keys=ON"))
    applied["user_version"] = SCHEMA_VERSION
    return applied


def _stamp(engine: Engine) -> None:
    with engine.connect() as c:
        c.execute(text(f"PRAGMA user_version={SCHEMA_VERSION}"))


def ensure_schema(engine: Engine, *, allow_rebuild: bool = False) -> dict[str, Any]:
    """Startup policy: fresh -> create; current -> ok; additive -> apply; rebuild or a data step with
    rows to touch -> refuse (unless allowed, e.g. by `convoy-server migrate`); newer -> refuse."""
    v = user_version(engine)
    if v > SCHEMA_VERSION:
        raise SchemaError(f"database schema user_version {v} is newer than this server ({SCHEMA_VERSION})")
    plan = plan_migration(engine)
    if plan.get("fresh"):
        return apply_migration(engine, plan, allow_rebuild=True)
    if plan["empty"]:
        if v != SCHEMA_VERSION:
            _stamp(engine)
        return {"user_version": SCHEMA_VERSION, "unchanged": True}
    out = apply_migration(engine, plan, allow_rebuild=allow_rebuild)
    log.warning("schema migrated: %s", out)
    return out


def migrate_database(settings: Settings, *, dry_run: bool = False) -> dict[str, Any]:
    """CLI entry point: exclusive lock, pre-migration copy, full migration including rebuilds."""
    import shutil

    from .db import DbLocked, acquire_db_lock, make_engine
    from .postgres import enabled, migrate

    if enabled(settings):
        return migrate(settings, dry_run=dry_run)

    path = db_file_path(settings)
    if path is None or not path.exists():
        return {"ok": False, "error": f"database not found at {path}"}
    try:
        lock = acquire_db_lock(settings, exclusive=True)
    except DbLocked as e:
        return {"ok": False, "error": str(e)}
    try:
        engine = make_engine(settings)
        try:
            v = user_version(engine)
            if v > SCHEMA_VERSION:
                return {
                    "ok": False,
                    "error": f"database schema user_version {v} is newer than this server ({SCHEMA_VERSION}); refusing to touch it",
                    "user_version": v,
                }
            plan = plan_migration(engine)
            if dry_run:
                return {"ok": True, "dry_run": True, "plan": plan}
            if plan.get("fresh") or plan["empty"]:
                if not plan.get("fresh") and user_version(engine) != SCHEMA_VERSION:
                    _stamp(engine)
                return {
                    "ok": True,
                    "plan": plan,
                    "applied": {"unchanged": True, "user_version": SCHEMA_VERSION},
                }
            copy = path.with_name(
                f"{path.stem}.pre-migrate-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.db"
            )
            # WAL-aware: fold committed WAL frames into the main file before the bare-file copy, so the
            # pre-migration copy is complete (we hold the exclusive lock; no other connection exists)
            engine.dispose()
            from .services.backup import LiveWalIncomplete, _settle_live_wal

            try:
                _settle_live_wal(path)
            except LiveWalIncomplete as e:
                # fail closed: no copy, no migration, the live file and its sidecars untouched
                return {"ok": False, "error": str(e), "plan": plan, "live_untouched": True}
            shutil.copy2(path, copy)
            applied = apply_migration(engine, plan, allow_rebuild=True)
            return {"ok": True, "plan": plan, "applied": applied, "pre_migration_copy": str(copy)}
        finally:
            engine.dispose()
    finally:
        lock.close()
