"""Full-loop tests — port of tests/sandbox-instance.test.ts: sandbox service +
run_until driving a minimal inline fake RuntimeClient (a tiny state machine —
deliberately NOT the executors package): email out → park on timer →
cooperative reply → approval gate → scripted approve → land. Plus the
deadlock and sim-guard paths.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from convoy_evals.runtime.ports import (
    ClockPort,
    DrainReport,
    OpenGate,
    ResolveGateInput,
    RuntimeClient,
    StartMissionInput,
)
from convoy_evals.sandbox import create_sandbox_service
from convoy_evals.schema.scenario import Scenario

HERE = Path(__file__).resolve().parent
PACK_MINI = str(HERE / "fixtures" / "pack_mini")
T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def make_scenario(**over: Any) -> Scenario:
    base: Dict[str, Any] = {
        "id": "sc-mini",
        "title": "mini renewal",
        "missionType": "renewal",
        "kind": "single",
        "fixture": {"pack": "pack_mini", "packHash": "test-unhashed"},
        "t0": "2026-08-01",
        "seed": 42,
        "trigger": {"kind": "api", "missionSpec": {"missionType": "renewal", "goal": "Renew POL-1"}},
        "counterparties": [
            {
                "actorId": "carrier",
                "channel": "email",
                "owns": ["carrier@acme.sim"],
                "profile": "cooperative",
                "params": {
                    "replyTemplate": {"subject": "RE: Loss runs", "body": "Attached as requested."},
                    "attachments": ["attachments/loss-runs.txt"],
                },
            }
        ],
        "approvals": {
            "mode": "scripted",
            "steps": [
                {
                    "id": "s1",
                    "expect": {
                        "kind": "action-approval",
                        "payload": {"all": [{"path": "action", "op": "eq", "value": "bind"}]},
                    },
                    "resolve": {"kind": "approve"},
                }
            ],
        },
        "budgets": {"usd": 5, "simTime": "30d", "wallClock": "60s"},
        "answerKeyRef": {"path": "keys/mini.json", "hash": "test-unhashed"},
        "graders": [],
        "provenance": {"kind": "authored"},
    }
    base.update(over)
    return Scenario.model_validate(base)


class FakeRenewalRuntime(RuntimeClient):
    """Fake renewal runtime: send loss-runs request → park on a 3d follow-up
    timer → once the reply is in, raise a bind approval gate → once approved,
    update the policy and land."""

    def __init__(self, env: Dict[str, Any]) -> None:
        self.env = env
        self.drains: List[str] = []
        self.resolve_calls: List[ResolveGateInput] = []
        self.phase = "init"
        self.timer_at: Optional[datetime] = None
        self.gate = OpenGate(
            gateId="g1",
            kind="action-approval",
            deadlineAt=None,
            payload={"action": "bind", "policyId": "POL-1"},
            stepTag="bind-policy",
        )

    async def start_mission(self, input: StartMissionInput, clock: ClockPort) -> str:
        return "m-fake"

    async def drain(self, mission_id: str, clock: ClockPort) -> DrainReport:
        now = clock.now()
        self.drains.append(now.isoformat())
        if self.phase == "init":
            await self.env["gateway"].invoke(
                "email.send",
                {
                    "to": "carrier@acme.sim",
                    "subject": "Loss runs request",
                    "body": "Please send loss runs for POL-1.",
                },
                _ctx(mission_id),
            )
            self.timer_at = now + 3 * DAY
            self.phase = "waiting"
            return DrainReport(terminal=None, openGates=[], nextTimerAt=self.timer_at, stepsExecuted=1)
        if self.phase == "waiting":
            if now < self.timer_at:
                return DrainReport(terminal=None, openGates=[], nextTimerAt=self.timer_at, stepsExecuted=0)
            replies = [
                m
                for m in self.env["world"].list_messages(direction="inbound")
                if m.subject.startswith("RE:")
            ]
            assert replies, "cooperative reply must have been delivered before the 3d timer fired"
            self.phase = "gated"
            return DrainReport(terminal=None, openGates=[self.gate], nextTimerAt=None, stepsExecuted=1)
        if self.phase == "gated":
            return DrainReport(terminal=None, openGates=[self.gate], nextTimerAt=None, stepsExecuted=0)
        # approved
        await self.env["gateway"].invoke(
            "ams.update_policy",
            {"policyId": "POL-1", "fields": {"renewal_status": "bound"}},
            _ctx(mission_id),
        )
        return DrainReport(terminal="landed", openGates=[], nextTimerAt=None, stepsExecuted=1)

    async def resolve_gate(self, input: ResolveGateInput, clock: ClockPort) -> None:
        self.resolve_calls.append(input)
        if input.gateId == "g1" and input.resolution.kind == "approve":
            self.phase = "approved"


def _ctx(mission_id: str):
    from convoy_evals.sandbox import ToolCallCtx

    return ToolCallCtx(missionId=mission_id)


def make_fake_renewal_runtime():
    holder: Dict[str, Any] = {}

    def factory(env: Dict[str, Any]) -> RuntimeClient:
        holder["runtime"] = FakeRenewalRuntime(env)
        return holder["runtime"]

    return factory, holder


class ParkedRuntime(RuntimeClient):
    """Emails a counterparty then parks on awaited inbound with NO timer."""

    def __init__(self, env: Dict[str, Any]) -> None:
        self.env = env
        self.sent = False

    async def start_mission(self, input: StartMissionInput, clock: ClockPort) -> str:
        return "m-parked"

    async def drain(self, mission_id: str, clock: ClockPort) -> DrainReport:
        if not self.sent:
            self.sent = True
            await self.env["gateway"].invoke(
                "email.send",
                {"to": "carrier@acme.sim", "subject": "Anyone there?", "body": "Please reply."},
                _ctx(mission_id),
            )
        replies = [
            m
            for m in self.env["world"].list_messages(direction="inbound")
            if m.subject.startswith("RE:")
        ]
        if replies:
            return DrainReport(terminal="landed", openGates=[], nextTimerAt=None, stepsExecuted=1)
        return DrainReport(terminal=None, openGates=[], nextTimerAt=None, stepsExecuted=1)

    async def resolve_gate(self, input: ResolveGateInput, clock: ClockPort) -> None:
        pass


# ---------------------------------------------------------------------------
# (f) full run_until
# ---------------------------------------------------------------------------


async def test_run_until_email_timer_reply_gate_approve_land():
    factory, holder = make_fake_renewal_runtime()
    service = create_sandbox_service()
    instance = await service.create(make_scenario(), factory, pack_dir=PACK_MINI)
    state = holder["runtime"]

    report = await instance.run_until({"kind": "terminal"})

    assert report.terminal == "landed"
    assert report.deadlock is False
    assert report.guardTripped is None
    assert report.simNow >= T0 + 3 * DAY, "sim time advanced through the timer"

    # DES advance order: t0 (send) → reply delivery (1-2d) → timer fire (3d) →
    # same-instant approved drain.
    drain_times = [datetime.fromisoformat(iso) for iso in state.drains]
    for prev, cur in zip(drain_times, drain_times[1:]):
        assert cur >= prev, "sim clock never goes backwards across drains"
    reply_drain = drain_times[1]
    assert T0 + DAY < reply_drain < T0 + 2 * DAY, "woken for the reply delivery"
    assert drain_times[2] == T0 + 3 * DAY, "then woken for the timer"

    # Gate resolution: attributed to the script step, mirrored into the log.
    assert len(state.resolve_calls) == 1
    assert state.resolve_calls[0].gateId == "g1"
    assert state.resolve_calls[0].resolvedBy == "harness:s1"
    gate_report = instance.gate_report()
    assert [(r.gateId, r.stepId, r.resolution) for r in gate_report.resolutions] == [
        ("g1", "s1", "approve")
    ]
    assert gate_report.neverRaised == []
    assert gate_report.unexpected == []

    # End state: world reflects the approved action; teardown bundle carries it.
    assert instance.world.query("records.policy.POL-1.renewal_status") == ["bound"]
    out = instance.destroy()
    events, world = out["events"], out["world"]
    assert any(e.type == "human_intervention" and e.kind == "gate_resolution" for e in events)
    assert any(e.type == "tool_intent" and e.tool == "email.send" for e in events)
    assert any(e.type == "tool_result" and e.tool == "ams.update_policy" for e in events)
    reply = next(
        (m for m in world.messages if m.direction == "inbound" and m.subject == "RE: Loss runs"),
        None,
    )
    assert reply is not None, "counterparty reply is in the exported bundle"
    assert [a.name for a in reply.attachments] == ["loss-runs.txt"]
    assert len(world.hash) == 64


async def test_run_until_gate_open_stop_halts_with_gate_open():
    factory, _ = make_fake_renewal_runtime()
    instance = await create_sandbox_service().create(make_scenario(), factory, pack_dir=PACK_MINI)
    report = await instance.run_until({"kind": "gate-open"})
    assert report.terminal is None
    assert len(report.openGates) == 1
    assert report.openGates[0].gateId == "g1"
    assert instance.gate_report().resolutions == [], "script has not resolved anything yet"


# ---------------------------------------------------------------------------
# (g) deadlock
# ---------------------------------------------------------------------------


async def test_run_until_silent_counterparty_deadlocks_with_diagnosis():
    scenario = make_scenario(
        counterparties=[
            {
                "actorId": "carrier",
                "channel": "email",
                "owns": ["carrier@acme.sim"],
                "profile": "silent",
                "default": "silent",
            }
        ]
    )
    instance = await create_sandbox_service().create(scenario, ParkedRuntime, pack_dir=PACK_MINI)
    report = await instance.run_until({"kind": "terminal"})

    assert report.terminal is None
    assert report.deadlock is True
    assert report.deadlockDiagnosis
    assert "no pending timers" in report.deadlockDiagnosis
    assert "no queued counterparty deliveries" in report.deadlockDiagnosis
    assert "no open gates" in report.deadlockDiagnosis
    assert report.simNow == T0, "nothing ever advanced the clock"


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


async def test_run_until_sim_guard_trips_beyond_sim_budget():
    scenario = make_scenario(
        budgets={"usd": 5, "simTime": "2d", "wallClock": "60s", "onExhaustion": "fail"}
    )
    # Cooperative reply lands within 1-2d of the send, but the runtime only
    # wakes for its 3d timer — beyond the 2d sim budget once the reply is consumed.
    factory, _ = make_fake_renewal_runtime()
    instance = await create_sandbox_service().create(scenario, factory, pack_dir=PACK_MINI)
    report = await instance.run_until({"kind": "terminal"})
    assert report.guardTripped == "sim"
    assert report.terminal is None


async def test_run_until_sim_time_stop_caps_advance_at_requested_instant():
    factory, _ = make_fake_renewal_runtime()
    instance = await create_sandbox_service().create(make_scenario(), factory, pack_dir=PACK_MINI)
    stop_at = T0 + DAY / 2
    report = await instance.run_until({"kind": "sim-time", "at": stop_at})
    assert report.terminal is None
    assert report.simNow == stop_at
