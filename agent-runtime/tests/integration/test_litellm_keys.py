"""LiteLLM virtual keys against the real proxy: per-run keys work, hard
budget caps reject spend, and foreign keys are refused — the infra-side
backstop that holds even if workflow accounting were wrong."""

import time
import uuid
from decimal import Decimal
from typing import Any

import anyio
import httpx
import pytest
from _support.e2e import LITELLM_MASTER_KEY, LITELLM_URL

from convoy_runtime.providers.model_keys import LiteLLMKeyProvider

pytestmark = [pytest.mark.anyio, pytest.mark.e2e]


async def _completion(key: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as http:
        return await http.post(
            f"{LITELLM_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "mock-fallback",
                "messages": [{"role": "user", "content": "[[done:ok]]"}],
            },
        )


@pytest.fixture
def provider() -> LiteLLMKeyProvider:
    return LiteLLMKeyProvider(base_url=LITELLM_URL, master_key=LITELLM_MASTER_KEY)


async def test_run_key_provisioning_is_idempotent_and_capped(
    provider: LiteLLMKeyProvider,
) -> None:
    run_id = f"run-keys-{uuid.uuid4().hex[:8]}"
    key = await provider.ensure_run_key(run_id, Decimal("0.5"))
    again = await provider.ensure_run_key(run_id, Decimal("0.5"))
    assert key == again

    # A second provider instance (another worker) converges on the same key.
    other = LiteLLMKeyProvider(base_url=LITELLM_URL, master_key=LITELLM_MASTER_KEY)
    assert await other.ensure_run_key(run_id, Decimal("0.5")) == key

    async with httpx.AsyncClient(timeout=15.0) as http:
        info = await http.get(
            f"{LITELLM_URL}/key/info",
            params={"key": key},
            headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}"},
        )
    assert info.status_code == 200
    payload: dict[str, Any] = info.json()["info"]
    assert payload["key_alias"] == f"run-{run_id}"
    assert payload["max_budget"] == 0.5

    completion = await _completion(key)
    assert completion.status_code == 200
    assert "x-litellm-response-cost" in completion.headers


async def test_hard_budget_cap_rejects_spend(provider: LiteLLMKeyProvider) -> None:
    run_id = f"run-keys-cap-{uuid.uuid4().hex[:8]}"
    key = await provider.ensure_run_key(run_id, Decimal("0.00001"))

    first = await _completion(key)
    assert first.status_code == 200  # the first call takes spend past the cap

    # Spend writes are batched proxy-side; poll until the cap bites.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = await _completion(key)
        if response.status_code == 429:
            assert "budget" in response.text.lower()
            return
        await anyio.sleep(2)
    raise AssertionError("budget cap never rejected spend")


async def test_unknown_key_is_rejected(provider: LiteLLMKeyProvider) -> None:
    rogue = provider.run_key("run-never-provisioned")
    response = await _completion(rogue)
    assert response.status_code == 401
