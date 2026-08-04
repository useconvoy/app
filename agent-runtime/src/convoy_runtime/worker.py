"""Temporal worker entrypoint: workflows + activities wired to real providers.

The payload codec is constructed unconditionally — there is no way to run a
worker without encryption. The turn executor is selected by configuration:
the deterministic scripted executor by default, or the Pydantic AI executor
speaking to real (or mock) models through the LiteLLM proxy.

TODO: OTel spans (run -> step -> turn -> tool) with the Langfuse sink.
"""

import asyncio
import logging
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from convoy_runtime.activities.compact import CompactActivities
from convoy_runtime.activities.context import ContextActivities
from convoy_runtime.activities.land import LandActivities
from convoy_runtime.activities.model_key import ModelKeyActivities
from convoy_runtime.activities.outbox import OutboxActivities
from convoy_runtime.activities.plan import PlanActivities
from convoy_runtime.activities.promoted import PromotedToolActivities, SandboxJobActivities
from convoy_runtime.activities.subagent import SubagentActivities
from convoy_runtime.activities.turn import TurnActivities
from convoy_runtime.codec import runtime_data_converter
from convoy_runtime.config import RuntimeConfig
from convoy_runtime.projections.db import ProjectionsDB
from convoy_runtime.projections.store import ProjectionStore
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.model_gateway import ModelGateway
from convoy_runtime.providers.model_keys import LiteLLMKeyProvider
from convoy_runtime.providers.pydantic_ai_turn import PydanticAITurnExecutor
from convoy_runtime.providers.sandbox import LocalSandboxProvider
from convoy_runtime.providers.turn_executor import ScriptedTurnExecutor, TurnExecutor
from convoy_runtime.workflows.agent_run import AgentRunWorkflow
from convoy_runtime.workflows.subagent import SubagentWorkflow


def build_turn_executor(
    config: RuntimeConfig,
    store: ArtifactStore,
    key_provider: LiteLLMKeyProvider | None,
) -> TurnExecutor:
    if config.turn_executor == "pydantic_ai":
        if key_provider is None:
            raise RuntimeError(
                "turn executor 'pydantic_ai' requires LITELLM_BASE_URL and "
                "LITELLM_MASTER_KEY so per-run virtual keys can be provisioned"
            )
        return PydanticAITurnExecutor(
            store=store,
            gateway=ModelGateway(config.model_gateway),
            litellm_base_url=config.litellm_base_url,
            key_provider=key_provider,
        )
    return ScriptedTurnExecutor(store, turn_delay_seconds=config.scripted_turn_delay_seconds)


async def run_worker(config: RuntimeConfig) -> None:
    client = await Client.connect(
        config.temporal_host,
        namespace=config.temporal_namespace,
        data_converter=runtime_data_converter(config.codec_key),
    )
    store = ArtifactStore(
        bucket=config.s3_bucket,
        endpoint_url=config.s3_endpoint_url,
        region=config.s3_region,
        access_key=config.s3_access_key,
        secret_key=config.s3_secret_key,
    )
    db = ProjectionsDB(config.pg_dsn)
    await db.open()
    projections = ProjectionStore(db)

    key_provider = None
    if config.litellm_base_url and config.litellm_master_key:
        key_provider = LiteLLMKeyProvider(
            base_url=config.litellm_base_url, master_key=config.litellm_master_key
        )

    plan_activities = PlanActivities(store)
    turn_activities = TurnActivities(build_turn_executor(config, store, key_provider))
    land_activities = LandActivities(store)
    outbox_activities = OutboxActivities(projections)
    context_activities = ContextActivities(store)
    model_key_activities = ModelKeyActivities(key_provider)
    subagent_activities = SubagentActivities(store)
    compact_activities = CompactActivities(store)
    promoted_activities = PromotedToolActivities(
        store,
        stub_env_url=config.stub_env_url,
        completion_delay_seconds=config.promoted_tool_delay_seconds,
    )
    sandbox_activities = SandboxJobActivities(
        store, LocalSandboxProvider(store, base_dir=Path(config.sandbox_dir))
    )

    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[AgentRunWorkflow, SubagentWorkflow],
        activities=[
            plan_activities.create_plan,
            plan_activities.archive_plan_snapshot,
            turn_activities.run_turn,
            land_activities.land_run,
            outbox_activities.emit_run_events,
            context_activities.assemble_pinned_header,
            model_key_activities.provision_model_key,
            subagent_activities.assemble_subagent_header,
            subagent_activities.wrap_subagent_result,
            subagent_activities.archive_subagent_result,
            compact_activities.compact_step,
            promoted_activities.run_promoted_tool,
            sandbox_activities.run_sandbox_job,
        ],
    )
    logging.getLogger(__name__).info("worker started on task queue %s", config.task_queue)
    try:
        await worker.run()
    finally:
        await db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker(RuntimeConfig.from_env()))


if __name__ == "__main__":
    main()
