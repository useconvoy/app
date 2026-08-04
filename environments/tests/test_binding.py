"""The frozen runtime seam: registry endpoint → EnvironmentBinding snapshots
(production + sandbox), and the per-connection MCP doors the binding points at."""

import httpx
import pytest
from convoy_core.binding import EnvironmentBinding

from convoy_environments.console_api import build_console_app
from convoy_environments.db import SqlEventLog
from convoy_environments.gateway import GatewayService, mint_run_token, RunClaims
from convoy_environments.gateway.mcp_server import build_app as build_gateway_app
from convoy_environments.schema import policy_hash
from convoy_environments.secrets import BuiltinBackend, MasterKey, SecretsService

GW_SECRET = "binding-test-secret-0123456789abcdef"
INTERNAL = "binding-internal-token"
BASE = "https://gw.convoy.internal/gateway"


@pytest.fixture()
async def world(session_factory):
    """Console-provisioned workspace with slack + github connections and one
    environment granting tools from both (read_messages escalated)."""
    mk = MasterKey(MasterKey.generate().encode())
    secrets = SecretsService(session_factory, {"builtin": BuiltinBackend(mk)})
    log = SqlEventLog(session_factory)
    console = build_console_app(session_factory, secrets)
    service = GatewayService(session_factory, secrets, event_log=log,
                             transport=httpx.MockTransport(
                                 lambda req: httpx.Response(200, json={"ok": True})))
    gateway = build_gateway_app(service, gateway_secret=GW_SECRET,
                                internal_token=INTERNAL, public_url=BASE)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=console),
                                 base_url="http://console") as c:
        ws_resp = (await c.post("/workspaces", json={"name": "Acme",
                                                     "creatorEmail": "vin@acme.com"})).json()
        ws, admin = ws_resp["workspaceId"], ws_resp["userId"]
        hdr = {"X-Convoy-User": admin}
        slack = (await c.post("/workspaces/%s/connections" % ws, headers=hdr,
                              json={"kind": "mcp_managed", "provider": "slack",
                                    "displayName": "Slack", "secretValue": "xoxb-1"})).json()
        github = (await c.post("/workspaces/%s/connections" % ws, headers=hdr,
                               json={"kind": "mcp_managed", "provider": "github",
                                     "displayName": "GitHub", "secretValue": "ghp-1"})).json()
        env = (await c.post("/workspaces/%s/environments" % ws, headers=hdr,
                            json={"name": "renewal-prep",
                                  "sandboxTemplate": "convoy-sbx-v3",
                                  "connections": [
                                      {"connectionId": slack["connectionId"],
                                       "toolAllowlist": ["slack.read_messages", "slack.post_message"],
                                       "promoteOverrides": ["slack.read_messages"]},
                                      {"connectionId": github["connectionId"],
                                       "toolAllowlist": ["github.list_issues"]},
                                  ]})).json()
        readonly_env = (await c.post("/workspaces/%s/environments" % ws, headers=hdr,
                                     json={"name": "readonly",
                                           "connections": [
                                               {"connectionId": github["connectionId"],
                                                "toolAllowlist": ["github.list_issues",
                                                                  "github.get_file"]}]})).json()
    return {"ws": ws, "env": env, "readonly_env": readonly_env, "slack": slack,
            "github": github, "gateway": gateway, "log": log}


def _gw_client(world):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=world["gateway"]),
                             base_url="http://gw")


async def test_production_binding_fulfills_frozen_seam(world):
    async with _gw_client(world) as gw:
        resp = await gw.get("/internal/environments/%s/binding" % world["env"]["environmentId"],
                            headers={"X-Convoy-Internal": INTERNAL})
    assert resp.status_code == 200
    binding = EnvironmentBinding.model_validate(resp.json())

    env_id = world["env"]["environmentId"]
    assert binding.id == "%s@1/production" % env_id  # id doubles as binding_ref
    assert binding.tenant_id == world["ws"]
    assert binding.kind == "production"
    assert binding.clock.mode == "real"
    assert binding.sandbox_template == "convoy-sbx-v3"
    assert binding.data_namespace == "%s/%s" % (world["ws"], env_id)

    grants = {g.tool_id: g for g in binding.tool_registry}
    assert set(grants) == {"slack.read_messages", "slack.post_message", "github.list_issues"}
    # manifest flags surface verbatim; promote_overrides escalate
    assert grants["slack.post_message"].execution == "promoted"
    assert grants["slack.post_message"].side_effecting is True
    assert grants["slack.read_messages"].execution == "promoted"  # escalated
    assert grants["slack.read_messages"].side_effecting is True
    assert grants["github.list_issues"].execution == "inline"
    assert grants["github.list_issues"].side_effecting is False
    assert grants["github.list_issues"].scope.connection_id == world["github"]["connectionId"]

    # every endpoint is a gateway door, keyed by connection
    slack_id, gh_id = world["slack"]["connectionId"], world["github"]["connectionId"]
    assert {str(u) for u in binding.connector_endpoints.values()} == {
        "%s/mcp/%s" % (BASE, slack_id), "%s/mcp/%s" % (BASE, gh_id)}


async def test_sandbox_binding_fails_closed_on_unmocked_side_effects(world):
    """One environment definition → two bindings; sandbox compilation refuses
    side-effecting tools without mocks rather than handing rehearsals real
    connectors."""
    async with _gw_client(world) as gw:
        resp = await gw.get("/internal/environments/%s/binding?kind=sandbox"
                            % world["env"]["environmentId"],
                            headers={"X-Convoy-Internal": INTERNAL})
        assert resp.status_code == 409
        assert "slack.post_message" in resp.json()["detail"]
        assert "slack.read_messages" in resp.json()["detail"]  # escalated → needs mock too

        # an all-read environment compiles a sandbox binding with a virtual clock
        resp = await gw.get("/internal/environments/%s/binding?kind=sandbox"
                            % world["readonly_env"]["environmentId"],
                            headers={"X-Convoy-Internal": INTERNAL})
        assert resp.status_code == 200
        binding = EnvironmentBinding.model_validate(resp.json())
        assert binding.kind == "sandbox"
        assert binding.id.endswith("@1/sandbox")
        assert binding.clock.mode == "virtual" and binding.clock.advance == "manual"


async def test_binding_auth_version_pin_and_unknown(world):
    async with _gw_client(world) as gw:
        assert (await gw.get("/internal/environments/x/binding")).status_code == 401
        resp = await gw.get("/internal/environments/%s/binding?version=1"
                            % world["env"]["environmentId"],
                            headers={"X-Convoy-Internal": INTERNAL})
        assert resp.json()["id"].endswith("@1/production")
        resp = await gw.get("/internal/environments/nope/binding",
                            headers={"X-Convoy-Internal": INTERNAL})
        assert resp.status_code == 404
        resp = await gw.get("/internal/environments/%s/binding?kind=bogus"
                            % world["env"]["environmentId"],
                            headers={"X-Convoy-Internal": INTERNAL})
        assert resp.status_code == 404


async def test_per_connection_door_scopes_discovery_and_dispatch(world):
    env = world["env"]
    claims = RunClaims(run_id="r1", mission_id="m1", workspace_id=world["ws"],
                       environment_id=env["environmentId"], environment_version=env["version"])
    token = mint_run_token(claims, secret=GW_SECRET)
    hdr = {"Authorization": "Bearer %s" % token}
    slack_id, gh_id = world["slack"]["connectionId"], world["github"]["connectionId"]

    async with _gw_client(world) as gw:
        resp = await gw.post("/mcp/%s" % slack_id, headers=hdr,
                             json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert {t["name"] for t in resp.json()["result"]["tools"]} == {
            "slack.read_messages", "slack.post_message"}
        resp = await gw.post("/mcp/%s" % gh_id, headers=hdr,
                             json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert {t["name"] for t in resp.json()["result"]["tools"]} == {"github.list_issues"}

        # dispatch through the wrong door is denied and audited
        resp = await gw.post("/mcp/%s" % gh_id, headers=hdr,
                             json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                   "params": {"name": "slack.read_messages",
                                              "arguments": {"channel": "#x"}}})
        assert resp.json()["result"]["isError"] is True

    denied = [e for e in world["log"].for_mission("m1") if e.type == "tool_denied"]
    assert len(denied) == 1


def test_sandbox_template_and_namespace_are_hash_relevant():
    h0 = policy_hash("live", [], None)
    h1 = policy_hash("live", [], None, sandbox_template="sbx-v2")
    h2 = policy_hash("live", [], None, data_namespace="acme/env1")
    assert len({h0, h1, h2}) == 3
