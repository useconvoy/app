"""MCP termination over HTTP: auth, tools/list, tools/call, parked gates,
and the internal token-mint endpoint."""

import httpx
import pytest

from convoy_environments.gateway import GatewayService, RunClaims, mint_run_token
from convoy_environments.gateway.mcp_server import build_app

from .test_gateway import CLAIMS, _ok_slack, world  # noqa: F401 — fixture reuse

SECRET = "test-gateway-secret"
INTERNAL = "test-internal-token"


@pytest.fixture()
def client(world):  # noqa: F811
    sf, secrets = world
    service = GatewayService(sf, secrets, transport=httpx.MockTransport(_ok_slack))
    app = build_app(service, gateway_secret=SECRET, internal_token=INTERNAL)
    token = mint_run_token(CLAIMS, secret=SECRET)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw",
                             headers={"Authorization": "Bearer %s" % token})


def _rpc(method, params=None, id=1):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


async def test_rejects_missing_or_bad_token(client):
    async with client as c:
        resp = await c.post("/mcp", json=_rpc("tools/list"), headers={"Authorization": ""})
        assert resp.status_code == 401
        resp = await c.post("/mcp", json=_rpc("tools/list"), headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401


async def test_initialize_and_tools_list(client):
    async with client as c:
        resp = await c.post("/mcp", json=_rpc("initialize"))
        assert resp.json()["result"]["serverInfo"]["name"] == "convoy-gateway"
        resp = await c.post("/mcp", json=_rpc("tools/list"))
        tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
        # only the environment's allowlist, with effect classes surfaced as annotations
        assert set(tools) == {"slack.read_messages", "slack.post_message"}
        assert tools["slack.read_messages"]["annotations"]["readOnlyHint"] is True
        assert tools["slack.post_message"]["annotations"]["readOnlyHint"] is False


async def test_read_call_returns_result(client):
    async with client as c:
        resp = await c.post("/mcp", json=_rpc("tools/call", {
            "name": "slack.read_messages", "arguments": {"channel": "#ops"}}))
        result = resp.json()["result"]
        assert result["isError"] is False and '"ok": true' in result["content"][0]["text"]


async def test_promoted_call_executes_with_runtime_key_and_denied_tool_is_error(client):
    async with client as c:
        resp = await c.post("/mcp", json=_rpc("tools/call", {
            "name": "slack.post_message", "arguments": {"channel": "#ops", "text": "hi"},
            "_meta": {"idempotencyKey": "rt-key-1", "stepId": "s1"}}))
        assert resp.json()["result"]["isError"] is False

        resp = await c.post("/mcp", json=_rpc("tools/call", {"name": "slack.list_channels", "arguments": {}}))
        assert resp.json()["result"]["isError"] is True


async def test_internal_mint_requires_shared_secret(client):
    body = {"runId": "r2", "missionId": "m2", "workspaceId": "w1",
            "environmentId": "env1", "environmentVersion": 1}
    async with client as c:
        resp = await c.post("/internal/run-tokens", json=body)
        assert resp.status_code == 401
        resp = await c.post("/internal/run-tokens", json=body,
                            headers={"X-Convoy-Internal": INTERNAL})
        assert resp.status_code == 200 and resp.json()["token"]
