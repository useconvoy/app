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


class ExecutionSessionProjection(BaseModel):
    """Latest cloud execution-session and checkpoint state for one run."""

    environment_id: str
    status: str
    sandbox_id: str | None = None
    sandbox_provider: str | None = None
    sandbox_template: str | None = None
    generation: int = 0
    latest_checkpoint_id: str | None = None
    latest_snapshot_ref: dict[str, Any] | None = None
    updated_at: datetime


class RunProjection(BaseModel):
    """User-facing view of a run, served exclusively from Postgres. A
    subagent child run is a row of its own, linked by `parent_run_id`."""

    run_id: str
    tenant_id: str
    parent_run_id: str | None = None
    status: str
    goal: str
    plan: dict[str, Any] | None = None
    land_report: dict[str, Any] | None = None
    budget_cap_usd: Decimal | None = None
    budget_spent_usd: Decimal | None = None
    budget_reserved_usd: Decimal | None = None
    environment_id: str = ""
    agent_id: str | None = None
    started_by: str | None = None
    started_via: str = "manual"
    # Step progress counters, populated by list_runs only (get_run callers
    # receive the full step rows instead).
    steps_done: int = 0
    steps_total: int = 0
    execution_session: ExecutionSessionProjection | None = None
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
        environment_id: str = "",
        status: str = "planning",
        budget_cap_usd: Decimal | None = None,
        agent_id: str | None = None,
        started_by: str | None = None,
        started_via: str = "manual",
    ) -> None:
        async with self._db.tenant_connection(tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO runs (tenant_id, run_id, status, goal, budget_cap_usd,
                                  environment_id, agent_id, started_by, started_via)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    tenant_id,
                    run_id,
                    status,
                    goal,
                    budget_cap_usd,
                    environment_id,
                    agent_id,
                    started_by,
                    started_via,
                ),
            )
            await conn.execute(
                """
                INSERT INTO run_execution_sessions
                    (tenant_id, run_id, environment_id, status)
                VALUES (%s, %s, %s, 'unprovisioned')
                ON CONFLICT (run_id) DO NOTHING
                """,
                (tenant_id, run_id, environment_id),
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
        if event.type == "child_spawned":
            # A spawned child gets its own run row, linked to the parent, so
            # child progress is visible through the same console read path.
            # The parent emits this before starting the child, so the row
            # exists by the time child events fold into it.
            child_run_id = payload.get("child_run_id")
            if isinstance(child_run_id, str):
                await conn.execute(
                    """
                    INSERT INTO runs
                        (tenant_id, run_id, parent_run_id, status, goal, budget_cap_usd)
                    VALUES (%s, %s, %s, 'running', %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        parent_run_id = EXCLUDED.parent_run_id,
                        updated_at = now()
                    """,
                    (
                        event.tenant_id,
                        child_run_id,
                        event.run_id,
                        str(payload.get("goal") or ""),
                        _decimal_or_none(payload.get("budget_reserved")),
                    ),
                )
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
        if event.type in ("environment_provisioned", "environment_restored"):
            await conn.execute(
                """
                INSERT INTO run_execution_sessions
                    (tenant_id, run_id, status, sandbox_id, sandbox_provider,
                     sandbox_template, generation, latest_checkpoint_id,
                     latest_snapshot_ref)
                VALUES (%s, %s, 'active', %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status               = 'active',
                    sandbox_id           = EXCLUDED.sandbox_id,
                    sandbox_provider     = EXCLUDED.sandbox_provider,
                    sandbox_template     = EXCLUDED.sandbox_template,
                    generation           = EXCLUDED.generation,
                    latest_checkpoint_id = COALESCE(EXCLUDED.latest_checkpoint_id,
                                                    run_execution_sessions.latest_checkpoint_id),
                    latest_snapshot_ref  = COALESCE(EXCLUDED.latest_snapshot_ref,
                                                    run_execution_sessions.latest_snapshot_ref),
                    updated_at           = now()
                """,
                (
                    event.tenant_id,
                    event.run_id,
                    payload.get("sandbox_id"),
                    payload.get("sandbox_provider"),
                    payload.get("sandbox_template"),
                    payload.get("generation") or 0,
                    payload.get("checkpoint_id"),
                    Jsonb(payload.get("snapshot_ref"))
                    if payload.get("snapshot_ref") is not None
                    else None,
                ),
            )
        if event.type == "environment_checkpointed":
            checkpoint_id = payload.get("checkpoint_id")
            if isinstance(checkpoint_id, str):
                snapshot_ref = payload.get("snapshot_ref")
                await conn.execute(
                    """
                    INSERT INTO run_checkpoints
                        (tenant_id, run_id, checkpoint_id, checkpoint_sequence,
                         reason, released_sandbox_id, generation, snapshot_ref)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, checkpoint_id) DO NOTHING
                    """,
                    (
                        event.tenant_id,
                        event.run_id,
                        checkpoint_id,
                        payload.get("checkpoint_sequence") or 0,
                        str(payload.get("reason") or "unknown"),
                        payload.get("released_sandbox_id"),
                        payload.get("generation") or 0,
                        Jsonb(snapshot_ref) if snapshot_ref is not None else None,
                    ),
                )
                await conn.execute(
                    """
                    UPDATE run_execution_sessions SET
                        status = 'checkpointed', sandbox_id = NULL,
                        latest_checkpoint_id = %s, latest_snapshot_ref = %s,
                        generation = %s, updated_at = now()
                    WHERE run_id = %s
                    """,
                    (
                        checkpoint_id,
                        Jsonb(snapshot_ref) if snapshot_ref is not None else None,
                        payload.get("generation") or 0,
                        event.run_id,
                    ),
                )
        if event.type in ("environment_hibernated", "environment_terminated"):
            await conn.execute(
                """
                UPDATE run_execution_sessions SET
                    status = %s, sandbox_id = NULL,
                    latest_checkpoint_id = COALESCE(%s, latest_checkpoint_id),
                    latest_snapshot_ref = COALESCE(%s, latest_snapshot_ref),
                    generation = %s, updated_at = now()
                WHERE run_id = %s
                """,
                (
                    payload.get("execution_status"),
                    payload.get("checkpoint_id"),
                    Jsonb(payload.get("snapshot_ref"))
                    if payload.get("snapshot_ref") is not None
                    else None,
                    payload.get("generation") or 0,
                    event.run_id,
                ),
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
                SELECT tenant_id, run_id, parent_run_id, status, goal, plan,
                       land_report, budget_cap_usd, budget_spent_usd,
                       budget_reserved_usd, environment_id, agent_id,
                       started_by, started_via, created_at, updated_at
                FROM runs WHERE run_id = %s
                """,
                (run_id,),
            )
            row = await cursor.fetchone()
            session_cursor = await conn.execute(
                """
                SELECT environment_id, status, sandbox_id, sandbox_provider,
                       sandbox_template, generation, latest_checkpoint_id,
                       latest_snapshot_ref, updated_at
                FROM run_execution_sessions WHERE run_id = %s
                """,
                (run_id,),
            )
            session_row = await session_cursor.fetchone()
        if row is None:
            return None
        raw = dict(row)
        raw["execution_session"] = dict(session_row) if session_row is not None else None
        return RunProjection.model_validate(raw)

    async def list_runs(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        agent_id: str | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
    ) -> list[RunProjection]:
        """Newest-first run list for one tenant, with keyset pagination on
        `created_at` (pass the last row's `created_at` back as
        `created_before` for the next page). Execution-session state joins in
        the same query so list rows match `get_run`'s shape. Filters are
        null-guarded parameters so the SQL stays a static literal."""
        async with self._db.tenant_connection(tenant_id) as conn:
            cursor = await conn.execute(
                """
                SELECT r.tenant_id, r.run_id, r.parent_run_id, r.status, r.goal,
                       r.plan, r.land_report, r.budget_cap_usd, r.budget_spent_usd,
                       r.budget_reserved_usd, r.environment_id, r.agent_id,
                       r.started_by, r.started_via, r.created_at, r.updated_at,
                       s.environment_id AS s_environment_id, s.status AS s_status,
                       s.sandbox_id AS s_sandbox_id,
                       s.sandbox_provider AS s_sandbox_provider,
                       s.sandbox_template AS s_sandbox_template,
                       s.generation AS s_generation,
                       s.latest_checkpoint_id AS s_latest_checkpoint_id,
                       s.latest_snapshot_ref AS s_latest_snapshot_ref,
                       s.updated_at AS s_updated_at,
                       COALESCE(p.steps_done, 0) AS steps_done,
                       COALESCE(p.steps_total, 0) AS steps_total
                FROM runs r
                LEFT JOIN run_execution_sessions s ON s.run_id = r.run_id
                LEFT JOIN LATERAL (
                    SELECT count(*) FILTER (WHERE rs.status = 'done') AS steps_done,
                           count(*) AS steps_total
                    FROM run_steps rs WHERE rs.run_id = r.run_id
                ) p ON TRUE
                WHERE (%(status)s::text IS NULL OR r.status = %(status)s)
                  AND (%(agent_id)s::text IS NULL OR r.agent_id = %(agent_id)s)
                  AND (%(created_before)s::timestamptz IS NULL
                       OR r.created_at < %(created_before)s)
                ORDER BY r.created_at DESC, r.run_id DESC
                LIMIT %(limit)s
                """,
                {
                    "status": status,
                    "agent_id": agent_id,
                    "created_before": created_before,
                    "limit": limit,
                },
            )
            rows = await cursor.fetchall()
        projections: list[RunProjection] = []
        for row in rows:
            raw = dict(row)
            session = None
            if raw.get("s_status") is not None:
                session = {
                    key.removeprefix("s_"): value
                    for key, value in raw.items()
                    if key.startswith("s_")
                }
            raw = {k: v for k, v in raw.items() if not k.startswith("s_")}
            raw["execution_session"] = session
            projections.append(RunProjection.model_validate(raw))
        return projections

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
