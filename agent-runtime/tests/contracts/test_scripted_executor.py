"""ScriptedTurnExecutor passes the shared contract battery (fast lane, moto),
plus its own determinism guarantees."""

from collections.abc import AsyncIterator

import pytest
from _support.common import fixture_run_state
from moto import mock_aws
from turn_executor_battery import ExecutorHarness, TurnExecutorBattery

from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.turn_executor import SCRIPTED_MODEL, ScriptedTurnExecutor

pytestmark = pytest.mark.anyio


@pytest.fixture
async def harness() -> AsyncIterator[ExecutorHarness]:
    with mock_aws():
        store = ArtifactStore(
            bucket="convoy-test", region="us-east-1", access_key="test", secret_key="test"
        )
        await store.ensure_bucket()
        state = fixture_run_state(run_id="run-contract-scripted")
        binding_ref = await store.put_json(f"runs/{state.run_id}/binding.json", {"stub": True})
        yield ExecutorHarness(
            executor=ScriptedTurnExecutor(store),
            store=store,
            run_id=state.run_id,
            agent=state.agent,
            binding_ref=binding_ref,
        )


class TestScriptedExecutorContract(TurnExecutorBattery):
    pass


async def test_scripted_results_are_fully_deterministic(harness: ExecutorHarness) -> None:
    first = await harness.execute()
    second = await harness.execute()
    assert first == second
    assert first.model_used == SCRIPTED_MODEL
    assert first.outcome == "step_done"
