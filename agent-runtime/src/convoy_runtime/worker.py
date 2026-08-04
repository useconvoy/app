"""Temporal worker entrypoint: workflows + activities wired to real providers.

The payload codec is constructed unconditionally — there is no way to run a
worker without encryption (CLAUDE.md rule 9).
"""

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from convoy_runtime.activities.land import LandActivities
from convoy_runtime.activities.outbox import OutboxActivities
from convoy_runtime.activities.plan import PlanActivities
from convoy_runtime.activities.turn import TurnActivities
from convoy_runtime.codec import runtime_data_converter
from convoy_runtime.config import RuntimeConfig
from convoy_runtime.projections.db import ProjectionsDB
from convoy_runtime.projections.store import ProjectionStore
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.turn_executor import ScriptedTurnExecutor
from convoy_runtime.workflows.agent_run import AgentRunWorkflow


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

    # M0: ScriptedTurnExecutor is the wired executor (CI default in every
    # deterministic lane). TODO(milestone-1): Pydantic AI executor via LiteLLM.
    plan_activities = PlanActivities(store)
    turn_activities = TurnActivities(
        ScriptedTurnExecutor(store, turn_delay_seconds=config.scripted_turn_delay_seconds)
    )
    land_activities = LandActivities(store)
    outbox_activities = OutboxActivities(projections)

    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[AgentRunWorkflow],
        activities=[
            plan_activities.create_plan,
            turn_activities.run_turn,
            land_activities.land_run,
            outbox_activities.emit_run_events,
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
