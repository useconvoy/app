"""Short-lived, self-verifying cloud acceptance run.

This entry point is intentionally not a service.  It runs inside ECS, starts a
real Temporal Cloud worker and workflow, drives real per-run Fargate sandboxes
through mutate -> pause/hibernate -> fresh-task restore -> completion, writes a
machine-readable evidence bundle to S3, and exits.  The deployment wrapper is
responsible for a hard wall-clock timeout and removing its temporary roles,
task definitions, log group, and image tags.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import secrets
import tarfile
import uuid
from decimal import Decimal
from typing import Any

import httpx
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from convoy_core import (
    AgentSpec,
    BudgetState,
    EnvironmentBinding,
    ModelGatewayConfig,
    PermissionScope,
    RunEvent,
    RunPolicy,
    RunState,
    SandboxJob,
    ToolGrant,
)
from convoy_runtime.activities import names
from convoy_runtime.activities.compact import CompactActivities
from convoy_runtime.activities.context import ContextActivities
from convoy_runtime.activities.land import LandActivities
from convoy_runtime.activities.model_key import ModelKeyActivities
from convoy_runtime.activities.plan import PlanActivities
from convoy_runtime.activities.promoted import PromotedToolActivities, SandboxJobActivities
from convoy_runtime.activities.subagent import SubagentActivities
from convoy_runtime.activities.turn import TurnActivities
from convoy_runtime.carry import BindingFacts, RunCarry, RunTuning
from convoy_runtime.codec import runtime_data_converter
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.model_gateway import ModelGateway
from convoy_runtime.providers.promoted import SandboxJobOutcome, SandboxJobRequest
from convoy_runtime.providers.pydantic_ai_turn import PydanticAITurnExecutor
from convoy_runtime.providers.sandbox_ecs import EcsSandboxProvider
from convoy_runtime.workflows.agent_run import AgentRunWorkflow
from convoy_runtime.workflows.subagent import SubagentWorkflow

LOG = logging.getLogger(__name__)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


class _StaticLiteLLMKeyProvider:
    """Use the proxy master key for this one bounded acceptance run.

    Production workers use per-run virtual keys.  The acceptance proxy has no
    database and exists only for the lifetime of one ECS task; the workflow's
    $0.50 cap and the deployment's 15-minute timeout bound its exposure.
    """

    def __init__(self, key: str) -> None:
        self._key = key

    async def ensure_run_key(self, run_id: str, cap_usd: Decimal) -> str:
        return self._key


class _RecordingOutbox:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    @activity.defn(name=names.EMIT_RUN_EVENTS)
    async def emit_run_events(self, events: list[RunEvent]) -> None:
        # Activity retries are possible. Mirror the production outbox's
        # idempotency on (run_id, seq) in this in-process evidence recorder.
        seen = {(event.run_id, event.seq) for event in self.events}
        self.events.extend(event for event in events if (event.run_id, event.seq) not in seen)


class _RecordingSandboxActivities(SandboxJobActivities):
    def __init__(self, store: ArtifactStore, provider: EcsSandboxProvider) -> None:
        super().__init__(store, provider)
        self.first_job_completed = asyncio.Event()
        self.requests: list[SandboxJobRequest] = []
        self.outcomes: list[SandboxJobOutcome] = []

    @activity.defn(name=names.RUN_SANDBOX_JOB)
    async def run_sandbox_job(self, request: SandboxJobRequest) -> SandboxJobOutcome:
        outcome = await super().run_sandbox_job(request)
        self.requests.append(request)
        self.outcomes.append(outcome)
        self.first_job_completed.set()
        return outcome


async def _wait_litellm() -> None:
    deadline = asyncio.get_running_loop().time() + 120
    async with httpx.AsyncClient(timeout=3) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await client.get("http://127.0.0.1:4000/health/liveliness")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2)
    raise TimeoutError("LiteLLM sidecar did not become healthy")


async def _eventually(check: Any, *, deadline_seconds: float = 180.0) -> Any:
    deadline = asyncio.get_running_loop().time() + deadline_seconds
    while asyncio.get_running_loop().time() < deadline:
        value = await check()
        if value:
            return value
        await asyncio.sleep(1)
    raise TimeoutError("acceptance condition was not met")


def _temporal_secret() -> dict[str, str]:
    raw = json.loads(_required("TEMPORAL_SECRET_JSON"))
    required = {"endpoint", "namespace", "apiKey"}
    missing = required - raw.keys()
    if missing:
        raise RuntimeError(f"Temporal secret is missing fields: {sorted(missing)}")
    return {key: str(raw[key]) for key in required}


async def run_acceptance() -> dict[str, Any]:
    test_id = _required("CONVOY_ACCEPTANCE_TEST_ID")
    run_id = f"cloud-e2e-{test_id}-{uuid.uuid4().hex[:8]}"
    bucket = _required("CONVOY_ARTIFACT_BUCKET")
    region = os.environ.get("AWS_REGION", "us-west-2")
    evidence_key = f"cloud-acceptance/{test_id}/evidence.json"

    store = ArtifactStore(bucket=bucket, endpoint_url=None, region=region)
    provider = EcsSandboxProvider(
        store,
        cluster=_required("CONVOY_SANDBOX_CLUSTER"),
        task_family=_required("CONVOY_SANDBOX_TASK_FAMILY"),
        subnets=[part for part in _required("CONVOY_SANDBOX_SUBNETS").split(",") if part],
        security_group=_required("CONVOY_SANDBOX_SECURITY_GROUP"),
        key_prefix=f"cloud-acceptance/{test_id}/sandboxes",
        region=region,
        assign_public_ip=True,
        sandbox_ttl_seconds=900,
        create_timeout_seconds=180,
        submit_timeout_seconds=180,
    )
    outbox = _RecordingOutbox()
    sandbox_activities = _RecordingSandboxActivities(store, provider)

    await _wait_litellm()
    temporal = _temporal_secret()
    codec_key = secrets.token_bytes(32)
    client = await Client.connect(
        temporal["endpoint"],
        namespace=temporal["namespace"],
        api_key=temporal["apiKey"],
        tls=True,
        data_converter=runtime_data_converter(codec_key),
    )

    grant = ToolGrant(
        tool_id="sandbox_exec",
        scope=PermissionScope(resource="workspace", actions=["execute"]),
        execution="promoted",
        side_effecting=True,
    )
    binding_ref = await store.put_json(
        f"runs/{run_id}/binding.json",
        EnvironmentBinding(
            id=f"acceptance-{test_id}",
            tenant_id="tenant-cloud-acceptance",
            kind="sandbox",
            tool_registry=[grant],
            connector_endpoints={},
            credential_scope="none",
            data_namespace=f"cloud-acceptance/{test_id}",
            sandbox_template="convoy-sandbox",
        ).model_dump(mode="json"),
    )
    prompt_ref = await store.put_json(
        f"runs/{run_id}/prompts/root.json",
        {"role": "cloud acceptance agent"},
    )
    goal = (
        "Cloud runtime acceptance. For the current plan step, call sandbox_exec exactly once. "
        "Pass command as [\"sh\",\"-c\",\"mkdir -p outputs; "
        "echo 'CURRENT_STEP_ID' >> data.log; wc -l < data.log > outputs/lines.txt\"], "
        "replacing CURRENT_STEP_ID with the step id shown in the prompt. After its durable "
        "result returns, do not call any tool again; reply with STEP_DONE."
    )
    pinned_ref = await store.put_json(
        f"runs/{run_id}/pinned.json",
        {"goal": goal, "success_criteria": ["step-1 and step-2 each appear once in data.log"]},
    )
    state = RunState(
        run_id=run_id,
        tenant_id="tenant-cloud-acceptance",
        environment_id=f"acceptance-{test_id}",
        binding_ref=binding_ref,
        status="planning",
        agent=AgentSpec(
            id=f"{run_id}-root",
            layer=0,
            max_children=0,
            model="live-anthropic",
            tools=[grant],
            prompt_ref=prompt_ref,
        ),
        policy=RunPolicy(require_plan_approval=False),
        plan=None,
        budget=BudgetState(cap_usd=Decimal("0.50")),
        pinned_ref=pinned_ref,
    )
    carry = RunCarry(
        binding=BindingFacts(kind="sandbox", sandbox_template="convoy-sandbox"),
        tuning=RunTuning(turn_limit=20, midstep_compaction_tokens=40_000),
    )

    key_provider = _StaticLiteLLMKeyProvider(_required("LITELLM_MASTER_KEY"))
    turn_executor = PydanticAITurnExecutor(
        store=store,
        gateway=ModelGateway(
            ModelGatewayConfig(
                endpoints={},
                approved_models=["live-anthropic"],
                fallback_chains={},
            )
        ),
        litellm_base_url="http://127.0.0.1:4000",
        key_provider=key_provider,  # type: ignore[arg-type]
        request_timeout_seconds=90,
    )
    plan_activities = PlanActivities(store)
    turn_activities = TurnActivities(turn_executor)
    land_activities = LandActivities(store)
    context_activities = ContextActivities(store)
    model_key_activities = ModelKeyActivities(None)
    subagent_activities = SubagentActivities(store)
    compact_activities = CompactActivities(store)
    promoted_activities = PromotedToolActivities(store, stub_env_url="http://127.0.0.1:9")
    task_queue = f"cloud-acceptance-{test_id}-{uuid.uuid4().hex[:8]}"

    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[AgentRunWorkflow, SubagentWorkflow],
        activities=[
            plan_activities.create_plan,
            plan_activities.archive_plan_snapshot,
            turn_activities.run_turn,
            land_activities.land_run,
            outbox.emit_run_events,
            context_activities.assemble_pinned_header,
            model_key_activities.provision_model_key,
            subagent_activities.assemble_subagent_header,
            subagent_activities.wrap_subagent_result,
            subagent_activities.archive_subagent_result,
            compact_activities.compact_step,
            promoted_activities.run_promoted_tool,
            sandbox_activities.run_sandbox_job,
            sandbox_activities.hibernate_sandbox,
            sandbox_activities.restore_sandbox,
        ],
    ):
        handle = await client.start_workflow(
            AgentRunWorkflow.run,
            args=[state, "cloud-acceptance@convoy", carry],
            id=run_id,
            task_queue=task_queue,
        )

        # The first real side effect has landed and its snapshot is durable.
        # Signal while the model's promoted-call re-entry is in flight; pause
        # remains edge-triggered and applies at the next workflow boundary.
        await asyncio.wait_for(sandbox_activities.first_job_completed.wait(), timeout=420)
        await handle.signal(AgentRunWorkflow.pause, "cloud-test-driver")

        async def paused_durability() -> dict[str, Any] | None:
            facts = await handle.query(AgentRunWorkflow.get_durability)
            return facts if facts["sandbox_status"] == "hibernated" else None

        paused = await _eventually(paused_durability, deadline_seconds=300)
        await handle.signal(AgentRunWorkflow.resume, "cloud-test-driver")
        result = await asyncio.wait_for(handle.result(), timeout=600)
        completed = await handle.query(AgentRunWorkflow.get_durability)

    if result.status != "completed":
        raise AssertionError(f"workflow ended as {result.status}: {result.error}")
    if len(sandbox_activities.outcomes) != 2:
        raise AssertionError(f"expected 2 sandbox jobs, got {len(sandbox_activities.outcomes)}")

    first_id = sandbox_activities.outcomes[0].handle.sandbox_id
    second_id = sandbox_activities.outcomes[1].handle.sandbox_id
    if first_id == second_id:
        raise AssertionError("resume reused the pre-pause sandbox task")
    if paused["sandbox_generation"] != 1 or completed["sandbox_generation"] != 2:
        raise AssertionError(f"unexpected sandbox generations: {paused!r} -> {completed!r}")
    if completed["sandbox_status"] != "terminated":
        raise AssertionError(f"completion did not terminate compute: {completed!r}")

    final_snapshot = sandbox_activities.outcomes[-1].snapshot_ref
    snapshot_bytes = await store.get_bytes(final_snapshot)
    with tarfile.open(fileobj=io.BytesIO(snapshot_bytes), mode="r:*") as archive:
        member = archive.extractfile("data.log")
        if member is None:
            raise AssertionError("final checkpoint has no data.log")
        log_lines = member.read().decode().splitlines()
    if log_lines != ["step-1", "step-2"]:
        raise AssertionError(f"checkpoint state is not exact: {log_lines!r}")

    # Force an idempotency replay on a third fresh task reconstructed from the
    # final checkpoint. The duplicate key must not append a third line.
    replay_handle = await provider.create("convoy-sandbox", final_snapshot)
    try:
        last_request = sandbox_activities.requests[-1]
        raw_args = await store.get_json(last_request.call.args_ref)  # type: ignore[arg-type]
        duplicate = SandboxJob(
            idempotency_key=last_request.call.idempotency_key,
            command=[str(part) for part in raw_args["command"]],
        )
        replayed = await provider.exec(replay_handle, duplicate)
        if replayed != sandbox_activities.outcomes[-1].result:
            raise AssertionError("idempotent replay did not return the recorded result")
        probe = await provider.exec(
            replay_handle,
            SandboxJob(
                idempotency_key=f"probe-{uuid.uuid4().hex}",
                command=["sh", "-c", "mkdir -p outputs; wc -l < data.log > outputs/lines.txt"],
            ),
        )
        line_count = int((await store.get_bytes(probe.outputs[0])).decode().strip())
        if line_count != 2:
            raise AssertionError(f"duplicate side effect detected: line count {line_count}")
    finally:
        await provider.destroy(replay_handle)

    events = sorted(outbox.events, key=lambda event: event.seq)
    event_types = [event.type for event in events]
    required_events = [
        "environment_provisioned",
        "environment_checkpointed",
        "environment_hibernated",
        "paused",
        "environment_restored",
        "resumed",
        "environment_terminated",
        "run_completed",
    ]
    missing_events = [event for event in required_events if event not in event_types]
    if missing_events:
        raise AssertionError(f"missing lifecycle events: {missing_events}; saw {event_types}")
    if not (
        event_types.index("environment_hibernated") < event_types.index("paused")
        < event_types.index("environment_restored") < event_types.index("resumed")
        < event_types.index("environment_terminated") < event_types.index("run_completed")
    ):
        raise AssertionError(f"lifecycle event order is wrong: {event_types}")

    evidence = {
        "schema_version": 1,
        "status": "passed",
        "test_id": test_id,
        "run_id": run_id,
        "temporal_namespace": temporal["namespace"],
        "model": "live-anthropic",
        "workflow_result": result.model_dump(mode="json"),
        "paused_durability": paused,
        "completed_durability": completed,
        "sandbox_ids": [first_id, second_id, replay_handle.sandbox_id],
        "fresh_compute_verified": first_id != second_id,
        "checkpoint_lines": log_lines,
        "idempotent_replay_line_count": line_count,
        "event_types": event_types,
        "events": [event.model_dump(mode="json") for event in events],
        "final_snapshot": final_snapshot.model_dump(mode="json"),
        "codec": "encrypted Temporal payloads with per-run random key",
    }
    evidence_ref = await store.put_json(evidence_key, evidence)
    evidence["evidence_ref"] = evidence_ref.model_dump(mode="json")
    return evidence


async def _main() -> None:
    evidence = await asyncio.wait_for(run_acceptance(), timeout=900)
    print("ACCEPTANCE_RESULT=" + json.dumps(evidence, sort_keys=True, default=str), flush=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
