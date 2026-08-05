"""RunEvent outbox activity.

Every state change emits a `RunEvent` through this activity into the Postgres
projection tables — if it didn't emit, it didn't happen. Inserts are
idempotent on (run_id, seq), so activity retries cannot duplicate events.
"""

from temporalio import activity

from convoy_core import RunEvent
from convoy_runtime.activities import names
from convoy_runtime.projections.store import ProjectionStore


class OutboxActivities:
    def __init__(self, projections: ProjectionStore) -> None:
        self._projections = projections

    @activity.defn(name=names.EMIT_RUN_EVENTS)
    async def emit_run_events(self, events: list[RunEvent]) -> None:
        await self._projections.record_events(events)
