"""Promoted tool activities — long or side-effecting calls the workflow
schedules as their own activities.

`run_promoted_tool` executes one data-plane tool through the environment's
side-effect endpoint, which journals the idempotency key: a retried call with
the same key returns the recorded result instead of acting twice. Production
result bodies stay claim-checked behind refs; rehearsal stand-ins may also
return a bounded display result for the audit UI.

`run_sandbox_job` executes one job through the SandboxProvider seam. The
sandbox is per-run cache keyed by the last workspace snapshot (the truth):
a missing or lost sandbox is rebuilt from that snapshot before the job runs,
and the job journal inside the workspace makes a repeated idempotency key a
no-op. Every job ends with a fresh snapshot the workflow carries forward.

TODO: short-lived STS credential minting scoped to the run's tenant/env
data namespace for materializing protected inputs — the sandbox itself
stays credential-free either way.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import httpx
from botocore.exceptions import ClientError
from pydantic import BaseModel
from temporalio import activity

from convoy_core import ArtifactRef, SandboxHandle, SandboxJob
from convoy_runtime.activities import names
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.promoted import (
    PromotedToolOutcome,
    PromotedToolRequest,
    SandboxHibernateOutcome,
    SandboxHibernateRequest,
    SandboxJobOutcome,
    SandboxJobRequest,
    SandboxRestoreOutcome,
    SandboxRestoreRequest,
)
from convoy_runtime.providers.sandbox import SandboxLostError, SandboxProvider

_BROWSER_CLI_SOURCE = (
    Path(__file__).parents[1] / "providers" / "sandbox_browser_cli.py"
).read_text()

_SIMULATED_RESULT_LIMIT = 8_192


def _bounded_simulated_result(endpoint: str, result: Any) -> Any | None:
    """Return small stand-in output for display, never a live connector body."""
    if "/simulated-data-plane" not in endpoint:
        return None
    encoded = json.dumps(result, sort_keys=True, default=str)
    if len(encoded) <= _SIMULATED_RESULT_LIMIT:
        return result
    return {
        "truncated": True,
        "preview": encoded[:_SIMULATED_RESULT_LIMIT],
    }


class PromotedToolActivities:
    def __init__(
        self,
        store: ArtifactStore,
        *,
        stub_env_url: str,
        environments_internal_token: str = "",
        completion_delay_seconds: float = 0.0,
    ) -> None:
        self._store = store
        self._stub_env_url = stub_env_url.rstrip("/")
        self._environments_internal_token = environments_internal_token
        # Test knob: widens the window between the side effect landing and
        # the activity completing, so chaos suites can kill the worker inside
        # it deterministically. Zero in production paths.
        self._completion_delay_seconds = completion_delay_seconds

    @activity.defn(name=names.RUN_PROMOTED_TOOL)
    async def run_promoted_tool(self, request: PromotedToolRequest) -> PromotedToolOutcome:
        call = request.call
        activity.heartbeat({"tool_id": call.tool_id, "phase": "start"})
        args: dict[str, Any] = {}
        if call.args_ref is not None:
            args = await self._store.get_json(call.args_ref)
        endpoint = request.endpoint_url.rstrip("/") or self._stub_env_url
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(
                f"{endpoint}/effects/{call.tool_id}",
                json={
                    "idempotency_key": call.idempotency_key,
                    "run_id": request.run_id,
                    "args": args,
                },
                headers=(
                    {"X-Convoy-Internal": self._environments_internal_token}
                    if self._environments_internal_token
                    else None
                ),
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        result_ref = await self._store.put_json(
            f"runs/{request.run_id}/promoted/{call.idempotency_key}.json",
            {
                "tool_id": call.tool_id,
                "idempotency_key": call.idempotency_key,
                "result": payload.get("result"),
                "replayed": bool(payload.get("replayed", False)),
            },
        )
        activity.heartbeat({"tool_id": call.tool_id, "phase": "effect-recorded"})
        if self._completion_delay_seconds > 0:
            await asyncio.sleep(self._completion_delay_seconds)
        return PromotedToolOutcome(
            tool_id=call.tool_id,
            idempotency_key=call.idempotency_key,
            result_ref=result_ref,
            replayed=bool(payload.get("replayed", False)),
            simulated_result=_bounded_simulated_result(endpoint, payload.get("result")),
        )


class SandboxJobActivities:
    def __init__(self, store: ArtifactStore, provider: SandboxProvider) -> None:
        self._store = store
        self._provider = provider
        # Per-run sandbox cache: the workspace is disposable, the snapshot in
        # the request is what a rebuild starts from.
        self._handles: dict[str, SandboxHandle] = {}

    @activity.defn(name=names.RUN_SANDBOX_JOB)
    async def run_sandbox_job(self, request: SandboxJobRequest) -> SandboxJobOutcome:
        call = request.call
        activity.heartbeat({"tool_id": call.tool_id, "phase": "start"})
        job = await self._build_job(request)

        handle = request.handle or self._handles.get(request.run_id)
        if handle is None:
            handle = await self._provider.create(
                request.template, request.snapshot_ref, request.browser
            )
        self._handles[request.run_id] = handle
        try:
            result = await self._provider.exec(handle, job)
        except SandboxLostError:
            # The cached sandbox is gone: rebuild from the last snapshot (the
            # truth) and re-run under the same idempotency key. The journal
            # inside the snapshot guarantees no doubled side effect.
            handle = await self._provider.create(
                request.template, request.snapshot_ref, request.browser
            )
            self._handles[request.run_id] = handle
            result = await self._provider.exec(handle, job)
        activity.heartbeat({"tool_id": call.tool_id, "phase": "executed"})

        snapshot_ref = await self._provider.snapshot(handle)
        result_ref = await self._store.put_json(
            f"runs/{request.run_id}/promoted/{call.idempotency_key}.json",
            {
                "tool_id": call.tool_id,
                "idempotency_key": call.idempotency_key,
                "result": result.model_dump(mode="json"),
            },
        )
        return SandboxJobOutcome(
            tool_id=call.tool_id,
            idempotency_key=call.idempotency_key,
            result=result,
            result_ref=result_ref,
            snapshot_ref=snapshot_ref,
            handle=handle,
        )

    @activity.defn(name=names.HIBERNATE_SANDBOX)
    async def hibernate_sandbox(self, request: SandboxHibernateRequest) -> SandboxHibernateOutcome:
        """Checkpoint the active workspace and release its compute.

        A marker is written before destroy. If the activity is retried after
        compute was already stopped, the marker supplies the exact checkpoint
        outcome and destroy is safely repeated instead of attempting another
        snapshot from a dead task.
        """

        marker_key = self._lifecycle_marker(request.run_id, "checkpoints", request.checkpoint_id)
        recorded = await self._read_marker(marker_key, SandboxHibernateOutcome)
        if recorded is not None:
            await self._provider.destroy(request.handle)
            self._handles.pop(request.run_id, None)
            return recorded

        try:
            snapshot_ref = await self._provider.snapshot(request.handle)
        except SandboxLostError:
            # A task may disappear immediately before a pause boundary. The
            # last completed-job snapshot remains authoritative; hibernation
            # can release the already-lost handle without losing that state.
            snapshot_ref = request.snapshot_ref

        outcome = SandboxHibernateOutcome(
            checkpoint_id=request.checkpoint_id,
            reason=request.reason,
            released_sandbox_id=request.handle.sandbox_id,
            snapshot_ref=snapshot_ref,
        )
        await self._store.put_json(marker_key, outcome.model_dump(mode="json"))
        await self._provider.destroy(request.handle)
        self._handles.pop(request.run_id, None)
        return outcome

    @activity.defn(name=names.RESTORE_SANDBOX)
    async def restore_sandbox(self, request: SandboxRestoreRequest) -> SandboxRestoreOutcome:
        """Allocate a fresh session from the latest checkpoint.

        The completion marker makes normal Temporal activity retries return
        the same opaque handle. ECS tasks also self-retire on their lease, so
        the tiny create-before-marker crash window cannot leak indefinitely.
        """

        marker_key = self._lifecycle_marker(request.run_id, "restores", request.restore_id)
        recorded = await self._read_marker(marker_key, SandboxRestoreOutcome)
        if recorded is not None:
            self._handles[request.run_id] = recorded.handle
            return recorded

        handle = await self._provider.create(
            request.template, request.snapshot_ref, request.browser
        )
        outcome = SandboxRestoreOutcome(
            restore_id=request.restore_id,
            handle=handle,
            snapshot_ref=request.snapshot_ref,
        )
        await self._store.put_json(marker_key, outcome.model_dump(mode="json"))
        self._handles[request.run_id] = handle
        return outcome

    @staticmethod
    def _lifecycle_marker(run_id: str, kind: str, operation_id: str) -> str:
        return f"runs/{run_id}/execution/{kind}/{operation_id}.json"

    async def _read_marker[MarkerT: BaseModel](
        self, key: str, model: type[MarkerT]
    ) -> MarkerT | None:
        try:
            raw = await self._store.get_json_at(key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return model.model_validate(raw)

    async def _build_job(self, request: SandboxJobRequest) -> SandboxJob:
        """The job spec comes claim-checked from the promoting turn: command,
        env, and input refs; the idempotency key is the call's."""
        spec: dict[str, Any] = {}
        if request.call.args_ref is not None:
            spec = await self._store.get_json(request.call.args_ref)
        if request.call.tool_id == "sandbox_browser":
            command = ["python3", "-c", _BROWSER_CLI_SOURCE, json.dumps(spec, sort_keys=True)]
        else:
            raw_command = spec.get("command")
            command = (
                [str(part) for part in cast("list[Any]", raw_command)]
                if isinstance(raw_command, list)
                else ["true"]
            )
        raw_env = spec.get("env")
        env = (
            {str(k): str(v) for k, v in cast("dict[Any, Any]", raw_env).items()}
            if isinstance(raw_env, dict)
            else {}
        )
        raw_inputs = spec.get("inputs")
        inputs = (
            [ArtifactRef.model_validate(entry) for entry in cast("list[Any]", raw_inputs)]
            if isinstance(raw_inputs, list)
            else []
        )
        return SandboxJob(
            idempotency_key=request.call.idempotency_key,
            command=command,
            env=env,
            inputs=inputs,
        )
