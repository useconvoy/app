"""Projection writes (via the outbox activity) and user-facing reads.

Every state change emits a `RunEvent` through the outbox into these tables
(CLAUDE.md rule 5); the control plane reads them — never Temporal
(CLAUDE.md rule 11). Event inserts are idempotent on (run_id, seq) so outbox
activity retries can never duplicate an event.

TODO(milestone-1): richer console-ready projections (per-step rows, budget
rollups) and SSE hardening.
"""

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from convoy_core import RunEvent
from convoy_runtime.projections.db import ProjectionsDB


class RunProjection(BaseModel):
    """User-facing view of a run, served exclusively from Postgres."""

    run_id: str
    tenant_id: str
    status: str
    goal: str
    plan: dict[str, Any] | None = None
    land_report: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ProjectionStore:
    def __init__(self, db: ProjectionsDB) -> None:
        self._db = db

    async def create_run(
        self, *, tenant_id: str, run_id: str, goal: str, status: str = "planning"
    ) -> None:
        async with self._db.tenant_connection(tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO runs (tenant_id, run_id, status, goal)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (tenant_id, run_id, status, goal),
            )

    async def record_events(self, events: list[RunEvent]) -> None:
        """Outbox write path: insert events and fold status/plan/report updates
        into the run row. Idempotent per (run_id, seq)."""
        for event in events:
            async with self._db.tenant_connection(event.tenant_id) as conn:
                await conn.execute(
                    """
                    INSERT INTO run_events
                        (tenant_id, run_id, seq, event_id, type, ts, virtual_ts,
                         sandbox, actor, actor_type, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, seq) DO NOTHING
                    """,
                    (
                        event.tenant_id,
                        event.run_id,
                        event.seq,
                        event.id,
                        event.type,
                        event.ts,
                        event.virtual_ts,
                        event.sandbox,
                        event.actor,
                        event.actor_type,
                        Jsonb(event.payload),
                    ),
                )
                run_status = event.payload.get("run_status")
                if isinstance(run_status, str):
                    await conn.execute(
                        "UPDATE runs SET status = %s, updated_at = now() WHERE run_id = %s",
                        (run_status, event.run_id),
                    )
                plan = event.payload.get("plan")
                if isinstance(plan, dict):
                    await conn.execute(
                        "UPDATE runs SET plan = %s, updated_at = now() WHERE run_id = %s",
                        (Jsonb(plan), event.run_id),
                    )
                land_report = event.payload.get("land_report")
                if isinstance(land_report, dict):
                    await conn.execute(
                        "UPDATE runs SET land_report = %s, updated_at = now() WHERE run_id = %s",
                        (Jsonb(land_report), event.run_id),
                    )

    async def get_run(self, tenant_id: str, run_id: str) -> RunProjection | None:
        async with self._db.tenant_connection(tenant_id) as conn:
            cursor = await conn.execute(
                """
                SELECT tenant_id, run_id, status, goal, plan, land_report,
                       created_at, updated_at
                FROM runs WHERE run_id = %s
                """,
                (run_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return RunProjection.model_validate(dict(row))

    async def get_events(
        self, tenant_id: str, run_id: str, after_seq: int = 0, limit: int = 500
    ) -> list[RunEvent]:
        async with self._db.tenant_connection(tenant_id) as conn:
            cursor = await conn.execute(
                """
                SELECT event_id AS id, run_id, tenant_id, seq, type, ts, virtual_ts,
                       sandbox, actor, actor_type, payload
                FROM run_events
                WHERE run_id = %s AND seq > %s
                ORDER BY seq
                LIMIT %s
                """,
                (run_id, after_seq, limit),
            )
            rows = await cursor.fetchall()
        return [RunEvent.model_validate(dict(row)) for row in rows]
