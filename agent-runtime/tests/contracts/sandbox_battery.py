"""Shared SandboxProvider contract battery.

Every provider implementation must pass these, driven through a small
`SandboxHarness` the concrete test module provides: the local subprocess
provider in the fast lane, the ECS provider later against real
infrastructure. The battery asserts seam-level behavior only: workspaces are
cache and snapshots are truth (a destroyed sandbox rebuilds from its last
snapshot), executions are journaled by idempotency key so a repeated key
never doubles a side effect — even across sandbox loss — jobs are
credential-free, streams and outputs are claim-checked, and timeouts surface
as job results rather than hangs.
"""

from dataclasses import dataclass
from datetime import timedelta

import pytest

from convoy_core import ArtifactRef, SandboxHandle, SandboxJob
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.sandbox import SandboxLostError, SandboxProvider


@dataclass
class SandboxHarness:
    """Everything a battery test needs to drive one provider implementation."""

    provider: SandboxProvider
    store: ArtifactStore
    template: str = "stub"

    async def create(self, workspace: ArtifactRef | None = None) -> SandboxHandle:
        return await self.provider.create(self.template, workspace)

    @staticmethod
    def job(
        key: str,
        script: str,
        *,
        inputs: list[ArtifactRef] | None = None,
        timeout_seconds: float | None = None,
    ) -> SandboxJob:
        return SandboxJob(
            idempotency_key=key,
            command=["sh", "-c", script],
            inputs=inputs or [],
            timeout=timedelta(seconds=timeout_seconds) if timeout_seconds is not None else None,
        )


class SandboxProviderBattery:
    """Inherit and provide a `harness` async fixture returning SandboxHarness."""

    async def test_exec_captures_streams_and_exit_code(self, harness: SandboxHarness) -> None:
        handle = await harness.create()
        result = await harness.provider.exec(
            handle, harness.job("key-streams", "echo out-line; echo err-line >&2; exit 3")
        )
        assert result.exit_code == 3
        assert result.stdout_ref is not None and result.stderr_ref is not None
        assert (await harness.store.get_bytes(result.stdout_ref)).decode() == "out-line\n"
        assert (await harness.store.get_bytes(result.stderr_ref)).decode() == "err-line\n"
        await harness.provider.destroy(handle)

    async def test_outputs_are_collected_as_artifacts(self, harness: SandboxHarness) -> None:
        handle = await harness.create()
        result = await harness.provider.exec(
            handle,
            harness.job("key-outputs", "mkdir -p outputs && printf convoy > outputs/report.txt"),
        )
        assert result.exit_code == 0
        assert len(result.outputs) == 1
        assert result.outputs[0].key.endswith("outputs/report.txt")
        assert await harness.store.get_bytes(result.outputs[0]) == b"convoy"
        await harness.provider.destroy(handle)

    async def test_inputs_are_materialized_into_the_workspace(
        self, harness: SandboxHarness
    ) -> None:
        data_ref = await harness.store.put_bytes("fixtures/ledger.csv", b"q3,1.2M\n")
        handle = await harness.create()
        result = await harness.provider.exec(
            handle,
            harness.job(
                "key-inputs",
                "mkdir -p outputs && cp inputs/ledger.csv outputs/copy.csv",
                inputs=[data_ref],
            ),
        )
        assert result.exit_code == 0
        assert await harness.store.get_bytes(result.outputs[0]) == b"q3,1.2M\n"
        await harness.provider.destroy(handle)

    async def test_jobs_are_credential_free(self, harness: SandboxHarness) -> None:
        # Worker process environment (where credentials would live) must not
        # leak into jobs; only the job's own variables and a minimal base do.
        handle = await harness.create()
        result = await harness.provider.exec(handle, harness.job("key-env", "env"))
        assert result.stdout_ref is not None
        env_dump = (await harness.store.get_bytes(result.stdout_ref)).decode()
        assert "CONVOY_CONTRACT_CANARY" not in env_dump
        assert "AWS_SECRET_ACCESS_KEY" not in env_dump
        await harness.provider.destroy(handle)

    async def test_repeated_key_is_a_no_op_returning_the_recorded_result(
        self, harness: SandboxHarness
    ) -> None:
        handle = await harness.create()
        script = "echo once >> effect.log && mkdir -p outputs && wc -l < effect.log > outputs/n"
        first = await harness.provider.exec(handle, harness.job("key-idem", script))
        second = await harness.provider.exec(handle, harness.job("key-idem", script))
        assert second == first  # replayed, not re-run
        assert (await harness.store.get_bytes(first.outputs[0])).strip() == b"1"
        # A different key does act again.
        third = await harness.provider.exec(handle, harness.job("key-idem-2", script))
        assert (await harness.store.get_bytes(third.outputs[0])).strip() == b"2"
        await harness.provider.destroy(handle)

    async def test_snapshot_rehydrate_round_trip(self, harness: SandboxHarness) -> None:
        handle = await harness.create()
        await harness.provider.exec(handle, harness.job("key-rt-1", "printf truth > state.txt"))
        snapshot = await harness.provider.snapshot(handle)
        await harness.provider.destroy(handle)

        rebuilt = await harness.create(workspace=snapshot)
        assert rebuilt.sandbox_id != handle.sandbox_id
        result = await harness.provider.exec(
            rebuilt, harness.job("key-rt-2", "mkdir -p outputs && cp state.txt outputs/state.txt")
        )
        assert result.exit_code == 0
        assert await harness.store.get_bytes(result.outputs[0]) == b"truth"
        await harness.provider.destroy(rebuilt)

    async def test_lost_sandbox_rebuilds_from_snapshot_without_double_side_effects(
        self, harness: SandboxHarness
    ) -> None:
        handle = await harness.create()
        script = "echo ran >> effect.log && mkdir -p outputs && wc -l < effect.log > outputs/n"
        first = await harness.provider.exec(handle, harness.job("key-loss", script))
        snapshot = await harness.provider.snapshot(handle)
        await harness.provider.destroy(handle)

        with pytest.raises(SandboxLostError):
            await harness.provider.exec(handle, harness.job("key-loss", script))

        # The journal rides the snapshot: the same key replays instead of
        # re-running, so the side effect stays single-fire across the loss.
        rebuilt = await harness.create(workspace=snapshot)
        replayed = await harness.provider.exec(rebuilt, harness.job("key-loss", script))
        assert replayed == first
        probe = await harness.provider.exec(
            rebuilt,
            harness.job("key-loss-probe", "mkdir -p outputs && wc -l < effect.log > outputs/n"),
        )
        assert (await harness.store.get_bytes(probe.outputs[0])).strip() == b"1"
        await harness.provider.destroy(rebuilt)

    async def test_timeout_surfaces_as_a_job_result(self, harness: SandboxHarness) -> None:
        handle = await harness.create()
        result = await harness.provider.exec(
            handle, harness.job("key-timeout", "sleep 30", timeout_seconds=0.5)
        )
        assert result.exit_code != 0
        assert result.stderr_ref is not None
        stderr = (await harness.store.get_bytes(result.stderr_ref)).decode()
        assert "timed out" in stderr
        await harness.provider.destroy(handle)

    async def test_destroy_is_idempotent(self, harness: SandboxHarness) -> None:
        handle = await harness.create()
        await harness.provider.destroy(handle)
        await harness.provider.destroy(handle)  # a second destroy is a no-op
