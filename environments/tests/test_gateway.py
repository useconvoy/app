"""Gateway end-to-end: policy, effect classes, two-phase events, gates,
idempotency, credential injection — against SQLite + a mocked Slack API."""

import json

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
from convoy_environments.gateway import GatewayService, Parked, RunClaims
from convoy_environments.gateway.policy import PolicyDenied
from convoy_environments.schema import ConnectionManifest, ToolSpec, policy_hash
from convoy_environments.secrets import BuiltinBackend, MasterKey, SecretsService

CLAIMS = RunClaims(run_id="run1", mission_id="m1", workspace_id="w1",
                   environment_id="env1", environment_version=1)


def _slack_manifest():
    return ConnectionManifest(tools=[
        ToolSpec(name="slack.read_messages", effectClass="read"),
        ToolSpec(name="slack.post_message", effectClass="effectful"),
        ToolSpec(name="slack.list_channels", effectClass="read"),
    ])


@pytest.fixture()
def world(session_factory):
    """Workspace + slack connection (token in builtin secrets) + environment
    allowlisting read_messages/post_message (not list_channels), with
    post_message escalated to gated via gate_overrides."""
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
                             backing_type="live", policy_hash=policy_hash("live", []),
                             budget_defaults={}))
        s.add(EnvConnRow(environment_id="env1", environment_version=1, connection_id="c1",
                         manifest_hash=manifest.hash,
                         tool_allowlist=["slack.read_messages", "slack.post_message"],
                         gate_overrides={"slack.post_message": "gated"}))
        s.commit()
    return session_factory, secrets


def _service(world, handler):
    sf, secrets = world
    return GatewayService(sf, secrets, transport=httpx.MockTransport(handler)), SqlEventLog(sf)


def _ok_slack(request):
    assert request.headers["Authorization"] == "Bearer xoxb-secret"  # credential injected at the edge
    return httpx.Response(200, json={"ok": True, "messages": []})


async def test_read_tool_emits_single_collapsed_event(world):
    svc, log = _service(world, _ok_slack)
    result = await svc.call_tool(CLAIMS, "slack.read_messages", {"channel": "#ops"})
    assert result["ok"] is True
    types = [e.type for e in log.for_mission("m1")]
    assert types == ["tool_call", "budget_debit"]


async def test_unallowlisted_tool_denied_and_logged(world):
    svc, log = _service(world, _ok_slack)
    with pytest.raises(PolicyDenied, match="not allowlisted"):
        await svc.call_tool(CLAIMS, "slack.list_channels", {})
    assert [e.type for e in log.for_mission("m1")] == ["tool_denied"]


async def test_gated_tool_parks_then_executes_after_approval(world):
    svc, log = _service(world, _ok_slack)
    with pytest.raises(Parked) as parked:
        await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"})
    key = parked.value.idempotency_key
    types = [e.type for e in log.for_mission("m1")]
    assert types == ["tool_intent", "gate_raised"]

    # still parked before resolution
    with pytest.raises(Parked):
        await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                            idempotency_key=key)

    log.append({"missionId": "m1", "type": "gate_resolved", "gateId": parked.value.gate_id,
                "resolution": "approve", "resolvedBy": "user:vin"}, workspace_id="w1")
    result = await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                                 idempotency_key=key)
    assert result["ok"] is True
    types = [e.type for e in log.for_mission("m1")]
    assert types == ["tool_intent", "gate_raised", "gate_resolved",
                     "tool_approved", "tool_executed", "tool_result", "budget_debit"]


async def test_gate_rejection_denies(world):
    svc, log = _service(world, _ok_slack)
    with pytest.raises(Parked) as parked:
        await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"})
    log.append({"missionId": "m1", "type": "gate_resolved", "gateId": parked.value.gate_id,
                "resolution": "reject", "resolvedBy": "user:vin", "reason": "wrong channel"},
               workspace_id="w1")
    with pytest.raises(PolicyDenied, match="reject"):
        await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                            idempotency_key=parked.value.idempotency_key)


async def test_idempotent_replay_returns_recorded_result_without_reinvoking(world):
    calls = {"n": 0}

    def counting(request):
        calls["n"] += 1
        return _ok_slack(request)

    svc, log = _service(world, counting)
    with pytest.raises(Parked) as parked:
        await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"})
    key = parked.value.idempotency_key
    log.append({"missionId": "m1", "type": "gate_resolved", "gateId": parked.value.gate_id,
                "resolution": "approve", "resolvedBy": "user:vin"}, workspace_id="w1")
    r1 = await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                             idempotency_key=key)
    r2 = await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                             idempotency_key=key)
    assert r1 == r2 and calls["n"] == 1  # crash-replay: no double side effect


async def test_manifest_drift_fails_closed(world):
    sf, secrets = world
    drifted = ConnectionManifest(tools=[ToolSpec(name="slack.read_messages", effectClass="read"),
                                        ToolSpec(name="slack.nuke", effectClass="effectful")])
    with sf() as s:
        conn = s.get(ConnectionRow, "c1")
        conn.manifest = drifted.model_dump(exclude_none=True)
        conn.manifest_hash = drifted.hash
        s.commit()
    svc, _ = _service(world, _ok_slack)
    with pytest.raises(PolicyDenied, match="drift"):
        await svc.call_tool(CLAIMS, "slack.read_messages", {"channel": "#ops"})


async def test_connector_error_logged_and_retry_reruns(world):
    attempts = {"n": 0}

    def flaky(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return _ok_slack(request)

    svc, log = _service(world, flaky)
    with pytest.raises(Parked) as parked:
        await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"})
    key = parked.value.idempotency_key
    log.append({"missionId": "m1", "type": "gate_resolved", "gateId": parked.value.gate_id,
                "resolution": "approve", "resolvedBy": "user:vin"}, workspace_id="w1")
    with pytest.raises(ConnectorError):
        await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                            idempotency_key=key)
    result = await svc.call_tool(CLAIMS, "slack.post_message", {"channel": "#ops", "text": "hi"},
                                 idempotency_key=key)
    assert result["ok"] is True and attempts["n"] == 2
