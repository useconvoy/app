"""Walking skeleton for the environments layer: the whole white-glove loop
through public HTTP surfaces only — console sets up the world, the runtime
mints a run token, the agent speaks MCP to the gateway, a gated call parks,
the console approves, the retry executes — and the resulting event log parses
cleanly through convoy_core (what agent-evals graders consume)."""

import httpx

from convoy_environments.console_api import build_console_app
from convoy_environments.db import SqlEventLog
from convoy_environments.gateway import GatewayService
from convoy_environments.gateway.mcp_server import build_app as build_gateway_app
from convoy_environments.secrets import BuiltinBackend, MasterKey, SecretsService

GW_SECRET = "integration-gateway-secret-0123456789ab"
INTERNAL = "integration-internal-token"


def _slack_ok(request):
    assert request.headers["Authorization"] == "Bearer xoxb-real-token"
    return httpx.Response(200, json={"ok": True, "ts": "123.456"})


async def test_full_loop(session_factory):
    mk = MasterKey(MasterKey.generate().encode())
    secrets = SecretsService(session_factory, {"builtin": BuiltinBackend(mk)})
    log = SqlEventLog(session_factory)
    console = build_console_app(session_factory, secrets, event_log=log)
    gateway = build_gateway_app(
        GatewayService(session_factory, secrets, event_log=log,
                       transport=httpx.MockTransport(_slack_ok)),
        gateway_secret=GW_SECRET, internal_token=INTERNAL,
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
                                            "toolAllowlist": ["slack.read_messages", "slack.post_message"],
                                            "gateOverrides": {"slack.post_message": "gated"}}]})).json()

        # 2. runtime mints a per-run token
        token = (await gw.post("/internal/run-tokens",
                               headers={"X-Convoy-Internal": INTERNAL},
                               json={"runId": "run1", "missionId": "m1", "workspaceId": ws,
                                     "environmentId": env["environmentId"],
                                     "environmentVersion": env["version"]})).json()["token"]
        agent = {"Authorization": "Bearer %s" % token}

        # 3. agent: discovers exactly the allowlisted tools
        tools = (await gw.post("/mcp", headers=agent,
                               json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})).json()
        assert {t["name"] for t in tools["result"]["tools"]} == {"slack.read_messages",
                                                                 "slack.post_message"}

        # 4. read flows straight through
        read = (await gw.post("/mcp", headers=agent,
                              json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                    "params": {"name": "slack.read_messages",
                                               "arguments": {"channel": "#renewals"}}})).json()
        assert read["result"]["isError"] is False

        # 5. gated write parks
        parked = (await gw.post("/mcp", headers=agent,
                                json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                      "params": {"name": "slack.post_message",
                                                 "arguments": {"channel": "#renewals",
                                                               "text": "packet ready"}}})).json()
        structured = parked["result"]["structuredContent"]
        assert structured["status"] == "parked"

        # 6. console: the gate is visible to the admin and gets approved
        gates = (await console.get("/workspaces/%s/gates" % ws,
                                   headers={"X-Convoy-User": admin})).json()
        assert [g["gateId"] for g in gates] == [structured["gateId"]]
        resolved = await console.post("/workspaces/%s/gates/%s/resolve" % (ws, structured["gateId"]),
                                      headers={"X-Convoy-User": admin},
                                      json={"resolution": "approve", "reason": "packet verified"})
        assert resolved.status_code == 200

        # 7. agent retries with the same idempotency key → executes
        done = (await gw.post("/mcp", headers=agent,
                              json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                    "params": {"name": "slack.post_message",
                                               "arguments": {"channel": "#renewals",
                                                             "text": "packet ready"},
                                               "_meta": {"idempotencyKey": structured["idempotencyKey"]}}})).json()
        assert done["result"]["isError"] is False and '"ok": true' in done["result"]["content"][0]["text"]

    # 8. the log tells the whole story in co-signed vocabulary
    types = [e.type for e in log.for_mission("m1")]
    assert types == ["tool_call", "budget_debit",                      # read
                     "tool_intent", "gate_raised",                     # parked
                     "gate_resolved", "human_intervention",            # console
                     "tool_approved", "tool_executed", "tool_result",  # execution
                     "budget_debit"]
    # and no secret ever landed in it
    import json as _json
    dump = _json.dumps([e.model_dump(exclude_none=True) for e in log.for_mission("m1")], default=str)
    assert "xoxb-real-token" not in dump
