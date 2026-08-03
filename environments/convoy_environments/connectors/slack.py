"""Slack connector (bot token). Wedge set: read channels/messages, post."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..schema import ConnectionManifest, ToolSpec
from .base import Connector, ConnectorError, raise_for_status, register

_TOOLS = [
    ToolSpec(
        name="slack.list_channels", effectClass="read",
        description="List public channels the bot can see.",
        inputSchema={"type": "object", "properties": {"limit": {"type": "integer"}}},
    ),
    ToolSpec(
        name="slack.read_messages", effectClass="read",
        description="Read recent messages from a channel.",
        inputSchema={"type": "object", "properties": {"channel": {"type": "string"}, "limit": {"type": "integer"}},
                     "required": ["channel"]},
    ),
    ToolSpec(
        name="slack.post_message", effectClass="effectful",
        description="Post a message to a channel.",
        inputSchema={"type": "object", "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
                     "required": ["channel", "text"]},
    ),
]

_API = "https://slack.com/api"


@register
class SlackConnector(Connector):
    provider = "slack"

    async def manifest(self, credential: Optional[str] = None) -> ConnectionManifest:
        return ConnectionManifest(tools=list(_TOOLS))

    async def invoke(self, tool: str, args: Dict[str, Any], credential: str) -> Any:
        headers = {"Authorization": "Bearer %s" % credential}
        async with self._client(headers=headers) as client:
            if tool == "slack.list_channels":
                resp = await client.get(_API + "/conversations.list",
                                        params={"limit": args.get("limit", 100), "types": "public_channel"})
            elif tool == "slack.read_messages":
                resp = await client.get(_API + "/conversations.history",
                                        params={"channel": args["channel"], "limit": args.get("limit", 50)})
            elif tool == "slack.post_message":
                resp = await client.post(_API + "/chat.postMessage",
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
