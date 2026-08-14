"""The hosted install: the provider directory advertises OAuth only when
this service holds the app's client credentials, and the exchange endpoint
turns an authorization code into the organization's one connection for the
provider, with the credential stored write-only and reinstalls rotating in
place instead of stacking connections."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from convoy_environments.console_api import app as console_app_module
from convoy_environments.console_api import build_console_app
from convoy_environments.db import Base, make_session_factory
from convoy_environments.schema import ConnectionManifest
from convoy_environments.secrets import SecretsService
from convoy_environments.secrets.builtin import BuiltinBackend, MasterKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class _FakeSlack:
    """Stands in for the Slack connector's exchange; the test steers it."""

    display_name = "Slack"
    fail = False
    seen: list = []

    def __init__(self, *args, **kwargs) -> None:  # noqa: D107
        pass

    async def manifest(self, credential: Optional[str] = None) -> ConnectionManifest:
        return ConnectionManifest(tools=[])

    async def exchange_oauth_code(
        self, code: str, redirect_uri: str, client_id: str, client_secret: str
    ) -> Dict[str, str]:
        _FakeSlack.seen.append((code, redirect_uri, client_id, client_secret))
        if _FakeSlack.fail:
            from convoy_environments.connectors import ConnectorError

            raise ConnectorError("slack: install exchange rejected (bad_code)")
        return {"secretValue": "xoxb-installed-%s" % code, "detail": "Acme HQ"}


@pytest.fixture()
def harness(monkeypatch) -> tuple:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    secrets = SecretsService(
        sessions, {"builtin": BuiltinBackend(MasterKey(MasterKey.generate().encode()))}
    )
    monkeypatch.setattr(console_app_module, "get_connector", lambda *a, **k: _FakeSlack())
    monkeypatch.setenv("CONVOY_OAUTH_SLACK_CLIENT_ID", "client-1")
    monkeypatch.setenv("CONVOY_OAUTH_SLACK_CLIENT_SECRET", "secret-1")
    _FakeSlack.fail = False
    _FakeSlack.seen = []
    app = build_console_app(sessions, secrets, provisioning_token="test-internal-token")
    return TestClient(app), secrets


def _org(client: TestClient) -> tuple[str, dict]:
    org = client.post(
        "/organizations",
        headers={"X-Convoy-Internal": "test-internal-token"},
        json={"name": "Acme", "creatorEmail": "admin@example.com"},
    ).json()
    return org["organizationId"], {"X-Convoy-User": org["userId"]}


def test_directory_offers_oauth_only_when_client_credentials_exist(
    harness, monkeypatch
) -> None:
    client, _ = harness
    _, headers = _org(client)
    entries = {e["provider"]: e for e in client.get("/providers", headers=headers).json()}
    slack = entries["slack"]
    assert slack["oauth"]["authorizeUrl"] == "https://slack.com/oauth/v2/authorize"
    assert slack["oauth"]["clientId"] == "client-1"
    # The client secret never rides the directory.
    assert "secret-1" not in str(slack["oauth"])
    assert slack["oauth"]["scopes"] == ["channels:read", "channels:history", "chat:write"]
    # Providers without a declared install carry no oauth block.
    assert "oauth" not in entries["github"]

    monkeypatch.delenv("CONVOY_OAUTH_SLACK_CLIENT_ID")
    entries = {e["provider"]: e for e in client.get("/providers", headers=headers).json()}
    assert "oauth" not in entries["slack"]


def test_exchange_creates_the_org_connection_and_reinstall_rotates(harness) -> None:
    client, secrets = harness
    org_id, headers = _org(client)

    first = client.post(
        f"/organizations/{org_id}/connections/oauth-exchange",
        headers=headers,
        json={"provider": "slack", "code": "code-1",
              "redirectUri": "https://console.example/api/connectors/slack/callback"},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "active" and body["detail"] == "Acme HQ"
    assert _FakeSlack.seen[-1] == (
        "code-1", "https://console.example/api/connectors/slack/callback",
        "client-1", "secret-1",
    )

    listed = client.get(f"/organizations/{org_id}/connections", headers=headers).json()
    slack_rows = [row for row in listed if row["provider"] == "slack"]
    assert len(slack_rows) == 1

    # Reinstalling rotates the credential on the same connection.
    second = client.post(
        f"/organizations/{org_id}/connections/oauth-exchange",
        headers=headers,
        json={"provider": "slack", "code": "code-2",
              "redirectUri": "https://console.example/api/connectors/slack/callback"},
    )
    assert second.json()["connectionId"] == body["connectionId"]
    listed = client.get(f"/organizations/{org_id}/connections", headers=headers).json()
    assert len([row for row in listed if row["provider"] == "slack"]) == 1


def test_exchange_fails_closed_without_config_or_on_rejection(harness, monkeypatch) -> None:
    client, _ = harness
    org_id, headers = _org(client)

    _FakeSlack.fail = True
    rejected = client.post(
        f"/organizations/{org_id}/connections/oauth-exchange",
        headers=headers,
        json={"provider": "slack", "code": "bad", "redirectUri": "https://console.example/cb"},
    )
    assert rejected.status_code == 400
    assert "install exchange failed" in rejected.json()["detail"]

    monkeypatch.delenv("CONVOY_OAUTH_SLACK_CLIENT_ID")
    unconfigured = client.post(
        f"/organizations/{org_id}/connections/oauth-exchange",
        headers=headers,
        json={"provider": "slack", "code": "code", "redirectUri": "https://console.example/cb"},
    )
    assert unconfigured.status_code == 400
    assert "no hosted install" in unconfigured.json()["detail"]
