"""Fixtures and helpers shared by unit, workflow, replay, and e2e tests."""

import base64
import hashlib
import os
from decimal import Decimal
from pathlib import Path

from temporalio.converter import DataConverter
from temporalio.testing import WorkflowEnvironment

from convoy_core import AgentSpec, ArtifactRef, BudgetState, RunPolicy, RunState
from convoy_runtime.codec import runtime_data_converter

# Fixed test key: replay of checked-in (encrypted) histories requires a stable
# key. Test-only material - real stacks provision per-stack keys as secrets.
TEST_CODEC_KEY = b"convoy-test-key-0123456789abcdef"
TEST_CODEC_KEY_B64 = base64.b64encode(TEST_CODEC_KEY).decode()
TEST_TASK_QUEUE = "agent-runtime-test"


def build_data_converter() -> DataConverter:
    return runtime_data_converter(TEST_CODEC_KEY)


def fixture_ref(key: str = "fixtures/fixture.json") -> ArtifactRef:
    data = b"fixture"
    return ArtifactRef(
        bucket="convoy-test",
        key=key,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        content_type="application/json",
    )


def fixture_run_state(
    run_id: str = "run-test-1",
    tenant_id: str = "tenant-test",
    *,
    budget_cap_usd: Decimal = Decimal("10"),
    policy: RunPolicy | None = None,
    model: str = "scripted-echo-1",
    max_children: int = 0,
) -> RunState:
    return RunState(
        run_id=run_id,
        tenant_id=tenant_id,
        environment_id="stub-local",
        binding_ref=fixture_ref(f"runs/{run_id}/binding.json"),
        status="planning",
        agent=AgentSpec(
            id=f"{run_id}-root",
            layer=0,
            max_children=max_children,
            model=model,
            tools=[],
            prompt_ref=fixture_ref(f"runs/{run_id}/prompts/root.json"),
        ),
        policy=policy or RunPolicy(require_plan_approval=False),
        plan=None,
        budget=BudgetState(cap_usd=budget_cap_usd),
        pinned_ref=fixture_ref(f"runs/{run_id}/pinned.json"),
    )


async def start_time_skipping_env() -> WorkflowEnvironment:
    """Time-skipping WorkflowEnvironment with the runtime data converter.

    Uses a pre-downloaded test-server binary when the default download host is
    unreachable (CONVOY_TEST_SERVER_PATH, then ~/.cache/convoy/temporal-test-server).
    """
    existing = os.environ.get("CONVOY_TEST_SERVER_PATH")
    if not existing:
        cached = Path.home() / ".cache" / "convoy" / "temporal-test-server"
        existing = str(cached) if cached.exists() else None
    return await WorkflowEnvironment.start_time_skipping(
        data_converter=build_data_converter(),
        test_server_existing_path=existing,
    )
