"""Walking skeleton for the environments layer under runtime DESIGN v1:
console provisions the world → the runtime resolves a binding and mints a
run token → a trusted runtime activity discovers tools over MCP → an inline
read flows through → a promoted side-effecting call executes under a
runtime idempotency key and survives an activity retry without double-firing
— and the audit log parses cleanly through convoy_core with no secret in it."""

import hashlib

import httpx
from convoy_core.binding import EnvironmentBinding

from convoy_environments.console_api import build_console_app
from convoy_environments.db import SqlEventLog
from convoy_environments.gateway import GatewayService
from convoy_environments.gateway.mcp_server import build_app as build_gateway_app
from convoy_environments.secrets import BuiltinBackend, MasterKey, SecretsService

GW_SECRET = "integration-gateway-secret-0123456789ab"
INTERNAL = "integration-internal-token"
BASE = "http://gw"


def _slack_ok(request):
    assert request.headers["Authorization"] == "Bearer xoxb-real-token"
    return httpx.Response(200, json={"ok": True, "ts": "123.456"})


async def test_full_loop(session_factory):
    mk = MasterKey(MasterKey.generate().encode())
    secrets = SecretsService(session_factory, {"builtin": BuiltinBackend(mk)})
    log = SqlEventLog(session_factory)
    console = build_console_app(session_factory, secrets)
    gateway = build_gateway_app(
        GatewayService(session_factory, secrets, event_log=log,
                       transport=httpx.MockTransport(_slack_ok)),
        gateway_secret=GW_SECRET, internal_token=INTERNAL, public_url=BASE,
    )
    console_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=console), base_url="http://console")
    gateway_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://gw")

    async with console_c as console, gateway_c as gw:
        # 1. console: workspace, connection, environment
        ws_resp = (await console.post("/workspaces", json={"name": "Acme Brokerage",
                                                           "creatorEmail": "vin@acme.com"})).json()
        ws, admin = ws_resp["workspaceId"], ws_resp["userId"]
        conn = (await console.post("/workspaces/%s/connections" % ws,
                                   headers={"X-Convoy-User": admin},
                                   json={"kind": "mcp_managed", "provider": "slack",
                                         "displayName": "Slack",
                                         "secretValue": "xoxb-real-token"})).json()
        env = (await console.post("/workspaces/%s/environments" % ws,
                                  headers={"X-Convoy-User": admin},
                                  json={"name": "renewal-prep",
                                        "connections": [{
                                            "connectionId": conn["connectionId"],
                                            "toolAllowlist": ["slack.read_messages",
                                                              "slack.post_message"]}]})).json()

        # 2. runtime: resolve + pin the production binding, mint a run token
        binding = EnvironmentBinding.model_validate(
            (await gw.get("/internal/environments/%s/binding" % env["environmentId"],
                          headers={"X-Convoy-Internal": INTERNAL})).json())
        assert binding.id == "%s@1/production" % env["environmentId"]
        grants = {g.tool_id: g for g in binding.tool_registry}
        assert grants["slack.post_message"].execution == "promoted"

        token = (await gw.post("/internal/run-tokens",
                               headers={"X-Convoy-Internal": INTERNAL},
                               json={"runId": "run1", "missionId": "m1", "workspaceId": ws,
                                     "environmentId": env["environmentId"],
                                     "environmentVersion": env["version"]})).json()["token"]
        runtime = {"Authorization": "Bearer %s" % token}
        door = "/mcp/%s" % conn["connectionId"]

        # 3. run_turn: discovers exactly the allowlisted tools through the binding's door
        tools = (await gw.post(door, headers=runtime,
                               json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})).json()
        assert {t["name"] for t in tools["result"]["tools"]} == {"slack.read_messages",
                                                                 "slack.post_message"}

        # 4. inline read flows straight through
        read = (await gw.post(door, headers=runtime,
                              json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                    "params": {"name": "slack.read_messages",
                                               "arguments": {"channel": "#renewals"}}})).json()
        assert read["result"]["isError"] is False

        # 5. promoted side-effecting call with the runtime's idempotency key
        key = hashlib.sha256(b"run1|s1|0|0").hexdigest()
        call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "slack.post_message",
                           "arguments": {"channel": "#renewals", "text": "packet ready"},
                           "_meta": {"idempotencyKey": key, "stepId": "s1"}}}
        first = (await gw.post(door, headers=runtime, json=call)).json()
        assert first["result"]["isError"] is False

        # 6. activity retry with the same key: same result, no double-fire
        retry = (await gw.post(door, headers=runtime, json=call)).json()
        assert retry["result"]["content"] == first["result"]["content"]

    # 7. the audit log tells the story — no gates, no budget debits (runtime-owned)
    types = [e.type for e in log.for_mission("m1")]
    assert types == ["tool_call",                                   # inline read
                     "tool_intent", "tool_executed", "tool_result"]  # promoted, once
    # and no secret ever landed in it
    import json as _json
    dump = _json.dumps([e.model_dump(exclude_none=True) for e in log.for_mission("m1")], default=str)
    assert "xoxb-real-token" not in dump
