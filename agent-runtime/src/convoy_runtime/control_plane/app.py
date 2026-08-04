"""FastAPI control plane.

E-endpoints in M0 scope: POST /runs, POST /runs/{id}/pause|resume|land,
GET /runs/{id}, GET /runs/{id}/events (SSE). Workflow ID = run ID so client
retries are idempotent (DESIGN.md section 7). Reads never touch Temporal.

TODO(milestone-2): steer, plan approval, gate response endpoints.
TODO(milestone-4): clock advance endpoint (sandbox-kind + virtual clock only).
"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, cast

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from convoy_core import (
    AgentSpec,
    BudgetState,
    EnvironmentBinding,
    RunPolicy,
    RunState,
)
from convoy_runtime.codec import runtime_data_converter
from convoy_runtime.config import RuntimeConfig
from convoy_runtime.control_plane.auth import Actor, require_actor
from convoy_runtime.control_plane.models import (
    CreateRunRequest,
    CreateRunResponse,
    RunView,
    SignalResponse,
)
from convoy_runtime.projections.db import ProjectionsDB
from convoy_runtime.projections.store import ProjectionStore
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.workflows.agent_run import AgentRunWorkflow

_SSE_POLL_INTERVAL = 0.25
_SSE_HEARTBEAT_EVERY = 60  # polls between keep-alive comments
_TERMINAL_EVENTS = {"run_completed", "run_failed"}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    config = RuntimeConfig.from_env()
    app.state.config = config
    app.state.temporal = await Client.connect(
        config.temporal_host,
        namespace=config.temporal_namespace,
        data_converter=runtime_data_converter(config.codec_key),
    )
    app.state.store = ArtifactStore(
        bucket=config.s3_bucket,
        endpoint_url=config.s3_endpoint_url,
        region=config.s3_region,
        access_key=config.s3_access_key,
        secret_key=config.s3_secret_key,
    )
    db = ProjectionsDB(config.pg_dsn)
    await db.open()
    app.state.db = db
    app.state.projections = ProjectionStore(db)
    app.state.http = httpx.AsyncClient(timeout=10.0)
    try:
        yield
    finally:
        await app.state.http.aclose()
        await db.close()


app = FastAPI(title="Convoy Agent Runtime", lifespan=_lifespan)

ActorDep = Annotated[Actor, Depends(require_actor)]


def _projections(request: Request) -> ProjectionStore:
    return cast(ProjectionStore, request.app.state.projections)


def _temporal(request: Request) -> Client:
    return cast(Client, request.app.state.temporal)


def _config(request: Request) -> RuntimeConfig:
    return cast(RuntimeConfig, request.app.state.config)


async def _resolve_binding(
    request: Request, environment_id: str, tenant_id: str
) -> EnvironmentBinding:
    """Resolve environment_id -> binding via the environments/ registry seam
    (stub-env in M0 compose)."""
    config = _config(request)
    http = cast(httpx.AsyncClient, request.app.state.http)
    response = await http.get(
        f"{config.stub_env_url}/environments/{environment_id}",
        params={"tenant_id": tenant_id},
    )
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"unknown environment {environment_id!r}")
    response.raise_for_status()
    return EnvironmentBinding.model_validate(response.json())


@app.post("/runs", response_model=CreateRunResponse, status_code=202)
async def create_run(
    request: Request, body: CreateRunRequest, actor: ActorDep
) -> CreateRunResponse:
    config = _config(request)
    store = cast(ArtifactStore, request.app.state.store)
    projections = _projections(request)
    run_id = body.run_id or f"run-{uuid.uuid4().hex[:12]}"

    binding = await _resolve_binding(request, body.environment_id, actor.tenant_id)
    # Pin the resolved binding as an immutable snapshot for the run's lifetime
    # (DESIGN.md section 5, seam ownership).
    binding_ref = await store.put_json(
        f"runs/{run_id}/binding.json", binding.model_dump(mode="json")
    )
    pinned_ref = await store.put_json(
        f"runs/{run_id}/pinned.json",
        {"goal": body.goal, "success_criteria": body.success_criteria},
    )
    prompt_ref = await store.put_json(
        f"runs/{run_id}/prompts/root.json",
        {"prompt": "M0 scripted agent - no model prompt"},
    )

    state = RunState(
        run_id=run_id,
        tenant_id=actor.tenant_id,
        environment_id=body.environment_id,
        binding_ref=binding_ref,
        status="planning",
        agent=AgentSpec(
            id=f"{run_id}-root",
            layer=0,
            max_children=0,
            model="scripted-echo-1",
            tools=[],
            prompt_ref=prompt_ref,
        ),
        # TODO(milestone-2): approval flow; M0 runs unapproved by policy.
        policy=RunPolicy(require_plan_approval=False),
        plan=None,
        budget=BudgetState(cap_usd=body.budget_usd),
        pinned_ref=pinned_ref,
    )

    await projections.create_run(
        tenant_id=actor.tenant_id, run_id=run_id, goal=body.goal, status="planning"
    )
    client = _temporal(request)
    # Idempotent retry: WorkflowAlreadyStartedError means the run already exists.
    with contextlib.suppress(WorkflowAlreadyStartedError):
        await client.start_workflow(
            AgentRunWorkflow.run,
            args=[state, actor.actor_id],
            id=run_id,  # workflow ID = run ID -> idempotent retries (DESIGN §7)
            task_queue=config.task_queue,
        )
    return CreateRunResponse(run_id=run_id, status="planning")


async def _signal_run(request: Request, run_id: str, actor: Actor, signal: str) -> SignalResponse:
    projections = _projections(request)
    run = await projections.get_run(actor.tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    handle = _temporal(request).get_workflow_handle(run_id)
    try:
        await handle.signal(signal, actor.actor_id)
    except RPCError as err:
        if err.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=409, detail="run is not active") from err
        raise
    return SignalResponse(run_id=run_id, signal=signal)


@app.post("/runs/{run_id}/pause", response_model=SignalResponse, status_code=202)
async def pause_run(request: Request, run_id: str, actor: ActorDep) -> SignalResponse:
    return await _signal_run(request, run_id, actor, "pause")


@app.post("/runs/{run_id}/resume", response_model=SignalResponse, status_code=202)
async def resume_run(request: Request, run_id: str, actor: ActorDep) -> SignalResponse:
    return await _signal_run(request, run_id, actor, "resume")


@app.post("/runs/{run_id}/land", response_model=SignalResponse, status_code=202)
async def land_run(request: Request, run_id: str, actor: ActorDep) -> SignalResponse:
    return await _signal_run(request, run_id, actor, "land")


@app.get("/runs/{run_id}", response_model=RunView)
async def get_run(request: Request, run_id: str, actor: ActorDep) -> RunView:
    # Projections only - never a Temporal query on a user-facing path
    # (CLAUDE.md rule 11).
    run = await _projections(request).get_run(actor.tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return RunView(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        status=run.status,
        goal=run.goal,
        plan=run.plan,
        land_report=run.land_report,
    )


@app.get("/runs/{run_id}/events")
async def stream_events(
    request: Request, run_id: str, actor: ActorDep, after: int = 0
) -> StreamingResponse:
    projections = _projections(request)
    run = await projections.get_run(actor.tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))

    async def _stream() -> AsyncIterator[str]:
        cursor = after
        idle_polls = 0
        while True:
            events = await projections.get_events(actor.tenant_id, run_id, after_seq=cursor)
            terminal = False
            for event in events:
                cursor = event.seq
                data = json.dumps(event.model_dump(mode="json"), default=str)
                yield f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"
                terminal = terminal or event.type in _TERMINAL_EVENTS
            if terminal:
                return
            if events:
                idle_polls = 0
            else:
                idle_polls += 1
                if idle_polls % _SSE_HEARTBEAT_EVERY == 0:
                    yield ": keep-alive\n\n"
            if await request.is_disconnected():
                return
            await asyncio.sleep(_SSE_POLL_INTERVAL)

    return StreamingResponse(_stream(), media_type="text/event-stream")
