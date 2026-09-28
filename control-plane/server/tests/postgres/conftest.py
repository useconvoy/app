"""Every case owns a new PostgreSQL database; the configured database is never modified."""

from __future__ import annotations

import os
import uuid

import pytest
from convoy_server import config, db
from convoy_server.migrations import migrate_database
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture()
def postgres_database():
    endpoint = os.environ.get("CONVOY_TEST_POSTGRES_URL")
    if not endpoint:
        pytest.skip("set CONVOY_TEST_POSTGRES_URL to run real PostgreSQL qualification")
    url = make_url(endpoint)
    if url.get_backend_name() != "postgresql":
        pytest.fail("CONVOY_TEST_POSTGRES_URL must name a PostgreSQL endpoint")
    url = url.set(drivername="postgresql+psycopg")
    name = "convoy_test_" + uuid.uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        db.reset_engine()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def settings(postgres_database, tmp_path):
    settings = config.Settings(
        database_url=postgres_database,
        data_dir=tmp_path / "data",
        simulator=True,
        scheduler_inprocess=False,
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="admin-password-1",
        public_url="http://testserver",
        offline_after_s=90,
        grant_ttl_s=30,
    )
    db.reset_engine()
    config.set_settings(settings)
    migrate_database(settings)
    yield settings
    db.reset_engine()
