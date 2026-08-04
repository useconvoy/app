"""Declarative scenario runner.

Scenario files under cases/ describe one end-to-end behavior each: the run's
budget and policy, the scripted turn results, external signals keyed to
observed events, and the expected outcome (final status, exact event
sequence, budget totals, turn count). The runner executes them against the
real workflow in the time-skipping environment with the scripted fakes.
"""

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from _support.common import TEST_TASK_QUEUE, fixture_run_state, start_time_skipping_env
from _support.fakes import FakeRuntime, ScriptedTurn
from temporalio.worker import Worker

from convoy_core import RunPolicy, RunResult
from convoy_runtime.workflows.agent_run import AgentRunWorkflow

CASES_DIR = Path(__file__).parent / "cases"


@dataclass(frozen=True)
class ScenarioAction:
    """One external signal, sent once a given event type has been observed
    `occurrence` times (so a second pause can be awaited distinctly)."""

    after_event: str
    signal: Literal["pause", "resume", "land"]
    occurrence: int = 1
    actor: str = "scenario@convoy.test"


@dataclass(frozen=True)
class ScenarioExpect:
    final_status: Literal["completed", "failed"]
    events: list[str] = field(default_factory=lambda: cast(list[str], []))
    budget_spent: Decimal | None = None
    turn_calls: int | None = None
    land_status: str | None = None
    error_contains: str | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    budget_cap_usd: Decimal
    policy: RunPolicy
    turns: list[ScriptedTurn]
    actions: list[ScenarioAction]
    expect: ScenarioExpect


def load_scenario(path: Path) -> Scenario:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    run: dict[str, Any] = raw.get("run", {})
    policy_overrides: dict[str, Any] = run.get("policy", {})
    policy = RunPolicy.model_validate({"require_plan_approval": False, **policy_overrides})
    turns = [
        ScriptedTurn(
            cost_usd=Decimal(str(turn.get("cost_usd", "0.0001"))),
            outcome=turn.get("outcome", "step_done"),
            model_used=turn.get("model_used", "scripted-echo-1"),
        )
        for turn in raw.get("turns", [])
    ]
    actions = [
        ScenarioAction(
            after_event=action["after_event"],
            signal=action["signal"],
            occurrence=action.get("occurrence", 1),
        )
        for action in raw.get("actions", [])
    ]
    expect_raw: dict[str, Any] = raw["expect"]
    expect = ScenarioExpect(
        final_status=expect_raw["final_status"],
        events=list(expect_raw.get("events", [])),
        budget_spent=(
            Decimal(str(expect_raw["budget_spent"])) if "budget_spent" in expect_raw else None
        ),
        turn_calls=expect_raw.get("turn_calls"),
        land_status=expect_raw.get("land_status"),
        error_contains=expect_raw.get("error_contains"),
    )
    return Scenario(
        name=raw["name"],
        budget_cap_usd=Decimal(str(run.get("budget_cap_usd", "10"))),
        policy=policy,
        turns=turns,
        actions=actions,
        expect=expect,
    )


async def _wait_for_event(
    fake: FakeRuntime, event_type: str, occurrence: int = 1, wait_seconds: float = 30.0
) -> None:
    # Polling is deliberate: events are appended by worker activity tasks the
    # runner does not control, so there is no shared Event to await.
    async with asyncio.timeout(wait_seconds):
        while fake.event_types.count(event_type) < occurrence:  # noqa: ASYNC110
            await asyncio.sleep(0.05)


async def run_scenario(scenario: Scenario) -> tuple[RunResult, FakeRuntime]:
    fake = FakeRuntime(turns=scenario.turns)
    state = fixture_run_state(
        run_id=f"run-scenario-{scenario.name}",
        budget_cap_usd=scenario.budget_cap_usd,
        policy=scenario.policy,
    )
    env = await start_time_skipping_env()
    async with (
        env,
        Worker(
            env.client,
            task_queue=TEST_TASK_QUEUE,
            workflows=[AgentRunWorkflow],
            activities=fake.activities,
        ),
    ):
        handle = await env.client.start_workflow(
            AgentRunWorkflow.run,
            args=[state, "scenario@convoy.test"],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )
        for action in scenario.actions:
            await _wait_for_event(fake, action.after_event, action.occurrence)
            await handle.signal(action.signal, action.actor)
        result = await handle.result()
    return result, fake


def assert_scenario(scenario: Scenario, result: RunResult, fake: FakeRuntime) -> None:
    expect = scenario.expect
    assert result.status == expect.final_status, (
        f"{scenario.name}: status {result.status} != {expect.final_status}"
    )
    if expect.events:
        assert fake.event_types == expect.events, (
            f"{scenario.name}: events {fake.event_types} != {expect.events}"
        )
    if expect.turn_calls is not None:
        assert fake.turn_calls == expect.turn_calls, (
            f"{scenario.name}: turn_calls {fake.turn_calls} != {expect.turn_calls}"
        )
    if expect.budget_spent is not None:
        snapshots = [
            e.payload["budget"] for e in fake.events if isinstance(e.payload.get("budget"), dict)
        ]
        assert snapshots, f"{scenario.name}: no budget snapshots in events"
        assert Decimal(str(snapshots[-1]["spent_usd"])) == expect.budget_spent
        # Committed spend never exceeds the cap silently: the moment it does,
        # the run must have emitted budget_exhausted.
        final = snapshots[-1]
        committed = Decimal(str(final["spent_usd"])) + Decimal(str(final["reserved_usd"]))
        if committed >= scenario.budget_cap_usd:
            assert "budget_exhausted" in fake.event_types
    if expect.land_status is not None:
        assert result.land_report is not None
        assert result.land_report.status == expect.land_status
    if expect.error_contains is not None:
        assert result.error is not None and expect.error_contains in result.error
