from __future__ import annotations

from types import MethodType

import httpx
import pytest

from convoy_environments.connectors.github import GitHubConnector
from convoy_environments.connectors.google import GoogleConnector
from convoy_environments.connectors.slack import SlackConnector


@pytest.mark.asyncio
async def test_slack_and_github_accept_rehearsal_api_base_urls() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("conversations.list"):
            return httpx.Response(200, json={"ok": True, "channels": []})
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    slack = SlackConnector(
        config={"apiBaseUrl": "http://sandboxes.test/s/run-1/slack/api/"},
        transport=transport,
    )
    github = GitHubConnector(
        config={"apiBaseUrl": "http://sandboxes.test/s/run-1/github"},
        transport=transport,
    )

    await slack.invoke("slack.list_channels", {}, "xoxb-test")
    await github.invoke("github.list_issues", {"repo": "acme/api"}, "github-test")

    assert seen == [
        "http://sandboxes.test/s/run-1/slack/api/conversations.list?limit=100&types=public_channel",
        "http://sandboxes.test/s/run-1/github/repos/acme/api/issues?state=open",
    ]


@pytest.mark.asyncio
async def test_slack_verifies_the_real_bot_token_without_reading_workspace_data() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json={"ok": True, "bot_id": "B-DEMO"})

    slack = SlackConnector(
        config={"apiBaseUrl": "http://slack.test/api"},
        transport=httpx.MockTransport(handler),
    )

    assert await slack.verify_credential("xoxb-test") is True
    assert seen == [("POST", "http://slack.test/api/auth.test", "Bearer xoxb-test")]


@pytest.mark.asyncio
async def test_google_accepts_independent_token_drive_and_sheets_base_urls() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.method == "GET":
            return httpx.Response(200, json={"files": []})
        return httpx.Response(200, json={"updates": {"updatedRows": 1}})

    connector = GoogleConnector(
        config={
            "tokenUrl": "http://sandboxes.test/s/run-1/google/oauth2/token",
            "driveBaseUrl": "http://sandboxes.test/s/run-1/google/drive/v3",
            "sheetsBaseUrl": "http://sandboxes.test/s/run-1/google/sheets/v4",
        },
        transport=httpx.MockTransport(handler),
    )

    async def fake_access_token(self, client, credential):
        return "ya29.sandbox"

    connector._access_token = MethodType(fake_access_token, connector)
    await connector.invoke(
        "google.drive_list_files",
        {"folderId": "folder-revops"},
        "service-account-json",
    )
    await connector.invoke(
        "google.sheets_append_row",
        {"spreadsheetId": "sheet-renewals", "range": "Renewals!A1", "values": ["Acme"]},
        "service-account-json",
    )

    assert seen[0].startswith("http://sandboxes.test/s/run-1/google/drive/v3/files?")
    assert seen[1] == (
        "http://sandboxes.test/s/run-1/google/sheets/v4/spreadsheets/"
        "sheet-renewals/values/Renewals!A1:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
    )


@pytest.mark.asyncio
async def test_google_docs_read_flattens_and_update_replaces_the_body() -> None:
    document = {
        "documentId": "doc-runbook",
        "title": "Operations Runbook",
        "body": {"content": [
            {"endIndex": 19, "paragraph": {"elements": [
                {"textRun": {"content": "Exports run nightly"}}]}},
        ]},
    }
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url),
                      request.content.decode() if request.method == "POST" else ""))
        if request.method == "GET":
            return httpx.Response(200, json=document)
        return httpx.Response(200, json={"documentId": "doc-runbook", "replies": [{}]})

    connector = GoogleConnector(
        config={"docsBaseUrl": "http://sandboxes.test/s/run-1/google/docs/v1"},
        transport=httpx.MockTransport(handler),
    )

    async def fake_access_token(self, client, credential):
        return "ya29.sandbox"

    connector._access_token = MethodType(fake_access_token, connector)

    read = await connector.invoke("google.docs_read", {"documentId": "doc-runbook"}, "key")
    assert read == {"documentId": "doc-runbook", "title": "Operations Runbook",
                    "text": "Exports run nightly"}

    await connector.invoke(
        "google.docs_update",
        {"documentId": "doc-runbook", "content": "Exports run hourly"},
        "key",
    )
    method, url, body = calls[-1]
    assert method == "POST"
    assert url == "http://sandboxes.test/s/run-1/google/docs/v1/documents/doc-runbook:batchUpdate"
    # One delete of the old body, then one insert of the approved text.
    assert '"deleteContentRange"' in body and '"endIndex":18' in body
    assert '"insertText"' in body and "Exports run hourly" in body
