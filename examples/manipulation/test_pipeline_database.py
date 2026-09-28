"""One real database ownership check; the six-case harness covers the runtime path."""

import os
import uuid

import psycopg
import pytest
from pipeline import disposable_postgres
from sqlalchemy.engine import make_url


def test_disposable_database_cleanup_preserves_existing_database_on_collision(monkeypatch, tmp_path):
    endpoint = os.environ.get("CONVOY_TEST_POSTGRES_URL")
    if not endpoint:
        pytest.skip("requires the disposable PostgreSQL service")
    url = make_url(endpoint).set(drivername="postgresql")
    suffix = uuid.uuid4()
    monkeypatch.setattr(uuid, "uuid4", lambda: suffix)
    env, evidence, collision = {}, {}, {}
    with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as admin:
        original = admin.execute("SELECT oid FROM pg_database WHERE datname=current_database()").fetchone()
        with pytest.raises(RuntimeError, match="abort this run"):
            with disposable_postgres(env, evidence, tmp_path):
                name = evidence["database"]["name"]
                assert make_url(env["DATABASE_URL"]).database == name != url.database
                with pytest.raises(psycopg.errors.DuplicateDatabase):
                    with disposable_postgres({}, collision, tmp_path):
                        pytest.fail("a collision must never enter the existing database")
                assert collision["database"]["cleanup"] == "not_created"
                assert admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone()
                raise RuntimeError("abort this run")
        assert admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() is None
        assert admin.execute("SELECT oid FROM pg_database WHERE datname=current_database()").fetchone() == original
    assert evidence["database"]["cleanup"] == "dropped"
    assert "DATABASE_URL" not in env
