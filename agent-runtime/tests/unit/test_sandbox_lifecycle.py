"""Execution-session hibernation over the real local sandbox provider.

This is the fast proof of the same lifecycle the ECS provider implements:
state is checkpointed, original compute is destroyed, a different sandbox is
restored, and the workspace journal prevents duplicate work after resume.
"""

from pathlib import Path

import pytest
from moto import mock_aws

from convoy_core import ArtifactRef, ToolCallRequest
from convoy_runtime.activities.promoted import SandboxJobActivities
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.promoted import (
    SANDBOX_JOB_ACTIVITY,
    SandboxHibernateRequest,
    SandboxJobRequest,
    SandboxRestoreRequest,
)
from convoy_runtime.providers.sandbox import LocalSandboxProvider

pytestmark = pytest.mark.anyio


def _call(args_ref: ArtifactRef, key: str) -> ToolCallRequest:
    return ToolCallRequest(
        tool_id="sandbox_exec",
        activity=SANDBOX_JOB_ACTIVITY,
        args_ref=args_ref,
        idempotency_key=key,
    )


def _ignore_heartbeat(_details: object) -> None:
    return None


async def test_checkpoint_release_restore_uses_fresh_compute_and_preserves_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("convoy_runtime.activities.promoted.activity.heartbeat", _ignore_heartbeat)
    with mock_aws():
        store = ArtifactStore(
            bucket="convoy-test",
            region="us-east-1",
            access_key="test",
            secret_key="test",
        )
        await store.ensure_bucket()
        base_dir = tmp_path / "sandboxes"
        activities = SandboxJobActivities(store, LocalSandboxProvider(store, base_dir=base_dir))
        write_args = await store.put_json(
            "fixtures/write.json",
            {"command": ["sh", "-c", "echo once >> effect.log"]},
        )
        write = await activities.run_sandbox_job(
            SandboxJobRequest(
                run_id="run-lifecycle-1",
                call=_call(write_args, "write-once"),
                template="stub",
            )
        )
        assert write.handle is not None
        original_handle = write.handle
        original_id = original_handle.sandbox_id
        assert (base_dir / original_id / "effect.log").read_text() == "once\n"

        hibernate_request = SandboxHibernateRequest(
            run_id="run-lifecycle-1",
            checkpoint_id="checkpoint-1",
            reason="pause",
            handle=original_handle,
            snapshot_ref=write.snapshot_ref,
        )
        checkpoint = await activities.hibernate_sandbox(hibernate_request)
        assert checkpoint.snapshot_ref is not None
        assert not (base_dir / original_id).exists()

        # Retrying after destroy reads the operation marker and remains a
        # no-op instead of trying to snapshot already-released compute.
        assert await activities.hibernate_sandbox(hibernate_request) == checkpoint

        restore_request = SandboxRestoreRequest(
            run_id="run-lifecycle-1",
            restore_id="restore-2",
            template="stub",
            snapshot_ref=checkpoint.snapshot_ref,
        )
        restored = await activities.restore_sandbox(restore_request)
        assert restored.handle.sandbox_id != original_id
        assert await activities.restore_sandbox(restore_request) == restored

        # Replaying the pre-pause idempotency key must not append a second
        # line; the journal was part of the checkpoint restored above.
        replay = await activities.run_sandbox_job(
            SandboxJobRequest(
                run_id="run-lifecycle-1",
                call=_call(write_args, "write-once"),
                template="stub",
                snapshot_ref=checkpoint.snapshot_ref,
                handle=restored.handle,
            )
        )
        probe_args = await store.put_json(
            "fixtures/probe.json",
            {
                "command": [
                    "sh",
                    "-c",
                    "mkdir -p outputs && wc -l < effect.log > outputs/count.txt",
                ]
            },
        )
        probe = await activities.run_sandbox_job(
            SandboxJobRequest(
                run_id="run-lifecycle-1",
                call=_call(probe_args, "probe"),
                template="stub",
                snapshot_ref=replay.snapshot_ref,
                handle=replay.handle,
            )
        )
        assert probe.handle is not None
        assert probe.handle.sandbox_id == restored.handle.sandbox_id
        assert len(probe.result.outputs) == 1
        assert (await store.get_bytes(probe.result.outputs[0])).strip() == b"1"
