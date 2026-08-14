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
_DOCS = "https://docs.googleapis.com/v1"
_SCOPES = " ".join([
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
])
_TOKEN_TTL_SLACK_S = 120  # refresh a couple of minutes early

_DOC_MIME = "application/vnd.google-apps.document"

_TOOLS = [
    ToolSpec(
        name="google.drive_list_files", execution="inline", sideEffecting=False,
        description="List files in a Drive folder. Defaults to spreadsheets; pass"
                    " mimeType application/vnd.google-apps.document for Docs.",
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
    ToolSpec(
        name="google.docs_read", execution="inline", sideEffecting=False,
        description="Read a Google Doc as plain text (title plus body).",
        inputSchema={"type": "object",
                     "properties": {"documentId": {"type": "string"}},
                     "required": ["documentId"]},
    ),
    ToolSpec(
        name="google.docs_update", execution="promoted", sideEffecting=True,
        description="Replace a Google Doc's body with new plain text content.",
        inputSchema={"type": "object",
                     "properties": {"documentId": {"type": "string"},
                                    "content": {"type": "string"}},
                     "required": ["documentId", "content"]},
    ),
]


def _doc_text(document: Dict[str, Any]) -> str:
    """Flatten a Docs API body into plain text: paragraph runs in order,
    table cells traversed recursively. Formatting is out of scope; the
    reader is a model deciding whether prose is stale."""
    def walk(elements: Any) -> str:
        text = []
        for element in elements or []:
            paragraph = element.get("paragraph")
            if paragraph:
                for part in paragraph.get("elements", []):
                    run = part.get("textRun")
                    if run:
                        text.append(run.get("content", ""))
            table = element.get("table")
            if table:
                for row in table.get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        text.append(walk(cell.get("content")))
        return "".join(text)

    return walk(document.get("body", {}).get("content"))

# (client_email, scopes, token_url) → (access_token, expires_at). Module-level so the
# hourly exchange amortizes across the per-call connector instances.
_token_cache: Dict[Tuple[str, str, str], Tuple[str, float]] = {}


@register
class GoogleConnector(Connector):
    provider = "google"
    display_name = "Google Drive"
    description = "List Drive files, read and update documents, and work with spreadsheets."
    credential_label = "Service account key (JSON)"
    credential_placeholder = '{ "type": "service_account", ... }'
    credential_multiline = True
    credential_steps = (
        "In your Google Cloud console, create a service account and download its JSON key.",
        "Turn on the Drive, Sheets, and Docs APIs for that project.",
        "Share the Drive folders, documents, and spreadsheets this organization works in with the service account's email address.",
    )
    oauth_authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    oauth_token_url = _TOKEN_URL
    oauth_scopes = tuple(_SCOPES.split(" "))
    oauth_scope_delimiter = " "
    # offline + consent: Google only issues the refresh token the connection
    # lives on when both are asked for explicitly.
    oauth_extra_params = {
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
    }

    async def manifest(self, credential: Optional[str] = None) -> ConnectionManifest:
        return ConnectionManifest(tools=list(_TOOLS))

    async def _access_token(self, client, credential: str) -> str:
        """Short-lived access token from either credential shape: a
        service-account JSON key (JWT-bearer grant) or the hosted install's
        authorized-user JSON (refresh-token grant, with the app client
        pair sealed inside the stored credential so the refresh needs
        nothing outside the vault)."""
        try:
            key = json.loads(credential)
        except ValueError as err:
            raise ConnectorError("google: secret is not a JSON credential (%s)" % err)

        token_url = self._config_url(_TOKEN_URL, "tokenUrl", "token_url")
        now = time.time()

        if key.get("type") == "authorized_user" or "refresh_token" in key:
            try:
                refresh_token = key["refresh_token"]
                client_id, client_secret = key["client_id"], key["client_secret"]
            except KeyError as err:
                raise ConnectorError("google: authorized-user credential is missing %s" % err)
            cache_key = ("refresh:%s" % client_id, refresh_token[-12:], token_url)
            cached = _token_cache.get(cache_key)
            if cached and cached[1] > now + _TOKEN_TTL_SLACK_S:
                return cached[0]
            resp = await client.post(token_url, data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            })
            if resp.status_code != 200:
                raise ConnectorError("google: credential rejected (token refresh %d: %s)"
                                     % (resp.status_code, resp.text[:200]))
            body = resp.json()
            token = body["access_token"]
            _token_cache[cache_key] = (token, now + int(body.get("expires_in", 3600)))
            return token

        try:
            client_email, private_key = key["client_email"], key["private_key"]
        except KeyError as err:
            raise ConnectorError("google: secret is not a service-account JSON key (%s)" % err)
        cache_key = (client_email, _SCOPES, token_url)
        cached = _token_cache.get(cache_key)
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

    async def exchange_oauth_code(self, code: str, redirect_uri: str) -> Dict[str, str]:
        """The hosted install's exchange. The stored credential is an
        authorized-user JSON carrying the refresh token plus the app client
        pair, so every later call (and refresh) is self-contained against
        the vault; the granting person's consent can be revoked at
        myaccount.google.com without touching Convoy."""
        client_id, client_secret = self.oauth_client_env()
        if not client_id:
            raise ConnectorError("google: hosted install is not configured")
        token_url = self._config_url(_TOKEN_URL, "tokenUrl", "token_url")
        async with self._client() as client:
            resp = await client.post(token_url, data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            })
            if resp.status_code != 200:
                raise ConnectorError("google: install exchange rejected (%d: %s)"
                                     % (resp.status_code, resp.text[:200]))
            body = resp.json()
        refresh_token = body.get("refresh_token")
        if not refresh_token:
            raise ConnectorError(
                "google: no refresh token was granted; remove the app's access at"
                " myaccount.google.com and install again")
        return {
            "secretValue": json.dumps({
                "type": "authorized_user",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            }),
            "detail": "",
        }

    async def verify_credential(self, credential: str) -> Optional[bool]:
        """The token exchange is the probe: a bad key or revoked service
        account fails here without touching any Drive data."""
        async with self._client() as client:
            await self._access_token(client, credential)
        return True

    async def invoke(self, tool: str, args: Dict[str, Any], credential: str) -> Any:
        drive_api = self._config_url(_DRIVE, "driveBaseUrl", "drive_base_url")
        sheets_api = self._config_url(_SHEETS, "sheetsBaseUrl", "sheets_base_url")
        docs_api = self._config_url(_DOCS, "docsBaseUrl", "docs_base_url")
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
            elif tool == "google.docs_read":
                resp = await client.get(
                    "%s/documents/%s" % (docs_api, args["documentId"]), headers=headers)
                await raise_for_status(resp, "google")
                document = resp.json()
                return {"documentId": document.get("documentId", args["documentId"]),
                        "title": document.get("title", ""),
                        "text": _doc_text(document)}
            elif tool == "google.docs_update":
                # Replace-the-body semantics: read the current end index, then
                # one batchUpdate that deletes the old body and inserts the new
                # text. Whole-document replacement keeps the approval story
                # honest (what was approved is exactly what lands) and stays
                # idempotent under runtime retries via the gateway's keyed
                # effects journal.
                current = await client.get(
                    "%s/documents/%s" % (docs_api, args["documentId"]), headers=headers)
                await raise_for_status(current, "google")
                body = current.json()
                content = body.get("body", {}).get("content", [])
                end_index = content[-1].get("endIndex", 1) if content else 1
                requests = []
                if end_index > 2:
                    requests.append({"deleteContentRange": {
                        "range": {"startIndex": 1, "endIndex": end_index - 1}}})
                new_text = args["content"]
                if new_text:
                    requests.append({"insertText": {
                        "location": {"index": 1}, "text": new_text}})
                if not requests:
                    return {"documentId": args["documentId"], "replies": []}
                resp = await client.post(
                    "%s/documents/%s:batchUpdate" % (docs_api, args["documentId"]),
                    headers=headers, json={"requests": requests})
            else:
                raise ConnectorError("google: unknown tool %s" % tool)
            await raise_for_status(resp, "google")
            return resp.json()
