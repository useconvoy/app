"""Event triggers: provider webhooks become runs.

The gateway is the one place external systems already terminate, so it owns
the webhook doors too: ``POST /gateway/hooks/{connection_id}``. A delivery is
verified against the connection's webhook signing secret (GitHub HMAC,
Slack v0 signature with timestamp freshness, shared-secret header for custom
systems — fail closed when no secret is configured), matched against the
organization's event rules, and each matching rule creates a run through the
control plane's normal ``POST /runs`` with ``started_via="event"``.

Idempotency: the run id derives from the provider's delivery id (GitHub
``X-GitHub-Delivery``, Slack ``event_id``, body hash as the fallback), so
provider redelivery converges on the same run instead of double-firing.

The webhook signing secret lives in ``connection.config["webhookSecret"]``
rather than the write-only credential vault: it is a *verification* secret
the service must read on every delivery, not an access credential — the
vault's one-read-path invariant stays intact.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from ..db.tables import AuditLog, Connection, EventRule

MAX_EVENT_CONTEXT_CHARS = 1500
_SLACK_TIMESTAMP_TOLERANCE_S = 300


class HookVerificationError(Exception):
    """Signature missing, stale, or wrong — the delivery is rejected."""


class HookDispatchError(Exception):
    """The delivery verified but runs could not be created; the provider
    should redeliver (idempotent run ids make that safe)."""


def verify_github(secret: str, body: bytes, headers: dict[str, str]) -> None:
    signature = headers.get("x-hub-signature-256", "")
    if not signature.startswith("sha256="):
        raise HookVerificationError("missing X-Hub-Signature-256")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HookVerificationError("GitHub signature mismatch")


def verify_slack(
    secret: str, body: bytes, headers: dict[str, str], now: float | None = None
) -> None:
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    if not timestamp or not signature:
        raise HookVerificationError("missing Slack signature headers")
    try:
        age = abs((now if now is not None else time.time()) - float(timestamp))
    except ValueError as err:
        raise HookVerificationError("bad Slack timestamp") from err
    if age > _SLACK_TIMESTAMP_TOLERANCE_S:
        raise HookVerificationError("stale Slack delivery")
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HookVerificationError("Slack signature mismatch")


def verify_custom(secret: str, headers: dict[str, str]) -> None:
    presented = headers.get("x-convoy-hook-secret", "")
    if not presented or not hmac.compare_digest(presented, secret):
        raise HookVerificationError("hook secret mismatch")


def verify_delivery(provider: str, secret: str, body: bytes, headers: dict[str, str]) -> None:
    """Fail closed: a connection without a configured webhook secret accepts
    no deliveries at all."""
    if not secret:
        raise HookVerificationError("connection has no webhook secret configured")
    if provider == "github":
        verify_github(secret, body, headers)
    elif provider == "slack":
        verify_slack(secret, body, headers)
    else:
        verify_custom(secret, headers)


def extract_event_type(provider: str, payload: dict[str, Any], headers: dict[str, str]) -> str:
    """The dotted event name rules match on: GitHub "issues.opened"
    (event header + action), Slack "app_mention" (inner event type),
    custom systems' own ``eventType`` field."""
    if provider == "github":
        event = headers.get("x-github-event", "")
        action = payload.get("action")
        return f"{event}.{action}" if isinstance(action, str) and action else event
    if provider == "slack":
        inner = payload.get("event")
        if isinstance(inner, dict) and isinstance(inner.get("type"), str):
            return str(inner["type"])
        return str(payload.get("type", ""))
    value = payload.get("eventType", "")
    return value if isinstance(value, str) else ""


def delivery_id(
    provider: str, payload: dict[str, Any], headers: dict[str, str], body: bytes
) -> str:
    if provider == "github" and headers.get("x-github-delivery"):
        return headers["x-github-delivery"]
    if provider == "slack" and isinstance(payload.get("event_id"), str):
        return str(payload["event_id"])
    if isinstance(payload.get("deliveryId"), str):
        return str(payload["deliveryId"])
    return hashlib.sha256(body).hexdigest()


def run_id_for_delivery(connection_id: str, delivery: str) -> str:
    digest = hashlib.sha256(f"{connection_id}:{delivery}".encode()).hexdigest()[:12]
    return f"run-evt-{digest}"


def event_context(event_type: str, payload: dict[str, Any]) -> str:
    """A compact, size-bounded rendering of the event for the run's goal."""
    rendered = json.dumps(payload, sort_keys=True)[:MAX_EVENT_CONTEXT_CHARS]
    return f"Triggering event {event_type}: {rendered}"


class HookDispatcher:
    """Verify one delivery, match rules, create runs. The control-plane
    credential is the same service token the website presents; without one
    configured, matched deliveries fail retryably so nothing is dropped."""

    def __init__(
        self,
        session_factory: Any,
        *,
        control_plane_url: str = "",
        control_plane_token: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._sessions = session_factory
        self._base_url = control_plane_url.rstrip("/")
        self._token = control_plane_token
        self._transport = transport

    async def dispatch(
        self, connection_id: str, body: bytes, headers: dict[str, str]
    ) -> dict[str, Any]:
        headers = {key.lower(): value for key, value in headers.items()}
        with self._sessions() as session:
            connection = session.get(Connection, connection_id)
            if connection is None:
                raise LookupError(f"unknown connection {connection_id}")
            provider = connection.provider
            organization_id = connection.organization_id
            secret = str((connection.config or {}).get("webhookSecret", ""))

        verify_delivery(provider, secret, body, headers)
        try:
            payload: dict[str, Any] = json.loads(body.decode() or "{}")
        except (ValueError, UnicodeDecodeError) as err:
            raise HookVerificationError("delivery body is not JSON") from err

        # Slack's endpoint handshake: echo the challenge, match no rules.
        if provider == "slack" and payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        event_type = extract_event_type(provider, payload, headers)
        delivery = delivery_id(provider, payload, headers, body)

        with self._sessions() as session:
            rules: list[EventRule] = (
                session.query(EventRule)
                .filter_by(connection_id=connection_id, event_type=event_type, enabled=True)
                .all()
            )
            templates = [
                {
                    "rule_id": rule.id,
                    "agent_id": rule.agent_id,
                    "goal": rule.goal,
                    "environment_id": rule.environment_id,
                    "budget_usd": rule.budget_usd,
                    "tools": list(rule.tools or []),
                    "instructions": list(rule.instructions or []),
                    "started_by": rule.started_by,
                    "tenant_id": rule.organization_id,
                }
                for rule in rules
            ]

        runs = []
        for template in templates:
            runs.append(await self._create_run(template, event_type, payload, delivery))

        with self._sessions() as session:
            session.add(
                AuditLog(
                    organization_id=organization_id,
                    actor_user_id=None,
                    action="hook.received",
                    subject_type="connection",
                    subject_id=connection_id,
                    diff={"event": event_type, "matched": len(templates), "runs": runs},
                )
            )
            session.commit()
        return {"event": event_type, "matched": len(templates), "runs": runs}

    async def _create_run(
        self,
        template: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        delivery: str,
    ) -> str:
        if not self._base_url or not self._token:
            raise HookDispatchError("event triggers need the control plane configured")
        run_id = run_id_for_delivery(str(template["rule_id"]), delivery)
        goal = f"{template['goal']}\n\n{event_context(event_type, payload)}"
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
                response = await client.post(
                    f"{self._base_url}/runs",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "X-Actor-Id": template["started_by"] or "system:event",
                        "X-Tenant-Id": template["tenant_id"],
                    },
                    json={
                        "goal": goal,
                        "environment_id": template["environment_id"],
                        "budget_usd": template["budget_usd"],
                        "run_id": run_id,
                        "tools": template["tools"],
                        "instructions": template["instructions"],
                        "success_criteria": [],
                        "fixture_gates": {},
                        "max_children": 5,
                        "agent_id": template["agent_id"],
                        "started_by": template["started_by"],
                        "started_via": "event",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as err:
            raise HookDispatchError(f"run creation failed: {err}") from err
        return run_id
