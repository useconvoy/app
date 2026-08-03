"""mcp_custom — bring-your-own remote MCP server.

The gateway terminates MCP on the agent side; this connector speaks MCP
outward to the customer's server (JSON-RPC 2.0 over HTTP POST — the
streamable-HTTP transport's plain-JSON response mode; SSE-streamed responses
are not supported in v1).

Effect classes: MCP tool annotations carry readOnlyHint; absent hints default
to `effectful` — never `read` — so an unannotated tool gets the two-phase
envelope rather than silently skipping it. Registering the connection snap-
shots the manifest (hash-pinned); a server that later changes its tool list
no longer matches its manifest_hash and fails closed at the gateway.
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, Optional

from ..schema import ConnectionManifest, ToolSpec
from .base import Connector, ConnectorError, raise_for_status, register

_PROTOCOL_VERSION = "2025-06-18"


@register
class McpCustomConnector(Connector):
    provider = "mcp_custom"

    def __init__(self, config: Optional[Dict[str, Any]] = None, transport=None) -> None:
        super().__init__(config, transport)
        self._ids = itertools.count(1)
        if not (self.config or {}).get("url"):
            raise ConnectorError("mcp_custom requires config.url")

    def _headers(self, credential: Optional[str]) -> Dict[str, str]:
        headers = {"Accept": "application/json", "MCP-Protocol-Version": _PROTOCOL_VERSION}
        if credential:
            scheme = self.config.get("authScheme", "Bearer")
            headers["Authorization"] = "%s %s" % (scheme, credential)
        return headers

    async def _rpc(self, client, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        resp = await client.post(self.config["url"],
                                 json={"jsonrpc": "2.0", "id": next(self._ids), "method": method,
                                       "params": params or {}})
        await raise_for_status(resp, "mcp_custom")
        body = resp.json()
        if "error" in body:
            raise ConnectorError("mcp_custom: %s" % body["error"].get("message", "rpc error"))
        return body.get("result")

    async def manifest(self, credential: Optional[str] = None) -> ConnectionManifest:
        async with self._client(headers=self._headers(credential)) as client:
            result = await self._rpc(client, "tools/list")
        tools = []
        for t in result.get("tools", []):
            read_only = bool((t.get("annotations") or {}).get("readOnlyHint", False))
            tools.append(
                ToolSpec(
                    name=t["name"],
                    description=t.get("description", ""),
                    inputSchema=t.get("inputSchema", {}),
                    effectClass="read" if read_only else "effectful",
                )
            )
        return ConnectionManifest(tools=tools)

    async def invoke(self, tool: str, args: Dict[str, Any], credential: str) -> Any:
        async with self._client(headers=self._headers(credential)) as client:
            result = await self._rpc(client, "tools/call", {"name": tool, "arguments": args})
        if result and result.get("isError"):
            content = result.get("content") or [{}]
            raise ConnectorError("mcp_custom: tool error: %s" % content[0].get("text", "unknown"))
        return result
