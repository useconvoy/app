"""Row-level security context checks against the real Postgres: a connection
without tenant context sees zero rows; a wrong tenant sees zero rows; writes
without context are rejected."""

import uuid
from typing import LiteralString

import httpx
import psycopg
import pytest
from _support.e2e import PG_APP_DSN, auth_headers

pytestmark = pytest.mark.e2e

TABLES = ("runs", "run_events", "run_steps")


def _seed_run(api: httpx.Client) -> str:
    run_id = f"run-rls-{uuid.uuid4().hex[:8]}"
    created = api.post(
        "/runs",
        json={"goal": "rls isolation check", "run_id": run_id},
        headers=auth_headers(),
    )
    assert created.status_code == 202
    # Wait until events and step rows exist for the run.
    import time

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
        if run["status"] == "completed" and run["steps"]:
            return run_id
        time.sleep(0.3)
    raise TimeoutError(f"run {run_id} did not complete in time")


_COUNT_QUERIES: dict[str, LiteralString] = {
    "runs": "SELECT count(*) FROM runs WHERE run_id = %s",
    "run_events": "SELECT count(*) FROM run_events WHERE run_id = %s",
    "run_steps": "SELECT count(*) FROM run_steps WHERE run_id = %s",
}


def _count(conn: psycopg.Connection, table: str, run_id: str) -> int:
    row = conn.execute(_COUNT_QUERIES[table], (run_id,)).fetchone()
    assert row is not None
    return int(row[0])


def test_no_tenant_context_sees_zero_rows(api: httpx.Client) -> None:
    run_id = _seed_run(api)
    with psycopg.connect(PG_APP_DSN) as conn:
        for table in TABLES:
            assert _count(conn, table, run_id) == 0, f"{table} leaked without context"


def test_wrong_tenant_sees_zero_rows(api: httpx.Client) -> None:
    run_id = _seed_run(api)
    with psycopg.connect(PG_APP_DSN) as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'tenant-other', false)")
        for table in TABLES:
            assert _count(conn, table, run_id) == 0, f"{table} leaked cross-tenant"


def test_right_tenant_sees_the_rows(api: httpx.Client) -> None:
    run_id = _seed_run(api)
    with psycopg.connect(PG_APP_DSN) as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'tenant-e2e', false)")
        assert _count(conn, "runs", run_id) == 1
        assert _count(conn, "run_events", run_id) > 0
        assert _count(conn, "run_steps", run_id) == 2


def test_write_without_tenant_context_is_rejected(api: httpx.Client) -> None:
    with psycopg.connect(PG_APP_DSN) as conn, pytest.raises(psycopg.errors.Error):
        conn.execute(
            "INSERT INTO runs (tenant_id, run_id, status, goal) VALUES (%s, %s, %s, %s)",
            ("tenant-e2e", f"run-rls-write-{uuid.uuid4().hex[:6]}", "planning", "x"),
        )
