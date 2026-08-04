"""Gateway: policy, inline vs promoted dispatch, runtime idempotency keys,
credential injection — against SQLite + a mocked Slack API. Callers model
trusted runtime activities (run_turn / promoted-tool activities)."""

import hashlib

import httpx
import pytest

from convoy_environments.connectors import ConnectorError
from convoy_environments.db import SqlEventLog
from convoy_environments.db.tables import (
    Connection as ConnectionRow,
    Environment as EnvironmentRow,
    EnvironmentConnection as EnvConnRow,
    Workspace,
)
from convoy_environments.gateway import GatewayService, RunClaims
from convoy_environments.gateway.policy import PolicyDenied
from convoy_environments.schema import ConnectionManifest, ToolSpec, policy_hash
from convoy_environments.secrets import BuiltinBackend, MasterKey, SecretsService

CLAIMS = RunClaims(run_id="run1", mission_id="m1", workspace_id="w1",
                   environment_id="env1", environment_version=1)


def runtime_key(step_id="s1", turn=0, call_index=0):
    """The runtime's idempotency recipe: hash(run_id, step_id, turn, call_index)."""
    return hashlib.sha256(("%s|%s|%d|%d" % (CLAIMS.run_id, step_id, turn, call_index)).encode()).hexdigest()


def _slack_manifest():
    return ConnectionManifest(tools=[
        ToolSpec(name="slack.read_messages", execution="inline", sideEffecting=False),
        ToolSpec(name="slack.post_message", execution="promoted", sideEffecting=True),
        ToolSpec(name="slack.list_channels", execution="inline", sideEffecting=False),
    ])


@pytest.fixture()
def world(session_factory):
    """Workspace + slack connection (token in builtin secrets) + environment
    allowlisting read_messages/post_message (not list_channels), with
    read_messages escalated via promote_overrides."""
    mk = MasterKey(MasterKey.generate().encode())
    secrets = SecretsService(session_factory, {"builtin": BuiltinBackend(mk)})
    sid = secrets.create("w1", "slack-token", "xoxb-secret")

    manifest = _slack_manifest()
    with session_factory() as s:
        s.add(Workspace(id="w1", name="Acme"))
        s.add(ConnectionRow(id="c1", workspace_id="w1", kind="mcp_managed", provider="slack",
                            display_name="Slack", config={}, secret_ref=sid,
                            manifest=manifest.model_dump(exclude_none=True),
                            manifest_hash=manifest.hash, status="active"))
        s.add(EnvironmentRow(id="env1", version=1, workspace_id="w1", name="renewal-prep",
                             backing_type="live", policy_hash=policy_hash("live", [])))
        s.add(EnvConnRow(environment_id="env1", environment_version=1, connection_id="c1",
                         manifest_hash=manifest.hash,
                         tool_allowlist=["slack.read_messages", "slack.post_message"],
                         promote_overrides=[]))
        s.commit()
    return session_factory, secrets


def _service(world, handler):
    sf, secrets = world
    return GatewayService(sf, secrets, transport=httpx.MockTransport(handler)), SqlEventLog(sf)


def _ok_slack(request):
    assert request.headers["Authorization"] == "Bearer xoxb-secret"  # credential injected at the edge
    return httpx.Response(200, json={"ok": True, "messages": []})


async def test_inline_tool_emits_single_collapsed_event(world):
    svc, log = _service(world, _ok_slack)
    result = await svc.call_tool(CLAIMS, "slack.read_messages", {"channel": "#ops"})
    assert result["ok"] is True
    # exactly one event, no budget debits (budgets are runtime-owned)
    assert [e.type for e in log.for_mission("m1")] == ["tool_call"]


async def test_unallowlisted_tool_denied_and_logged(world):
    svc, log = _service(world, _ok_slack)
    with pytest.raises(PolicyDenied, match="not allowlisted"):
        await svc.call_tool(CLAIMS, "slack.list_channels", {})
    assert [e.type for e in log.for_mission("m1")] == ["tool_denied"]


async def test_promoted_tool_two_phase_envelope(world):
    svc, log = _service(world, _ok_slack)
    key = runtime_key()
    result = await svc.call_tool(CLAIMS, "slack.post_message",
                                 {"channel": "#ops", "text": "hi"},
                                 step_id="s1", idempotency_key=key)
    assert result["ok"] is True
    events = log.for_mission("m1")
    assert [e.type for e in events] == ["tool_intent", "tool_executed", "tool_result"]
    assert all(e.idempotencyKey == key for e in events)


async def test_promoted_retry_dedupes_without_reinvoking(world):
    """Runtime activity retry with the same key must not double-fire."""
    calls = {"n": 0}

    def counting(request):
        calls["n"] += 1
        return _ok_slack(request)

    svc, log = _service(world, counting)
    key = runtime_key()
    r1 = await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                             idempotency_key=key)
    r2 = await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                             idempotency_key=key)
    assert r1 == r2 and calls["n"] == 1


async def test_errored_result_never_dedupes(world):
    attempts = {"n": 0}

    def flaky(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return _ok_slack(request)

    svc, log = _service(world, flaky)
    key = runtime_key()
    with pytest.raises(ConnectorError):
        await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                            idempotency_key=key)
    result = await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                                 idempotency_key=key)
    assert result["ok"] is True and attempts["n"] == 2


async def test_promote_override_escalates_inline_tool(world):
    sf, secrets = world
    with sf() as s:
        row = s.get(EnvConnRow, ("env1", 1, "c1"))
        row.promote_overrides = ["slack.read_messages"]
        s.commit()
    svc, log = _service(world, _ok_slack)
    await svc.call_tool(CLAIMS, "slack.read_messages", {"channel": "#ops"},
                        idempotency_key=runtime_key())
    # escalated: two-phase envelope instead of collapsed read
    assert [e.type for e in log.for_mission("m1")] == ["tool_intent", "tool_executed", "tool_result"]


async def test_manifest_drift_fails_closed(world):
    sf, secrets = world
    drifted = ConnectionManifest(tools=[
        ToolSpec(name="slack.read_messages", execution="inline", sideEffecting=False),
        ToolSpec(name="slack.nuke", execution="promoted", sideEffecting=True)])
    with sf() as s:
        conn = s.get(ConnectionRow, "c1")
        conn.manifest = drifted.model_dump(exclude_none=True)
        conn.manifest_hash = drifted.hash
        s.commit()
    svc, _ = _service(world, _ok_slack)
    with pytest.raises(PolicyDenied, match="drift"):
        await svc.call_tool(CLAIMS, "slack.read_messages", {"channel": "#ops"})
