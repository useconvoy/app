"""Chaos-lane fixtures: a dedicated worker + control-plane pair whose worker
the tests can SIGKILL and restart at will.

Runs against the same compose services as the e2e lane (Temporal dev,
Postgres+RLS, MinIO, stub-env) but on its own task queue, its own control
plane port, and chaos-tuned limits: a tiny turn limit so runs hop via
continue_as_new, slowed scripted turns so a kill lands mid-turn
deterministically, a delayed promoted-tool completion so a kill lands after
the side effect but before the activity completes, and a known sandbox
workspace directory the tests can destroy.
"""

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import LiteralString, cast

import httpx
import pytest
import urllib3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support.common import TEST_CODEC_KEY_B64
from _support.e2e import DEV_TOKEN, PG_ADMIN_DSN, PG_APP_DSN

CHAOS_CONTROL_PLANE_PORT = 8702
CHAOS_CONTROL_PLANE_URL = f"http://localhost:{CHAOS_CONTROL_PLANE_PORT}"
CHAOS_TASK_QUEUE = "agent-runtime-chaos"
CHAOS_TURN_LIMIT = 3
CHAOS_TURN_DELAY_SECONDS = 1.5
CHAOS_PROMOTED_DELAY_SECONDS = 3.0

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "convoy_runtime" / "projections" / "schema.sql"
)


def _chaos_environ(sandbox_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "CONVOY_CODEC_KEY_B64": TEST_CODEC_KEY_B64,
            "CONVOY_TEMPORAL_HOST": "localhost:7233",
            "CONVOY_TEMPORAL_NAMESPACE": "default",
            "CONVOY_PG_DSN": PG_APP_DSN,
            "CONVOY_S3_ENDPOINT": "http://localhost:9000",
            "CONVOY_S3_BUCKET": "convoy-artifacts",
            "CONVOY_S3_ACCESS_KEY": "convoy",
            "CONVOY_S3_SECRET_KEY": "convoy-secret-key",
            "CONVOY_STUB_ENV_URL": "http://localhost:8902",
            "CONVOY_DEV_TOKEN": DEV_TOKEN,
            "CONVOY_TURN_EXECUTOR": "scripted",
            "CONVOY_TASK_QUEUE": CHAOS_TASK_QUEUE,
            "CONVOY_TURN_LIMIT": str(CHAOS_TURN_LIMIT),
            "CONVOY_SCRIPTED_TURN_DELAY": str(CHAOS_TURN_DELAY_SECONDS),
            "CONVOY_PROMOTED_TOOL_DELAY": str(CHAOS_PROMOTED_DELAY_SECONDS),
            "CONVOY_SANDBOX_DIR": str(sandbox_dir),
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
    schema_sql = cast("LiteralString", SCHEMA_PATH.read_text())
    async with await psycopg.AsyncConnection.connect(PG_ADMIN_DSN) as conn:
        await conn.execute(schema_sql)
        await conn.commit()


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


@dataclass
class ChaosStack:
    """The chaos pair plus the levers the tests pull."""

    env: dict[str, str]
    sandbox_dir: Path
    control_plane: subprocess.Popen[bytes]
    worker: subprocess.Popen[bytes] | None = None
    _spawned: list[subprocess.Popen[bytes]] = field(default_factory=lambda: [])

    def start_worker(self) -> None:
        assert self.worker is None or self.worker.poll() is not None, "worker already running"
        self.worker = subprocess.Popen(
            [sys.executable, "-m", "convoy_runtime.worker"],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._spawned.append(self.worker)

    def kill_worker(self) -> None:
        """SIGKILL — no graceful shutdown, exactly like a crashed host."""
        assert self.worker is not None
        self.worker.kill()
        self.worker.wait(timeout=10)

    def shutdown(self) -> None:
        for proc in [self.control_plane, *self._spawned]:
            if proc.poll() is None:
                proc.terminate()
        for proc in [self.control_plane, *self._spawned]:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="session")
def chaos_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ChaosStack]:
    sandbox_dir = tmp_path_factory.mktemp("chaos-sandboxes")
    env = _chaos_environ(sandbox_dir)
    asyncio.run(_bootstrap_async())
    control_plane = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "convoy_runtime.control_plane.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(CHAOS_CONTROL_PLANE_PORT),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    stack = ChaosStack(env=env, sandbox_dir=sandbox_dir, control_plane=control_plane)
    stack.start_worker()
    try:
        _wait_http_ready(f"{CHAOS_CONTROL_PLANE_URL}/openapi.json", timeout=60)
        worker = stack.worker
        assert worker is not None and worker.poll() is None, "chaos worker exited early"
        yield stack
    finally:
        stack.shutdown()


@pytest.fixture
def api(chaos_stack: ChaosStack) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=CHAOS_CONTROL_PLANE_URL, timeout=30.0) as client:
        yield client
