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
