"""Declarative scenario runner.

Scenario files under cases/ describe one end-to-end behavior each: the run's
budget and policy, the scripted turn results (including gate requests and
proposed revisions), human gates attached to fixture-plan steps, external
signals keyed to observed events (pause/resume/land, steers, plan approval
decisions, gate responses), and the expected outcome (final status, exact
event sequence, budget totals, turn count, final plan version). The runner
executes them against the real workflow in the time-skipping environment with
the scripted fakes; a "skip_time" action advances the environment clock so
durable timers (gate timeouts) fire deterministically.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from _support.common import TEST_TASK_QUEUE, fixture_run_state, start_time_skipping_env
from _support.fakes import FakeRuntime, ScriptedTurn
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from convoy_core import HumanGate, PlanPatchOp, RunPolicy, RunResult, SteerMessage
from convoy_runtime.signals import GateResponse, PlanApprovalDecision
from convoy_runtime.workflows.agent_run import AgentRunWorkflow

CASES_DIR = Path(__file__).parent / "cases"

SignalKind = Literal[
    "pause", "resume", "land", "steer", "approve", "reject", "respond", "skip_time"
]


@dataclass(frozen=True)
class ScenarioAction:
    """One external action, sent once a given event type has been observed
    `occurrence` times (so a second pause can be awaited distinctly).
    "skip_time" is not a signal: it advances the time-skipping environment's
    clock so durable timers (gate timeouts) fire deterministically."""

    after_event: str
    signal: SignalKind
    occurrence: int = 1
    actor: str = "scenario@convoy.test"
    # steer fields
    mode: Literal["note", "redirect"] = "note"
    body: str = ""
    # approval fields
    plan_version: int = 1
    reason: str | None = None
    # gate response fields
    step_id: str = ""
    response: str = ""
    # skip_time field
    seconds: float = 0.0


@dataclass(frozen=True)
class ScenarioExpect:
    final_status: Literal["completed", "failed"]
    events: list[str] = field(default_factory=lambda: cast(list[str], []))
    budget_spent: Decimal | None = None
    turn_calls: int | None = None
    land_status: str | None = None
    error_contains: str | None = None
    plan_version: int | None = None
    revision_steer_linked: bool = False


@dataclass(frozen=True)
class Scenario:
    name: str
    budget_cap_usd: Decimal
    policy: RunPolicy
    gates: dict[str, HumanGate]
    turns: list[ScriptedTurn]
    actions: list[ScenarioAction]
    expect: ScenarioExpect


def _parse_gate(raw: dict[str, Any]) -> HumanGate:
    timeout_seconds = raw.get("timeout_seconds")
    return HumanGate(
        kind=raw.get("kind", "approval"),
        prompt=raw.get("prompt", ""),
        timeout=timedelta(seconds=timeout_seconds) if timeout_seconds is not None else None,
        on_timeout=raw.get("on_timeout", "pause"),
    )


def _parse_turn(raw: dict[str, Any]) -> ScriptedTurn:
    return ScriptedTurn(
        cost_usd=Decimal(str(raw.get("cost_usd", "0.0001"))),
        outcome=raw.get("outcome", "step_done"),
        model_used=raw.get("model_used", "scripted-echo-1"),
        gate=_parse_gate(cast("dict[str, Any]", raw["gate"])) if "gate" in raw else None,
        ops=tuple(PlanPatchOp.model_validate(op) for op in cast("list[Any]", raw.get("ops", []))),
    )


def load_scenario(path: Path) -> Scenario:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    run: dict[str, Any] = raw.get("run", {})
    policy_overrides: dict[str, Any] = run.get("policy", {})
    policy = RunPolicy.model_validate({"require_plan_approval": False, **policy_overrides})
    gates = {
        str(step_id): _parse_gate(cast("dict[str, Any]", gate))
        for step_id, gate in cast("dict[Any, Any]", run.get("gates", {})).items()
    }
    turns = [_parse_turn(cast("dict[str, Any]", turn)) for turn in raw.get("turns", [])]
    actions = [
        ScenarioAction(
            after_event=action["after_event"],
            signal=action["signal"],
            occurrence=action.get("occurrence", 1),
            actor=action.get("actor", "scenario@convoy.test"),
            mode=action.get("mode", "note"),
            body=action.get("body", ""),
            plan_version=action.get("plan_version", 1),
            reason=action.get("reason"),
            step_id=action.get("step_id", ""),
            response=action.get("response", ""),
            seconds=float(action.get("seconds", 0.0)),
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
        plan_version=expect_raw.get("plan_version"),
        revision_steer_linked=expect_raw.get("revision_steer_linked", False),
    )
    return Scenario(
        name=raw["name"],
        budget_cap_usd=Decimal(str(run.get("budget_cap_usd", "10"))),
        policy=policy,
        gates=gates,
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


async def _send_action(
    env: WorkflowEnvironment,
    handle: WorkflowHandle[AgentRunWorkflow, RunResult],
    fake: FakeRuntime,
    action: ScenarioAction,
) -> None:
    if action.signal == "skip_time":
        # Advance the test server's clock so durable timers (gate timeouts)
        # fire without waiting in real time.
        await env.sleep(action.seconds)
    elif action.signal == "steer":
        steer_id = f"steer-{len(fake.sent_steer_ids) + 1}"
        fake.sent_steer_ids.append(steer_id)
        await handle.signal(
            AgentRunWorkflow.steer,
            SteerMessage(
                id=steer_id,
                author="human",
                author_id=action.actor,
                mode=action.mode,
                body=action.body,
            ),
        )
    elif action.signal in ("approve", "reject"):
        await handle.signal(
            AgentRunWorkflow.approve_plan,
            PlanApprovalDecision(
                plan_version=action.plan_version,
                approve=action.signal == "approve",
                reason=action.reason,
                actor=action.actor,
            ),
        )
    elif action.signal == "respond":
        await handle.signal(
            AgentRunWorkflow.human_response,
            GateResponse(step_id=action.step_id, response=action.response, actor=action.actor),
        )
    else:
        await handle.signal(action.signal, action.actor)


async def run_scenario(scenario: Scenario) -> tuple[RunResult, FakeRuntime]:
    fake = FakeRuntime(turns=scenario.turns, plan_gates=scenario.gates)
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
            await _send_action(env, handle, fake, action)
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
    if expect.plan_version is not None:
        plans = [e.payload["plan"] for e in fake.events if isinstance(e.payload.get("plan"), dict)]
        assert plans, f"{scenario.name}: no plan payloads in events"
        assert plans[-1]["version"] == expect.plan_version, (
            f"{scenario.name}: plan version {plans[-1]['version']} != {expect.plan_version}"
        )
    if expect.revision_steer_linked:
        applied = fake.events_of("revision_applied")
        assert applied, f"{scenario.name}: no revision_applied event"
        linked = applied[-1].payload.get("steer_ids")
        assert linked == fake.sent_steer_ids, (
            f"{scenario.name}: revision steer_ids {linked} != sent {fake.sent_steer_ids}"
        )
