"""Notion connector (internal integration token)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..schema import ConnectionManifest, ToolSpec
from .base import Connector, ConnectorError, raise_for_status, register

_TOOLS = [
    ToolSpec(
        name="notion.search", execution="inline", sideEffecting=False,
        description="Search pages and databases shared with the integration.",
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    ),
    ToolSpec(
        name="notion.get_page", execution="inline", sideEffecting=False,
        description="Fetch a page's properties by id.",
        inputSchema={"type": "object", "properties": {"pageId": {"type": "string"}}, "required": ["pageId"]},
    ),
    ToolSpec(
        name="notion.create_page", execution="promoted", sideEffecting=True,
        description="Create a page under a parent page or database.",
        inputSchema={"type": "object",
                     "properties": {"parent": {"type": "object"}, "properties": {"type": "object"},
                                    "children": {"type": "array"}},
                     "required": ["parent", "properties"]},
    ),
]

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"


@register
class NotionConnector(Connector):
    provider = "notion"

    async def manifest(self, credential: Optional[str] = None) -> ConnectionManifest:
        return ConnectionManifest(tools=list(_TOOLS))

    async def invoke(self, tool: str, args: Dict[str, Any], credential: str) -> Any:
        headers = {"Authorization": "Bearer %s" % credential, "Notion-Version": _VERSION}
        async with self._client(headers=headers) as client:
            if tool == "notion.search":
                resp = await client.post(_API + "/search", json={"query": args["query"]})
            elif tool == "notion.get_page":
                resp = await client.get(_API + "/pages/%s" % args["pageId"])
            elif tool == "notion.create_page":
                payload = {"parent": args["parent"], "properties": args["properties"]}
                if args.get("children"):
                    payload["children"] = args["children"]
                resp = await client.post(_API + "/pages", json=payload)
            else:
                raise ConnectorError("notion: unknown tool %s" % tool)
            await raise_for_status(resp, "notion")
            return resp.json()
