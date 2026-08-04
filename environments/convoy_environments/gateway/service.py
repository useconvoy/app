"""GatewayService — the enforcement point every tool call passes through.

read   → invoke, one collapsed tool_call event
effectful → tool_intent → invoke → tool_executed → tool_result
gated  → tool_intent → gate_raised (parked; the agent lands state and dies).
         On retry with the same idempotency key after console approval:
         tool_approved → invoke → tool_executed → tool_result. Rejection →
         tool_denied.

Idempotency: a retry whose key already has a recorded tool_result returns the
recorded result without re-invoking (crash-replay safety — the same contract
the sim gateway in agent-evals enforces). Every executed call appends a
budget_debit; envelope *enforcement* is the runtime's job, metering is ours.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import httpx

from ..connectors import ConnectorError, get_connector
from ..db import SqlEventLog
from ..secrets import SecretsService
from .policy import PolicyDenied, PolicyEngine, ToolResolution
from .tokens import RunClaims

FLAT_TOOL_DEBIT_USD = 0.001


class Parked(Exception):
    """Raised to the transport layer when a call is waiting on a gate."""

    def __init__(self, gate_id: str, idempotency_key: str) -> None:
        super().__init__("parked on gate %s" % gate_id)
        self.gate_id = gate_id
        self.idempotency_key = idempotency_key


class GatewayService:
    def __init__(self, session_factory, secrets: SecretsService,
                 event_log: Optional[SqlEventLog] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._policy = PolicyEngine(session_factory)
        self._secrets = secrets
        self._log = event_log or SqlEventLog(session_factory)
        self._transport = transport  # test seam for connectors

    # -- helpers -----------------------------------------------------------

    def _base(self, claims: RunClaims, step_id: Optional[str]) -> Dict[str, Any]:
        return {"missionId": claims.mission_id, "stepId": step_id}

    def _mission_events(self, claims: RunClaims):
        return self._log.for_mission(claims.mission_id)

    def _recorded_result(self, claims: RunClaims, key: str):
        """Successful result recorded for this key, if any. Errored results
        never dedupe — a retry with the same key re-runs the call."""
        for e in self._mission_events(claims):
            if e.type == "tool_result" and e.idempotencyKey == key and e.error is None:
                return e
        return None

    def _gate_state(self, claims: RunClaims, key: str):
        """(gate_id, resolution|None) for the gate guarding idempotency key,
        or (None, None) if no gate was raised for it."""
        gate_id = None
        resolution = None
        for e in self._mission_events(claims):
            if e.type == "gate_raised" and (getattr(e, "payload", None) or {}).get("idempotencyKey") == key:
                gate_id = e.gateId
            elif e.type == "gate_resolved" and gate_id and e.gateId == gate_id:
                resolution = e.resolution
        return gate_id, resolution

    async def _invoke(self, resolution: ToolResolution, tool: str, args: Dict[str, Any]) -> Any:
        conn = resolution.connection
        credential = self._secrets.reveal(conn.secret_ref) if conn.secret_ref else ""
        connector = get_connector(conn.provider, config=conn.config, transport=self._transport)
        return await connector.invoke(tool, args, credential)

    # -- API ---------------------------------------------------------------

    def list_tools(self, claims: RunClaims, connection_id: Optional[str] = None):
        snapshot = self._policy.load_environment(claims.environment_id, claims.environment_version)
        return self._policy.allowed_tools(snapshot, connection_id=connection_id)

    async def call_tool(self, claims: RunClaims, tool: str, args: Dict[str, Any],
                        step_id: Optional[str] = None,
                        idempotency_key: Optional[str] = None,
                        connection_id: Optional[str] = None) -> Any:
        base = self._base(claims, step_id)
        snapshot = self._policy.load_environment(claims.environment_id, claims.environment_version)
        try:
            resolution = self._policy.resolve(snapshot, tool, connection_id=connection_id)
        except PolicyDenied as denial:
            self._log.append({**base, "type": "tool_denied", "tool": tool, "args": args,
                              "reason": denial.reason}, workspace_id=claims.workspace_id)
            raise

        if resolution.effect_class == "read":
            try:
                result = await self._invoke(resolution, tool, args)
                self._log.append({**base, "type": "tool_call", "tool": tool, "args": args,
                                  "result": result}, workspace_id=claims.workspace_id)
            except ConnectorError as err:
                self._log.append({**base, "type": "tool_call", "tool": tool, "args": args,
                                  "error": str(err)}, workspace_id=claims.workspace_id)
                raise
            self._debit(base, claims)
            return result

        key = idempotency_key or "%s:%s:%s" % (claims.run_id, tool, uuid.uuid4().hex[:12])

        recorded = self._recorded_result(claims, key)
        if recorded is not None:
            return recorded.result

        gate_id, gate_resolution = self._gate_state(claims, key)
        if resolution.effect_class == "gated" and gate_id is None:
            self._log.append({**base, "type": "tool_intent", "tool": tool, "args": args,
                              "idempotencyKey": key}, workspace_id=claims.workspace_id)
            new_gate = "gate_" + uuid.uuid4().hex[:16]
            self._log.append({**base, "type": "gate_raised", "gateId": new_gate,
                              "kind": "action-approval",
                              "payload": {"tool": tool, "args": args, "idempotencyKey": key,
                                          "runId": claims.run_id,
                                          "environmentId": claims.environment_id}},
                             workspace_id=claims.workspace_id)
            raise Parked(new_gate, key)
        if resolution.effect_class == "gated":
            if gate_resolution is None:
                raise Parked(gate_id, key)
            if gate_resolution not in ("approve", "edit_then_approve"):
                self._log.append({**base, "type": "tool_denied", "tool": tool, "args": args,
                                  "reason": "gate %s resolved: %s" % (gate_id, gate_resolution)},
                                 workspace_id=claims.workspace_id)
                raise PolicyDenied("gate %s resolved: %s" % (gate_id, gate_resolution))
            self._log.append({**base, "type": "tool_approved", "tool": tool,
                              "idempotencyKey": key, "gateId": gate_id},
                             workspace_id=claims.workspace_id)
        else:
            self._log.append({**base, "type": "tool_intent", "tool": tool, "args": args,
                              "idempotencyKey": key}, workspace_id=claims.workspace_id)

        self._log.append({**base, "type": "tool_executed", "tool": tool, "args": args,
                          "idempotencyKey": key}, workspace_id=claims.workspace_id)
        try:
            result = await self._invoke(resolution, tool, args)
        except ConnectorError as err:
            # Errored results are logged but never dedupe: retries re-run.
            self._log.append({**base, "type": "tool_result", "tool": tool, "idempotencyKey": key,
                              "error": str(err)}, workspace_id=claims.workspace_id)
            raise
        self._log.append({**base, "type": "tool_result", "tool": tool, "idempotencyKey": key,
                          "result": result}, workspace_id=claims.workspace_id)
        self._debit(base, claims)
        return result

    def environment_binding(self, environment_id: str, version: Optional[int] = None,
                            base_url: str = "") -> Any:
        """Registry fulfillment of the frozen runtime seam (convoy_core.binding).

        Resolves environment@version (latest when unpinned) into an immutable
        EnvironmentBinding snapshot. Every connector endpoint is a gateway
        door — /mcp/{connection_id} on this server — per the fulfillment
        clause; browser identities carry no MCP endpoint (they surface
        through the fill sidecar instead)."""
        from convoy_core.binding import EnvironmentBinding, ToolGrant
        from sqlalchemy import select

        from ..db.tables import Environment as EnvironmentRow

        if version is None:
            with self._policy._sf() as session:
                version = session.execute(
                    select(EnvironmentRow.version).where(EnvironmentRow.id == environment_id)
                    .order_by(EnvironmentRow.version.desc()).limit(1)
                ).scalar_one_or_none()
            if version is None:
                raise PolicyDenied("unknown environment %s" % environment_id)
        snapshot = self._policy.load_environment(environment_id, version)
        env = snapshot.row
        grants = self._policy.allowed_tools(snapshot)
        endpoints = {
            r.connection.id: "%s/mcp/%s" % (base_url.rstrip("/"), r.connection.id)
            for r in grants
        }
        return EnvironmentBinding(
            id="%s@%d" % (environment_id, version),
            tenant_id=env.workspace_id,
            kind="production" if env.backing_type == "live" else "sandbox",
            tool_registry=[
                ToolGrant(tool=r.spec.name, connection_id=r.connection.id,
                          effect_class=r.effect_class, description=r.spec.description,
                          input_schema=r.spec.inputSchema or {})
                for r in grants
            ],
            connector_endpoints=endpoints,
            credential_scope="convoy-gateway:run-jwt:%s@%d" % (environment_id, version),
            data_namespace=env.data_namespace or "%s/%s" % (env.workspace_id, environment_id),
            sandbox_template=env.sandbox_template or "",
        )

    def browser_credential_lease(self, claims: RunClaims, domain: str) -> Dict[str, Any]:
        """Resolve a browser_identity credential for `domain`. Requires: the
        environment's browser policy allowlists the domain, AND a
        browser_identity connection in this environment covers it. The secret
        value is a JSON object ({"username", "password"}) handed to the fill
        sidecar; the lease itself is logged as a collapsed tool_call with the
        credential elided."""
        import json as _json

        snapshot = self._policy.load_environment(claims.environment_id, claims.environment_version)
        browser = (snapshot.row.browser_policy or {})
        allowed = browser.get("allowedDomains") or []
        base = self._base(claims, None)
        if domain not in allowed:
            reason = "domain %s is not in the environment browser allowlist" % domain
            self._log.append({**base, "type": "tool_denied", "tool": "browser.request_login",
                              "args": {"domain": domain}, "reason": reason},
                             workspace_id=claims.workspace_id)
            raise PolicyDenied(reason)
        for ec, conn in snapshot.connections:
            if conn.kind != "browser_identity" or conn.status != "active":
                continue
            if conn.manifest_hash != ec.manifest_hash:
                continue  # drifted identities contribute nothing
            domains = (conn.manifest or {}).get("domains") or []
            if domain in domains and conn.secret_ref:
                value = _json.loads(self._secrets.reveal(conn.secret_ref))
                self._log.append({**base, "type": "tool_call", "tool": "browser.request_login",
                                  "args": {"domain": domain, "connectionId": conn.id},
                                  "result": {"leased": True}}, workspace_id=claims.workspace_id)
                return {"domain": domain, "connectionId": conn.id,
                        "username": value.get("username", ""),
                        "password": value.get("password", "")}
        reason = "no browser identity covers %s in this environment" % domain
        self._log.append({**base, "type": "tool_denied", "tool": "browser.request_login",
                          "args": {"domain": domain}, "reason": reason},
                         workspace_id=claims.workspace_id)
        raise PolicyDenied(reason)

    def _debit(self, base: Dict[str, Any], claims: RunClaims) -> None:
        self._log.append({**base, "type": "budget_debit", "usd": FLAT_TOOL_DEBIT_USD,
                          "resource": "tool"}, workspace_id=claims.workspace_id)
