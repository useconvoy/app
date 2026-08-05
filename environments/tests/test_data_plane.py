"""Plain-HTTP data-plane facade — byte-parity with the shapes the runtime
actually sends (providers/pydantic_ai_turn.py and activities/promoted.py),
which mirror the stub-env container's contract."""

import httpx
import pytest

from convoy_environments.gateway import GatewayService, mint_run_token
from convoy_environments.gateway.mcp_server import build_app as build_gateway_app

from .test_gateway import CLAIMS, _ok_slack, runtime_key, world  # noqa: F401 — fixture reuse

SECRET = "data-plane-test-secret-0123456789abcd"


def _app(world, allow_anon=False):  # noqa: F811
    sf, secrets = world
    service = GatewayService(sf, secrets, transport=httpx.MockTransport(_ok_slack))
    return build_gateway_app(service, gateway_secret=SECRET,
                             allow_anonymous_data_plane=allow_anon)


def _client(app, token=None):
    headers = {"Authorization": "Bearer %s" % token} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://gw", headers=headers)


async def test_inline_tool_runtime_wire_shape(world):  # noqa: F811
    """POST /tools/{tool_id} with {args, run_id, step_id} → {"result": ...}."""
    token = mint_run_token(CLAIMS, secret=SECRET)
    async with _client(_app(world), token) as c:
        resp = await c.post("/data-plane/env1/1/tools/slack.read_messages",
                            json={"args": {"channel": "#ops"}, "run_id": "run1", "step_id": "s1"})
    assert resp.status_code == 200
    assert resp.json()["result"]["ok"] is True


async def test_promoted_tool_refused_on_tools_route(world):  # noqa: F811
    token = mint_run_token(CLAIMS, secret=SECRET)
    async with _client(_app(world), token) as c:
        resp = await c.post("/data-plane/env1/1/tools/slack.post_message",
                            json={"args": {"channel": "#ops", "text": "hi"}, "run_id": "run1"})
    assert resp.status_code == 409  # side-effecting → must come through /effects


async def test_effect_executes_once_and_replays(world):  # noqa: F811
    """POST /effects/{tool_id} with {idempotency_key, run_id, args} →
    {"result", "replayed"}; a repeated key returns replayed=True and the
    connector fires exactly once."""
    calls = {"n": 0}

    def counting(request):
        calls["n"] += 1
        return _ok_slack(request)

    sf, secrets = world
    service = GatewayService(sf, secrets, transport=httpx.MockTransport(counting))
    app = build_gateway_app(service, gateway_secret=SECRET)
    token = mint_run_token(CLAIMS, secret=SECRET)
    key = runtime_key()
    body = {"idempotency_key": key, "run_id": "run1",
            "args": {"channel": "#ops", "text": "hi"}}
    async with _client(app, token) as c:
        first = (await c.post("/data-plane/env1/1/effects/slack.post_message", json=body)).json()
        second = (await c.post("/data-plane/env1/1/effects/slack.post_message", json=body)).json()
    assert first == {"result": {"ok": True, "messages": []}, "replayed": False}
    assert second["replayed"] is True and second["result"] == first["result"]
    assert calls["n"] == 1


async def test_auth_required_by_default_anon_only_when_flagged(world):  # noqa: F811
    body = {"args": {"channel": "#ops"}, "run_id": "run1"}
    async with _client(_app(world)) as c:  # no token, no flag
        resp = await c.post("/data-plane/env1/1/tools/slack.read_messages", json=body)
        assert resp.status_code == 401

    async with _client(_app(world, allow_anon=True)) as c:  # compose parity
        resp = await c.post("/data-plane/env1/1/tools/slack.read_messages", json=body)
        assert resp.status_code == 200


async def test_token_scoped_to_other_environment_rejected(world):  # noqa: F811
    from convoy_environments.gateway import RunClaims

    other = RunClaims(run_id="r9", mission_id="m9", workspace_id="w1",
                      environment_id="env-other", environment_version=1)
    token = mint_run_token(other, secret=SECRET)
    async with _client(_app(world), token) as c:
        resp = await c.post("/data-plane/env1/1/tools/slack.read_messages",
                            json={"args": {}, "run_id": "r9"})
    assert resp.status_code == 403


async def test_unallowlisted_tool_403(world):  # noqa: F811
    token = mint_run_token(CLAIMS, secret=SECRET)
    async with _client(_app(world), token) as c:
        resp = await c.post("/data-plane/env1/1/tools/slack.list_channels",
                            json={"args": {}, "run_id": "run1"})
    assert resp.status_code == 403
