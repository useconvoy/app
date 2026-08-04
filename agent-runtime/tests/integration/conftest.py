"""e2e-lane fixtures: bootstrap compose services, run workers + control planes.

The compose stack (Temporal dev, Postgres+RLS, MinIO, mock-model, stub-env,
LiteLLM proxy) is brought up by `make e2e` before pytest runs. These fixtures
bootstrap the bucket/schema and launch two real worker + control-plane pairs:
the scripted executor on the default task queue, and the Pydantic AI executor
(through LiteLLM -> mock-model) on its own queue.
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
    AI_CONTROL_PLANE_PORT,
    AI_CONTROL_PLANE_URL,
    AI_TASK_QUEUE,
    CONTROL_PLANE_PORT,
    CONTROL_PLANE_URL,
    DEV_TOKEN,
    LITELLM_MASTER_KEY,
    LITELLM_URL,
    PG_ADMIN_DSN,
    PG_APP_DSN,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "convoy_runtime" / "projections" / "schema.sql"
)


def _base_environ() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "CONVOY_CODEC_KEY_B64": TEST_CODEC_KEY_B64,  # codec ON in compose too
            "CONVOY_TEMPORAL_HOST": "localhost:7233",
            "CONVOY_TEMPORAL_NAMESPACE": "default",
            "CONVOY_PG_DSN": PG_APP_DSN,
            "CONVOY_S3_ENDPOINT": "http://localhost:9000",
            "CONVOY_S3_BUCKET": "convoy-artifacts",
            "CONVOY_S3_ACCESS_KEY": "convoy",
            "CONVOY_S3_SECRET_KEY": "convoy-secret-key",
            "CONVOY_STUB_ENV_URL": "http://localhost:8902",
            "CONVOY_DEV_TOKEN": DEV_TOKEN,
            "LITELLM_BASE_URL": LITELLM_URL,
            "LITELLM_MASTER_KEY": LITELLM_MASTER_KEY,
        }
    )
    return env


def _scripted_environ() -> dict[str, str]:
    env = _base_environ()
    # Slow scripted turns slightly so pause/land land mid-run deterministically.
    env["CONVOY_SCRIPTED_TURN_DELAY"] = "0.4"
    env["CONVOY_TURN_EXECUTOR"] = "scripted"
    return env


def _ai_environ() -> dict[str, str]:
    env = _base_environ()
    env["CONVOY_TURN_EXECUTOR"] = "pydantic_ai"
    env["CONVOY_TASK_QUEUE"] = AI_TASK_QUEUE
    env["CONVOY_DEFAULT_MODEL"] = "mock-fallback"
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


def _spawn(args: list[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _control_plane_cmd(port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "convoy_runtime.control_plane.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]


@pytest.fixture(scope="session")
def e2e_stack() -> Iterator[dict[str, str]]:
    env = _scripted_environ()
    asyncio.run(_bootstrap_async())

    procs: list[subprocess.Popen[bytes]] = []
    worker = _spawn([sys.executable, "-m", "convoy_runtime.worker"], env)
    procs.append(worker)
    ai_worker = _spawn([sys.executable, "-m", "convoy_runtime.worker"], _ai_environ())
    procs.append(ai_worker)
    procs.append(_spawn(_control_plane_cmd(CONTROL_PLANE_PORT), env))
    procs.append(_spawn(_control_plane_cmd(AI_CONTROL_PLANE_PORT), _ai_environ()))
    try:
        _wait_http_ready(f"{CONTROL_PLANE_URL}/openapi.json", timeout=60)
        _wait_http_ready(f"{AI_CONTROL_PLANE_URL}/openapi.json", timeout=60)
        for proc in (worker, ai_worker):
            if proc.poll() is not None:
                raise RuntimeError(f"worker exited early: {_drain(proc)}")
        yield env
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
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


@pytest.fixture
def api_ai(e2e_stack: dict[str, str]) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=AI_CONTROL_PLANE_URL, timeout=60.0) as client:
        yield client
