"""Agent schedules: "every weekday at 9am" as a Temporal Schedule.

One Temporal Schedule per agent (id ``agent-schedule-{agent_id}``) carries a
frozen run template — everything the console resolved at save time (binding
target, tools, instructions, budget, tenant). Each firing starts a
``ScheduledRunWorkflow`` (workflows/scheduled_run.py) whose single activity
creates the run through the control plane's own ``POST /runs``.

Overlap policy is SKIP: while a firing's starter workflow is still running,
the next firing is dropped rather than queued. The catchup window is one
hour: worker downtime shorter than that fires the missed occurrence late;
anything older is skipped, never replayed in bulk.

Pure types and id derivations live in ``schedule_template.py`` so the
workflow sandbox never imports the HTTP client below.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
from temporalio import activity
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.service import RPCError, RPCStatusCode

from convoy_runtime.activities import names
from convoy_runtime.schedule_template import (
    SCHEDULED_RUN_WORKFLOW,
    AgentScheduleSpec,
    ScheduledRunTemplate,
    run_id_for_firing,
    schedule_id_for_agent,
)

_CATCHUP_WINDOW = timedelta(hours=1)


def build_schedule(
    template: ScheduledRunTemplate, spec: AgentScheduleSpec, task_queue: str
) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            SCHEDULED_RUN_WORKFLOW,
            args=[template],
            # Temporal appends the fire timestamp per action, so each firing
            # gets a distinct starter-workflow id derived from this prefix.
            id=f"scheduled-run-{template.agent_id}",
            task_queue=task_queue,
        ),
        spec=ScheduleSpec(cron_expressions=[spec.cron], time_zone_name=spec.timezone),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,
            catchup_window=_CATCHUP_WINDOW,
        ),
        # Temporal normalizes cron_expressions into calendar specs, so the
        # authored cron would vanish from describe; the note carries it back.
        state=ScheduleState(paused=not spec.enabled, note=spec.cron),
    )


async def upsert_agent_schedule(
    client: Client,
    template: ScheduledRunTemplate,
    spec: AgentScheduleSpec,
    task_queue: str,
) -> None:
    """Replace the agent's schedule definition in place (keeping its
    identity and recent-actions history), creating it on first save."""
    schedule = build_schedule(template, spec, task_queue)
    handle = client.get_schedule_handle(schedule_id_for_agent(template.agent_id))

    def _replace(_input: ScheduleUpdateInput) -> ScheduleUpdate:
        return ScheduleUpdate(schedule=schedule)

    try:
        await handle.update(_replace)
    except RPCError as err:
        if err.status != RPCStatusCode.NOT_FOUND:
            raise
        await client.create_schedule(schedule_id_for_agent(template.agent_id), schedule)


async def delete_agent_schedule(client: Client, agent_id: str) -> bool:
    """Remove the agent's schedule; False when none existed."""
    handle = client.get_schedule_handle(schedule_id_for_agent(agent_id))
    try:
        await handle.delete()
    except RPCError as err:
        if err.status == RPCStatusCode.NOT_FOUND:
            return False
        raise
    return True


class ScheduleView:
    """Describe output the control plane serves; plain data, no client."""

    def __init__(
        self, *, cron: str, timezone: str, enabled: bool, next_run_times: list[str]
    ) -> None:
        self.cron = cron
        self.timezone = timezone
        self.enabled = enabled
        self.next_run_times = next_run_times


async def describe_agent_schedule(client: Client, agent_id: str) -> ScheduleView | None:
    handle = client.get_schedule_handle(schedule_id_for_agent(agent_id))
    try:
        description = await handle.describe()
    except RPCError as err:
        if err.status == RPCStatusCode.NOT_FOUND:
            return None
        raise
    spec = description.schedule.spec
    cron = (
        spec.cron_expressions[0]
        if spec.cron_expressions
        else (description.schedule.state.note or "")
    )
    return ScheduleView(
        cron=cron,
        timezone=spec.time_zone_name or "UTC",
        enabled=not description.schedule.state.paused,
        next_run_times=[moment.isoformat() for moment in description.info.next_action_times[:3]],
    )


class ScheduledRunStarter:
    """The activity half: one bounded HTTP call to the control plane. The
    dev token is the same service credential the website presents; the fired
    run's actor is the schedule's saver (or the schedule system identity)."""

    def __init__(
        self,
        *,
        control_plane_url: str,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = control_plane_url.rstrip("/")
        self._token = token
        self._transport = transport

    @activity.defn(name=names.CREATE_SCHEDULED_RUN)
    async def create_scheduled_run(self, template: ScheduledRunTemplate) -> str:
        workflow_id = activity.info().workflow_id or "scheduled-run-unknown"
        run_id = run_id_for_firing(workflow_id)
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/runs",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "X-Actor-Id": template.started_by or "system:schedule",
                    "X-Tenant-Id": template.tenant_id,
                },
                json={
                    "goal": template.goal,
                    "environment_id": template.environment_id,
                    "budget_usd": template.budget_usd,
                    "run_id": run_id,
                    "tools": template.tools,
                    "instructions": template.instructions,
                    "success_criteria": [],
                    "fixture_gates": {},
                    "max_children": 5,
                    "agent_id": template.agent_id,
                    "started_by": template.started_by,
                    "started_via": "schedule",
                },
            )
            response.raise_for_status()
        return run_id
