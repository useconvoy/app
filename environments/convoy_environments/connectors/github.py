"""GitHub connector: fine-grained PAT, or the installed GitHub App.

Two credential shapes reach invoke(): a pasted personal access token
(plain string), or the hosted install's JSON carrying an installation id.
The App path stores no long-lived secret at all: every call mints a
short-lived installation token from the app's private key (held only by
this service) and the installation the customer approved on GitHub.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional, Tuple

import jwt

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
_TOKEN_TTL_SLACK_S = 120

# installation id -> (token, expires_at). Installation tokens last an hour;
# module-level so the mint amortizes across per-call connector instances.
_installation_tokens: Dict[str, Tuple[str, float]] = {}


def _app_env() -> Tuple[str, str, str]:
    """The GitHub App's identity from this service's environment:
    (slug, app id, private key PEM). Empty strings while unconfigured."""
    return (
        os.environ.get("CONVOY_GITHUB_APP_SLUG", ""),
        os.environ.get("CONVOY_GITHUB_APP_ID", ""),
        os.environ.get("CONVOY_GITHUB_APP_PRIVATE_KEY", ""),
    )


@register
class GitHubConnector(Connector):
    provider = "github"
    display_name = "GitHub"
    description = "Read issues and files, and open issues in your repositories."
    credential_label = "Personal access token"
    credential_placeholder = "github_pat_..."
    credential_steps = (
        "In GitHub, create a fine-grained personal access token.",
        "Grant it read access to the repositories your Agents work in, and issues write access if they should open issues.",
        "Paste the token here.",
    )

    @classmethod
    def oauth_directory_entry(cls) -> Optional[Dict[str, Any]]:
        """The GitHub App install is the hosted door: the customer picks
        which repositories to grant on GitHub's own screen. Permissions
        live on the app, so there is no scope list to send."""
        slug, app_id, private_key = _app_env()
        if not slug or not app_id or not private_key:
            return None
        return {
            "authorizeUrl": "https://github.com/apps/%s/installations/new" % slug,
            "clientId": app_id,
            "scopes": [],
        }

    async def manifest(self, credential: Optional[str] = None) -> ConnectionManifest:
        return ConnectionManifest(tools=list(_TOOLS))

    def _app_jwt(self, app_id: str, private_key: str) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": app_id},
            private_key, algorithm="RS256",
        )

    async def _bearer_token(self, client, credential: str) -> str:
        """Resolve either credential shape to the bearer for API calls. A
        pasted token is used as-is; the installed App's JSON mints (and
        caches) a short-lived installation token."""
        if not credential.lstrip().startswith("{"):
            return credential
        try:
            parsed = json.loads(credential)
            installation_id = str(parsed["installation_id"])
        except (ValueError, KeyError) as err:
            raise ConnectorError("github: credential JSON is missing %s" % err)
        _, app_id, private_key = _app_env()
        if not app_id or not private_key:
            raise ConnectorError("github: the app install needs the service's app key")

        now = time.time()
        cached = _installation_tokens.get(installation_id)
        if cached and cached[1] > now + _TOKEN_TTL_SLACK_S:
            return cached[0]
        api = self._config_url(_API, "apiBaseUrl", "api_base_url")
        resp = await client.post(
            "%s/app/installations/%s/access_tokens" % (api, installation_id),
            headers={"Authorization": "Bearer %s" % self._app_jwt(app_id, private_key),
                     "Accept": "application/vnd.github+json"},
        )
        if resp.status_code not in (200, 201):
            raise ConnectorError("github: credential rejected (installation token %d: %s)"
                                 % (resp.status_code, resp.text[:200]))
        body = resp.json()
        # Tokens last an hour; the header-declared expiry is authoritative
        # but parsing it buys nothing over a conservative fixed window.
        _installation_tokens[installation_id] = (body["token"], now + 55 * 60)
        return body["token"]

    async def verify_credential(self, credential: str) -> Optional[bool]:
        """For the App install, minting an installation token IS the probe;
        pasted tokens keep the stored-but-unverified behavior."""
        if not credential.lstrip().startswith("{"):
            return None
        async with self._client() as client:
            await self._bearer_token(client, credential)
        return True

    async def exchange_oauth_code(self, code: str, redirect_uri: str) -> Dict[str, str]:
        """The install flow's completion. GitHub redirects back with an
        installation id rather than an OAuth code; proving we can mint an
        installation token for it is the verification, and the stored
        credential is just that id. No long-lived secret exists."""
        installation_id = code.strip()
        if not installation_id.isdigit():
            raise ConnectorError("github: install did not return an installation id")
        _, app_id, private_key = _app_env()
        if not app_id or not private_key:
            raise ConnectorError("github: hosted install is not configured")
        api = self._config_url(_API, "apiBaseUrl", "api_base_url")
        headers = {"Authorization": "Bearer %s" % self._app_jwt(app_id, private_key),
                   "Accept": "application/vnd.github+json"}
        async with self._client() as client:
            detail = ""
            lookup = await client.get("%s/app/installations/%s" % (api, installation_id),
                                      headers=headers)
            if lookup.status_code == 200:
                detail = (lookup.json().get("account") or {}).get("login", "")
            minted = await client.post(
                "%s/app/installations/%s/access_tokens" % (api, installation_id),
                headers=headers,
            )
            if minted.status_code not in (200, 201):
                raise ConnectorError("github: install exchange rejected (%d: %s)"
                                     % (minted.status_code, minted.text[:200]))
        return {
            "secretValue": json.dumps({"type": "github_app_installation",
                                       "installation_id": int(installation_id)}),
            "detail": detail,
        }

    async def invoke(self, tool: str, args: Dict[str, Any], credential: str) -> Any:
        api = self._config_url(_API, "apiBaseUrl", "api_base_url")
        async with self._client(headers={"Accept": "application/vnd.github+json"}) as client:
            bearer = await self._bearer_token(client, credential)
            headers = {"Authorization": "Bearer %s" % bearer}
            if tool == "github.list_issues":
                resp = await client.get("%s/repos/%s/issues" % (api, args["repo"]),
                                        headers=headers,
                                        params={"state": args.get("state", "open")})
            elif tool == "github.get_file":
                params = {"ref": args["ref"]} if args.get("ref") else None
                resp = await client.get("%s/repos/%s/contents/%s" % (api, args["repo"], args["path"]),
                                        headers=headers, params=params)
            elif tool == "github.create_issue":
                resp = await client.post("%s/repos/%s/issues" % (api, args["repo"]),
                                         headers=headers,
                                         json={"title": args["title"], "body": args.get("body", "")})
            else:
                raise ConnectorError("github: unknown tool %s" % tool)
            await raise_for_status(resp, "github")
            return resp.json()
