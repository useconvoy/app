"""Promoted tool activities — long or side-effecting calls the workflow
schedules as their own activities.

`run_promoted_tool` executes one data-plane tool through the environment's
side-effect endpoint, which journals the idempotency key: a retried call with
the same key returns the recorded result instead of acting twice. The result
body is claim-checked; only refs ride back through Temporal.

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
from typing import Any, cast

import httpx
from temporalio import activity

from convoy_core import ArtifactRef, SandboxHandle, SandboxJob
from convoy_runtime.activities import names
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.promoted import (
    PromotedToolOutcome,
    PromotedToolRequest,
    SandboxJobOutcome,
    SandboxJobRequest,
)
from convoy_runtime.providers.sandbox import SandboxLostError, SandboxProvider


class PromotedToolActivities:
    def __init__(
        self,
        store: ArtifactStore,
        *,
        stub_env_url: str,
        completion_delay_seconds: float = 0.0,
    ) -> None:
        self._store = store
        self._stub_env_url = stub_env_url.rstrip("/")
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
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(
                f"{self._stub_env_url}/effects/{call.tool_id}",
                json={
                    "idempotency_key": call.idempotency_key,
                    "run_id": request.run_id,
                    "args": args,
                },
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

        handle = self._handles.get(request.run_id)
        if handle is None:
            handle = await self._provider.create(request.template, request.snapshot_ref)
            self._handles[request.run_id] = handle
        try:
            result = await self._provider.exec(handle, job)
        except SandboxLostError:
            # The cached sandbox is gone: rebuild from the last snapshot (the
            # truth) and re-run under the same idempotency key. The journal
            # inside the snapshot guarantees no doubled side effect.
            handle = await self._provider.create(request.template, request.snapshot_ref)
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
        )

    async def _build_job(self, request: SandboxJobRequest) -> SandboxJob:
        """The job spec comes claim-checked from the promoting turn: command,
        env, and input refs; the idempotency key is the call's."""
        spec: dict[str, Any] = {}
        if request.call.args_ref is not None:
            spec = await self._store.get_json(request.call.args_ref)
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
