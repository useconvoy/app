from pathlib import Path
from typing import Any

import pytest
from moto import mock_aws

from convoy_core import BrowserRuntimeConfig, SandboxJob
from convoy_runtime.providers import sandbox as sandbox_module
from convoy_runtime.providers.artifact_store import ArtifactStore

pytestmark = pytest.mark.anyio


async def test_local_provider_injects_browser_session_and_checkpoints_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instances: list[Any] = []

    class FakeBrowser:
        def __init__(self, workspace: Path, policy: Any) -> None:
            self.workspace = workspace
            self.enabled = bool(policy and policy.enabled)
            self.job_env = {"CONVOY_CDP_URL": "http://127.0.0.1:9222"}
            self.calls: list[str] = []
            instances.append(self)

        def start(self) -> None:
            self.calls.append("start")
            profile = self.workspace / ".convoy/browser/profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "Cookies").write_text("session")

        def stop_browser(self) -> None:
            self.calls.append("stop")

        def close(self) -> None:
            self.calls.append("close")

    monkeypatch.setattr(sandbox_module, "BrowserRuntime", FakeBrowser)
    with mock_aws():
        store = ArtifactStore(
            bucket="convoy-test", region="us-east-1", access_key="t", secret_key="t"
        )
        await store.ensure_bucket()
        provider = sandbox_module.LocalSandboxProvider(store, base_dir=tmp_path / "sandboxes")
        handle = await provider.create(
            "browser",
            None,
            BrowserRuntimeConfig(allowedDomains=["example.com"]),
        )
        result = await provider.exec(
            handle,
            SandboxJob(
                idempotency_key="browser-env",
                command=["sh", "-c", 'printf %s "$CONVOY_CDP_URL"'],
            ),
        )
        assert result.stdout_ref is not None
        assert await store.get_bytes(result.stdout_ref) == b"http://127.0.0.1:9222"
        snapshot = await provider.snapshot(handle)
        assert instances[0].calls == ["start", "stop", "start"]

        await provider.destroy(handle)
        assert instances[0].calls[-1] == "close"

        restored = await provider.create(
            "browser",
            snapshot,
            BrowserRuntimeConfig(allowedDomains=["example.com"]),
        )
        root = provider._workspace(restored.sandbox_id)  # pyright: ignore[reportPrivateUsage]
        assert (root / ".convoy/browser/profile/Cookies").read_text() == "session"
        await provider.destroy(restored)


async def test_browser_cli_job_is_constructed_by_the_activity() -> None:
    """The promoted browser grant executes the owned CDP client, not agent command input."""

    class MemoryStore:
        async def get_json(self, _ref: Any) -> dict[str, Any]:
            return {"action": "navigate", "url": "https://example.com"}

    from convoy_core import ArtifactRef, ToolCallRequest
    from convoy_runtime.activities.promoted import SandboxJobActivities
    from convoy_runtime.providers.promoted import SandboxJobRequest

    ref = ArtifactRef(bucket="b", key="args", size_bytes=1, sha256="0" * 64)
    request = SandboxJobRequest(
        run_id="run",
        call=ToolCallRequest(
            tool_id="sandbox_browser",
            activity="run_sandbox_job",
            args_ref=ref,
            idempotency_key="key",
        ),
        template="browser",
    )
    activities = SandboxJobActivities(MemoryStore(), object())  # type: ignore[arg-type]
    job = await activities._build_job(request)  # pyright: ignore[reportPrivateUsage]
    assert job.command[:2] == ["python3", "-c"]
    assert "class CdpClient" in job.command[2]
    assert "https://example.com" in job.command[3]
