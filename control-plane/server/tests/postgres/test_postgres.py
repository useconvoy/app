"""PostgreSQL qualification reuses application scenarios and adds database boundaries."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from conftest import WEB
from convoy_server import config, db
from convoy_server.cli import main
from convoy_server.migrations import SchemaError, migrate_database
from convoy_server.models import Base, Installation, User
from convoy_server.platform_models import Mission
from convoy_server.postgres import WRITE_LOCK
from convoy_server.services.backup import BackupError, restore_backup, take_backup
from convoy_server.services.identity import enter_quarantine
from convoy_server.services.scheduler import Worker
from convoy_server.services.worker import worker_health
from sqlalchemy import inspect, select, text
from test_platform_lifecycle import (  # reuse the same HTTP contract on the real backend
    pipeline,
    test_cancel_races_unknown_and_generation_conflicts,
    test_expiry_missing_secret_and_revoked_binding,
    test_human_auth_project_scope_and_device_ownership,
    test_lifecycle_is_idempotent_fenced_and_produces_immutable_episode,
)

# Imported fixture/scenarios are intentionally collected by pytest here.
__all__ = [
    "pipeline",
    "test_cancel_races_unknown_and_generation_conflicts",
    "test_expiry_missing_secret_and_revoked_binding",
    "test_human_auth_project_scope_and_device_ownership",
    "test_lifecycle_is_idempotent_fenced_and_produces_immutable_episode",
]


def test_explicit_fresh_migration_is_repeatable_and_startup_refuses_unknown_schema(
    postgres_database, tmp_path
):
    settings = config.Settings(database_url=postgres_database, data_dir=tmp_path)
    config.set_settings(settings)
    with pytest.raises(SchemaError, match="schema is absent"):
        db.init_engine(settings)
    plan = migrate_database(settings, dry_run=True)
    assert plan["from_revision"] is None
    engine = db.make_engine(settings)
    try:
        assert inspect(engine).get_table_names() == []  # planning did not stamp/create anything
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE unrelated (id integer)"))
        with pytest.raises(SchemaError, match="unversioned"):
            migrate_database(settings)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE unrelated"))
        assert migrate_database(settings)["target_revision"] == "0003_workspace_documents"
        assert migrate_database(settings)["from_revision"] == "0003_workspace_documents"
        assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        db.init_engine(settings)
        assert db.db_status()["schema"]["compatible"] is True
        db.reset_engine()
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='unknown_future'"))
        with pytest.raises(SchemaError, match="incompatible"):
            db.init_engine(settings)
        with pytest.raises(SchemaError, match="unknown to this server"):
            migrate_database(settings)
    finally:
        engine.dispose()


def test_concurrent_idempotency_and_exclusive_mission_admission(pipeline):
    p = pipeline
    path = f"/api/v1/robots/{p['robot']['id']}/missions"
    body = {"deployment_id": p["deployment"]["id"], "expected_generation": 1, "seed": 2**32 - 1, "ttl_s": 60}
    barrier = threading.Barrier(2)

    def submit(key):
        barrier.wait(timeout=5)
        return p["admin"].post(path, json=body, headers={**WEB, "Idempotency-Key": key})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [
            job.result(timeout=10) for job in [pool.submit(submit, "same"), pool.submit(submit, "same")]
        ]
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert first.json()["seed"] == 2**32 - 1
    with db.session_scope() as session:
        assert len(list(session.scalars(select(Mission)))) == 1
    # Finish the first unclaimed mission, then race two distinct starts on an idle robot.
    mission_id = first.json()["id"]
    cancelled = p["admin"].post(
        f"/api/v1/missions/{mission_id}/cancel",
        json={},
        headers={**WEB, "Idempotency-Key": "cancel-first"},
    )
    assert cancelled.status_code == 200
    assert (
        p["agent"]
        .client.post(
            f"{p['base']}/missions/{mission_id}/report",
            json={"identity": None, "state": "cancelled"},
        )
        .status_code
        == 200
    )
    barrier.reset()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            job.result(timeout=10) for job in [pool.submit(submit, "another"), pool.submit(submit, "third")]
        ]
    assert sorted(result.status_code for result in results) == [201, 409]
    with db.session_scope() as session:
        assert len(list(session.scalars(select(Mission)))) == 2


def test_lock_contention_returns_retryable_error_without_partial_mutation(admin):
    body = {"name": "Retry after contention"}
    headers = {**WEB, "Idempotency-Key": "bounded-contention"}
    with db.get_engine().begin() as blocker:
        blocker.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WRITE_LOCK})
        response = admin.post("/api/v1/projects", json=body, headers=headers)
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
    assert all(project["name"] != body["name"] for project in admin.get("/api/v1/projects").json())
    result = admin.post("/api/v1/projects", json=body, headers=headers)
    assert result.status_code == 201
    assert admin.post("/api/v1/projects", json=body, headers=headers).json() == result.json()


def test_artifact_receipt_preserves_sizes_above_signed_32_bit(admin):
    recipe = admin.post(
        "/api/v1/recipes",
        headers=WEB,
        json={"name": "large simulated artifact", "commit": "a" * 40, "backend": "simulated"},
    )
    assert recipe.status_code == 201, recipe.text
    receipt = {
        "archive_sha256": "a" * 64,
        "archive_size": 3 * 1024**3,
        "files": [{"path": "bin/llama-server.sim", "size": 3 * 1024**3, "sha256": "b" * 64}],
        "provenance": {"simulated": True},
    }
    artifact = admin.post(
        "/api/v1/runtime-artifacts",
        headers=WEB,
        json={"recipe_id": recipe.json()["id"], "receipt": receipt, "scope": "fleet", "storage": "device"},
    )
    assert artifact.status_code == 201, artifact.text
    assert artifact.json()["archive_size"] == 3 * 1024**3
    assert admin.get("/api/v1/runtime-artifacts").json()[0]["archive_size"] == 3 * 1024**3


def test_transactions_rollback_release_lock_and_fence_competing_workers(app, settings):
    with db.session_scope() as session:
        with pytest.raises(RuntimeError, match="abort"):
            with db.write_txn(session):
                session.add(User(id="aborted", email="aborted@example.com", password_hash="never-used"))
                session.flush()
                raise RuntimeError("abort")
        assert session.get(User, "aborted") is None
    barrier = threading.Barrier(2)

    def acquire(owner):
        with db.session_scope() as session:
            barrier.wait(timeout=5)
            return Worker(owner).acquire(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        acquired = [
            job.result(timeout=10) for job in [pool.submit(acquire, "one"), pool.submit(acquire, "two")]
        ]
    assert sorted(acquired) == [False, True]
    assert worker_health(settings)["ok"] is True


def test_sqlite_maintenance_refuses_postgres_and_quarantine_revokes_restored_authority(pipeline, tmp_path):
    p = pipeline
    health = p["admin"].get("/api/health").json()["db"]
    assert health["backend"] == "postgresql"
    assert health["backup"] == {"mode": "external", "managed_by_convoy": False}
    assert p["admin"].post("/api/v1/admin/backups", headers=WEB).status_code == 501
    assert db.change_journal_mode(p["settings"], "wal")["ok"] is False
    assert restore_backup(str(tmp_path / "does-not-exist.db"), confirm=True)["ok"] is False
    with db.session_scope() as session:
        with pytest.raises(BackupError, match="unavailable for PostgreSQL"):
            take_backup(session, str(tmp_path / "must-not-exist.db"))
        enter_quarantine(session, "restored PostgreSQL qualification copy")
        installation = session.get(Installation, 1)
        assert installation.quarantined_at and installation.dispatch_paused_at
    assert not (tmp_path / "must-not-exist.db").exists()
    assert p["admin"].get("/api/v1/projects").status_code == 401
    assert p["agent"].client.get(f"{p['base']}/desired").status_code == 401
    assert main(["doctor"]) == 0  # database health does not mislabel itself as SQLite
