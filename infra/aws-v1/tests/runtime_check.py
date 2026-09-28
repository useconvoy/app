"""Inside the AWS API image, against a fresh disposable local PostgreSQL 17.

No AWS client or test framework is needed. Credentials below belong only to the
private Docker network created by verify_runtime.py and are never AWS secrets.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app/aws-runtime")
import psycopg
from bootstrap import configure_roles
from convoy_server.config import Settings, set_settings
from convoy_server.migrations import migrate_database
from fastapi.testclient import TestClient
from hosted import app

password = os.environ["TEST_ADMIN_PASSWORD"]
with psycopg.connect(host="postgres", dbname="convoy", user="convoy_admin", password=password) as owner:
    owner.execute("CREATE ROLE limited_admin LOGIN CREATEROLE PASSWORD 'disposable-limited-admin'")
    owner.execute("ALTER DATABASE convoy OWNER TO limited_admin")
with psycopg.connect(host="postgres", dbname="convoy", user="limited_admin", password="disposable-limited-admin") as admin:
    configure_roles(admin, "disposable-migration-password", "disposable-runtime-password")

migrator = "postgresql+psycopg://convoy_migrator:disposable-migration-password@postgres/convoy"
runtime = "postgresql+psycopg://convoy_app:disposable-runtime-password@postgres/convoy"
result = migrate_database(Settings(database_url=migrator, data_dir=Path("/tmp/migration")))
assert result["ok"] and result["target_revision"] == "0002_evaluations"
set_settings(Settings(database_url=runtime, data_dir=Path("/tmp/api"), simulator=True,
                      bootstrap_admin_email="operator@example.com",
                      bootstrap_admin_password="disposable-bootstrap-password"))
with TestClient(app()) as client:
    assert client.get("/api/health").json()["db"]["backend"] == "postgresql"
    response = client.post("/api/v1/auth/login", json={"email":"operator@example.com", "password":"disposable-bootstrap-password"})
    assert response.status_code == 200, response.text
    response = client.post("/api/v1/projects", json={"name":"runtime-role-check"},
                           headers={"X-Convoy-Client":"web", "Idempotency-Key":"create-once"})
    assert response.status_code == 201, response.text
    assert client.get("/api/v1/projects").json()[0]["name"] == "runtime-role-check"
    for path in ("/api/v1/releases", "/api/v1/artifacts", "/api/v1/devices/test/chat", "/api/docs"):
        response = client.get(path)
        assert response.status_code == 404 and "CPU lifecycle staging" in response.text
with psycopg.connect(host="postgres", dbname="convoy", user="convoy_app", password="disposable-runtime-password") as client:
    try:
        client.execute("CREATE TABLE must_not_create (id int)")
    except psycopg.errors.InsufficientPrivilege:
        client.rollback()
    else:
        raise AssertionError("runtime role unexpectedly owns DDL")
with psycopg.connect(host="postgres", dbname="convoy", user="limited_admin", password="disposable-limited-admin") as admin:
    configure_roles(admin, "disposable-migration-password", "disposable-runtime-password")
print("passed: real migration + API DML under runtime role, denied runtime DDL, legacy routes blocked, repeatable grants")
