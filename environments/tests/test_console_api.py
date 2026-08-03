"""Console API: white-glove flow end-to-end — workspace → connection →
environment → grants → gates — with RBAC enforced at every step."""

import httpx
import pytest

from convoy_environments.console_api import build_console_app
from convoy_environments.db import SqlEventLog
from convoy_environments.db.tables import Membership, User
from convoy_environments.secrets import BuiltinBackend, MasterKey, SecretsService


@pytest.fixture()
def ctx(session_factory):
    mk = MasterKey(MasterKey.generate().encode())
    secrets = SecretsService(session_factory, {"builtin": BuiltinBackend(mk)})
    log = SqlEventLog(session_factory)
    app = build_console_app(session_factory, secrets, event_log=log)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://console")
    return client, session_factory, log


def _as(user):
    return {"X-Convoy-User": user}


async def _bootstrap(client):
    resp = await client.post("/workspaces", json={"name": "Acme", "creatorEmail": "vin@acme.com"})
    body = resp.json()
    return body["workspaceId"], body["userId"]


async def test_full_white_glove_flow(ctx):
    client, sf, log = ctx
    async with client as c:
        ws, admin = await _bootstrap(c)

        # register a slack connection; static manifest, secret is write-only
        resp = await c.post("/workspaces/%s/connections" % ws, headers=_as(admin),
                            json={"kind": "mcp_managed", "provider": "slack",
                                  "displayName": "Slack", "secretValue": "xoxb-token"})
        assert resp.status_code == 200
        conn = resp.json()
        assert "slack.post_message" in conn["tools"]
        assert "xoxb" not in resp.text  # value never echoed

        # environment allowlisting a subset, with an escalation
        resp = await c.post("/workspaces/%s/environments" % ws, headers=_as(admin),
                            json={"name": "renewal-prep",
                                  "connections": [{"connectionId": conn["connectionId"],
                                                   "toolAllowlist": ["slack.read_messages",
                                                                     "slack.post_message"],
                                                   "gateOverrides": {"slack.post_message": "gated"}}]})
        assert resp.status_code == 200
        env = resp.json()
        assert env["version"] == 1 and env["policyHash"]

        # a new version gets a different policy hash
        resp = await c.post("/workspaces/%s/environments/%s/versions" % (ws, env["environmentId"]),
                            headers=_as(admin),
                            json={"name": "renewal-prep",
                                  "connections": [{"connectionId": conn["connectionId"],
                                                   "toolAllowlist": ["slack.read_messages"]}]})
        v2 = resp.json()
        assert v2["version"] == 2 and v2["policyHash"] != env["policyHash"]

        resp = await c.get("/workspaces/%s/environments" % ws, headers=_as(admin))
        assert [e["version"] for e in resp.json()] == [1, 2]


async def test_rbac_denials(ctx):
    client, sf, log = ctx
    async with client as c:
        ws, admin = await _bootstrap(c)
        with sf() as s:
            s.add(User(id="usr_member", email="m@acme.com"))
            s.add(Membership(workspace_id=ws, user_id="usr_member", role="member"))
            s.commit()

        # member cannot register connections
        resp = await c.post("/workspaces/%s/connections" % ws, headers=_as("usr_member"),
                            json={"kind": "mcp_managed", "provider": "slack",
                                  "displayName": "Slack", "secretValue": "x"})
        assert resp.status_code == 403
        # member cannot create environments (builder required)
        resp = await c.post("/workspaces/%s/environments" % ws, headers=_as("usr_member"),
                            json={"name": "x"})
        assert resp.status_code == 403
        # outsider cannot even list
        resp = await c.get("/workspaces/%s/environments" % ws, headers=_as("usr_stranger"))
        assert resp.status_code == 403
        # missing header is 401
        resp = await c.get("/workspaces/%s/environments" % ws)
        assert resp.status_code == 401


async def test_environment_visibility_requires_grant(ctx):
    client, sf, log = ctx
    async with client as c:
        ws, admin = await _bootstrap(c)
        resp = await c.post("/workspaces/%s/connections" % ws, headers=_as(admin),
                            json={"kind": "mcp_managed", "provider": "slack",
                                  "displayName": "Slack", "secretValue": "x"})
        conn_id = resp.json()["connectionId"]
        resp = await c.post("/workspaces/%s/environments" % ws, headers=_as(admin),
                            json={"name": "secret-env",
                                  "connections": [{"connectionId": conn_id,
                                                   "toolAllowlist": ["slack.read_messages"]}]})
        env_id = resp.json()["environmentId"]
        with sf() as s:
            s.add(User(id="usr_member", email="m@acme.com"))
            s.add(Membership(workspace_id=ws, user_id="usr_member", role="member"))
            s.commit()

        resp = await c.get("/workspaces/%s/environments" % ws, headers=_as("usr_member"))
        assert resp.json() == []  # no grant, invisible

        await c.post("/workspaces/%s/environments/%s/grants" % (ws, env_id), headers=_as(admin),
                     json={"userId": "usr_member", "role": "viewer"})
        resp = await c.get("/workspaces/%s/environments" % ws, headers=_as("usr_member"))
        assert len(resp.json()) == 1


async def test_gate_list_and_resolve_with_operator_grant(ctx):
    client, sf, log = ctx
    async with client as c:
        ws, admin = await _bootstrap(c)
        # a gate raised by the gateway for some environment
        log.append({"missionId": "m1", "type": "gate_raised", "gateId": "g1",
                    "kind": "action-approval",
                    "payload": {"tool": "slack.post_message", "idempotencyKey": "k",
                                "environmentId": "env_x"}}, workspace_id=ws)
        with sf() as s:
            s.add(User(id="usr_op", email="op@acme.com"))
            s.add(Membership(workspace_id=ws, user_id="usr_op", role="member"))
            s.add(User(id="usr_view", email="v@acme.com"))
            s.add(Membership(workspace_id=ws, user_id="usr_view", role="member"))
            s.commit()
        from convoy_environments.db.tables import EnvironmentGrant
        with sf() as s:
            s.add(EnvironmentGrant(environment_id="env_x", user_id="usr_op", role="operator"))
            s.add(EnvironmentGrant(environment_id="env_x", user_id="usr_view", role="viewer"))
            s.commit()

        # viewer sees the gate but cannot resolve it
        resp = await c.get("/workspaces/%s/gates" % ws, headers=_as("usr_view"))
        assert [g["gateId"] for g in resp.json()] == ["g1"]
        resp = await c.post("/workspaces/%s/gates/g1/resolve" % ws, headers=_as("usr_view"),
                            json={"resolution": "approve"})
        assert resp.status_code == 403

        # operator resolves with a reason; events land with attribution
        resp = await c.post("/workspaces/%s/gates/g1/resolve" % ws, headers=_as("usr_op"),
                            json={"resolution": "approve", "reason": "looks right"})
        assert resp.status_code == 200
        events = log.for_mission("m1")
        assert [e.type for e in events] == ["gate_raised", "gate_resolved", "human_intervention"]
        assert events[1].resolvedBy == "user:usr_op" and events[1].reason == "looks right"
        assert events[2].kind == "gate_resolution"

        # resolved gates disappear; double-resolve 404s
        resp = await c.get("/workspaces/%s/gates" % ws, headers=_as("usr_op"))
        assert resp.json() == []
        resp = await c.post("/workspaces/%s/gates/g1/resolve" % ws, headers=_as("usr_op"),
                            json={"resolution": "approve"})
        assert resp.status_code == 404
