"""Sandbox tests — port of tests/sandbox.test.ts, sandbox-engines.test.ts and
sandbox-instance.test.ts: SimClock monotonicity, WorldStore seeding/query/
export, ToolGateway two-phase envelope + idempotency dedupe, counterparty
profiles, the gate-script engine, and the full DES loop (run_until) driving a
minimal inline fake RuntimeClient (deliberately NOT the executors package).
"""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from convoy_evals.runtime.log import EventLog
from convoy_evals.runtime.ports import GateResolutionWire, OpenGate
from convoy_evals.sandbox import (
    ToolCallCtx,
    ToolEmulator,
    calendar_emulator,
    create_counterparty_engine,
    create_gate_script_engine,
    create_sim_clock,
    create_tool_gateway,
    create_world_store,
    email_emulator,
)

HERE = Path(__file__).resolve().parent
PACK_MINI = str(HERE / "fixtures" / "pack_mini")
T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


# ---------------------------------------------------------------------------
# (a) clock
# ---------------------------------------------------------------------------


def test_clock_starts_at_t0_and_advances_forward():
    clock = create_sim_clock(T0)
    assert clock.now() == T0
    clock.advance_to(T0 + 3 * DAY)
    assert clock.now() == datetime(2026, 8, 4, tzinfo=timezone.utc)
    clock.advance_to(clock.now())  # same instant is fine


def test_clock_advancing_backwards_raises_monotonic():
    clock = create_sim_clock(T0)
    clock.advance_to(T0 + DAY)
    with pytest.raises(ValueError, match="monotonic"):
        clock.advance_to(T0)


def test_clock_datetimes_are_tz_aware_utc():
    clock = create_sim_clock(datetime(2026, 8, 1))  # naive → assumed UTC
    assert clock.now().tzinfo == timezone.utc
    clock.advance_to(datetime(2026, 8, 2))
    assert clock.now() == datetime(2026, 8, 2, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# (b) world seeding + query
# ---------------------------------------------------------------------------


def test_world_seeds_pack_with_t0_materialization():
    clock = create_sim_clock(T0)
    world = create_world_store(clock, 42)
    world.seed_from_pack(PACK_MINI, T0, 42)

    assert world.query("records.policy.POL-1.expiring_date") == ["2026-08-04"]
    assert world.query("records.policy.POL-1.bound_on") == ["2025-08-06"]
    assert world.query("records.policy.POL-2.expiring_date") == ["2026-09-10"]

    loss_runs = world.file_by_pack_path("attachments/loss-runs.txt")
    assert loss_runs is not None, "file resolvable by pack path"
    assert "Report date: 2026-08-01" in loss_runs.content
    assert "to 2026-08-04" in loss_runs.content
    assert loss_runs.hash == hashlib.sha256(loss_runs.content.encode("utf-8")).hexdigest()

    names = world.query("files.*.name")
    assert "loss-runs.txt" in names and "acord-125.txt" in names

    inbound = world.list_messages(direction="inbound")
    assert len(inbound) == 1
    assert "expires on 2026-08-04" in inbound[0].body
    assert inbound[0].ts == "2026-07-31"
    # Messages serialize with the wire key 'from' so query paths match TS.
    assert world.query("messages.inbound.*.from") == ["maria@acme-robotics.com"]


def test_world_mutations_emit_events_directions_and_upsert_merge():
    clock = create_sim_clock(T0)
    world = create_world_store(clock, 1)
    seen: List[Dict[str, Any]] = []
    world.on_event(seen.append)

    out = world.send_message(
        threadId="t1", from_="agent@convoy.sim", to=["x@y.z"], subject="s", body="b",
        attachments=[], direction="outbound",
    )
    assert out.direction == "outbound"
    assert out.ts == "2026-08-01T00:00:00Z"
    inb = world.deliver_message(
        threadId="t1", from_="x@y.z", to=["agent@convoy.sim"], subject="re: s", body="b2",
        attachments=[], direction="inbound",
    )
    assert inb.direction == "inbound"
    world.upsert_record("policy", "P", {"a": 1})
    world.upsert_record("policy", "P", {"b": 2})
    assert world.get_record("policy", "P").fields == {"a": 1, "b": 2}

    assert [e["kind"] for e in seen] == [
        "message_sent", "message_delivered", "record_changed", "record_changed",
    ]
    assert world.query("messages.sent.*.to") == [["x@y.z"]]


def test_world_export_bundle_hash_stable_and_moves_on_change():
    def build():
        world = create_world_store(create_sim_clock(T0), 42)
        world.seed_from_pack(PACK_MINI, T0, 42)
        return world

    a, b = build(), build()
    assert a.export_bundle().hash == b.export_bundle().hash
    b.upsert_record("policy", "POL-1", {"renewal_status": "bound"})
    assert a.export_bundle().hash != b.export_bundle().hash


# ---------------------------------------------------------------------------
# (c) gateway: two-phase events + idempotency dedupe
# ---------------------------------------------------------------------------


def gateway_rig(bindings: Optional[Dict[str, Any]] = None):
    clock = create_sim_clock(T0)
    world = create_world_store(clock, 1)
    log = EventLog(clock)
    counts = {"effect": 0, "pure": 0}

    def effect_handler(args, world, ctx):
        counts["effect"] += 1
        return {"ok": counts["effect"]}

    def pure_handler(args, world, ctx):
        counts["pure"] += 1
        return {"n": counts["pure"]}

    def boom_handler(args, world, ctx):
        raise ValueError("kaboom")

    emulators = [
        ToolEmulator(tool="test.effect", effectful=True, handler=effect_handler),
        ToolEmulator(tool="test.read", effectful=False, handler=pure_handler),
        ToolEmulator(tool="test.boom", effectful=False, handler=boom_handler),
    ]
    gateway = create_tool_gateway(emulators, bindings or {}, log, world)
    return gateway, log, world, counts


CTX = ToolCallCtx(missionId="m1")


async def test_gateway_pure_tool_emits_collapsed_tool_call_plus_debit():
    gateway, log, _, _ = gateway_rig()
    result = await gateway.invoke("test.read", {"q": 1}, CTX)
    assert result == {"n": 1}
    events = log.for_mission("m1")
    assert [e.type for e in events] == ["tool_call", "budget_debit"]
    assert events[0].result == {"n": 1}
    assert events[1].usd == 0.001 and events[1].resource == "tool"


async def test_gateway_pure_tool_error_recorded_and_rethrown():
    gateway, log, _, _ = gateway_rig()
    with pytest.raises(ValueError, match="kaboom"):
        await gateway.invoke("test.boom", {}, CTX)
    call = next(e for e in log.for_mission("m1") if e.type == "tool_call")
    assert call.error == "kaboom"


async def test_gateway_effectful_two_phase_envelope_and_dedupe():
    gateway, log, _, counts = gateway_rig()
    # Caller-minted key models crash replay: the runtime re-fires the SAME
    # attempt with the SAME key, and the side effect must not run twice.
    replay_ctx = ToolCallCtx(missionId="m1", idempotencyKey="attempt-1")
    first = await gateway.invoke("test.effect", {"policyId": "POL-1"}, replay_ctx)
    second = await gateway.invoke("test.effect", {"policyId": "POL-1"}, replay_ctx)

    assert counts["effect"] == 1, "handler must not re-run on idempotent replay"
    assert second == first, "replay returns the recorded result"

    events = log.for_mission("m1")
    assert len(events) == 8
    assert [e.type for e in events] == [
        "tool_intent", "tool_approved", "tool_executed", "tool_result", "budget_debit",
        "tool_intent", "tool_result", "budget_debit",
    ]
    keys = [e.idempotencyKey for e in events if e.type == "tool_intent"]
    assert keys[0] == keys[1], "caller-minted key is carried on both intents"

    # No caller key → fresh key per invoke → an INTENTIONAL retry re-executes.
    await gateway.invoke("test.effect", {"policyId": "POL-1"}, CTX)
    await gateway.invoke("test.effect", {"policyId": "POL-1"}, CTX)
    assert counts["effect"] == 3, "intentional retries (no caller key) re-run the handler"


async def test_gateway_unknown_tool_and_non_emulator_binding_raise():
    gateway, _, _, _ = gateway_rig(
        {"test.effect": {"kind": "replay", "cassette": "c", "miss": "fail"}}
    )
    with pytest.raises(ValueError, match="unknown tool"):
        await gateway.invoke("nope.tool", {}, CTX)
    with pytest.raises(ValueError, match="not implemented in v1"):
        await gateway.invoke("test.effect", {}, CTX)


def test_gateway_manifest_hash_order_independent():
    clock = create_sim_clock(T0)
    world = create_world_store(clock, 1)
    a = ToolEmulator(tool="a", effectful=True, handler=lambda *_: 0)
    b = ToolEmulator(tool="b", effectful=False, handler=lambda *_: 0)
    g1 = create_tool_gateway([a, b], {}, EventLog(clock), world)
    g2 = create_tool_gateway([b, a], {}, EventLog(clock), world)
    assert g1.manifest_hash() == g2.manifest_hash()
    b_eff = ToolEmulator(tool="b", effectful=True, handler=lambda *_: 0)
    g3 = create_tool_gateway([a, b_eff], {}, EventLog(clock), world)
    assert g1.manifest_hash() != g3.manifest_hash()


async def test_gateway_emulators_operate_on_world_email_roundtrip_sim_today():
    clock = create_sim_clock(T0)
    world = create_world_store(clock, 1)
    world.seed_from_pack(PACK_MINI, T0, 1)
    log = EventLog(clock)
    gateway = create_tool_gateway([*email_emulator, *calendar_emulator], {}, log, world)

    today = await gateway.invoke("calendar.today", {}, CTX)
    assert today["today"] == "2026-08-01"

    file = world.file_by_pack_path("attachments/acord-125.txt")
    await gateway.invoke(
        "email.send",
        {
            "to": "carrier@acme.sim",
            "subject": "Loss runs request",
            "body": "Please send loss runs.",
            "attachments": [file.id],
        },
        CTX,
    )
    sent = world.list_messages(direction="outbound")
    assert len(sent) == 1
    assert [x.name for x in sent[0].attachments] == ["acord-125.txt"]

    inbox = await gateway.invoke("email.list_inbox", {}, CTX)
    assert len(inbox["messages"]) == 1  # the seeded kickoff thread
    read = await gateway.invoke(
        "email.read", {"messageId": inbox["messages"][0]["messageId"]}, CTX
    )
    assert "2026-08-04" in read["body"]


# ---------------------------------------------------------------------------
# (d) counterparty
# ---------------------------------------------------------------------------


def outbound_to_carrier(world, subject="Loss runs please"):
    return world.send_message(
        threadId="thread_loss-runs",
        from_="agent@convoy.sim",
        to=["carrier@acme.sim"],
        subject=subject,
        body="Requesting loss runs for POL-1.",
        attachments=[],
        direction="outbound",
    )


def carrier_script(profile: str) -> Dict[str, Any]:
    return {
        "actorId": "carrier",
        "channel": "email",
        "owns": ["carrier@acme.sim"],
        "profile": profile,
        "params": {
            "replyTemplate": {"subject": "RE: Loss runs", "body": "Attached as requested."},
            "attachments": ["attachments/loss-runs.txt"],
        },
        "default": "silent",
    }


def test_counterparty_cooperative_replies_1_2_days_with_correct_doc():
    clock = create_sim_clock(T0)
    world = create_world_store(clock, 7)
    world.seed_from_pack(PACK_MINI, T0, 7)
    engine = create_counterparty_engine(
        [carrier_script("cooperative")], world, clock, seed=7, pack_dir=PACK_MINI
    )

    assert engine.peek() is None, "quiet until the agent writes"
    outbound_to_carrier(world)

    at = engine.peek()
    assert at is not None, "reply queued"
    delay = at - T0
    assert DAY <= delay <= 2 * DAY, "delay %s must be within [1d, 2d]" % delay

    clock.advance_to(at)
    engine.deliver_due(clock.now())
    assert engine.peek() is None

    # The seeded pack contains one inbound kickoff message; the reply threads onto our ask.
    inbound = world.list_messages(direction="inbound", thread_id="thread_loss-runs")
    assert len(inbound) == 1
    reply = inbound[0]
    assert reply.from_ == "carrier@acme.sim"
    assert reply.subject == "RE: Loss runs"
    assert reply.threadId == "thread_loss-runs", "reply stays on the triggering thread"
    assert [a.name for a in reply.attachments] == ["loss-runs.txt"]
    file = world.get_file(reply.attachments[0].fileId)
    assert "LOSS RUN REPORT" in file.content


def test_counterparty_cooperative_reply_deterministic_under_seed():
    def run():
        clock = create_sim_clock(T0)
        world = create_world_store(clock, 7)
        world.seed_from_pack(PACK_MINI, T0, 7)
        engine = create_counterparty_engine(
            [carrier_script("cooperative")], world, clock, seed=7, pack_dir=PACK_MINI
        )
        outbound_to_carrier(world)
        return engine.peek().isoformat()

    assert run() == run()


def test_counterparty_slow_replies_only_to_the_chase():
    clock = create_sim_clock(T0)
    world = create_world_store(clock, 11)
    world.seed_from_pack(PACK_MINI, T0, 11)
    engine = create_counterparty_engine(
        [carrier_script("slow")], world, clock, seed=11, pack_dir=PACK_MINI
    )

    outbound_to_carrier(world, "Loss runs please")
    assert engine.peek() is None, "first ask is ignored"

    clock.advance_to(T0 + 3 * DAY)
    outbound_to_carrier(world, "Following up: loss runs")
    at = engine.peek()
    assert at is not None, "the chase gets a reply"
    clock.advance_to(at)
    engine.deliver_due(clock.now())

    inbound = world.list_messages(direction="inbound", thread_id="thread_loss-runs")
    assert len(inbound) == 1, "exactly one reply, to the chase"


def test_counterparty_unowned_addresses_invisible():
    clock = create_sim_clock(T0)
    world = create_world_store(clock, 3)
    engine = create_counterparty_engine(
        [carrier_script("cooperative")], world, clock, seed=3, pack_dir=PACK_MINI
    )
    world.send_message(
        threadId="t", from_="agent@convoy.sim", to=["someoneelse@other.sim"],
        subject="s", body="b", attachments=[], direction="outbound",
    )
    assert engine.peek() is None


def test_counterparty_portal_actor_fulfills_pending_requests():
    clock = create_sim_clock(T0)
    world = create_world_store(clock, 5)
    world.seed_from_pack(PACK_MINI, T0, 5)
    events: List[Dict[str, Any]] = []
    world.on_event(events.append)
    portal_script = {
        "actorId": "sentinel-portal",
        "channel": "portal",
        "owns": ["sentinel"],
        "profile": "cooperative",
        "params": {"document": "attachments/loss-runs.txt"},
        "default": "silent",
    }
    engine = create_counterparty_engine([portal_script], world, clock, seed=5, pack_dir=PACK_MINI)

    world.upsert_record(
        "portal_request", "req_1", {"status": "pending", "carrier": "sentinel", "policyId": "POL-1"}
    )
    at = engine.peek()
    assert at is not None, "fulfillment scheduled"
    clock.advance_to(at)
    engine.deliver_due(clock.now())

    record = world.get_record("portal_request", "req_1")
    assert record.fields["status"] == "fulfilled"
    file_id = record.fields["documentFileId"]
    assert isinstance(file_id, str)
    assert "LOSS RUN REPORT" in world.get_file(file_id).content
    assert any(
        e.get("kind") == "portal_transition" and e["requestId"] == "req_1" and e["to"] == "fulfilled"
        for e in events
    )


def test_counterparty_unsolicited_sends_arrive_at_t0_plus_atsim():
    clock = create_sim_clock(T0)
    world = create_world_store(clock, 9)
    script = dict(carrier_script("silent"))
    script["unsolicited"] = [
        {"atSim": "5d", "message": {"subject": "Notice of non-renewal", "body": "FYI."}}
    ]
    engine = create_counterparty_engine([script], world, clock, seed=9, pack_dir=PACK_MINI)

    at = engine.peek()
    assert at == T0 + 5 * DAY
    clock.advance_to(at)
    engine.deliver_due(clock.now())
    inbound = world.list_messages(direction="inbound")
    assert len(inbound) == 1
    assert inbound[0].subject == "Notice of non-renewal"
    assert inbound[0].to == ["agent@convoy.sim"]


# ---------------------------------------------------------------------------
# (e) gate script engine
# ---------------------------------------------------------------------------


def recorder():
    calls: List[Dict[str, Any]] = []

    async def fn(gate_id, wire, resolved_by, reason=None):
        calls.append({"gateId": gate_id, "wire": wire, "resolvedBy": resolved_by, "reason": reason})

    return calls, fn


def gate(gate_id: str, kind: str, payload: Any) -> OpenGate:
    return OpenGate(gateId=gate_id, kind=kind, deadlineAt=None, payload=payload)


SCRIPT = {
    "mode": "scripted",
    "steps": [
        {
            "id": "s1",
            "expect": {
                "kind": "action-approval",
                "payload": {"all": [{"path": "action", "op": "eq", "value": "bind"}]},
            },
            "resolve": {"kind": "approve"},
            "optional": False,
            "ordered": True,
            "maxFires": 1,
        },
        {
            "id": "s2",
            "expect": {"kind": "input-request"},
            "resolve": {"kind": "provide_input", "payload": {"answer": 42}},
            "optional": False,
            "ordered": True,
            "maxFires": 1,
        },
    ],
    "onUnexpectedGate": "fail_scenario",
}


async def test_gates_scripted_approve_resolves_with_step_attribution():
    clock = create_sim_clock(T0)
    engine = create_gate_script_engine(SCRIPT, clock, seed=1)
    calls, fn = recorder()

    await engine.apply_scripts([gate("g1", "action-approval", {"action": "bind", "policyId": "POL-1"})], fn)
    assert len(calls) == 1
    assert calls[0]["wire"] == GateResolutionWire(kind="approve")
    assert calls[0]["resolvedBy"] == "harness:s1"

    # Same gate re-offered on the next drain: already handled, not re-resolved.
    await engine.apply_scripts([gate("g1", "action-approval", {"action": "bind"})], fn)
    assert len(calls) == 1

    report = engine.report()
    assert [(r.gateId, r.stepId, r.resolution) for r in report.resolutions] == [
        ("g1", "s1", "approve")
    ]
    assert report.neverRaised == ["s2"], "unfired required step is reported"
    assert report.unexpected == []


async def test_gates_unexpected_gate_recorded_not_resolved_under_fail_scenario():
    clock = create_sim_clock(T0)
    engine = create_gate_script_engine(SCRIPT, clock, seed=1)
    calls, fn = recorder()

    await engine.apply_scripts([gate("g9", "budget-raise", {"addUsd": 100})], fn)
    assert calls == [], "fail_scenario leaves the gate open"
    assert engine.report().unexpected == [{"gateId": "g9", "kind": "budget-raise"}]


async def test_gates_on_unexpected_gate_resolve_uses_harness_unexpected():
    clock = create_sim_clock(T0)
    script = dict(SCRIPT)
    script["onUnexpectedGate"] = {"resolve": {"kind": "reject", "reason": "not in script"}}
    engine = create_gate_script_engine(script, clock, seed=1)
    calls, fn = recorder()
    await engine.apply_scripts([gate("g9", "budget-raise", {})], fn)
    assert len(calls) == 1
    assert calls[0]["resolvedBy"] == "harness:unexpected"
    assert calls[0]["wire"] == GateResolutionWire(kind="reject", reason="not in script")
    assert engine.report().unexpected == [{"gateId": "g9", "kind": "budget-raise"}]


async def test_gates_ordered_steps_out_of_order_gate_is_unexpected():
    clock = create_sim_clock(T0)
    engine = create_gate_script_engine(SCRIPT, clock, seed=1)
    calls, fn = recorder()
    # s2's gate arrives while s1 (required, ordered) is still unmatched.
    await engine.apply_scripts([gate("g2", "input-request", {})], fn)
    assert calls == []
    assert engine.report().unexpected == [{"gateId": "g2", "kind": "input-request"}]


async def test_gates_after_sim_schedules_resolution_on_sim_timeline():
    clock = create_sim_clock(T0)
    script = {
        "mode": "scripted",
        "steps": [
            {
                "id": "s1",
                "expect": {"kind": "action-approval"},
                "resolve": {"kind": "approve"},
                "afterSim": "1d",
            }
        ],
        "onUnexpectedGate": "fail_scenario",
    }
    engine = create_gate_script_engine(script, clock, seed=1)
    calls, fn = recorder()

    await engine.apply_scripts([gate("g1", "action-approval", {})], fn)
    assert calls == [], "not resolved yet — simulated human latency"
    at = engine.next_resolution_at()
    assert at == T0 + DAY

    await engine.resolve_due(T0 + DAY / 2, fn)
    assert calls == [], "not due yet"
    await engine.resolve_due(at, fn)
    assert len(calls) == 1
    assert engine.next_resolution_at() is None
    report = engine.report()
    assert [(r.gateId, r.stepId, r.resolution) for r in report.resolutions] == [
        ("g1", "s1", "approve")
    ]


async def test_gates_auto_approve_stops_at_max_gates():
    clock = create_sim_clock(T0)
    engine = create_gate_script_engine({"mode": "auto_approve", "maxGates": 1}, clock, seed=1)
    calls, fn = recorder()
    await engine.apply_scripts(
        [gate("g1", "action-approval", {}), gate("g2", "action-approval", {})], fn
    )
    assert len(calls) == 1, "second gate is left unresolved"
    assert calls[0]["resolvedBy"] == "harness:auto"


async def test_gates_auto_reject_rejects_with_scripted_reason():
    clock = create_sim_clock(T0)
    engine = create_gate_script_engine({"mode": "auto_reject", "reason": "nope"}, clock, seed=1)
    calls, fn = recorder()
    await engine.apply_scripts([gate("g1", "plan-approval", {})], fn)
    assert calls[0]["wire"] == GateResolutionWire(kind="reject", reason="nope")
