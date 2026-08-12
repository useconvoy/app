"""Schedule tests: spec validation, deterministic firing run ids, the
Temporal Schedule mapping, and the starter activity's control-plane call."""

import json
from typing import Any

import httpx
import pytest
from temporalio.client import ScheduleOverlapPolicy
from temporalio.testing import ActivityEnvironment

from convoy_runtime.schedule_template import (
    AgentScheduleSpec,
    ScheduledRunTemplate,
    run_id_for_firing,
    schedule_id_for_agent,
    validate_schedule_spec,
)
from convoy_runtime.schedules import ScheduledRunStarter, build_schedule

pytestmark = pytest.mark.anyio


def _template(**over: Any) -> ScheduledRunTemplate:
    defaults: dict[str, Any] = {
        "tenant_id": "org_demo",
        "agent_id": "agent-1",
        "goal": "Post the morning digest",
        "environment_id": "env_x/sandbox",
        "budget_usd": "5",
        "tools": ["slack.post_message"],
        "instructions": ["Read", "checkpoint: OK?", "Post"],
        "started_by": "user-1",
    }
    defaults.update(over)
    return ScheduledRunTemplate.model_validate(defaults)


# ------------------------------------------------------------- validation


def test_valid_cron_and_timezone_pass() -> None:
    validate_schedule_spec(AgentScheduleSpec(cron="0 9 * * 1-5", timezone="America/Chicago"))


def test_wrong_field_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="5 fields"):
        validate_schedule_spec(AgentScheduleSpec(cron="0 9 * * * *"))


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        validate_schedule_spec(AgentScheduleSpec(cron="0 9 * * 1-5", timezone="Mars/Olympus"))


# ---------------------------------------------------------------- run ids


def test_firing_run_ids_are_deterministic_and_distinct() -> None:
    first = run_id_for_firing("scheduled-run-agent-1-2026-08-12T09:00:00Z")
    again = run_id_for_firing("scheduled-run-agent-1-2026-08-12T09:00:00Z")
    tomorrow = run_id_for_firing("scheduled-run-agent-1-2026-08-13T09:00:00Z")
    assert first == again
    assert first != tomorrow
    assert first.startswith("run-sch-")


# ----------------------------------------------------------- schedule map


def test_build_schedule_maps_spec_policy_and_action() -> None:
    template = _template()
    schedule = build_schedule(
        template, AgentScheduleSpec(cron="0 9 * * 1-5", timezone="UTC", enabled=False), "queue-1"
    )
    assert schedule.spec.cron_expressions == ["0 9 * * 1-5"]
    assert schedule.spec.time_zone_name == "UTC"
    assert schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
    assert schedule.state.paused is True
    action = schedule.action
    assert action.id == "scheduled-run-agent-1"  # type: ignore[union-attr]
    assert action.task_queue == "queue-1"  # type: ignore[union-attr]
    assert action.args[0].agent_id == "agent-1"  # type: ignore[union-attr, index]
    assert schedule_id_for_agent("agent-1") == "agent-schedule-agent-1"


# ------------------------------------------------------- starter activity


async def test_starter_creates_the_run_through_the_control_plane() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            202, json={"run_id": captured["body"]["run_id"], "status": "planning"}
        )

    starter = ScheduledRunStarter(
        control_plane_url="http://control-plane.test",
        token="service-token",
        transport=httpx.MockTransport(handler),
    )
    env = ActivityEnvironment()
    run_id = await env.run(starter.create_scheduled_run, _template())

    assert captured["path"] == "/runs"
    assert captured["headers"]["authorization"] == "Bearer service-token"
    assert captured["headers"]["x-tenant-id"] == "org_demo"
    assert captured["headers"]["x-actor-id"] == "user-1"
    body = captured["body"]
    assert body["run_id"] == run_id
    assert body["started_via"] == "schedule"
    assert body["agent_id"] == "agent-1"
    assert body["environment_id"] == "env_x/sandbox"
    assert body["instructions"] == ["Read", "checkpoint: OK?", "Post"]
    # The run id derives from the activity's workflow id — deterministic per
    # firing, so a retry converges on the same run.
    rerun = await env.run(starter.create_scheduled_run, _template())
    assert rerun == run_id


async def test_starter_raises_on_control_plane_rejection() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "unknown tool"})

    starter = ScheduledRunStarter(
        control_plane_url="http://control-plane.test",
        token="service-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await ActivityEnvironment().run(starter.create_scheduled_run, _template())
