"""The console health check: POST /connections/{id}/verify re-probes the
stored credential and moves the connection between active and needs_reauth.
The probe is fed by the vault's internal reveal; the value never appears in
any response."""

from __future__ import annotations

from typing import Optional

import pytest
from convoy_environments.console_api import app as console_app_module
from convoy_environments.console_api import build_console_app
from convoy_environments.db import Base, make_session_factory
from convoy_environments.secrets import SecretsService
from convoy_environments.secrets.builtin import BuiltinBackend, MasterKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class _FakeConnector:
    """Stands in for the provider probe; the test steers its verdict."""

    verdict: Optional[bool] = True
    seen: list = []

    def __init__(self, *args, **kwargs) -> None:  # noqa: D107
        pass

    async def verify_credential(self, credential: str) -> Optional[bool]:
        _FakeConnector.seen.append(credential)
        return _FakeConnector.verdict


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    secrets = SecretsService(sessions, {"builtin": BuiltinBackend(MasterKey(MasterKey.generate().encode()))})
    monkeypatch.setattr(console_app_module, "get_connector", lambda *a, **k: _FakeConnector())
    _FakeConnector.verdict = True
    _FakeConnector.seen = []
    app = build_console_app(sessions, secrets, provisioning_token="test-internal-token")
    return TestClient(app)


def _org_and_connection(client: TestClient) -> tuple[str, dict, str]:
    org = client.post(
        "/organizations",
        headers={"X-Convoy-Internal": "test-internal-token"},
        json={"name": "Acme", "creatorEmail": "admin@example.com"},
    ).json()
    headers = {"X-Convoy-User": org["userId"]}
    connection = client.post(
        f"/organizations/{org['organizationId']}/connections",
        headers=headers,
        json={
            "kind": "mcp_managed",
            "provider": "slack",
            "displayName": "Slack",
            "config": {},
            "manifest": {"tools": [], "domains": []},
        },
    ).json()
    return org["organizationId"], headers, connection["connectionId"]


def test_verify_without_a_credential_reports_and_changes_nothing(client: TestClient) -> None:
    org_id, headers, connection_id = _org_and_connection(client)
    resp = client.post(f"/organizations/{org_id}/connections/{connection_id}/verify", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verified"] is None
    assert body["reason"] == "no credential stored"
    assert _FakeConnector.seen == []


def test_verify_flips_status_both_ways_using_the_stored_credential(client: TestClient) -> None:
    org_id, headers, connection_id = _org_and_connection(client)
    attach = client.post(
        f"/organizations/{org_id}/connections/{connection_id}/secret",
        headers=headers,
        json={"secretValue": "xoxb-stored-token"},
    )
    assert attach.status_code == 200, attach.text
    assert attach.json()["status"] == "active"

    # The provider starts rejecting the token: verify says so and flags it.
    _FakeConnector.verdict = False
    down = client.post(f"/organizations/{org_id}/connections/{connection_id}/verify", headers=headers)
    assert down.json() == {"connectionId": connection_id, "status": "needs_reauth", "verified": False}

    # The probe read the STORED value through the vault, not a resupplied one.
    assert _FakeConnector.seen[-1] == "xoxb-stored-token"

    # Recovery: the provider accepts again and the connection heals in place.
    _FakeConnector.verdict = True
    up = client.post(f"/organizations/{org_id}/connections/{connection_id}/verify", headers=headers)
    assert up.json() == {"connectionId": connection_id, "status": "active", "verified": True}


def test_unknown_probe_keeps_the_status(client: TestClient) -> None:
    org_id, headers, connection_id = _org_and_connection(client)
    client.post(
        f"/organizations/{org_id}/connections/{connection_id}/secret",
        headers=headers,
        json={"secretValue": "token"},
    )
    _FakeConnector.verdict = None
    resp = client.post(f"/organizations/{org_id}/connections/{connection_id}/verify", headers=headers)
    assert resp.json()["verified"] is None
    assert resp.json()["status"] == "active"
