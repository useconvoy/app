"""The provider directory: code connectors listed straight from the
registry with their console metadata, declarative platform connectors
published through the provisioning-token door, and the org custom flow
kept out of it."""

from __future__ import annotations

import pytest
from convoy_environments.console_api import build_console_app
from convoy_environments.db import Base, make_session_factory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class _UnusedSecrets:
    pass


@pytest.fixture()
def client() -> TestClient:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    app = build_console_app(sessions, _UnusedSecrets(), provisioning_token="test-internal-token")
    return TestClient(app)


def _user(client: TestClient) -> dict:
    org = client.post(
        "/organizations",
        headers={"X-Convoy-Internal": "test-internal-token"},
        json={"name": "Acme", "creatorEmail": "admin@example.com"},
    ).json()
    return {"X-Convoy-User": org["userId"]}


def test_directory_lists_code_connectors_with_console_metadata(client: TestClient) -> None:
    entries = client.get("/providers", headers=_user(client)).json()
    by_provider = {entry["provider"]: entry for entry in entries}

    assert "mcp_custom" not in by_provider  # the mechanism, not a provider
    for provider in ("slack", "google", "github", "notion"):
        assert by_provider[provider]["kind"] == "code"
        assert by_provider[provider]["scope"] == "platform"

    slack = by_provider["slack"]
    assert slack["displayName"] == "Slack"
    assert slack["credential"]["label"] == "Bot token"
    assert len(slack["credential"]["steps"]) == 3
    assert {tool["name"] for tool in slack["tools"]} == {
        "slack.list_channels", "slack.read_messages", "slack.post_message",
    }
    post = next(tool for tool in slack["tools"] if tool["name"] == "slack.post_message")
    assert post["sideEffecting"] is True and post["execution"] == "promoted"


def test_declarative_platform_connectors_publish_through_the_internal_door(
    client: TestClient,
) -> None:
    body = {
        "provider": "linear",
        "displayName": "Linear",
        "description": "Read and file issues in your Linear workspace.",
        "mcpUrl": "https://mcp.convoy.internal/linear",
        "manifest": {"tools": [
            {"name": "linear.list_issues", "execution": "inline", "sideEffecting": False},
            {"name": "linear.create_issue", "execution": "promoted", "sideEffecting": True},
        ]},
        "credentialLabel": "API key",
        "credentialSteps": ["Create a Linear API key.", "Paste it here."],
    }
    denied = client.post("/platform-connectors", json=body)
    assert denied.status_code == 401

    created = client.post(
        "/platform-connectors", json=body,
        headers={"X-Convoy-Internal": "test-internal-token"},
    )
    assert created.status_code == 200, created.text

    entries = client.get("/providers", headers=_user(client)).json()
    linear = next(entry for entry in entries if entry["provider"] == "linear")
    assert linear["kind"] == "declarative"
    assert linear["scope"] == "platform"
    assert linear["mcpUrl"] == "https://mcp.convoy.internal/linear"
    assert [tool["name"] for tool in linear["tools"]] == [
        "linear.list_issues", "linear.create_issue",
    ]

    # Disabling removes it from the directory without deleting the row.
    client.post(
        "/platform-connectors", json={**body, "enabled": False},
        headers={"X-Convoy-Internal": "test-internal-token"},
    )
    entries = client.get("/providers", headers=_user(client)).json()
    assert all(entry["provider"] != "linear" for entry in entries)
