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


@pytest.mark.asyncio
async def test_google_authorized_user_credential_refreshes_then_calls() -> None:
    import json as jsonlib

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request.content.decode()))
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "ya29.refreshed", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer ya29.refreshed"
        return httpx.Response(200, json={"files": []})

    connector = GoogleConnector(
        config={
            "tokenUrl": "http://google.test/token",
            "driveBaseUrl": "http://google.test/drive/v3",
        },
        transport=httpx.MockTransport(handler),
    )
    credential = jsonlib.dumps({
        "type": "authorized_user",
        "refresh_token": "refresh-1",
        "client_id": "client-1",
        "client_secret": "secret-1",
    })
    await connector.invoke("google.drive_list_files", {"folderId": "folder-1"}, credential)
    token_call = calls[0]
    assert "grant_type=refresh_token" in token_call[1]
    assert "refresh-1" in token_call[1]


@pytest.mark.asyncio
async def test_github_app_installation_credential_mints_and_calls(monkeypatch) -> None:
    import json as jsonlib

    monkeypatch.setenv("CONVOY_GITHUB_APP_SLUG", "convoy")
    monkeypatch.setenv("CONVOY_GITHUB_APP_ID", "4242")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv("CONVOY_GITHUB_APP_PRIVATE_KEY", pem)

    import convoy_environments.connectors.github as github_module

    monkeypatch.setattr(github_module, "_installation_tokens", {})

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_short_lived"})
        assert request.headers["authorization"] == "Bearer ghs_short_lived"
        return httpx.Response(200, json=[])

    connector = GitHubConnector(
        config={"apiBaseUrl": "http://github.test"},
        transport=httpx.MockTransport(handler),
    )
    credential = jsonlib.dumps({"type": "github_app_installation", "installation_id": 77})
    await connector.invoke("github.list_issues", {"repo": "acme/api"}, credential)
    assert calls[0] == "http://github.test/app/installations/77/access_tokens"
    assert calls[1] == "http://github.test/repos/acme/api/issues?state=open"

    # The directory advertises the install door once the app env exists.
    advert = GitHubConnector.oauth_directory_entry()
    assert advert == {
        "authorizeUrl": "https://github.com/apps/convoy/installations/new",
        "clientId": "4242",
        "scopes": [],
    }
