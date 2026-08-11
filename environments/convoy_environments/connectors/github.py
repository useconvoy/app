"""GitHub connector (personal access token / fine-grained PAT)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..schema import ConnectionManifest, ToolSpec
from .base import Connector, ConnectorError, raise_for_status, register

_TOOLS = [
    ToolSpec(
        name="github.list_issues", execution="inline", sideEffecting=False,
        description="List issues in a repository.",
        inputSchema={"type": "object", "properties": {"repo": {"type": "string"}, "state": {"type": "string"}},
                     "required": ["repo"]},
    ),
    ToolSpec(
        name="github.get_file", execution="inline", sideEffecting=False,
        description="Read a file's content from a repository.",
        inputSchema={"type": "object", "properties": {"repo": {"type": "string"}, "path": {"type": "string"},
                                                      "ref": {"type": "string"}},
                     "required": ["repo", "path"]},
    ),
    ToolSpec(
        name="github.create_issue", execution="promoted", sideEffecting=True,
        description="Open an issue in a repository.",
        inputSchema={"type": "object", "properties": {"repo": {"type": "string"}, "title": {"type": "string"},
                                                      "body": {"type": "string"}},
                     "required": ["repo", "title"]},
    ),
]

_API = "https://api.github.com"


@register
class GitHubConnector(Connector):
    provider = "github"

    async def manifest(self, credential: Optional[str] = None) -> ConnectionManifest:
        return ConnectionManifest(tools=list(_TOOLS))

    async def invoke(self, tool: str, args: Dict[str, Any], credential: str) -> Any:
        api = self._config_url(_API, "apiBaseUrl", "api_base_url")
        headers = {"Authorization": "Bearer %s" % credential,
                   "Accept": "application/vnd.github+json"}
        async with self._client(headers=headers) as client:
            if tool == "github.list_issues":
                resp = await client.get("%s/repos/%s/issues" % (api, args["repo"]),
                                        params={"state": args.get("state", "open")})
            elif tool == "github.get_file":
                params = {"ref": args["ref"]} if args.get("ref") else None
                resp = await client.get("%s/repos/%s/contents/%s" % (api, args["repo"], args["path"]),
                                        params=params)
            elif tool == "github.create_issue":
                resp = await client.post("%s/repos/%s/issues" % (api, args["repo"]),
                                         json={"title": args["title"], "body": args.get("body", "")})
            else:
                raise ConnectorError("github: unknown tool %s" % tool)
            await raise_for_status(resp, "github")
            return resp.json()
