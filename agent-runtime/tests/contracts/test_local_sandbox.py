"""LocalSandboxProvider against the shared SandboxProvider contract battery
(fast lane: moto object store, temp workspace directory, no containers)."""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from moto import mock_aws
from sandbox_battery import SandboxHarness, SandboxProviderBattery

from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.sandbox import LocalSandboxProvider

pytestmark = pytest.mark.anyio


class TestLocalSandboxProvider(SandboxProviderBattery):
    @pytest.fixture
    async def harness(self, tmp_path: Path) -> AsyncIterator[SandboxHarness]:
        # A canary in the worker environment must never reach jobs.
        os.environ["CONVOY_CONTRACT_CANARY"] = "worker-secret"
        try:
            with mock_aws():
                store = ArtifactStore(
                    bucket="convoy-test",
                    region="us-east-1",
                    access_key="test",
                    secret_key="test",
                )
                await store.ensure_bucket()
                provider = LocalSandboxProvider(store, base_dir=tmp_path / "sandboxes")
                yield SandboxHarness(provider=provider, store=store)
        finally:
            del os.environ["CONVOY_CONTRACT_CANARY"]
