"""PydanticAITurnExecutor passes the shared contract battery against the
compose stack (LiteLLM proxy -> mock-model, MinIO, stub-env) — no real model
keys anywhere. Executor-specific behavior is covered on top: inline tool
dispatch with grant intersection, unknown/unauthorized tool rejection in the
transcript, policy-constrained fallback, per-tool heartbeats, and proxy-priced
cost capture.

Mock-model behavior is scripted through directives embedded in the run goal
(see docker/mock-model/server.py).
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from turn_executor_battery import ExecutorHarness, TurnExecutorBattery

from convoy_core import EnvironmentBinding, ModelGatewayConfig
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.grants import resolve_requested_tools
from convoy_runtime.providers.model_gateway import ModelGateway
from convoy_runtime.providers.model_keys import LiteLLMKeyProvider
from convoy_runtime.providers.pydantic_ai_turn import PydanticAITurnExecutor

pytestmark = [pytest.mark.anyio, pytest.mark.e2e]

LITELLM_URL = "http://localhost:4000"
LITELLM_MASTER_KEY = "sk-convoy-e2e-master-key"
STUB_ENV_URL = "http://localhost:8902"

GATEWAY = ModelGatewayConfig(
    endpoints={},
    approved_models=["mock-primary", "mock-fallback"],
    fallback_chains={"mock-primary": ["mock-fallback"]},
)


async def _fetch_binding(run_id: str) -> EnvironmentBinding:
    async with httpx.AsyncClient(timeout=10.0) as http:
        response = await http.get(
            f"{STUB_ENV_URL}/environments/stub-local", params={"tenant_id": "tenant-e2e"}
        )
        response.raise_for_status()
        return EnvironmentBinding.model_validate(response.json())


async def _make_harness(*, model: str, tools: list[str]) -> ExecutorHarness:
    run_id = f"run-contract-pai-{uuid.uuid4().hex[:8]}"
    store = ArtifactStore(
        bucket="convoy-artifacts",
        endpoint_url="http://localhost:9000",
        access_key="convoy",
        secret_key="convoy-secret-key",
    )
    await store.ensure_bucket()
    binding = await _fetch_binding(run_id)
    binding_ref = await store.put_json(
        f"runs/{run_id}/binding.json", binding.model_dump(mode="json")
    )
    from _support.common import fixture_run_state

    state = fixture_run_state(run_id=run_id, model=model)
    state.agent.tools = resolve_requested_tools(tools, binding.tool_registry)
    executor = PydanticAITurnExecutor(
        store=store,
        gateway=ModelGateway(GATEWAY),
        litellm_base_url=LITELLM_URL,
        key_provider=LiteLLMKeyProvider(base_url=LITELLM_URL, master_key=LITELLM_MASTER_KEY),
    )
    return ExecutorHarness(
        executor=executor,
        store=store,
        run_id=run_id,
        agent=state.agent,
        binding_ref=binding_ref,
    )


@pytest.fixture
async def harness() -> AsyncIterator[ExecutorHarness]:
    yield await _make_harness(model="mock-fallback", tools=["kb_lookup", "kb_search"])


class TestPydanticAIExecutorContract(TurnExecutorBattery):
    pass


async def test_inline_tool_calls_execute_and_are_recorded(
    harness: ExecutorHarness,
) -> None:
    result = await harness.execute(
        goal=('Find revenue [[call:kb_lookup {"key": "q3-revenue"}]] [[done:revenue recorded]]')
    )
    assert result.outcome == "step_done"
    transcript: dict[str, Any] = await harness.store.get_json(result.transcript_ref)
    tool_log: list[dict[str, Any]] = transcript["tool_log"]
    assert tool_log[0]["tool_id"] == "kb_lookup"
    assert tool_log[0]["status"] == "ok"
    assert tool_log[0]["args"] == {"key": "q3-revenue"}
    assert "1.2M" in str(tool_log[0]["result"])
    # The tool result flowed back through the model conversation.
    assert any(
        "1.2M" in str(part.get("content", ""))
        for message in transcript["messages"]
        for part in message.get("parts", [])
    )


async def test_per_tool_heartbeats_carry_turn_and_index(harness: ExecutorHarness) -> None:
    await harness.execute(
        goal=(
            '[[call:kb_lookup {"key": "runway"}]] [[call:kb_search {"query": "Q3"}]] '
            "[[done:both checked]]"
        ),
        turn=4,
    )
    assert harness.heartbeats == [
        {"turn": 4, "tool_index": 0},
        {"turn": 4, "tool_index": 1},
    ]


async def test_unknown_tool_is_rejected_cleanly_and_recorded(
    harness: ExecutorHarness,
) -> None:
    result = await harness.execute(goal='[[call:not_a_tool {"x": 1}]] [[done:moving on]]')
    # Clean rejection: the turn still completes, nothing crashed.
    assert result.outcome == "step_done"
    transcript: dict[str, Any] = await harness.store.get_json(result.transcript_ref)
    rejected = [e for e in transcript["tool_log"] if e["status"] == "rejected"]
    assert [e["tool_id"] for e in rejected] == ["not_a_tool"]


async def test_ungrated_registry_tool_is_unauthorized(harness: ExecutorHarness) -> None:
    # kb_delete exists in the environment registry but is promoted +
    # side-effecting, so grant intersection keeps it out of the inline set.
    result = await harness.execute(
        goal='[[call:kb_delete {"key": "q3-revenue"}]] [[done:tried anyway]]'
    )
    assert result.outcome == "step_done"
    transcript: dict[str, Any] = await harness.store.get_json(result.transcript_ref)
    rejected = [e for e in transcript["tool_log"] if e["status"] == "rejected"]
    assert [e["tool_id"] for e in rejected] == ["kb_delete"]
    assert not [e for e in transcript["tool_log"] if e["status"] == "ok"]


async def test_fallback_stays_within_approved_models_and_is_recorded() -> None:
    # mock-primary maps to an upstream that always fails; the chain's only
    # other member is mock-fallback, which serves the turn.
    harness = await _make_harness(model="mock-primary", tools=[])
    result = await harness.execute(goal="[[done:served by the fallback]]")
    assert result.model_used == "mock-fallback"
    assert result.model_used in GATEWAY.approved_models
    transcript: dict[str, Any] = await harness.store.get_json(result.transcript_ref)
    assert transcript["model_requested"] == "mock-primary"
    assert transcript["fallback"] == {"from": "mock-primary", "to": "mock-fallback"}


async def test_cost_comes_from_the_proxy_and_is_positive(harness: ExecutorHarness) -> None:
    result = await harness.execute(goal="[[done:cost check]]")
    # Fixed mock usage (12 in / 7 out) at the configured per-token prices.
    assert result.cost_usd > 0
    assert result.tokens.input_tokens == 12
    assert result.tokens.output_tokens == 7


async def test_continue_turn_resumes_from_working_transcript(
    harness: ExecutorHarness,
) -> None:
    first = await harness.execute(goal="[[say:starting the work]] [[done:finished the work]]")
    assert first.outcome == "continue"
    second = await harness.execute(turn=2, working_transcript_ref=first.transcript_ref)
    assert second.outcome == "step_done"
    transcript: dict[str, Any] = await harness.store.get_json(second.transcript_ref)
    # The resumed conversation contains the first turn's assistant text.
    assert any(
        "starting the work" in str(part.get("content", ""))
        for message in transcript["messages"]
        for part in message.get("parts", [])
    )
