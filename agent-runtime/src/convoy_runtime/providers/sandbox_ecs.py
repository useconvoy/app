"""ECS (Fargate) SandboxProvider — the dedicated-stack execution substrate.

One sandbox = one per-run Fargate task RunTask'd on demand from the stack's
registered sandbox task family; there is no long-running sandbox service.
The task boots a runtime-owned runner and browser controller (both source
claim-checked to the artifact store and fetched by an integrity-pinned
bootstrap) that keep the workspace on ephemeral storage for the sandbox's
lifetime. Browser-enabled templates must supply Chromium; the canonical
Convoy sandbox image does.

The whole data plane is credential-free by construction. The sandbox holds
no IAM credentials — its task role carries an explicit deny — and instead
receives short-lived, capability-scoped presigned URLs minted by the trusted
worker: a GET-poll mailbox for control documents, per-job GET URLs for input
artifacts, PUT URLs for streams/results/snapshots, and a POST policy scoped
to the job's outputs prefix for files whose names only exist at runtime.
Data goes in and artifacts come out as bytes over those URLs; keys never
cross the boundary in either direction.

Contract parity with the local provider: identical artifact-store key layout,
identical workspace journal convention (the journal rides in the workspace
and therefore in every snapshot), so a repeated idempotency key replays the
recorded result — including after sandbox loss and rebuild — and the shared
contract battery drives both implementations unchanged.

Control documents are processed one at a time; the mailbox is last-writer-
wins, so a superseded submission surfaces as a poll timeout for its caller to
retry — the journal keeps the retry single-fire. Sandboxes retire themselves
when their mailbox capability expires or they sit idle past their lease, so a
leaked create can never outlive its lease.
"""

import asyncio
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import anyio.to_thread
import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from convoy_core import (
    ArtifactRef,
    BrowserRuntimeConfig,
    SandboxHandle,
    SandboxJob,
    SandboxJobResult,
)
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.sandbox import SandboxLostError

PROVIDER_ECS = "ecs"

_RUNNER_SOURCE_PATH = Path(__file__).with_name("sandbox_ecs_runner.py")
_BROWSER_SOURCE_PATH = Path(__file__).with_name("sandbox_browser.py")

# The bootstrap is the only code the task definition needs to run: it fetches
# the runner over its capability URL, refuses anything that does not hash to
# the pinned digest, and executes it. Small enough to ride RunTask overrides.
_BOOTSTRAP = (
    "import hashlib, os, pathlib, sys, urllib.request\n"
    "browser = urllib.request.urlopen(os.environ['CONVOY_SANDBOX_BROWSER_SOURCE_URL']).read()\n"
    "browser_digest = hashlib.sha256(browser).hexdigest()\n"
    "assert browser_digest == os.environ['CONVOY_SANDBOX_BROWSER_SOURCE_SHA256'], "
    "'browser source integrity mismatch'\n"
    "pathlib.Path('/tmp/convoy_sandbox_browser.py').write_bytes(browser)\n"
    "sys.path.insert(0, '/tmp')\n"
    "source = urllib.request.urlopen(os.environ['CONVOY_SANDBOX_RUNNER_URL']).read()\n"
    "digest = hashlib.sha256(source).hexdigest()\n"
    "assert digest == os.environ['CONVOY_SANDBOX_RUNNER_SHA256'], 'runner integrity mismatch'\n"
    "exec(compile(source, 'sandbox_ecs_runner.py', 'exec'))\n"
)

_DEFAULT_JOB_TIMEOUT_SECONDS = 60.0
_STREAM_CAP_BYTES = 1_000_000


def _default_ecs_client(region: str | None) -> Any:
    factory = cast("Callable[..., Any]", boto3.client)  # pyright: ignore[reportUnknownMemberType]
    return factory(
        "ecs",
        region_name=region,
        config=BotoConfig(
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )


class EcsSandboxProvider:
    """SandboxProvider over per-run Fargate tasks and presigned-URL I/O."""

    def __init__(
        self,
        store: ArtifactStore,
        *,
        cluster: str,
        task_family: str,
        subnets: list[str],
        security_group: str,
        container_name: str = "sandbox",
        key_prefix: str = "sandboxes",
        region: str | None = None,
        assign_public_ip: bool = False,
        sandbox_ttl_seconds: int = 28_800,
        create_timeout_seconds: float = 300.0,
        submit_timeout_seconds: float = 180.0,
        poll_interval_seconds: float = 1.0,
        ecs_client: Any | None = None,
    ) -> None:
        self._store = store
        self._cluster = cluster
        self._family = task_family
        self._subnets = subnets
        self._security_group = security_group
        self._assign_public_ip = assign_public_ip
        self._container = container_name
        self._prefix = key_prefix.strip("/")
        self._ttl = sandbox_ttl_seconds
        self._create_timeout = create_timeout_seconds
        self._submit_timeout = submit_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._ecs: Any = ecs_client if ecs_client is not None else _default_ecs_client(region)
        # Task-ARN cache only — sandbox_id -> ARN resolves statelessly via
        # the task's startedBy tag, so a fresh worker finds existing sandboxes.
        self._tasks: dict[str, str] = {}

    # ------------------------------------------------------------- protocol

    async def create(
        self,
        template: str,
        workspace: ArtifactRef | None,
        browser: BrowserRuntimeConfig | None = None,
    ) -> SandboxHandle:
        sandbox_id = f"sbx-{uuid.uuid4().hex[:12]}"
        runner_source = _RUNNER_SOURCE_PATH.read_bytes()
        runner_ref = await self._store.put_bytes(
            f"{self._sandbox_prefix(sandbox_id)}/control/runner.py",
            runner_source,
            content_type="text/x-python",
        )
        browser_ref = await self._store.put_bytes(
            f"{self._sandbox_prefix(sandbox_id)}/control/browser.py",
            _BROWSER_SOURCE_PATH.read_bytes(),
            content_type="text/x-python",
        )
        environment = [
            {"name": "CONVOY_SANDBOX_ID", "value": sandbox_id},
            {
                "name": "CONVOY_SANDBOX_RUNNER_URL",
                "value": self._store.presign_get(runner_ref, expires_seconds=900),
            },
            {"name": "CONVOY_SANDBOX_RUNNER_SHA256", "value": runner_ref.sha256},
            {
                "name": "CONVOY_SANDBOX_BROWSER_SOURCE_URL",
                "value": self._store.presign_get(browser_ref, expires_seconds=900),
            },
            {"name": "CONVOY_SANDBOX_BROWSER_SOURCE_SHA256", "value": browser_ref.sha256},
            {
                "name": "CONVOY_SANDBOX_BROWSER_POLICY",
                "value": (browser or BrowserRuntimeConfig()).model_dump_json(by_alias=True),
            },
            {
                "name": "CONVOY_SANDBOX_MAILBOX_URL",
                "value": self._store.presign_get_key(
                    self._mailbox_key(sandbox_id), expires_seconds=self._ttl
                ),
            },
            {"name": "CONVOY_SANDBOX_POLL_SECONDS", "value": str(self._poll_interval)},
            {"name": "CONVOY_SANDBOX_MAX_IDLE_SECONDS", "value": str(self._ttl)},
        ]
        if workspace is not None:
            environment.append(
                {
                    "name": "CONVOY_SANDBOX_WORKSPACE_URL",
                    "value": self._store.presign_get(workspace, expires_seconds=900),
                }
            )

        def _run_task() -> Any:
            return self._ecs.run_task(
                cluster=self._cluster,
                taskDefinition=self._family,
                count=1,
                launchType="FARGATE",
                startedBy=sandbox_id,
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": self._subnets,
                        "securityGroups": [self._security_group],
                        "assignPublicIp": "ENABLED" if self._assign_public_ip else "DISABLED",
                    }
                },
                overrides={
                    "containerOverrides": [
                        {
                            "name": self._container,
                            "command": ["python3", "-c", _BOOTSTRAP],
                            "environment": environment,
                        }
                    ]
                },
                tags=[
                    {"key": "convoy:sandbox-id", "value": sandbox_id},
                    {"key": "convoy:sandbox-template", "value": template},
                ],
                propagateTags="TASK_DEFINITION",
            )

        response = await anyio.to_thread.run_sync(_run_task)
        failures = list(response.get("failures") or [])
        if failures or not response.get("tasks"):
            raise RuntimeError(f"sandbox RunTask failed: {failures!r}")
        task_arn = str(response["tasks"][0]["taskArn"])
        self._tasks[sandbox_id] = task_arn
        await self._wait_until_running(sandbox_id, task_arn)
        return SandboxHandle(sandbox_id=sandbox_id, provider=PROVIDER_ECS, template=template)

    async def exec(self, h: SandboxHandle, job: SandboxJob) -> SandboxJobResult:
        task_arn = await self._require_running_task(h.sandbox_id)
        timeout = (
            job.timeout.total_seconds() if job.timeout is not None else _DEFAULT_JOB_TIMEOUT_SECONDS
        )
        url_ttl = int(timeout + self._submit_timeout + 300)
        job_prefix = f"{self._sandbox_prefix(h.sandbox_id)}/jobs/{job.idempotency_key}"
        outputs_url, outputs_fields = self._store.presign_post_prefix(
            f"{job_prefix}/outputs", expires_seconds=url_ttl
        )
        doc_id = uuid.uuid4().hex
        document = {
            "doc_id": doc_id,
            "kind": "job",
            "idempotency_key": job.idempotency_key,
            "command": job.command,
            "env": job.env,
            "timeout_seconds": timeout,
            "stream_cap_bytes": _STREAM_CAP_BYTES,
            "bucket": self._store.bucket,
            "inputs": [
                {
                    "name": Path(ref.key).name,
                    "url": self._store.presign_get(ref, expires_seconds=url_ttl),
                }
                for ref in job.inputs
            ],
            "stdout": self._put_target(f"{job_prefix}/stdout.txt", url_ttl, "text/plain"),
            "stderr": self._put_target(f"{job_prefix}/stderr.txt", url_ttl, "text/plain"),
            "outputs_post": {
                "url": outputs_url,
                "fields": outputs_fields,
                "key_prefix": f"{job_prefix}/outputs/",
            },
            "result": {
                "url": self._store.presign_put(
                    self._result_key(h.sandbox_id, doc_id),
                    expires_seconds=url_ttl,
                    content_type="application/json",
                )
            },
        }
        payload = await self._submit_and_await(
            h.sandbox_id,
            task_arn,
            doc_id,
            document,
            deadline_seconds=timeout + self._submit_timeout,
        )
        return SandboxJobResult.model_validate(payload["result"])

    async def snapshot(self, h: SandboxHandle) -> ArtifactRef:
        task_arn = await self._require_running_task(h.sandbox_id)
        token = uuid.uuid4().hex[:16]
        key = f"{self._sandbox_prefix(h.sandbox_id)}/snapshot-{token}.tar"
        doc_id = uuid.uuid4().hex
        document = {
            "doc_id": doc_id,
            "kind": "snapshot",
            "bucket": self._store.bucket,
            "key": key,
            "url": self._store.presign_put(
                key, expires_seconds=900, content_type="application/x-tar"
            ),
            "result": {
                "url": self._store.presign_put(
                    self._result_key(h.sandbox_id, doc_id),
                    expires_seconds=900,
                    content_type="application/json",
                )
            },
        }
        payload = await self._submit_and_await(
            h.sandbox_id, task_arn, doc_id, document, deadline_seconds=self._submit_timeout
        )
        return ArtifactRef(
            bucket=self._store.bucket,
            key=key,
            size_bytes=int(payload["size_bytes"]),
            sha256=str(payload["sha256"]),
            content_type="application/x-tar",
        )

    async def destroy(self, h: SandboxHandle) -> None:
        task_arn = await self._find_task(h.sandbox_id)
        self._tasks.pop(h.sandbox_id, None)
        if task_arn is None:
            return

        def _stop() -> None:
            self._ecs.stop_task(
                cluster=self._cluster, task=task_arn, reason="sandbox destroyed by the runtime"
            )

        await anyio.to_thread.run_sync(_stop)

    # -------------------------------------------------------------- helpers

    def _sandbox_prefix(self, sandbox_id: str) -> str:
        return f"{self._prefix}/{sandbox_id}"

    def _mailbox_key(self, sandbox_id: str) -> str:
        return f"{self._sandbox_prefix(sandbox_id)}/control/mailbox.json"

    def _result_key(self, sandbox_id: str, doc_id: str) -> str:
        return f"{self._sandbox_prefix(sandbox_id)}/control/results/{doc_id}.json"

    def _put_target(self, key: str, expires_seconds: int, content_type: str) -> dict[str, str]:
        return {
            "key": key,
            "url": self._store.presign_put(
                key, expires_seconds=expires_seconds, content_type=content_type
            ),
        }

    async def _find_task(self, sandbox_id: str) -> str | None:
        """The sandbox's task ARN, from cache or by its startedBy stamp —
        stateless, so any worker can locate any live sandbox."""
        cached = self._tasks.get(sandbox_id)
        if cached is not None:
            return cached

        def _list() -> Any:
            return self._ecs.list_tasks(
                cluster=self._cluster,
                startedBy=sandbox_id,
            )

        response = await anyio.to_thread.run_sync(_list)
        listed: list[Any] = list(response.get("taskArns") or [])
        arns = [str(arn) for arn in listed]
        if not arns:
            return None
        self._tasks[sandbox_id] = arns[0]
        return arns[0]

    async def _require_running_task(self, sandbox_id: str) -> str:
        task_arn = await self._find_task(sandbox_id)
        if task_arn is None or not await self._task_is_running(sandbox_id, task_arn):
            self._tasks.pop(sandbox_id, None)
            raise SandboxLostError(f"sandbox {sandbox_id} has no running task")
        return task_arn

    async def _task_is_running(self, sandbox_id: str, task_arn: str) -> bool:
        status = await self._task_status(task_arn)
        if status == "RUNNING":
            return True
        if status in (None, "STOPPED", "DEPROVISIONING", "DEACTIVATING", "STOPPING"):
            self._tasks.pop(sandbox_id, None)
        return False

    async def _task_status(self, task_arn: str) -> str | None:
        def _describe() -> Any:
            return self._ecs.describe_tasks(cluster=self._cluster, tasks=[task_arn])

        response = await anyio.to_thread.run_sync(_describe)
        tasks = list(response.get("tasks") or [])
        if not tasks:
            return None
        return str(tasks[0]["lastStatus"])

    async def _wait_until_running(self, sandbox_id: str, task_arn: str) -> None:
        deadline = time.monotonic() + self._create_timeout
        while time.monotonic() < deadline:
            status = await self._task_status(task_arn)
            if status == "RUNNING":
                return
            if status in (None, "STOPPED", "DEPROVISIONING", "DEACTIVATING"):
                self._tasks.pop(sandbox_id, None)
                raise RuntimeError(f"sandbox task {task_arn} stopped before reaching RUNNING")
            await asyncio.sleep(self._poll_interval)
        raise TimeoutError(f"sandbox task {task_arn} not RUNNING after {self._create_timeout}s")

    async def _submit_and_await(
        self,
        sandbox_id: str,
        task_arn: str,
        doc_id: str,
        document: dict[str, Any],
        *,
        deadline_seconds: float,
    ) -> dict[str, Any]:
        """Post one control document to the sandbox's mailbox and poll for its
        result. A sandbox that stops mid-wait surfaces as `SandboxLostError`
        so callers rebuild from the last snapshot and retry the same key."""
        await self._store.put_json(self._mailbox_key(sandbox_id), document)
        result_key = self._result_key(sandbox_id, doc_id)
        deadline = time.monotonic() + deadline_seconds
        liveness_checked = time.monotonic()
        while time.monotonic() < deadline:
            try:
                payload: dict[str, Any] = await self._store.get_json_at(result_key)
            except ClientError:
                payload = {}
            if payload:
                if payload.get("doc_id") != doc_id:
                    raise RuntimeError(
                        f"sandbox {sandbox_id} answered document {payload.get('doc_id')!r} "
                        f"while awaiting {doc_id!r}"
                    )
                return payload
            if time.monotonic() - liveness_checked >= 5.0:
                liveness_checked = time.monotonic()
                if not await self._task_is_running(sandbox_id, task_arn):
                    raise SandboxLostError(f"sandbox {sandbox_id} stopped while executing {doc_id}")
            await asyncio.sleep(self._poll_interval)
        raise TimeoutError(
            f"sandbox {sandbox_id} did not answer document {doc_id} within {deadline_seconds}s"
        )
