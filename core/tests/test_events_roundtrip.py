"""Wire-format guarantees: camelCase JSON round-trips through the models."""

import json

from convoy_core.mission_events import parse_event


def _roundtrip(raw):
    event = parse_event(raw)
    dumped = json.loads(event.model_dump_json(exclude_none=True))
    assert dumped == raw
    return event


def test_two_phase_envelope_roundtrip():
    base = {"missionId": "m1", "seq": 0, "ts": "2026-08-02T00:00:00Z", "wallTs": "2026-08-02T00:00:00Z"}
    intent = _roundtrip(
        {**base, "eventId": "e1", "type": "tool_intent", "tool": "slack.post_message",
         "args": {"channel": "#ops"}, "idempotencyKey": "k1"}
    )
    assert intent.idempotencyKey == "k1"
    _roundtrip({**base, "eventId": "e2", "seq": 1, "type": "tool_executed", "tool": "slack.post_message", "idempotencyKey": "k1"})
    _roundtrip({**base, "eventId": "e3", "seq": 2, "type": "tool_result", "tool": "slack.post_message", "idempotencyKey": "k1", "result": {"ok": True}})


def test_collapsed_read_and_gate_roundtrip():
    base = {"missionId": "m1", "ts": "2026-08-02T00:00:00Z", "wallTs": "2026-08-02T00:00:00Z"}
    _roundtrip({**base, "eventId": "e1", "seq": 0, "type": "tool_call", "tool": "notion.search", "args": {"q": "renewal"}, "result": []})
    gate = _roundtrip(
        {**base, "eventId": "e2", "seq": 1, "type": "gate_raised", "gateId": "g1",
         "kind": "action-approval", "payload": {"tool": "slack.post_message"}}
    )
    assert gate.kind == "action-approval"
    _roundtrip({**base, "eventId": "e3", "seq": 2, "type": "gate_resolved", "gateId": "g1", "resolution": "approve", "resolvedBy": "user:vin"})


def test_unknown_event_type_rejected():
    import pytest

    with pytest.raises(Exception):
        parse_event({"eventId": "e", "missionId": "m", "seq": 0, "ts": "t", "wallTs": "t", "type": "nonsense"})
