"""Google Workspace connector (service-account auth).

Server-to-server: the connection's secret is a service-account JSON key; the
customer shares the target Drive folder/sheets with the service account's
email. Each call exchanges the key for a short-lived access token via the
OAuth2 JWT-bearer grant (RS256; no consent screens, no refresh tokens) with
a module-level cache so the exchange happens ~hourly, not per call. The
browser-consent OAuth broker remains a separate, deferred piece — this
connector is the enterprise server-to-server story.

Wedge set for the sheets demo: list spreadsheets in a folder (inline),
read a range (inline), append a row (promoted + side-effecting — one keyed
call per row, so runtime retries can never double-append).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

import jwt

from ..schema import ConnectionManifest, ToolSpec
from .base import Connector, ConnectorError, raise_for_status, register

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRIVE = "https://www.googleapis.com/drive/v3"
_SHEETS = "https://sheets.googleapis.com/v4"
_SCOPES = "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/spreadsheets"
_TOKEN_TTL_SLACK_S = 120  # refresh a couple of minutes early

_TOOLS = [
    ToolSpec(
        name="google.drive_list_files", execution="inline", sideEffecting=False,
        description="List files in a Drive folder (defaults to spreadsheets only).",
        inputSchema={"type": "object",
                     "properties": {"folderId": {"type": "string"},
                                    "mimeType": {"type": "string"},
                                    "pageSize": {"type": "integer"}},
                     "required": ["folderId"]},
    ),
    ToolSpec(
        name="google.sheets_read_range", execution="inline", sideEffecting=False,
        description="Read a range of values from a spreadsheet.",
        inputSchema={"type": "object",
                     "properties": {"spreadsheetId": {"type": "string"},
                                    "range": {"type": "string"}},
                     "required": ["spreadsheetId", "range"]},
    ),
    ToolSpec(
        name="google.sheets_append_row", execution="promoted", sideEffecting=True,
        description="Append one row of values to a spreadsheet.",
        inputSchema={"type": "object",
                     "properties": {"spreadsheetId": {"type": "string"},
                                    "values": {"type": "array", "items": {}},
                                    "range": {"type": "string", "default": "A1"}},
                     "required": ["spreadsheetId", "values"]},
    ),
]

# (client_email, scopes, token_url) → (access_token, expires_at). Module-level so the
# hourly exchange amortizes across the per-call connector instances.
_token_cache: Dict[Tuple[str, str, str], Tuple[str, float]] = {}


@register
class GoogleConnector(Connector):
    provider = "google"

    async def manifest(self, credential: Optional[str] = None) -> ConnectionManifest:
        return ConnectionManifest(tools=list(_TOOLS))

    async def _access_token(self, client, credential: str) -> str:
        try:
            key = json.loads(credential)
            client_email, private_key = key["client_email"], key["private_key"]
        except (ValueError, KeyError) as err:
            raise ConnectorError("google: secret is not a service-account JSON key (%s)" % err)

        token_url = self._config_url(_TOKEN_URL, "tokenUrl", "token_url")
        cache_key = (client_email, _SCOPES, token_url)
        cached = _token_cache.get(cache_key)
        now = time.time()
        if cached and cached[1] > now + _TOKEN_TTL_SLACK_S:
            return cached[0]

        assertion = jwt.encode(
            {"iss": client_email, "scope": _SCOPES, "aud": token_url,
             "iat": int(now), "exp": int(now) + 3600},
            private_key, algorithm="RS256",
        )
        resp = await client.post(token_url, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        })
        if resp.status_code != 200:
            raise ConnectorError("google: credential rejected (token exchange %d: %s)"
                                 % (resp.status_code, resp.text[:200]))
        body = resp.json()
        token = body["access_token"]
        _token_cache[cache_key] = (token, now + int(body.get("expires_in", 3600)))
        return token

    async def verify_credential(self, credential: str) -> Optional[bool]:
        """The token exchange is the probe: a bad key or revoked service
        account fails here without touching any Drive data."""
        async with self._client() as client:
            await self._access_token(client, credential)
        return True

    async def invoke(self, tool: str, args: Dict[str, Any], credential: str) -> Any:
        drive_api = self._config_url(_DRIVE, "driveBaseUrl", "drive_base_url")
        sheets_api = self._config_url(_SHEETS, "sheetsBaseUrl", "sheets_base_url")
        async with self._client() as client:
            token = await self._access_token(client, credential)
            headers = {"Authorization": "Bearer %s" % token}
            if tool == "google.drive_list_files":
                mime = args.get("mimeType", "application/vnd.google-apps.spreadsheet")
                query = "'%s' in parents and trashed = false" % args["folderId"]
                if mime:
                    query += " and mimeType = '%s'" % mime
                resp = await client.get(drive_api + "/files", headers=headers,
                                        params={"q": query, "pageSize": args.get("pageSize", 200),
                                                "fields": "files(id,name),nextPageToken"})
            elif tool == "google.sheets_read_range":
                resp = await client.get(
                    "%s/spreadsheets/%s/values/%s" % (sheets_api, args["spreadsheetId"], args["range"]),
                    headers=headers)
            elif tool == "google.sheets_append_row":
                resp = await client.post(
                    "%s/spreadsheets/%s/values/%s:append" % (sheets_api, args["spreadsheetId"],
                                                             args.get("range", "A1")),
                    headers=headers,
                    params={"valueInputOption": "USER_ENTERED",
                            "insertDataOption": "INSERT_ROWS"},
                    json={"values": [args["values"]]})
            else:
                raise ConnectorError("google: unknown tool %s" % tool)
            await raise_for_status(resp, "google")
            return resp.json()
