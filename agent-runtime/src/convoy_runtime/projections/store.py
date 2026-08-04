"""Projection writes (via the outbox activity) and user-facing reads.

Every state change emits a `RunEvent` through the outbox into these tables;
the control plane reads them — never Temporal. Event inserts are idempotent
on (run_id, seq) so outbox activity retries can never duplicate an event, and
the per-run seq is what guarantees SSE ordering and resume.

Beyond the raw event log, events fold into console-ready rollups: the run row
(status, plan, land report, budget totals) and one row per plan step.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from convoy_core import RunEvent
from convoy_runtime.projections.db import ProjectionsDB


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float | str) and str(value):
        try:
            return Decimal(str(value))
        except ArithmeticError:
            return None
    return None


class StepProjection(BaseModel):
    """User-facing view of one plan step, folded from step events."""

    step_id: str
    description: str = ""
    status: str
    attempt: int = 0
    model_used: str | None = None
    cost_usd: Decimal | None = None
    updated_at: datetime


class RunProjection(BaseModel):
    """User-facing view of a run, served exclusively from Postgres."""

    run_id: str
    tenant_id: str
    status: str
    goal: str
    plan: dict[str, Any] | None = None
    land_report: dict[str, Any] | None = None
    budget_cap_usd: Decimal | None = None
    budget_spent_usd: Decimal | None = None
    budget_reserved_usd: Decimal | None = None
    created_at: datetime
    updated_at: datetime


class ProjectionStore:
    def __init__(self, db: ProjectionsDB) -> None:
        self._db = db

    async def create_run(
        self,
        *,
        tenant_id: str,
        run_id: str,
        goal: str,
        status: str = "planning",
        budget_cap_usd: Decimal | None = None,
    ) -> None:
        async with self._db.tenant_connection(tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO runs (tenant_id, run_id, status, goal, budget_cap_usd)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (tenant_id, run_id, status, goal, budget_cap_usd),
            )

    async def record_events(self, events: list[RunEvent]) -> None:
        """Outbox write path: insert events and fold rollup updates into the
        run and step rows. Idempotent per (run_id, seq)."""
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
                await self._fold_event(conn, event)

    async def _fold_event(self, conn: AsyncConnection[DictRow], event: RunEvent) -> None:
        """Fold one event into the console rollups. Every branch is an
        idempotent upsert, so replays and outbox retries are harmless."""
        payload = event.payload
        run_status = payload.get("run_status")
        if isinstance(run_status, str):
            await conn.execute(
                "UPDATE runs SET status = %s, updated_at = now() WHERE run_id = %s",
                (run_status, event.run_id),
            )
        plan = _as_dict(payload.get("plan"))
        if plan is not None:
            await conn.execute(
                "UPDATE runs SET plan = %s, updated_at = now() WHERE run_id = %s",
                (Jsonb(plan), event.run_id),
            )
            steps = plan.get("steps")
            if isinstance(steps, list):
                for raw_step in cast(list[Any], steps):
                    step = _as_dict(raw_step)
                    if step is not None:
                        await self._upsert_step(conn, event, step)
        land_report = _as_dict(payload.get("land_report"))
        if land_report is not None:
            await conn.execute(
                "UPDATE runs SET land_report = %s, updated_at = now() WHERE run_id = %s",
                (Jsonb(land_report), event.run_id),
            )
        budget = _as_dict(payload.get("budget"))
        if budget is not None:
            await conn.execute(
                """
                UPDATE runs SET budget_cap_usd = %s, budget_spent_usd = %s,
                                budget_reserved_usd = %s, updated_at = now()
                WHERE run_id = %s
                """,
                (
                    _decimal_or_none(budget.get("cap_usd")),
                    _decimal_or_none(budget.get("spent_usd")),
                    _decimal_or_none(budget.get("reserved_usd")),
                    event.run_id,
                ),
            )
        if event.type in ("step_started", "step_done", "step_failed", "step_skipped"):
            status = event.type.removeprefix("step_")
            status = "running" if status == "started" else status
            await self._upsert_step(
                conn,
                event,
                {
                    "id": payload.get("step_id"),
                    "status": status,
                    "attempt": payload.get("attempt"),
                    "model_used": payload.get("model_used"),
                    "cost_usd": payload.get("cost_usd"),
                },
            )
        if event.type in ("gate_opened", "gate_answered"):
            # An open gate blocks its step on a human; the answer puts the
            # step back to work. Timeout consequences arrive as their own
            # step events (or a pause), so gate_timed_out folds nothing.
            await self._upsert_step(
                conn,
                event,
                {
                    "id": payload.get("step_id"),
                    "status": "blocked_on_human" if event.type == "gate_opened" else "running",
                },
            )

    @staticmethod
    async def _upsert_step(
        conn: AsyncConnection[DictRow], event: RunEvent, step: dict[str, Any]
    ) -> None:
        step_id = step.get("id")
        if not isinstance(step_id, str):
            return
        await conn.execute(
            """
            INSERT INTO run_steps
                (tenant_id, run_id, step_id, description, status, attempt,
                 model_used, cost_usd, updated_at)
            VALUES (%s, %s, %s, COALESCE(%s, ''), COALESCE(%s, 'pending'),
                    COALESCE(%s, 0), %s, %s, now())
            ON CONFLICT (run_id, step_id) DO UPDATE SET
                description = COALESCE(NULLIF(EXCLUDED.description, ''),
                                       run_steps.description),
                status      = EXCLUDED.status,
                attempt     = GREATEST(EXCLUDED.attempt, run_steps.attempt),
                model_used  = COALESCE(EXCLUDED.model_used, run_steps.model_used),
                cost_usd    = COALESCE(EXCLUDED.cost_usd, run_steps.cost_usd),
                updated_at  = now()
            """,
            (
                event.tenant_id,
                event.run_id,
                step_id,
                step.get("description"),
                step.get("status"),
                step.get("attempt"),
                step.get("model_used"),
                _decimal_or_none(step.get("cost_usd")),
            ),
        )

    async def get_run(self, tenant_id: str, run_id: str) -> RunProjection | None:
        async with self._db.tenant_connection(tenant_id) as conn:
            cursor = await conn.execute(
                """
                SELECT tenant_id, run_id, status, goal, plan, land_report,
                       budget_cap_usd, budget_spent_usd, budget_reserved_usd,
                       created_at, updated_at
                FROM runs WHERE run_id = %s
                """,
                (run_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return RunProjection.model_validate(dict(row))

    async def get_steps(self, tenant_id: str, run_id: str) -> list[StepProjection]:
        async with self._db.tenant_connection(tenant_id) as conn:
            cursor = await conn.execute(
                """
                SELECT step_id, description, status, attempt, model_used,
                       cost_usd, updated_at
                FROM run_steps WHERE run_id = %s
                ORDER BY step_id
                """,
                (run_id,),
            )
            rows = await cursor.fetchall()
        return [StepProjection.model_validate(dict(row)) for row in rows]

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
