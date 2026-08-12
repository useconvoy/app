"""FastAPI control plane.

External endpoints: POST /runs, POST /runs/{id}/pause|resume|land|steer,
POST /runs/{id}/plan/approve, POST /runs/{id}/steps/{sid}/respond,
POST /runs/{id}/clock/advance, GET /runs/{id}, GET /runs/{id}/events (SSE).
Workflow ID = run ID so client retries are idempotent. Every mutation signals
the workflow with the verified actor identity, which flows into the run's
events. Reads are served from Postgres projections and never touch Temporal.
The SSE stream is resumable: events carry their per-run seq as the SSE id,
and both the Last-Event-ID header and the `after` query parameter continue
from a cursor. Clock advances are guarded here and again in the workflow:
only sandbox-kind bindings running a virtual clock accept them.
"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, cast

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
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
    SteerMessage,
)
from convoy_runtime.carry import BindingFacts, RunCarry, RunTuning
from convoy_runtime.codec import runtime_data_converter
from convoy_runtime.config import RuntimeConfig
from convoy_runtime.control_plane.auth import Actor, require_actor
from convoy_runtime.control_plane.models import (
    AgentScheduleRequest,
    AgentScheduleView,
    ApprovePlanRequest,
    BudgetView,
    ClockAdvanceRequest,
    CreateRunRequest,
    CreateRunResponse,
    ExecutionSessionView,
    GateRespondRequest,
    RunListResponse,
    RunSummary,
    RunView,
    SignalResponse,
    SteerRequest,
    SteerResponse,
    StepView,
)
from convoy_runtime.projections.db import ProjectionsDB
from convoy_runtime.projections.store import ProjectionStore, RunProjection
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.grants import GrantValidationError, resolve_requested_tools
from convoy_runtime.providers.model_gateway import ModelGateway, ModelGatewayError
from convoy_runtime.schedule_template import (
    ScheduledRunTemplate,
    validate_schedule_spec,
)
from convoy_runtime.schedules import (
    delete_agent_schedule,
    describe_agent_schedule,
    upsert_agent_schedule,
)
from convoy_runtime.signals import ClockAdvance, GateResponse, PlanApprovalDecision
from convoy_runtime.workflows.agent_run import AgentRunWorkflow

_SSE_POLL_INTERVAL = 0.25
_SSE_HEARTBEAT_EVERY = 60  # polls between keep-alive comments
_TERMINAL_EVENTS = {"run_completed", "run_failed"}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    config = RuntimeConfig.from_env()
    app.state.config = config
    # Bad gateway config (chains outside the approved set) fails here at boot.
    app.state.gateway = ModelGateway(config.model_gateway)
    app.state.temporal = await Client.connect(
        config.temporal_host,
        namespace=config.temporal_namespace,
        data_converter=runtime_data_converter(config.codec_key),
        tls=config.temporal_tls,
    )
    app.state.store = ArtifactStore(
        bucket=config.s3_bucket,
        endpoint_url=config.s3_endpoint_url,
        region=config.s3_region,
        access_key=config.s3_access_key,
        secret_key=config.s3_secret_key,
        role_arn=config.data_access_role_arn,
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


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


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
    (stub-env in the local stack)."""
    config = _config(request)
    http = cast(httpx.AsyncClient, request.app.state.http)
    registry_environment_id, binding_kind = _environment_registry_target(environment_id)
    response = await http.get(
        f"{config.stub_env_url}/environments/{registry_environment_id}",
        params={"tenant_id": tenant_id, "kind": binding_kind},
        headers=(
            {"X-Convoy-Internal": config.environments_internal_token}
            if config.environments_internal_token
            else None
        ),
    )
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"unknown environment {environment_id!r}")
    response.raise_for_status()
    return EnvironmentBinding.model_validate(response.json())


def _environment_registry_target(environment_id: str) -> tuple[str, str]:
    """Translate a console binding alias into the frozen registry seam.

    The registry selects rehearsal with ``?kind=sandbox`` while the website
    stores a convenient ``<environment>/sandbox`` reference. Keeping the
    translation at the control-plane edge means RunState and projections can
    retain the exact environment target the user selected.
    """

    suffix = "/sandbox"
    if environment_id.endswith(suffix):
        registry_id = environment_id[: -len(suffix)]
        if registry_id:
            return registry_id, "sandbox"
    return environment_id, "production"


@app.post("/runs", response_model=CreateRunResponse, status_code=202)
async def create_run(
    request: Request, body: CreateRunRequest, actor: ActorDep
) -> CreateRunResponse:
    config = _config(request)
    store = cast(ArtifactStore, request.app.state.store)
    gateway = cast(ModelGateway, request.app.state.gateway)
    projections = _projections(request)
    run_id = body.run_id or f"run-{uuid.uuid4().hex[:12]}"

    binding = await _resolve_binding(request, body.environment_id, actor.tenant_id)

    # Requested tools resolve against the environment registry now, so an
    # unknown tool or an invalid inline+side-effecting grant is a clean 422
    # at the API instead of a surprise mid-run.
    try:
        grants = resolve_requested_tools(body.tools, binding.tool_registry)
    except GrantValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    model = body.model or config.default_model
    if config.turn_executor == "pydantic_ai":
        try:
            gateway.resolve_chain(model)
        except ModelGatewayError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    # A fixture fan-out group must fit under the agent's child cap — reject
    # at the API instead of a deterministic spawn failure mid-run.
    if body.fixture_fanout is not None and body.fixture_fanout.size > body.max_children:
        raise HTTPException(
            status_code=422,
            detail=(
                f"fixture_fanout.size {body.fixture_fanout.size} exceeds "
                f"max_children {body.max_children}"
            ),
        )

    # Pin the resolved binding as an immutable snapshot for the run's lifetime.
    binding_ref = await store.put_json(
        f"runs/{run_id}/binding.json", binding.model_dump(mode="json")
    )
    pinned_payload: dict[str, object] = {
        "goal": body.goal,
        "success_criteria": body.success_criteria,
    }
    if body.instructions:
        pinned_payload["instructions"] = body.instructions
    if body.fixture_gates:
        pinned_payload["fixture_gates"] = {
            step_id: gate.model_dump(mode="json") for step_id, gate in body.fixture_gates.items()
        }
    if body.fixture_fanout is not None:
        pinned_payload["fixture_fanout"] = body.fixture_fanout.model_dump(mode="json")
    pinned_ref = await store.put_json(f"runs/{run_id}/pinned.json", pinned_payload)
    prompt_ref = await store.put_json(
        f"runs/{run_id}/prompts/root.json",
        {"prompt": "Convoy run agent - execute the plan step by step."},
    )

    # Plan approval defaults off for sandbox-kind environments and on for
    # production ones; a policy that sets the field explicitly is honored as
    # given, while partial policies pick up the environment default.
    default_approval = binding.kind != "sandbox"
    if body.policy is None:
        policy = RunPolicy(require_plan_approval=default_approval)
    elif "require_plan_approval" in body.policy.model_fields_set:
        policy = body.policy
    else:
        policy = body.policy.model_copy(update={"require_plan_approval": default_approval})

    state = RunState(
        run_id=run_id,
        tenant_id=actor.tenant_id,
        environment_id=body.environment_id,
        binding_ref=binding_ref,
        status="planning",
        agent=AgentSpec(
            id=f"{run_id}-root",
            layer=0,
            max_children=body.max_children,
            model=model,
            tools=grants,
            prompt_ref=prompt_ref,
        ),
        policy=policy,
        plan=None,
        budget=BudgetState(cap_usd=body.budget_usd),
        pinned_ref=pinned_ref,
    )

    # The initial runtime carry: the binding facts the workflow branches on
    # (clock, kind, template) and the deployment's durability tuning, all
    # recorded as workflow input so replay sees the same limits.
    carry = RunCarry(
        binding=BindingFacts(
            kind=binding.kind,
            clock=binding.clock,
            sandbox_template=binding.sandbox_template,
            browser=binding.browser,
            connector_endpoints={
                name: str(url) for name, url in binding.connector_endpoints.items()
            },
        ),
        tuning=RunTuning(
            turn_limit=config.turn_limit,
            midstep_compaction_tokens=config.midstep_compaction_tokens,
        ),
    )

    await projections.create_run(
        tenant_id=actor.tenant_id,
        run_id=run_id,
        goal=body.goal,
        environment_id=body.environment_id,
        status="planning",
        budget_cap_usd=body.budget_usd,
        agent_id=body.agent_id,
        started_by=body.started_by or actor.actor_id,
        started_via=body.started_via,
    )
    client = _temporal(request)
    # Idempotent retry: WorkflowAlreadyStartedError means the run already exists.
    with contextlib.suppress(WorkflowAlreadyStartedError):
        await client.start_workflow(
            AgentRunWorkflow.run,
            args=[state, actor.actor_id, carry],
            id=run_id,  # workflow ID = run ID -> idempotent retries
            task_queue=config.task_queue,
        )
    return CreateRunResponse(run_id=run_id, status="planning")


async def _send_signal(request: Request, run_id: str, signal: str, arg: object) -> None:
    handle = _temporal(request).get_workflow_handle(run_id)
    try:
        await handle.signal(signal, arg)
    except RPCError as err:
        if err.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=409, detail="run is not active") from err
        raise


async def _require_run(request: Request, run_id: str, actor: Actor) -> RunProjection:
    run = await _projections(request).get_run(actor.tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return run


async def _signal_run(request: Request, run_id: str, actor: Actor, signal: str) -> SignalResponse:
    await _require_run(request, run_id, actor)
    await _send_signal(request, run_id, signal, actor.actor_id)
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


@app.post("/runs/{run_id}/steer", response_model=SteerResponse, status_code=202)
async def steer_run(
    request: Request, run_id: str, body: SteerRequest, actor: ActorDep
) -> SteerResponse:
    """Append one steer to the run's mailbox; the workflow loop drains it
    into the next turn."""
    await _require_run(request, run_id, actor)
    message = SteerMessage(
        id=f"steer-{uuid.uuid4().hex[:12]}",
        author="human",
        author_id=actor.actor_id,
        mode=body.mode,
        body=body.body,
    )
    await _send_signal(request, run_id, "steer", message)
    return SteerResponse(run_id=run_id, steer_id=message.id, mode=body.mode)


@app.post("/runs/{run_id}/plan/approve", response_model=SignalResponse, status_code=202)
async def approve_plan(
    request: Request, run_id: str, body: ApprovePlanRequest, actor: ActorDep
) -> SignalResponse:
    """Approve or reject the plan version awaiting approval. The supplied
    version must match the live plan so decisions can never target a plan
    the human did not review; the workflow re-checks and drops mismatches."""
    run = await _require_run(request, run_id, actor)
    if not body.approve and not (body.reason and body.reason.strip()):
        raise HTTPException(status_code=422, detail="a rejection requires a reason")
    current_version = run.plan.get("version") if run.plan else None
    if current_version != body.plan_version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"plan version {body.plan_version} does not match the "
                f"current plan version {current_version}"
            ),
        )
    decision = PlanApprovalDecision(
        plan_version=body.plan_version,
        approve=body.approve,
        reason=body.reason,
        actor=actor.actor_id,
    )
    await _send_signal(request, run_id, "approve_plan", decision)
    return SignalResponse(run_id=run_id, signal="approve_plan")


@app.post("/runs/{run_id}/steps/{step_id}/respond", response_model=SignalResponse, status_code=202)
async def respond_to_gate(
    request: Request, run_id: str, step_id: str, body: GateRespondRequest, actor: ActorDep
) -> SignalResponse:
    """Answer exactly one step's open human gate. Responding to a step that
    is not blocked on a human is a clean conflict; the workflow re-checks and
    drops stale answers. A response carrying `at_virtual` is a scheduled
    simulated answer, so it may target a gate that has not opened yet."""
    await _require_run(request, run_id, actor)
    steps = await _projections(request).get_steps(actor.tenant_id, run_id)
    step = next((s for s in steps if s.step_id == step_id), None)
    if step is None:
        raise HTTPException(status_code=404, detail=f"step {step_id!r} not found")
    if body.at_virtual is None and step.status != "blocked_on_human":
        raise HTTPException(
            status_code=409,
            detail=f"step {step_id!r} is not awaiting a human response (status {step.status!r})",
        )
    response = GateResponse(
        step_id=step_id, response=body.response, actor=actor.actor_id, at_virtual=body.at_virtual
    )
    await _send_signal(request, run_id, "human_response", response)
    return SignalResponse(run_id=run_id, signal="human_response")


@app.post("/runs/{run_id}/clock/advance", response_model=SignalResponse, status_code=202)
async def advance_clock(
    request: Request, run_id: str, body: ClockAdvanceRequest, actor: ActorDep
) -> SignalResponse:
    """Fast-forward a rehearsal run's virtual clock, resolving every virtual
    timer with a deadline at or before the target. Guarded twice: only
    sandbox-kind bindings running a virtual clock accept advances (the
    workflow re-checks and drops anything else), and time never moves
    backward. The advance is an audited external mutation — the signal
    carries the verified actor, and the events its resolved timers emit
    attribute it."""
    await _require_run(request, run_id, actor)
    store = cast(ArtifactStore, request.app.state.store)
    # The binding snapshot pinned at run creation is the authority on kind
    # and clock mode for the run's whole life; it lives at a key this control
    # plane wrote when it created the run.
    binding_raw: dict[str, Any] = await store.get_json_at(f"runs/{run_id}/binding.json")
    binding = EnvironmentBinding.model_validate(binding_raw)
    if binding.kind != "sandbox":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id!r} is bound to a {binding.kind!r} environment; "
            "clock advances are sandbox-only",
        )
    if binding.clock.mode != "virtual":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id!r} runs a {binding.clock.mode!r} clock; "
            "clock advances require virtual mode",
        )
    advance = ClockAdvance(to=body.to, actor=actor.actor_id)
    await _send_signal(request, run_id, "advance_time", advance)
    return SignalResponse(run_id=run_id, signal="advance_time")


@app.get("/runs/{run_id}", response_model=RunView)
async def get_run(request: Request, run_id: str, actor: ActorDep) -> RunView:
    # Projections only - never a Temporal query on a user-facing path.
    projections = _projections(request)
    run = await projections.get_run(actor.tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    steps = await projections.get_steps(actor.tenant_id, run_id)
    budget = None
    if run.budget_cap_usd is not None:
        budget = BudgetView(
            cap_usd=run.budget_cap_usd,
            spent_usd=run.budget_spent_usd,
            reserved_usd=run.budget_reserved_usd,
        )
    return RunView(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        parent_run_id=run.parent_run_id,
        status=run.status,
        goal=run.goal,
        plan=run.plan,
        land_report=run.land_report,
        budget=budget,
        execution_session=(
            ExecutionSessionView.model_validate(run.execution_session.model_dump())
            if run.execution_session is not None
            else None
        ),
        steps=[
            StepView(
                step_id=step.step_id,
                description=step.description,
                status=step.status,
                attempt=step.attempt,
                model_used=step.model_used,
                cost_usd=step.cost_usd,
                updated_at=step.updated_at,
            )
            for step in steps
        ],
        environment_id=run.environment_id,
        agent_id=run.agent_id,
        started_by=run.started_by,
        started_via=run.started_via,
        created_at=run.created_at,
    )


@app.get("/runs", response_model=RunListResponse)
async def list_runs(
    request: Request,
    actor: ActorDep,
    status: str | None = None,
    agent_id: str | None = None,
    created_before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> RunListResponse:
    """Tenant-scoped run list, newest first, from projections only. This is
    the console's run directory — no client should have to remember run ids
    it created (the website's in-process map retires against this)."""
    projections = _projections(request)
    runs = await projections.list_runs(
        actor.tenant_id,
        status=status,
        agent_id=agent_id,
        created_before=created_before,
        limit=limit,
    )
    summaries = [
        RunSummary(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            parent_run_id=run.parent_run_id,
            status=run.status,
            goal=run.goal,
            land_report=run.land_report,
            budget=(
                BudgetView(
                    cap_usd=run.budget_cap_usd,
                    spent_usd=run.budget_spent_usd,
                    reserved_usd=run.budget_reserved_usd,
                )
                if run.budget_cap_usd is not None
                else None
            ),
            environment_id=run.environment_id,
            agent_id=run.agent_id,
            started_by=run.started_by,
            started_via=run.started_via,
            steps_done=run.steps_done,
            steps_total=run.steps_total,
            execution_session=(
                ExecutionSessionView.model_validate(run.execution_session.model_dump())
                if run.execution_session is not None
                else None
            ),
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        for run in runs
    ]
    next_cursor = runs[-1].created_at if len(runs) == limit else None
    return RunListResponse(runs=summaries, next_created_before=next_cursor)


@app.put("/agents/{agent_id}/schedule", response_model=AgentScheduleView)
async def put_agent_schedule(
    request: Request, agent_id: str, body: AgentScheduleRequest, actor: ActorDep
) -> AgentScheduleView:
    """Create or replace the agent's schedule. The run template freezes what
    the console resolved at save time; the tenant comes from the
    authenticated caller, never the body. Firings create runs through the
    normal POST /runs path with started_via="schedule"."""
    try:
        validate_schedule_spec(body.schedule)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    template = ScheduledRunTemplate(
        tenant_id=actor.tenant_id,
        agent_id=agent_id,
        goal=body.template.goal,
        environment_id=body.template.environment_id,
        budget_usd=body.template.budget_usd,
        tools=body.template.tools,
        instructions=body.template.instructions,
        started_by=body.template.started_by or actor.actor_id,
    )
    config = _config(request)
    await upsert_agent_schedule(_temporal(request), template, body.schedule, config.task_queue)
    view = await describe_agent_schedule(_temporal(request), agent_id)
    assert view is not None
    return AgentScheduleView(
        agent_id=agent_id,
        cron=view.cron,
        timezone=view.timezone,
        enabled=view.enabled,
        next_run_times=view.next_run_times,
    )


@app.get("/agents/{agent_id}/schedule", response_model=AgentScheduleView)
async def get_agent_schedule(request: Request, agent_id: str, actor: ActorDep) -> AgentScheduleView:
    view = await describe_agent_schedule(_temporal(request), agent_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id!r} has no schedule")
    return AgentScheduleView(
        agent_id=agent_id,
        cron=view.cron,
        timezone=view.timezone,
        enabled=view.enabled,
        next_run_times=view.next_run_times,
    )


@app.delete("/agents/{agent_id}/schedule", response_model=SignalResponse)
async def remove_agent_schedule(request: Request, agent_id: str, actor: ActorDep) -> SignalResponse:
    removed = await delete_agent_schedule(_temporal(request), agent_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"agent {agent_id!r} has no schedule")
    return SignalResponse(run_id=agent_id, signal="schedule_removed")


@app.get("/runs/{run_id}/events")
async def stream_events(
    request: Request, run_id: str, actor: ActorDep, after: int = 0
) -> StreamingResponse:
    projections = _projections(request)
    run = await projections.get_run(actor.tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    # Resume support: Last-Event-ID (standard SSE reconnect) or ?after=seq.
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
