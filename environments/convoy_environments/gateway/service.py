"""GatewayService — the data-plane enforcement point.

Callers are TRUSTED RUNTIME WORKERS (run_turn / promoted-tool activities),
never sandboxed agent code — sandboxes hold no credentials and no run tokens
(DESIGN §12). Per-run tokens are minted to the runtime at run start and
identify the run for policy resolution and audit attribution.

Dispatch by the manifest's flags (the trust obligation — we flag, the
runtime believes):

inline (read-only idempotent)  invoke → one collapsed tool_call event
promoted / side-effecting      tool_intent → invoke → tool_executed →
                               tool_result, deduped on the idempotency key

Idempotency: the runtime keys every promoted call hash(run_id, step_id,
turn, call_index) and passes it via _meta.idempotencyKey. A retry whose key
has a recorded successful tool_result returns it WITHOUT re-invoking — this
is what makes runtime activity retries safe against double-firing real-world
actions. Errored results never dedupe.

Deliberately absent (runtime-owned by DESIGN v1): budget accounting (dollar
caps live in workflow BudgetState + LiteLLM virtual keys), human gates
(plan-step HumanGate via human_response signals), approvals. This layer's
whole approval story is honest flags + idempotent execution.
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

    def _recorded_result(self, claims: RunClaims, key: str):
        """Successful result recorded for this key, if any. Errored results
        never dedupe — a retry with the same key re-runs the call."""
        for e in self._log.for_mission(claims.mission_id):
            if e.type == "tool_result" and e.idempotencyKey == key and e.error is None:
                return e
        return None

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

        if resolution.execution == "inline":
            try:
                result = await self._invoke(resolution, tool, args)
                self._log.append({**base, "type": "tool_call", "tool": tool, "args": args,
                                  "result": result}, workspace_id=claims.workspace_id)
            except ConnectorError as err:
                self._log.append({**base, "type": "tool_call", "tool": tool, "args": args,
                                  "error": str(err)}, workspace_id=claims.workspace_id)
                raise
            return result

        # promoted (side-effecting or long): keyed two-phase envelope.
        key = idempotency_key or "%s:%s:%s" % (claims.run_id, tool, uuid.uuid4().hex[:12])
        recorded = self._recorded_result(claims, key)
        if recorded is not None:
            return recorded.result

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
        return result

    # -- binding registry --------------------------------------------------

    def environment_binding(self, environment_id: str, version: Optional[int] = None,
                            kind: str = "production", base_url: str = "") -> Any:
        """Registry fulfillment of the runtime seam (convoy_core DESIGN §5).

        One environment definition compiles into two immutable bindings
        (SERVICE-CONTRACTS §2): `production` (real connectors, real clock) and
        `sandbox` (mocks, virtual-capable clock). Sandbox compilation is
        validated: every side-effecting tool must resolve to a mock — until
        the mock registry exists, a sandbox request with side-effecting tools
        fails closed listing the unmocked tools rather than silently handing
        production connectors to a rehearsal.

        connector_endpoints carries the reserved `data_plane` key (what the
        runtime's turn executor and promoted-tool activities call today) plus
        one MCP door per connection (the richer surface it can move to)."""
        from convoy_core import ClockConfig, EnvironmentBinding, PermissionScope, ToolGrant
        from sqlalchemy import select

        from ..db.tables import Environment as EnvironmentRow

        if kind not in ("production", "sandbox"):
            raise PolicyDenied("unknown binding kind %s" % kind)
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

        if kind == "sandbox":
            unmocked = sorted(r.spec.name for r in grants if r.side_effecting)
            if unmocked:
                raise PolicyDenied(
                    "sandbox binding requires mocks for side-effecting tools; unmocked: %s"
                    % ", ".join(unmocked))
            clock = ClockConfig(mode="virtual", advance="manual")
        else:
            clock = ClockConfig(mode="real")

        base = base_url.rstrip("/")
        endpoints = {
            # Reserved key the runtime consumes today: plain-HTTP data plane,
            # environment-scoped so unauthenticated compose parity still
            # resolves policy correctly.
            "data_plane": "%s/data-plane/%s/%d" % (base, environment_id, version),
        }
        for r in grants:
            endpoints[r.connection.id] = "%s/mcp/%s" % (base, r.connection.id)
        return EnvironmentBinding(
            id="%s@%d/%s" % (environment_id, version, kind),
            tenant_id=env.workspace_id,
            kind=kind,
            tool_registry=[
                ToolGrant(tool_id=r.spec.name,
                          scope=PermissionScope(
                              resource="connector:%s" % r.connection.id,
                              actions=["write"] if r.side_effecting else ["read"]),
                          execution=r.execution, side_effecting=r.side_effecting)
                for r in grants
            ],
            connector_endpoints=endpoints,
            credential_scope="convoy-gateway:run-jwt:%s@%d" % (environment_id, version),
            data_namespace=env.data_namespace or "%s/%s" % (env.workspace_id, environment_id),
            sandbox_template=env.sandbox_template or "",
            clock=clock,
        )

    async def call_effect(self, claims: RunClaims, tool: str, args: Dict[str, Any],
                          idempotency_key: str, step_id: Optional[str] = None,
                          connection_id: Optional[str] = None):
        """Promoted-call entry for the data-plane facade: returns
        (result, replayed) matching the runtime's PromotedToolOutcome
        expectations — replayed=True means the key had already executed and
        the recorded result was returned without touching the world."""
        recorded = self._recorded_result(claims, idempotency_key)
        if recorded is not None:
            return recorded.result, True
        result = await self.call_tool(claims, tool, args, step_id=step_id,
                                      idempotency_key=idempotency_key,
                                      connection_id=connection_id)
        return result, False

    # -- browser credential lease (trusted fill service only) --------------

    def browser_credential_lease(self, claims: RunClaims, domain: str) -> Dict[str, Any]:
        """Resolve a browser_identity credential for `domain`. The caller is
        the TRUSTED fill service (runs beside the gateway, drives the sandbox
        browser over CDP from outside) — never a process inside the sandbox,
        per the sandboxes-never-hold-credentials rule. Requires: the
        environment's browser policy allowlists the domain AND a
        browser_identity connection covers it. The lease is logged with the
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
