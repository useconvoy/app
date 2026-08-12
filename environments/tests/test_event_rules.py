"""Event triggers: signature verification, rule matching, idempotent
run creation, the Slack handshake, and the console CRUD (including the
registry's first delete endpoint)."""

import hashlib
import hmac
import json
import time

import httpx
import pytest
from convoy_environments.console_api import build_console_app
from convoy_environments.db import Base, make_session_factory
from convoy_environments.db.tables import Connection, EventRule, Organization
from convoy_environments.gateway.hooks import (
    HookDispatcher,
    HookVerificationError,
    run_id_for_delivery,
    verify_delivery,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


def _sessions():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _seed(sessions, *, provider: str = "github", secret: str = "hook-secret-123") -> None:
    with sessions() as session:
        session.add(Organization(id="org-1", name="Acme"))
        session.add(
            Connection(
                id="conn-1",
                organization_id="org-1",
                kind="mcp_managed",
                provider=provider,
                display_name=provider,
                config={"webhookSecret": secret} if secret else {},
                manifest={},
                manifest_hash="",
                status="active",
            )
        )
        session.add(
            EventRule(
                id="rule-1",
                organization_id="org-1",
                connection_id="conn-1",
                event_type="issues.opened",
                agent_id="agent-1",
                enabled=True,
                goal="Triage the new issue",
                environment_id="env-1/sandbox",
                budget_usd="5",
                tools=["github.list_issues"],
                instructions=["Read the issue", "Summarize it"],
                started_by="user-1",
            )
        )
        session.commit()


def _github_headers(secret: str, body: bytes, event: str = "issues") -> dict:
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": signature,
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "delivery-1",
    }


# ------------------------------------------------------------ verification


def test_verification_fails_closed_without_a_secret() -> None:
    with pytest.raises(HookVerificationError, match="no webhook secret"):
        verify_delivery("github", "", b"{}", {})


def test_github_signature_round_trip_and_mismatch() -> None:
    body = b'{"action": "opened"}'
    headers = {k.lower(): v for k, v in _github_headers("s3cret-value", body).items()}
    verify_delivery("github", "s3cret-value", body, headers)
    with pytest.raises(HookVerificationError, match="mismatch"):
        verify_delivery("github", "other-secret", body, headers)


def test_slack_signature_and_staleness() -> None:
    secret, body = "slack-secret", b'{"type": "event_callback"}'
    now = time.time()
    timestamp = str(int(now))
    base = b"v0:" + timestamp.encode() + b":" + body
    good = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    headers = {"x-slack-request-timestamp": timestamp, "x-slack-signature": good}
    verify_delivery("slack", secret, body, headers)
    stale = {"x-slack-request-timestamp": str(int(now) - 4000), "x-slack-signature": good}
    with pytest.raises(HookVerificationError):
        verify_delivery("slack", secret, body, stale)


def test_custom_shared_secret_header() -> None:
    verify_delivery("acme-internal", "hook-secret", b"{}", {"x-convoy-hook-secret": "hook-secret"})
    with pytest.raises(HookVerificationError):
        verify_delivery("acme-internal", "hook-secret", b"{}", {})


# --------------------------------------------------------------- dispatch


@pytest.mark.anyio
async def test_matching_delivery_creates_an_idempotent_event_run() -> None:
    sessions = _sessions()
    _seed(sessions)
    captured: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(202, json={"run_id": captured[-1]["run_id"], "status": "planning"})

    dispatcher = HookDispatcher(
        sessions,
        control_plane_url="http://control-plane.test",
        control_plane_token="service-token",
        transport=httpx.MockTransport(handler),
    )
    body = json.dumps({"action": "opened", "issue": {"number": 7}}).encode()
    result = await dispatcher.dispatch("conn-1", body, _github_headers("hook-secret-123", body))

    assert result["event"] == "issues.opened"
    assert result["matched"] == 1
    assert result["runs"] == [run_id_for_delivery("rule-1", "delivery-1")]
    sent = captured[0]
    assert sent["started_via"] == "event"
    assert sent["agent_id"] == "agent-1"
    assert sent["environment_id"] == "env-1/sandbox"
    assert sent["instructions"] == ["Read the issue", "Summarize it"]
    assert sent["goal"].startswith("Triage the new issue")
    assert "Triggering event issues.opened" in sent["goal"]

    # Redelivery converges on the same run id.
    again = await dispatcher.dispatch("conn-1", body, _github_headers("hook-secret-123", body))
    assert again["runs"] == result["runs"]


@pytest.mark.anyio
async def test_non_matching_event_types_match_no_rules() -> None:
    sessions = _sessions()
    _seed(sessions)
    dispatcher = HookDispatcher(
        sessions, transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    body = json.dumps({"action": "closed"}).encode()
    result = await dispatcher.dispatch("conn-1", body, _github_headers("hook-secret-123", body))
    assert result["matched"] == 0
    assert result["runs"] == []


@pytest.mark.anyio
async def test_slack_url_verification_echoes_the_challenge() -> None:
    sessions = _sessions()
    _seed(sessions, provider="slack", secret="slack-secret")
    dispatcher = HookDispatcher(sessions)
    body = json.dumps({"type": "url_verification", "challenge": "chal-123"}).encode()
    timestamp = str(int(time.time()))
    base = b"v0:" + timestamp.encode() + b":" + body
    signature = "v0=" + hmac.new(b"slack-secret", base, hashlib.sha256).hexdigest()
    result = await dispatcher.dispatch(
        "conn-1",
        body,
        {"x-slack-request-timestamp": timestamp, "x-slack-signature": signature},
    )
    assert result == {"challenge": "chal-123"}


# ------------------------------------------------------------ console CRUD


def test_event_rule_crud_via_the_console_api() -> None:
    sessions = _sessions()

    class _UnusedSecrets:
        pass

    app = build_console_app(sessions, _UnusedSecrets(), provisioning_token="test-internal-token")
    client = TestClient(app)
    org = client.post(
        "/organizations",
        headers={"X-Convoy-Internal": "test-internal-token"},
        json={"name": "Acme", "creatorEmail": "admin@example.com"},
    ).json()
    org_id, user_id = org["organizationId"], org["userId"]
    headers = {"X-Convoy-User": user_id}

    connection = client.post(
        f"/organizations/{org_id}/connections",
        headers=headers,
        json={
            "kind": "mcp_managed",
            "provider": "github",
            "displayName": "GitHub",
            "config": {},
            "manifest": {"tools": [], "domains": []},
        },
    ).json()

    secret = client.post(
        f"/organizations/{org_id}/connections/{connection['connectionId']}/webhook-secret",
        headers=headers,
        json={"secret": "hook-secret-123"},
    )
    assert secret.status_code == 200, secret.text
    assert secret.json()["hookPath"].endswith(connection["connectionId"])

    created = client.post(
        f"/organizations/{org_id}/event-rules",
        headers=headers,
        json={
            "connectionId": connection["connectionId"],
            "eventType": "issues.opened",
            "agentId": "agent-1",
            "goal": "Triage the new issue",
            "environmentId": "env-1/sandbox",
            "budgetUsd": "5",
            "tools": ["github.list_issues"],
            "instructions": ["Read", "Summarize"],
        },
    )
    assert created.status_code == 200, created.text
    rule_id = created.json()["ruleId"]

    listed = client.get(f"/organizations/{org_id}/event-rules?agent_id=agent-1", headers=headers)
    assert [rule["ruleId"] for rule in listed.json()] == [rule_id]

    deleted = client.delete(f"/organizations/{org_id}/event-rules/{rule_id}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/organizations/{org_id}/event-rules", headers=headers).json() == []
    missing = client.delete(f"/organizations/{org_id}/event-rules/{rule_id}", headers=headers)
    assert missing.status_code == 404
