"""ScriptedRuntime + golden executor tests. Deliberately does NOT import the
convoy_evals.sandbox implementations (built concurrently by another owner) --
tiny inline fakes implement just the surfaces ExecutorCtx/ScriptedRuntime
touch: a dict-backed ToolGateway, a WorldStore-ish message/file store, and a
controllable SimClock.

Port of tests/executors.test.ts.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import pytest

from convoy_evals.executors.golden import golden_renewal_prep
from convoy_evals.executors.scripted_runtime import create_scripted_runtime_factory
from convoy_evals.runtime.log import EventLog
from convoy_evals.runtime.ports import (
    GateResolutionWire,
    ResolveGateInput,
    StartMissionInput,
)
from convoy_evals.sandbox.api import (
    ScriptedExecutorFn,
    SimClock,
    ToolCallCtx,
    ToolGateway,
    WorldFile,
    WorldMessage,
)

DAY_MS = 86_400_000
DAY = timedelta(milliseconds=DAY_MS)
T0 = datetime(2026, 8, 3, 0, 0, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClock(SimClock):
    def __init__(self, t0: datetime) -> None:
        self._now = t0

    def now(self) -> datetime:
        return self._now

    def advance_to(self, t: datetime) -> None:
        if t > self._now:
            self._now = t


class FakeWorld:
    """Duck-typed WorldStore covering exactly what the scripted runtime uses."""

    def __init__(self) -> None:
        self.inbound: List[WorldMessage] = []
        self.files: List[WorldFile] = []
        self._n = 0

    def deliver(self, from_: str, to: List[str], subject: str, body: str, ts: str) -> WorldMessage:
        self._n += 1
        msg = WorldMessage(
            id="msg-{0}".format(self._n),
            threadId="thread-1",
            from_=from_,
            to=to,
            subject=subject,
            body=body,
            attachments=[],
            ts=ts,
            direction="inbound",
        )
        self.inbound.append(msg)
        return msg

    def list_messages(
        self,
        direction: Optional[str] = None,
        to_contains: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> List[WorldMessage]:
        msgs = list(self.inbound)
        if direction is not None:
            msgs = [m for m in msgs if m.direction == direction]
        if to_contains is not None:
            msgs = [m for m in msgs if any(to_contains in t for t in m.to)]
        return msgs

    def put_file(self, name: str, mime: str, content: str) -> WorldFile:
        self._n += 1
        file = WorldFile(
            id="file-{0}".format(self._n),
            name=name,
            mime=mime,
            hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            content=content,
        )
        self.files.append(file)
        return file


Handler = Callable[[Dict[str, Any], ToolCallCtx], Any]


class FakeGateway(ToolGateway):
    def __init__(self, handlers: Dict[str, Handler]) -> None:
        self.handlers = handlers
        self.calls: List[Dict[str, Any]] = []

    async def invoke(self, tool: str, args: Any, ctx: ToolCallCtx) -> Any:
        record = args if isinstance(args, dict) else {}
        self.calls.append({"tool": tool, "args": record})
        h = self.handlers.get(tool)
        if h is None:
            raise RuntimeError("fake gateway: no handler for tool {0}".format(tool))
        return h(record, ctx)

    def manifest_hash(self) -> str:
        return "fake-manifest"


def make_runtime(executor: ScriptedExecutorFn, gateway: ToolGateway, world: Any, clock: SimClock, log: EventLog):
    return create_scripted_runtime_factory(executor)(
        {"gateway": gateway, "log": log, "world": world, "clock": clock}
    )


def start_input(goal: str, params: Optional[Dict[str, Any]] = None) -> StartMissionInput:
    return StartMissionInput(
        missionType="t",
        environmentId="env-1",
        goal=goal,
        params=params if params is not None else {},
    )


# ---------------------------------------------------------------------------
# (a) wait() parks; drain reports nextTimerAt; advancing the clock resumes
# ---------------------------------------------------------------------------


async def test_wait_parks_on_sim_clock_and_resumes_via_drain():
    clock = FakeClock(T0)
    log = EventLog(clock)
    world = FakeWorld()
    gateway = FakeGateway({})

    async def exec_fn(ctx):
        await ctx.wait(3 * DAY_MS)
        ctx.land("done waiting")

    rt = make_runtime(exec_fn, gateway, world, clock, log)
    mission_id = await rt.start_mission(start_input("wait then land"), clock)

    report = await rt.drain(mission_id, clock)
    assert report.terminal is None
    assert report.stepsExecuted == 0
    assert report.nextTimerAt is not None
    assert report.nextTimerAt == T0 + 3 * DAY

    # Sequential drains with nothing runnable are idempotent.
    report = await rt.drain(mission_id, clock)
    assert report.terminal is None
    assert report.stepsExecuted == 0
    assert report.nextTimerAt == T0 + 3 * DAY

    clock.advance_to(T0 + 3 * DAY)
    report = await rt.drain(mission_id, clock)
    assert report.terminal == "landed"
    assert report.stepsExecuted == 1
    assert report.nextTimerAt is None

    types = [e.type for e in log.for_mission(mission_id)]
    assert types == ["mission_started", "timer_scheduled", "timer_fired", "terminal_outcome"]


# ---------------------------------------------------------------------------
# (b) raise_gate parks; drain lists the open gate; resolve_gate resumes
# ---------------------------------------------------------------------------


async def test_raise_gate_parks_until_resolve_gate():
    clock = FakeClock(T0)
    log = EventLog(clock)
    world = FakeWorld()
    gateway = FakeGateway({})
    seen: List[str] = []

    async def exec_fn(ctx):
        res = await ctx.raise_gate(
            "action-approval",
            {"action": "ship-it"},
            step_tag="step-1",
            item_ref="packet/POL-X",
        )
        seen.append(res["resolution"])
        if res["resolution"] == "approve":
            ctx.land("approved")
        else:
            ctx.fail("not approved")

    rt = make_runtime(exec_fn, gateway, world, clock, log)
    mission_id = await rt.start_mission(start_input("gate"), clock)

    report = await rt.drain(mission_id, clock)
    assert report.terminal is None
    assert len(report.openGates) == 1
    gate = report.openGates[0]
    assert gate.kind == "action-approval"
    assert gate.payload == {"action": "ship-it"}
    assert gate.stepTag == "step-1"
    assert gate.deadlineAt is None

    await rt.resolve_gate(
        ResolveGateInput(
            gateId=gate.gateId,
            resolution=GateResolutionWire(kind="approve"),
            resolvedBy="test-human",
        ),
        clock,
    )
    report = await rt.drain(mission_id, clock)
    assert report.terminal == "landed"
    assert len(report.openGates) == 0
    assert seen == ["approve"]

    types = [e.type for e in log.for_mission(mission_id)]
    assert types == ["mission_started", "gate_raised", "gate_resolved", "terminal_outcome"]
    resolved = next(e for e in log.for_mission(mission_id) if e.type == "gate_resolved")
    assert resolved.resolvedBy == "test-human"
    # gate_resolved inherits the raising gate's item attribution.
    assert resolved.itemRef == "packet/POL-X"
    raised = next(e for e in log.for_mission(mission_id) if e.type == "gate_raised")
    assert raised.itemRef == "packet/POL-X"


# ---------------------------------------------------------------------------
# (c) await_inbound resolves only once a matching message exists; two awaits
#     consume distinct messages
# ---------------------------------------------------------------------------


async def test_await_inbound_parks_until_matching_message_arrives():
    clock = FakeClock(T0)
    log = EventLog(clock)
    world = FakeWorld()
    gateway = FakeGateway({})
    got: List[WorldMessage] = []

    async def exec_fn(ctx):
        got.append(await ctx.await_inbound(to_contains="insured@client.test"))
        got.append(await ctx.await_inbound(to_contains="insured@client.test"))
        ctx.land("got both")

    rt = make_runtime(exec_fn, gateway, world, clock, log)
    mission_id = await rt.start_mission(start_input("inbound"), clock)

    report = await rt.drain(mission_id, clock)
    assert report.terminal is None
    assert len(got) == 0

    # to_contains matches the FROM address too (reply-style matching).
    world.deliver(
        from_="insured@client.test",
        to=["agent@convoy.test"],
        subject="Re: exposure",
        body="first reply",
        ts=iso(T0 + 1 * DAY),
    )
    report = await rt.drain(mission_id, clock)
    assert report.terminal is None
    assert len(got) == 1
    assert got[0].body == "first reply"

    world.deliver(
        from_="insured@client.test",
        to=["agent@convoy.test"],
        subject="Re: exposure again",
        body="second reply",
        ts=iso(T0 + 2 * DAY),
    )
    report = await rt.drain(mission_id, clock)
    assert report.terminal == "landed"
    assert len(got) == 2
    assert got[0].id != got[1].id
    assert got[1].body == "second reply"


# ---------------------------------------------------------------------------
# (d) terminal semantics: land, implicit land on return, raise -> failed
# ---------------------------------------------------------------------------


async def test_return_lands_and_raise_fails_with_error_summary():
    clock = FakeClock(T0)
    log = EventLog(clock)
    world = FakeWorld()
    gateway = FakeGateway({})

    async def returns(ctx):
        return None

    rt1 = make_runtime(returns, gateway, world, clock, log)
    m1 = await rt1.start_mission(start_input("return"), clock)
    r1 = await rt1.drain(m1, clock)
    assert r1.terminal == "landed"
    t1 = next(e for e in log.for_mission(m1) if e.type == "terminal_outcome")
    assert t1.status == "landed"
    assert t1.judgedBy == "agent"

    async def throws(ctx):
        raise RuntimeError("boom: could not fetch policy")

    rt2 = make_runtime(throws, gateway, world, clock, log)
    m2 = await rt2.start_mission(start_input("throw"), clock)
    r2 = await rt2.drain(m2, clock)
    assert r2.terminal == "failed"
    t2 = next(e for e in log.for_mission(m2) if e.type == "terminal_outcome")
    assert t2.status == "failed"
    assert "boom" in (t2.summary or "")


# ---------------------------------------------------------------------------
# (e) + (f) golden happy-path smoke, run through a drive loop; determinism
# ---------------------------------------------------------------------------


async def run_golden_smoke() -> Dict[str, Any]:
    clock = FakeClock(T0)
    log = EventLog(clock)
    world = FakeWorld()

    policies: Dict[str, Dict[str, Any]] = {
        "POL-1": {
            "insured_email": "ins1@client.test",
            "insured_name": "Ada One",
            "carrier": "Acme Mutual",
            "premium": 1200,
            "expiring_date": "2026-10-01",
            "prior_carrier_contact": "renewals@oldco.test",
        },
        "POL-2": {
            "insured_email": "ins2@client.test",
            "insured_name": "Bob Two",
            "carrier": "Zenith Ins",
            "premium": 3400,
            "expiring_date": "2026-11-15",
            "prior_carrier_contact": "contact@priorco.test",
        },
    }
    portal_requests: Dict[str, datetime] = {}  # requestId -> fulfillAt
    updates: List[Dict[str, Any]] = []
    replied: set = set()
    req_n = [0]

    def message_as_dict(m: WorldMessage) -> Dict[str, Any]:
        return {"id": m.id, "from": m.from_, "to": m.to, "subject": m.subject, "body": m.body, "ts": m.ts}

    def ams_get_policy(args: Dict[str, Any], _ctx: ToolCallCtx) -> Any:
        policy = policies.get(str(args.get("policyId")))
        if policy is None:
            raise RuntimeError("no such policy {0}".format(args.get("policyId")))
        # FLATTENED result: fields at top level next to policyId.
        return dict({"policyId": args.get("policyId")}, **policy)

    def ams_update_policy(args: Dict[str, Any], _ctx: ToolCallCtx) -> Any:
        updates.append(
            {"policyId": str(args.get("policyId")), "fields": args.get("fields") if isinstance(args.get("fields"), dict) else {}}
        )
        return {"ok": True}

    def email_send(args: Dict[str, Any], _ctx: ToolCallCtx) -> Any:
        to = [str(t) for t in args.get("to")] if isinstance(args.get("to"), list) else []
        # Cooperative insured: replies 1 sim-day after any request addressed to them.
        for policy_id, policy in policies.items():
            insured = str(policy["insured_email"])
            if insured in to and insured not in replied:
                replied.add(insured)
                world.deliver(
                    from_=insured,
                    to=["agent@convoy.test"],
                    subject="Re: {0}".format(args.get("subject") or ""),
                    body="Exposure update for {0}: fleet size 12, payroll 1.4M, no new operations.".format(policy_id),
                    ts=iso(clock.now() + 1 * DAY),
                )
        return {"ok": True, "messageId": "out-{0}".format(len(gateway.calls))}

    def email_list_inbox(args: Dict[str, Any], _ctx: ToolCallCtx) -> Any:
        # Only messages that have "arrived" by sim-now are visible.
        msgs = [m for m in world.inbound if datetime.fromisoformat(m.ts.replace("Z", "+00:00")) <= clock.now()]
        if isinstance(args.get("toContains"), str):
            needle = args["toContains"]
            msgs = [m for m in msgs if any(needle in t for t in m.to)]
        if isinstance(args.get("subjectRegex"), str):
            pattern = re.compile(args["subjectRegex"], re.I)
            msgs = [m for m in msgs if pattern.search(m.subject)]
        return [message_as_dict(m) for m in msgs]

    def portal_request_loss_runs(_args: Dict[str, Any], _ctx: ToolCallCtx) -> Any:
        req_n[0] += 1
        request_id = "REQ-{0}".format(req_n[0])
        portal_requests[request_id] = clock.now() + 1 * DAY
        return {"requestId": request_id}

    def portal_check_status(args: Dict[str, Any], _ctx: ToolCallCtx) -> Any:
        fulfill_at = portal_requests.get(str(args.get("requestId")))
        if fulfill_at is None:
            raise RuntimeError("unknown portal request {0}".format(args.get("requestId")))
        return {"status": "fulfilled" if clock.now() >= fulfill_at else "pending"}

    def portal_download(args: Dict[str, Any], _ctx: ToolCallCtx) -> Any:
        return {
            "name": "loss-runs-{0}.txt".format(args.get("requestId")),
            "fileId": "fixture-{0}".format(args.get("requestId")),
            "content": "expected_year: 2025\nyear: 2025\nlosses: none reported\n",
        }

    gateway = FakeGateway(
        {
            "ams.get_policy": ams_get_policy,
            "ams.update_policy": ams_update_policy,
            "email.send": email_send,
            "email.list_inbox": email_list_inbox,
            "portal.request_loss_runs": portal_request_loss_runs,
            "portal.check_status": portal_check_status,
            "portal.download": portal_download,
        }
    )

    rt = make_runtime(golden_renewal_prep, gateway, world, clock, log)
    mission_id = await rt.start_mission(
        StartMissionInput(
            missionType="renewal-prep",
            environmentId="env-golden",
            goal="Prepare renewal submission packets for the listed policies.",
            params={
                "policyIds": ["POL-1", "POL-2"],
                "marketEmail": "submissions@market.test",
                "agentEmail": "agent@convoy.test",
            },
        ),
        clock,
    )

    gates_resolved = 0
    report = await rt.drain(mission_id, clock)
    for _safety in range(200):
        if report.terminal is not None:
            break
        if report.openGates:
            for gate in report.openGates:
                await rt.resolve_gate(
                    ResolveGateInput(
                        gateId=gate.gateId,
                        resolution=GateResolutionWire(kind="approve"),
                        resolvedBy="test-approver",
                    ),
                    clock,
                )
                gates_resolved += 1
        elif report.nextTimerAt is not None:
            clock.advance_to(report.nextTimerAt)
        else:
            raise RuntimeError("deadlock: no terminal, no open gates, no pending timer")
        report = await rt.drain(mission_id, clock)

    return {
        "mission_id": mission_id,
        "log": log,
        "world": world,
        "calls": gateway.calls,
        "updates": updates,
        "terminal": report.terminal,
        "gates_resolved": gates_resolved,
    }


async def test_golden_happy_path_two_policies():
    run = await run_golden_smoke()
    assert run["terminal"] == "landed"

    events = run["log"].for_mission(run["mission_id"])

    # Two packet artifacts with the item tags.
    artifacts = [e for e in events if e.type == "artifact_created"]
    assert [a.tag for a in artifacts] == ["packet/POL-1", "packet/POL-2"]
    assert [f.name for f in run["world"].files] == ["packet/POL-1", "packet/POL-2"]

    # Packet contents: correct premium, exposure from the reply, no drift field
    # (no checklist amendment arrived in the happy path).
    packet1 = json.loads(run["world"].files[0].content)
    assert packet1["policy_number"] == "POL-1"
    assert packet1["premium"] == 1200
    assert packet1["loss_run_year"] == 2025
    assert "fleet size 12" in str(packet1["exposure_summary"])
    assert "prior_carrier_contact" not in packet1

    # Two action-approval gates, raised BEFORE the market sends, tagged send-packet.
    gates = [e for e in events if e.type == "gate_raised"]
    assert len(gates) == 2
    for g in gates:
        assert g.kind == "action-approval"
        assert g.stepTag == "send-packet"
    assert run["gates_resolved"] == 2

    # Market sends: one per policy, subject carries the policyId, packet attached.
    market_sends = [
        c
        for c in run["calls"]
        if c["tool"] == "email.send"
        and isinstance(c["args"].get("to"), list)
        and "submissions@market.test" in c["args"]["to"]
    ]
    assert len(market_sends) == 2
    assert "POL-1" in str(market_sends[0]["args"]["subject"])
    assert "POL-2" in str(market_sends[1]["args"]["subject"])
    for send in market_sends:
        attachments = send["args"].get("attachments")
        assert isinstance(attachments, list) and len(attachments) == 1
        assert isinstance(attachments[0].get("fileId"), str)

    # Gate is raised before its send: both exist in the trace.
    gate_idx = next((i for i, e in enumerate(events) if e.type == "gate_raised"), -1)
    first_market_send = next(
        (
            i
            for i, c in enumerate(run["calls"])
            if c["tool"] == "email.send"
            and isinstance(c["args"].get("to"), list)
            and "submissions@market.test" in c["args"]["to"]
        ),
        -1,
    )
    assert gate_idx >= 0 and first_market_send >= 0

    # AMS updated to submitted for both.
    assert [
        {"policyId": u["policyId"], "status": u["fields"].get("renewal_status")} for u in run["updates"]
    ] == [
        {"policyId": "POL-1", "status": "submitted"},
        {"policyId": "POL-2", "status": "submitted"},
    ]

    # Cooperative insureds replied on the first check: exactly one request email
    # per insured, no chase emails.
    insured_sends = [
        c
        for c in run["calls"]
        if c["tool"] == "email.send"
        and isinstance(c["args"].get("to"), list)
        and ("ins1@client.test" in c["args"]["to"] or "ins2@client.test" in c["args"]["to"])
    ]
    assert len(insured_sends) == 2
    for send in insured_sends:
        assert not str(send["args"]["subject"]).startswith("Follow-up")


async def test_determinism_two_runs_identical_event_sequences():
    run1 = await run_golden_smoke()
    run2 = await run_golden_smoke()
    assert run1["terminal"] == "landed"
    assert run2["terminal"] == "landed"

    seq1 = [e.type for e in run1["log"].for_mission(run1["mission_id"])]
    seq2 = [e.type for e in run2["log"].for_mission(run2["mission_id"])]
    assert seq1 == seq2

    # Domain timestamps are sim-clock driven, so they are identical too.
    ts1 = [e.ts for e in run1["log"].for_mission(run1["mission_id"])]
    ts2 = [e.ts for e in run2["log"].for_mission(run2["mission_id"])]
    assert ts1 == ts2

    # And the tool-call traces line up.
    assert [c["tool"] for c in run1["calls"]] == [c["tool"] for c in run2["calls"]]
