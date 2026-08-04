"""SandboxProvider seam + the local (subprocess) implementation.

The execution substrate hides behind an owned interface: create a sandbox
from a template (optionally rehydrating a workspace snapshot), execute jobs,
snapshot the workspace to the artifact store, destroy. Workspaces are cache;
snapshots are truth — a lost sandbox rebuilds from its last snapshot and,
because the job journal lives inside the workspace (and therefore inside
every snapshot), re-running a journaled idempotency key is a no-op that
returns the recorded result instead of doubling the side effect.

`LocalSandboxProvider` runs jobs as local subprocesses in a temp workspace
directory — CI-safe, no container-in-container. Jobs are credential-free by
construction: they see a minimal environment plus the job's own variables,
with data materialized in and artifacts collected out. The dedicated-stack
implementation over per-run Fargate tasks lives in `sandbox_ecs` behind this
same Protocol and passes the identical contract battery.
"""

import asyncio
import hashlib
import io
import json
import shutil
import tarfile
import uuid
from pathlib import Path
from typing import Protocol

import anyio.to_thread

from convoy_core import ArtifactRef, SandboxHandle, SandboxJob, SandboxJobResult
from convoy_runtime.providers.artifact_store import ArtifactStore

PROVIDER_LOCAL = "local"

_DEFAULT_JOB_TIMEOUT_SECONDS = 60.0
_STREAM_CAP_BYTES = 1_000_000
_TIMEOUT_EXIT_CODE = 124

# Workspace layout: materialized inputs, collected outputs, and the job
# journal that makes idempotency keys survive sandbox loss via snapshots.
_INPUTS_DIR = "inputs"
_OUTPUTS_DIR = "outputs"
_JOURNAL_DIR = ".convoy/journal"

# Jobs run credential-free: a minimal base environment plus the job's own
# variables. Worker credentials never reach sandbox processes.
_BASE_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}


class SandboxLostError(RuntimeError):
    """The sandbox behind a handle no longer exists; rebuild from the last
    workspace snapshot and re-run the job with the same idempotency key."""


class SandboxProvider(Protocol):
    """The owned execution-substrate seam."""

    async def create(self, template: str, workspace: ArtifactRef | None) -> SandboxHandle:
        """New sandbox from `template`, rehydrating `workspace` when given."""
        ...

    async def exec(self, h: SandboxHandle, job: SandboxJob) -> SandboxJobResult:
        """Run one job. A journaled idempotency key returns the recorded
        result without re-executing. Raises `SandboxLostError` when the
        sandbox is gone."""
        ...

    async def snapshot(self, h: SandboxHandle) -> ArtifactRef:
        """Archive the whole workspace (journal included) as the new truth."""
        ...

    async def destroy(self, h: SandboxHandle) -> None:
        """Tear the sandbox down. The workspace is cache; snapshots persist."""
        ...


class LocalSandboxProvider:
    """Subprocess-backed sandbox over a temp workspace directory."""

    def __init__(
        self,
        store: ArtifactStore,
        *,
        base_dir: Path,
        key_prefix: str = "sandboxes",
    ) -> None:
        self._store = store
        self._base_dir = base_dir
        self._prefix = key_prefix.strip("/")

    # ------------------------------------------------------------- protocol

    async def create(self, template: str, workspace: ArtifactRef | None) -> SandboxHandle:
        sandbox_id = f"sbx-{uuid.uuid4().hex[:12]}"
        root = self._workspace(sandbox_id)
        root.mkdir(parents=True, exist_ok=False)
        if workspace is not None:
            data = await self._store.get_bytes(workspace)
            await anyio.to_thread.run_sync(self._extract, data, root)
        return SandboxHandle(sandbox_id=sandbox_id, provider=PROVIDER_LOCAL, template=template)

    async def exec(self, h: SandboxHandle, job: SandboxJob) -> SandboxJobResult:
        root = self._workspace(h.sandbox_id)
        journal_path = root / _JOURNAL_DIR / f"{job.idempotency_key}.json"
        journaled = await anyio.to_thread.run_sync(self._read_journal, root, journal_path)
        if journaled is not None:
            # This key already executed (possibly before a crash or sandbox
            # loss — the journal rides in every snapshot). No second effect.
            return journaled

        inputs = [(Path(ref.key).name, await self._store.get_bytes(ref)) for ref in job.inputs]
        await anyio.to_thread.run_sync(self._prepare_workspace, root, inputs)

        timeout = (
            job.timeout.total_seconds() if job.timeout is not None else _DEFAULT_JOB_TIMEOUT_SECONDS
        )
        process = await asyncio.create_subprocess_exec(
            *job.command,
            cwd=root,
            env={**_BASE_ENV, "HOME": str(root), **job.env},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            exit_code = process.returncode if process.returncode is not None else 1
        except TimeoutError:
            process.kill()
            await process.wait()
            stdout, stderr = b"", f"job timed out after {timeout}s".encode()
            exit_code = _TIMEOUT_EXIT_CODE

        job_prefix = f"{self._prefix}/{h.sandbox_id}/jobs/{job.idempotency_key}"
        stdout_ref = await self._store.put_bytes(
            f"{job_prefix}/stdout.txt", stdout[:_STREAM_CAP_BYTES], content_type="text/plain"
        )
        stderr_ref = await self._store.put_bytes(
            f"{job_prefix}/stderr.txt", stderr[:_STREAM_CAP_BYTES], content_type="text/plain"
        )
        produced = await anyio.to_thread.run_sync(self._collect_outputs, root)
        outputs: list[ArtifactRef] = []
        for rel, data in produced:
            outputs.append(await self._store.put_bytes(f"{job_prefix}/outputs/{rel}", data))

        result = SandboxJobResult(
            exit_code=exit_code, stdout_ref=stdout_ref, stderr_ref=stderr_ref, outputs=outputs
        )
        # Journal before returning: from here on this key never re-executes.
        await anyio.to_thread.run_sync(self._write_journal, journal_path, result)
        return result

    async def snapshot(self, h: SandboxHandle) -> ArtifactRef:
        root = self._workspace(h.sandbox_id)
        if not root.is_dir():
            raise SandboxLostError(f"sandbox {h.sandbox_id} has no workspace")
        data = await anyio.to_thread.run_sync(self._archive, root)
        digest = hashlib.sha256(data).hexdigest()
        return await self._store.put_bytes(
            f"{self._prefix}/{h.sandbox_id}/snapshot-{digest[:16]}.tar",
            data,
            content_type="application/x-tar",
        )

    async def destroy(self, h: SandboxHandle) -> None:
        await anyio.to_thread.run_sync(
            lambda: shutil.rmtree(self._workspace(h.sandbox_id), ignore_errors=True)
        )

    # -------------------------------------------------------------- helpers

    def _workspace(self, sandbox_id: str) -> Path:
        return self._base_dir / sandbox_id

    @staticmethod
    def _read_journal(root: Path, journal_path: Path) -> SandboxJobResult | None:
        if not root.is_dir():
            raise SandboxLostError(f"sandbox workspace {root} is gone")
        if not journal_path.is_file():
            return None
        return SandboxJobResult.model_validate_json(journal_path.read_text())

    @staticmethod
    def _write_journal(journal_path: Path, result: SandboxJobResult) -> None:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(json.dumps(result.model_dump(mode="json"), sort_keys=True))

    @staticmethod
    def _prepare_workspace(root: Path, inputs: list[tuple[str, bytes]]) -> None:
        """Materialize input data and reset the per-job outputs scratch.

        The outputs directory belongs to one job at a time: earlier jobs'
        outputs are already claim-checked artifacts, and durable workspace
        state lives outside it — so it starts empty for every execution.
        """
        for name, data in inputs:
            target = root / _INPUTS_DIR / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        outputs = root / _OUTPUTS_DIR
        shutil.rmtree(outputs, ignore_errors=True)
        outputs.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _collect_outputs(root: Path) -> list[tuple[str, bytes]]:
        """Everything the job produced under the outputs directory."""
        return [
            (str(Path(path).relative_to(root / _OUTPUTS_DIR)), Path(path).read_bytes())
            for path in sorted(LocalSandboxProvider._output_files(root))
        ]

    @staticmethod
    def _output_files(root: Path) -> list[str]:
        outputs = root / _OUTPUTS_DIR
        if not outputs.is_dir():
            return []
        return [str(p) for p in outputs.rglob("*") if p.is_file()]

    @staticmethod
    def _archive(root: Path) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for path in sorted(root.rglob("*")):
                tar.add(path, arcname=str(path.relative_to(root)), recursive=False)
        return buffer.getvalue()

    @staticmethod
    def _extract(data: bytes, root: Path) -> None:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
            tar.extractall(root, filter="data")
