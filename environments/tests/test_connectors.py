import json

import httpx
import pytest

from convoy_environments.connectors import ConnectorError, get_connector


def transport_of(handler):
    return httpx.MockTransport(handler)


async def test_slack_post_message_injects_bearer_and_parses():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "ts": "1.2"})

    slack = get_connector("slack", transport=transport_of(handler))
    result = await slack.invoke("slack.post_message", {"channel": "#ops", "text": "hi"}, "xoxb-123")
    assert seen["auth"] == "Bearer xoxb-123"
    assert seen["body"] == {"channel": "#ops", "text": "hi"}
    assert result["ok"] is True


async def test_slack_api_level_error_maps_to_connector_error():
    def handler(request):
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    slack = get_connector("slack", transport=transport_of(handler))
    with pytest.raises(ConnectorError, match="credential rejected"):
        await slack.invoke("slack.list_channels", {}, "bad")


async def test_5xx_is_retryable():
    def handler(request):
        return httpx.Response(503)

    gh = get_connector("github", transport=transport_of(handler))
    with pytest.raises(ConnectorError) as err:
        await gh.invoke("github.list_issues", {"repo": "useconvoy/app"}, "ghp_x")
    assert err.value.retryable is True


async def test_manifests_annotate_effect_classes():
    slack = get_connector("slack")
    manifest = await slack.manifest()
    by_name = {t.name: t.effectClass for t in manifest.tools}
    assert by_name["slack.read_messages"] == "read"
    assert by_name["slack.post_message"] == "effectful"


async def test_mcp_custom_manifest_defaults_unannotated_to_effectful():
    def handler(request):
        body = json.loads(request.content)
        assert body["method"] == "tools/list"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [
            {"name": "crm.lookup", "inputSchema": {}, "annotations": {"readOnlyHint": True}},
            {"name": "crm.update", "inputSchema": {}},
        ]}})

    mcp = get_connector("mcp_custom", config={"url": "https://tools.acme.com/mcp"}, transport=transport_of(handler))
    manifest = await mcp.manifest()
    by_name = {t.name: t.effectClass for t in manifest.tools}
    assert by_name == {"crm.lookup": "read", "crm.update": "effectful"}


async def test_mcp_custom_call_forwards_and_unwraps_errors():
    def handler(request):
        body = json.loads(request.content)
        assert body["method"] == "tools/call"
        assert body["params"] == {"name": "crm.update", "arguments": {"id": 1}}
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "result": {"isError": True, "content": [{"text": "boom"}]}})

    mcp = get_connector("mcp_custom", config={"url": "https://tools.acme.com/mcp"}, transport=transport_of(handler))
    with pytest.raises(ConnectorError, match="boom"):
        await mcp.invoke("crm.update", {"id": 1}, "tok")


def test_unknown_provider():
    with pytest.raises(ConnectorError):
        get_connector("salesforce")
