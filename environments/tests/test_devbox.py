"""Devbox components: egress policy + live proxy, credential lease path, and
the fill sidecar (credentials reach the page, never the response)."""

import asyncio
import json

import httpx
import pytest

from convoy_environments.devbox.egress_proxy import EgressPolicy, run_proxy
from convoy_environments.devbox.fill_sidecar import FillRequest, FillService, build_sidecar_app
from convoy_environments.gateway import GatewayService, RunClaims, mint_run_token
from convoy_environments.gateway.mcp_server import build_app as build_gateway_app
from convoy_environments.gateway.policy import PolicyDenied
from convoy_environments.db.tables import (
    Connection as ConnectionRow,
    Environment as EnvironmentRow,
    EnvironmentConnection as EnvConnRow,
    Workspace,
)
from convoy_environments.schema import policy_hash
from convoy_environments.secrets import BuiltinBackend, MasterKey, SecretsService


# -- egress policy ---------------------------------------------------------

def test_egress_policy_exact_and_subdomains():
    policy = EgressPolicy(["dmv.ca.gov", "Slack.com "])
    assert policy.allows("dmv.ca.gov")
    assert policy.allows("www.dmv.ca.gov")
    assert policy.allows("slack.com:443")
    assert not policy.allows("evil-dmv.ca.gov.example.com")
    assert not policy.allows("cadmv.ca.gov.attacker.io")
    assert not policy.allows("google.com")
    assert not policy.allows("")


async def test_proxy_tunnels_allowed_and_blocks_denied():
    async def echo(reader, writer):
        data = await reader.readline()
        writer.write(b"pong:" + data)
        await writer.drain()
        writer.close()

    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    proxy = await run_proxy(["127.0.0.1"], port=0)
    proxy_port = proxy.sockets[0].getsockname()[1]

    async def connect_through(host, port):
        r, w = await asyncio.open_connection("127.0.0.1", proxy_port)
        w.write(("CONNECT %s:%d HTTP/1.1\r\n\r\n" % (host, port)).encode())
        await w.drain()
        status = await r.readline()
        return r, w, status

    # allowed: tunnel established, bytes flow both ways
    r, w, status = await connect_through("127.0.0.1", upstream_port)
    assert b"200" in status
    await r.readline()  # drain blank line
    w.write(b"ping\n")
    await w.drain()
    assert await r.readline() == b"pong:ping\n"
    w.close()

    # denied: 403 before any upstream connection
    r, w, status = await connect_through("blocked.example.com", 443)
    assert b"403" in status
    w.close()

    upstream.close()
    proxy.close()


# -- credential lease ------------------------------------------------------

@pytest.fixture()
def browser_world(session_factory):
    """Environment with a browser policy allowlisting dmv.ca.gov and a
    browser_identity connection covering it."""
    mk = MasterKey(MasterKey.generate().encode())
    secrets = SecretsService(session_factory, {"builtin": BuiltinBackend(mk)})
    sid = secrets.create("w1", "dmv-login", json.dumps({"username": "ops@acme.com",
                                                       "password": "hunter2"}))
    manifest = {"tools": [], "domains": ["dmv.ca.gov"]}
    from convoy_environments.schema import manifest_hash
    mhash = manifest_hash(manifest)
    with session_factory() as s:
        s.add(Workspace(id="w1", name="Acme"))
        s.add(ConnectionRow(id="cb", workspace_id="w1", kind="browser_identity", provider="browser",
                            display_name="DMV login", config={}, secret_ref=sid,
                            manifest=manifest, manifest_hash=mhash, status="active"))
        s.add(EnvironmentRow(id="env1", version=1, workspace_id="w1", name="dmv",
                             backing_type="live",
                             browser_policy={"allowedDomains": ["dmv.ca.gov"], "persistProfile": True},
                             policy_hash=policy_hash("live", []), budget_defaults={}))
        s.add(EnvConnRow(environment_id="env1", environment_version=1, connection_id="cb",
                         manifest_hash=mhash, tool_allowlist=[], gate_overrides={}))
        s.commit()
    return session_factory, secrets


CLAIMS = RunClaims(run_id="r1", mission_id="m1", workspace_id="w1",
                   environment_id="env1", environment_version=1)


def test_lease_resolves_and_logs_without_credential(browser_world):
    sf, secrets = browser_world
    from convoy_environments.db import SqlEventLog

    log = SqlEventLog(sf)
    svc = GatewayService(sf, secrets, event_log=log)
    lease = svc.browser_credential_lease(CLAIMS, "dmv.ca.gov")
    assert lease["username"] == "ops@acme.com" and lease["password"] == "hunter2"
    events = log.for_mission("m1")
    assert events[0].type == "tool_call" and events[0].tool == "browser.request_login"
    assert "hunter2" not in json.dumps(events[0].model_dump(exclude_none=True), default=str)

    with pytest.raises(PolicyDenied, match="allowlist"):
        svc.browser_credential_lease(CLAIMS, "chase.com")


# -- fill sidecar ----------------------------------------------------------

async def test_sidecar_fills_via_cdp_and_never_returns_values(browser_world):
    sf, secrets = browser_world
    gateway_app = build_gateway_app(GatewayService(sf, secrets), gateway_secret="s")
    token = mint_run_token(CLAIMS, secret="s")

    evaluated = {}

    async def fake_eval(js, cdp_url):
        evaluated["js"] = js
        return json.dumps({"usernameFilled": True, "passwordFilled": True})

    service = FillService(
        gateway_url="http://gw", run_token=token, evaluator=fake_eval,
        transport=httpx.ASGITransport(app=gateway_app),
    )
    app = build_sidecar_app(service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sc") as c:
        resp = await c.post("/fill", json={"domain": "dmv.ca.gov"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "filled", "domain": "dmv.ca.gov"}
    # the credential went into the page script...
    assert "hunter2" in evaluated["js"] and "ops@acme.com" in evaluated["js"]
    # ...and never into the HTTP response
    assert "hunter2" not in resp.text

    # unallowlisted domain: gateway denies, sidecar surfaces 403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sc") as c:
        resp = await c.post("/fill", json={"domain": "chase.com"})
    assert resp.status_code == 403


async def test_sidecar_rejects_unmatched_selectors(browser_world):
    sf, secrets = browser_world
    gateway_app = build_gateway_app(GatewayService(sf, secrets), gateway_secret="s")
    token = mint_run_token(CLAIMS, secret="s")

    async def fake_eval(js, cdp_url):
        return json.dumps({"usernameFilled": True, "passwordFilled": False})

    service = FillService(gateway_url="http://gw", run_token=token, evaluator=fake_eval,
                          transport=httpx.ASGITransport(app=gateway_app))
    app = build_sidecar_app(service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sc") as c:
        resp = await c.post("/fill", json={"domain": "dmv.ca.gov"})
    assert resp.status_code == 422


def test_fill_request_defaults():
    req = FillRequest(domain="dmv.ca.gov")
    assert "password" in req.passwordSelector
