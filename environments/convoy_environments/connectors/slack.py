"""Slack connector (bot token). Wedge set: read channels/messages, post."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..schema import ConnectionManifest, ToolSpec
from .base import Connector, ConnectorError, raise_for_status, register

_TOOLS = [
    ToolSpec(
        name="slack.list_channels", execution="inline", sideEffecting=False,
        description="List public channels the bot can see.",
        inputSchema={"type": "object", "properties": {"limit": {"type": "integer"}}},
    ),
    ToolSpec(
        name="slack.read_messages", execution="inline", sideEffecting=False,
        description="Read recent messages from a channel.",
        inputSchema={"type": "object", "properties": {"channel": {"type": "string"}, "limit": {"type": "integer"}},
                     "required": ["channel"]},
    ),
    ToolSpec(
        name="slack.post_message", execution="promoted", sideEffecting=True,
        description="Post a message to a channel.",
        inputSchema={"type": "object", "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
                     "required": ["channel", "text"]},
    ),
]

_API = "https://slack.com/api"


@register
class SlackConnector(Connector):
    provider = "slack"
    display_name = "Slack"
    description = "Read channels and messages, and post as this organization's app."
    credential_label = "Bot token"
    credential_placeholder = "xoxb-..."
    credential_steps = (
        "Create a Slack app for your workspace and install it.",
        "Give it the channel read and write scopes your Agents need.",
        "Paste the bot token that starts with xoxb.",
    )
    oauth_authorize_url = "https://slack.com/oauth/v2/authorize"
    oauth_token_url = "https://slack.com/api/oauth.v2.access"
    oauth_scopes = ("channels:read", "channels:history", "chat:write")

    async def manifest(self, credential: Optional[str] = None) -> ConnectionManifest:
        return ConnectionManifest(tools=list(_TOOLS))

    async def exchange_oauth_code(self, code: str, redirect_uri: str) -> Dict[str, str]:
        """The hosted install's exchange: the code becomes the workspace bot
        token, org-level by nature (it belongs to the installed app, not to
        whoever clicked)."""
        client_id, client_secret = self.oauth_client_env()
        if not client_id:
            raise ConnectorError("slack: hosted install is not configured")
        token_url = self._config_url(self.oauth_token_url, "oauthTokenUrl")
        async with self._client() as client:
            resp = await client.post(token_url, data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            })
            await raise_for_status(resp, "slack")
            body = resp.json()
        token = body.get("access_token")
        if not body.get("ok", False) or not token:
            raise ConnectorError(
                "slack: install exchange rejected (%s)" % body.get("error", "unknown_error")
            )
        return {"secretValue": token, "detail": (body.get("team") or {}).get("name", "")}

    async def verify_credential(self, credential: str) -> Optional[bool]:
        """Prove the bot token against Slack without reading or mutating data."""
        api = self._config_url(_API, "apiBaseUrl", "api_base_url")
        headers = {"Authorization": "Bearer %s" % credential}
        async with self._client(headers=headers) as client:
            resp = await client.post(api + "/auth.test")
            await raise_for_status(resp, "slack")
            body = resp.json()
            if not body.get("ok", False):
                raise ConnectorError(
                    "slack: credential rejected (%s)" % body.get("error", "unknown_error")
                )
        return True

    async def invoke(self, tool: str, args: Dict[str, Any], credential: str) -> Any:
        api = self._config_url(_API, "apiBaseUrl", "api_base_url")
        headers = {"Authorization": "Bearer %s" % credential}
        async with self._client(headers=headers) as client:
            if tool == "slack.list_channels":
                resp = await client.get(api + "/conversations.list",
                                        params={"limit": args.get("limit", 100), "types": "public_channel"})
            elif tool == "slack.read_messages":
                resp = await client.get(api + "/conversations.history",
                                        params={"channel": args["channel"], "limit": args.get("limit", 50)})
            elif tool == "slack.post_message":
                resp = await client.post(api + "/chat.postMessage",
                                         json={"channel": args["channel"], "text": args["text"]})
            else:
                raise ConnectorError("slack: unknown tool %s" % tool)
            await raise_for_status(resp, "slack")
            body = resp.json()
            if not body.get("ok", False):
                err = body.get("error", "unknown_error")
                if err in ("invalid_auth", "token_revoked", "account_inactive"):
                    raise ConnectorError("slack: credential rejected (%s)" % err)
                raise ConnectorError("slack: %s" % err, retryable=err in ("ratelimited",))
            return body
