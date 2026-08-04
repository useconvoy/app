"""e2e-lane fixtures: bootstrap compose services, run worker + control plane.

The compose stack (Temporal dev, Postgres+RLS, MinIO, mock-model, stub-env) is
brought up by `make e2e` before pytest runs. These fixtures bootstrap the
bucket/schema and launch the real worker and control-plane processes.
"""

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import LiteralString, cast

import httpx
import pytest
import urllib3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support.common import TEST_CODEC_KEY_B64
from _support.e2e import (
    CONTROL_PLANE_PORT,
    CONTROL_PLANE_URL,
    DEV_TOKEN,
    PG_ADMIN_DSN,
    PG_APP_DSN,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "convoy_runtime" / "projections" / "schema.sql"
)


def _e2e_environ() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "CONVOY_CODEC_KEY": TEST_CODEC_KEY_B64,  # codec ON in compose too (rule 9)
            "CONVOY_TEMPORAL_HOST": "localhost:7233",
            "CONVOY_TEMPORAL_NAMESPACE": "default",
            "CONVOY_PG_DSN": PG_APP_DSN,
            "CONVOY_S3_ENDPOINT": "http://localhost:9000",
            "CONVOY_S3_BUCKET": "convoy-artifacts",
            "CONVOY_S3_ACCESS_KEY": "convoy",
            "CONVOY_S3_SECRET_KEY": "convoy-secret-key",
            "CONVOY_STUB_ENV_URL": "http://localhost:8902",
            "CONVOY_DEV_TOKEN": DEV_TOKEN,
            # Slow scripted turns slightly so pause/land land mid-run deterministically.
            "CONVOY_SCRIPTED_TURN_DELAY": "0.4",
        }
    )
    return env


async def _bootstrap_async() -> None:
    import psycopg

    from convoy_runtime.providers.artifact_store import ArtifactStore

    store = ArtifactStore(
        bucket="convoy-artifacts",
        endpoint_url="http://localhost:9000",
        access_key="convoy",
        secret_key="convoy-secret-key",
    )
    await store.ensure_bucket()
    # Schema is applied by the postgres container's initdb; re-apply is a no-op
    # safety net for reused volumes (idempotent DDL).
    schema_sql = cast("LiteralString", SCHEMA_PATH.read_text())
    async with await psycopg.AsyncConnection.connect(PG_ADMIN_DSN) as conn:
        await conn.execute(schema_sql)
        await conn.commit()


@pytest.fixture(scope="session")
def e2e_stack() -> Iterator[dict[str, str]]:
    env = _e2e_environ()
    asyncio.run(_bootstrap_async())

    worker = subprocess.Popen(
        [sys.executable, "-m", "convoy_runtime.worker"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    control_plane = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "convoy_runtime.control_plane.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(CONTROL_PLANE_PORT),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_http_ready(f"{CONTROL_PLANE_URL}/openapi.json", timeout=60)
        if worker.poll() is not None:
            raise RuntimeError(f"worker exited early: {_drain(worker)}")
        yield env
    finally:
        for proc in (control_plane, worker):
            proc.terminate()
        for proc in (control_plane, worker):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _drain(proc: subprocess.Popen[bytes]) -> str:
    out = proc.stdout.read() if proc.stdout else b""
    return out.decode(errors="replace")[-2000:]


def _wait_http_ready(url: str, timeout: float) -> None:
    http = urllib3.PoolManager()
    deadline = time.monotonic() + timeout
    last_error = "no attempt"
    while time.monotonic() < deadline:
        try:
            response = http.request("GET", url, timeout=2.0)
            if response.status < 500:
                return
            last_error = f"status {response.status}"
        except Exception as err:
            last_error = str(err)
        time.sleep(0.5)
    raise TimeoutError(f"{url} not ready after {timeout}s: {last_error}")


@pytest.fixture
def api(e2e_stack: dict[str, str]) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=CONTROL_PLANE_URL, timeout=30.0) as client:
        yield client
