from convoy_core.events import ToolIntent
from convoy_environments.db import SqlEventLog


def _intent(log, mission="m1", key="k1"):
    return log.append(
        {"missionId": mission, "type": "tool_intent", "tool": "slack.post_message",
         "args": {"channel": "#ops"}, "idempotencyKey": key}
    )


def test_append_stamps_and_validates(session_factory):
    log = SqlEventLog(session_factory, workspace_id="w1")
    e = _intent(log)
    assert isinstance(e, ToolIntent)
    assert e.seq == 0 and e.eventId and e.ts.endswith("Z")


def test_seq_is_per_mission_monotonic(session_factory):
    log = SqlEventLog(session_factory)
    assert _intent(log, "m1", "a").seq == 0
    assert _intent(log, "m1", "b").seq == 1
    assert _intent(log, "m2", "c").seq == 0


def test_for_mission_returns_ordered_models(session_factory):
    log = SqlEventLog(session_factory)
    _intent(log, "m1", "a")
    log.append({"missionId": "m1", "type": "tool_result", "tool": "slack.post_message",
                "idempotencyKey": "a", "result": {"ok": True}})
    events = log.for_mission("m1")
    assert [e.type for e in events] == ["tool_intent", "tool_result"]
    # round-trips through convoy_core models, not raw dicts
    assert events[1].result == {"ok": True}


def test_listeners_fire_on_append(session_factory):
    log = SqlEventLog(session_factory)
    seen = []
    log.on_append(lambda e: seen.append(e.type))
    _intent(log)
    assert seen == ["tool_intent"]


def test_open_gates_projection(session_factory):
    log = SqlEventLog(session_factory, workspace_id="w1")
    log.append({"missionId": "m1", "type": "gate_raised", "gateId": "g1",
                "kind": "action-approval", "payload": {"tool": "x"}})
    log.append({"missionId": "m1", "type": "gate_raised", "gateId": "g2",
                "kind": "input-request"})
    log.append({"missionId": "m1", "type": "gate_resolved", "gateId": "g1",
                "resolution": "approve", "resolvedBy": "user:vin"})
    open_gates = log.open_gates(workspace_id="w1")
    assert [g["gateId"] for g in open_gates] == ["g2"]
    assert log.open_gates(workspace_id="other") == []
