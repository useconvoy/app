"""Constants and helpers shared between the e2e conftest and e2e tests."""

import json
import os
import time
from typing import Any

import httpx

CONTROL_PLANE_PORT = 8700
CONTROL_PLANE_URL = f"http://localhost:{CONTROL_PLANE_PORT}"
# A second control plane + worker pair runs the Pydantic AI executor against
# the LiteLLM proxy -> mock-model, on its own task queue.
AI_CONTROL_PLANE_PORT = 8701
AI_CONTROL_PLANE_URL = f"http://localhost:{AI_CONTROL_PLANE_PORT}"
AI_TASK_QUEUE = "agent-runtime-ai"
# The opt-in live-smoke pair: same executor, real provider models through the
# proxy's live routes, keyed from the operator's environment.
LIVE_CONTROL_PLANE_PORT = 8703
LIVE_CONTROL_PLANE_URL = f"http://localhost:{LIVE_CONTROL_PLANE_PORT}"
LIVE_TASK_QUEUE = "agent-runtime-live"
DEV_TOKEN = "e2e-dev-token"
PG_APP_DSN = "postgresql://convoy_app:convoy_app@localhost:5433/convoy"
PG_ADMIN_DSN = "postgresql://convoy_admin:convoy_admin@localhost:5433/convoy"
LITELLM_URL = "http://localhost:4000"
LITELLM_MASTER_KEY = "sk-convoy-e2e-master-key"


def live_smoke_model() -> str | None:
    """The proxy route the live-smoke lane runs against, or None when no
    provider key is available (a key is required regardless of overrides —
    without one the lane must skip). CONVOY_LIVE_SMOKE_MODEL overrides the
    route (any model_name the proxy serves); otherwise the available key
    picks the default live route.
    """
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        return None
    explicit = os.environ.get("CONVOY_LIVE_SMOKE_MODEL")
    if explicit:
        return explicit
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "live-anthropic"
    return "live-openai"


def auth_headers(tenant: str = "tenant-e2e", actor: str = "e2e@convoy.test") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {DEV_TOKEN}",
        "X-Actor-Id": actor,
        "X-Tenant-Id": tenant,
    }


def collect_sse(
    api: httpx.Client,
    run_id: str,
    *,
    terminal: set[str],
    timeout: float = 120.0,
    after: int = 0,
) -> list[dict[str, Any]]:
    """Consume the run's SSE stream until a terminal event type arrives."""
    events: list[dict[str, Any]] = []
    with api.stream(
        "GET",
        f"/runs/{run_id}/events",
        params={"after": after},
        headers=auth_headers(),
        timeout=httpx.Timeout(timeout, read=timeout),
    ) as response:
        assert response.status_code == 200
        current_type: str | None = None
        deadline = time.monotonic() + timeout
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                raise TimeoutError(f"SSE stream did not reach {terminal} in {timeout}s")
            if line.startswith("event: "):
                current_type = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
                assert payload["type"] == current_type
                events.append(payload)
                if current_type in terminal:
                    return events
    raise AssertionError(f"SSE stream closed before a terminal event in {terminal}")


def wait_status(api: httpx.Client, run_id: str, statuses: set[str], timeout: float = 90.0) -> str:
    """Poll the projection-backed run view until it reaches one of the
    wanted statuses."""
    deadline = time.monotonic() + timeout
    last = "<none>"
    while time.monotonic() < deadline:
        response = api.get(f"/runs/{run_id}", headers=auth_headers())
        assert response.status_code == 200
        last = response.json()["status"]
        if last in statuses:
            return last
        time.sleep(0.2)
    raise TimeoutError(f"run {run_id} never reached {statuses}; last status {last!r}")
