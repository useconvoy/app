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

import hashlib
import json
import uuid
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ..connectors import ConnectorError, get_connector
from ..db import SqlEventLog
from ..secrets import SecretsService
from .policy import PolicyDenied, PolicyEngine, ToolResolution
from .standins import simulate
from .tokens import RunClaims


class GatewayService:
    def __init__(
        self,
        session_factory,
        secrets: SecretsService,
        event_log: Optional[SqlEventLog] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sandbox_url: str = "",
        sandbox_admin_token: str = "",
    ) -> None:
        self._policy = PolicyEngine(session_factory)
        self._secrets = secrets
        self._log = event_log or SqlEventLog(session_factory)
        self._transport = transport  # test seam for connectors
        self._sandbox_url = sandbox_url.rstrip("/")
        self._sandbox_admin_token = sandbox_admin_token
        self._sandbox_ready: set[str] = set()
        self._sandbox_google_credential: Optional[str] = None

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
        try:
            return await connector.invoke(tool, args, credential)
        except ConnectorError as err:
            if "credential rejected" in str(err):
                self._flag_needs_reauth(conn.id)  # console surfaces this as "Needs re-auth"
            raise

    @staticmethod
    def _sandbox_id(claims: RunClaims) -> str:
        """Stable, opaque sandbox identity for one run."""
        material = "%s:%s:%s" % (
            claims.workspace_id,
            claims.environment_id,
            claims.run_id,
        )
        return "run-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    async def _ensure_connector_sandbox(self, sandbox_id: str) -> None:
        if sandbox_id in self._sandbox_ready:
            return
        headers = {"content-type": "application/json"}
        if self._sandbox_admin_token:
            headers["authorization"] = "Bearer " + self._sandbox_admin_token
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
                response = await client.post(
                    self._sandbox_url + "/v1/sandboxes",
                    headers=headers,
                    json={
                        "sandboxId": sandbox_id,
                        "fixtureName": "connector-development",
                    },
                )
        except httpx.HTTPError as err:
            raise ConnectorError(
                "connector sandbox unavailable (%s)" % err,
                retryable=True,
            ) from err
        if response.status_code not in (201, 409):
            raise ConnectorError(
                "connector sandbox provisioning failed (%d: %s)"
                % (response.status_code, response.text[:300]),
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        self._sandbox_ready.add(sandbox_id)

    def _google_sandbox_credential(self) -> str:
        if self._sandbox_google_credential is None:
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("utf-8")
            self._sandbox_google_credential = json.dumps(
                {
                    "client_email": "convoy-agent@sandbox.invalid",
                    "private_key": pem,
                }
            )
        return self._sandbox_google_credential

    async def _invoke_connector_sandbox(
        self,
        claims: RunClaims,
        resolution: ToolResolution,
        tool: str,
        args: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> Any:
        """Call a provider-shaped stub through the production connector."""
        sandbox_id = self._sandbox_id(claims)
        await self._ensure_connector_sandbox(sandbox_id)
        base = "%s/s/%s" % (self._sandbox_url, sandbox_id)
        provider = resolution.connection.provider
        request_headers = {"x-convoy-idempotency-key": idempotency_key} if idempotency_key else {}
        if provider == "slack":
            config = {
                "apiBaseUrl": base + "/slack/api",
                "_requestHeaders": request_headers,
            }
            credential = "xoxb-convoy-sandbox"
        elif provider == "google":
            config = {
                "tokenUrl": base + "/google/oauth2/token",
                "driveBaseUrl": base + "/google/drive/v3",
                "sheetsBaseUrl": base + "/google/sheets/v4",
                "_requestHeaders": request_headers,
            }
            credential = self._google_sandbox_credential()
        elif provider == "github":
            config = {
                "apiBaseUrl": base + "/github",
                "_requestHeaders": request_headers,
            }
            credential = "github-convoy-sandbox"
        else:
            raise ConnectorError("no connector sandbox for provider %s" % provider)
        connector = get_connector(provider, config=config, transport=self._transport)
        return await connector.invoke(tool, args, credential)

    def _flag_needs_reauth(self, connection_id: str) -> None:
        """Best-effort status flip on a rejected credential; never raises —
        the caller's ConnectorError is the signal that matters."""
        try:
            with self._policy._sf() as session:
                from ..db.tables import Connection as ConnectionRow

                row = session.get(ConnectionRow, connection_id)
                if row is not None and row.status == "active":
                    row.status = "needs_reauth"
                    session.commit()
        except Exception:  # noqa: BLE001 — flagging must not mask the tool error
            pass

    # -- API ---------------------------------------------------------------

    def list_tools(self, claims: RunClaims, connection_id: Optional[str] = None):
        snapshot = self._policy.load_environment(claims.environment_id, claims.environment_version)
        return self._policy.allowed_tools(snapshot, connection_id=connection_id)

    async def call_tool(
        self,
        claims: RunClaims,
        tool: str,
        args: Dict[str, Any],
        step_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        connection_id: Optional[str] = None,
    ) -> Any:
        base = self._base(claims, step_id)
        snapshot = self._policy.load_environment(claims.environment_id, claims.environment_version)
        try:
            resolution = self._policy.resolve(snapshot, tool, connection_id=connection_id)
        except PolicyDenied as denial:
            self._log.append(
                {
                    **base,
                    "type": "tool_denied",
                    "tool": tool,
                    "args": args,
                    "reason": denial.reason,
                },
                workspace_id=claims.workspace_id,
            )
            raise

        if resolution.execution == "inline":
            try:
                result = await self._invoke(resolution, tool, args)
                self._log.append(
                    {**base, "type": "tool_call", "tool": tool, "args": args, "result": result},
                    workspace_id=claims.workspace_id,
                )
            except ConnectorError as err:
                self._log.append(
                    {**base, "type": "tool_call", "tool": tool, "args": args, "error": str(err)},
                    workspace_id=claims.workspace_id,
                )
                raise
            return result

        # promoted (side-effecting or long): keyed two-phase envelope.
        key = idempotency_key or "%s:%s:%s" % (claims.run_id, tool, uuid.uuid4().hex[:12])
        recorded = self._recorded_result(claims, key)
        if recorded is not None:
            return recorded.result

        self._log.append(
            {**base, "type": "tool_intent", "tool": tool, "args": args, "idempotencyKey": key},
            workspace_id=claims.workspace_id,
        )
        self._log.append(
            {**base, "type": "tool_executed", "tool": tool, "args": args, "idempotencyKey": key},
            workspace_id=claims.workspace_id,
        )
        try:
            result = await self._invoke(resolution, tool, args)
        except ConnectorError as err:
            # Errored results are logged but never dedupe: retries re-run.
            self._log.append(
                {
                    **base,
                    "type": "tool_result",
                    "tool": tool,
                    "idempotencyKey": key,
                    "error": str(err),
                },
                workspace_id=claims.workspace_id,
            )
            raise
        self._log.append(
            {**base, "type": "tool_result", "tool": tool, "idempotencyKey": key, "result": result},
            workspace_id=claims.workspace_id,
        )
        return result

    # -- binding registry --------------------------------------------------

    def environment_binding(
        self,
        environment_id: str,
        version: Optional[int] = None,
        kind: str = "production",
        base_url: str = "",
    ) -> Any:
        """Registry fulfillment of the runtime seam (convoy_core DESIGN §5).

        One environment definition compiles into two immutable bindings
        (SERVICE-CONTRACTS §2): `production` (real connectors, real clock) and
        `sandbox` (deterministic stand-ins, virtual-capable clock). A sandbox
        binding never calls a provider: writes become explicit, durable
        simulated-effect journal entries.

        When the environment sets a `sandbox_template`, both binding kinds
        additionally grant `sandbox_exec` (promoted, side-effecting, scoped to
        `sandbox:<template>`): the runtime routes it by its `sandbox_` prefix
        to its own SandboxProvider, so it gets no connector_endpoints entry
        and is exempt from the sandbox mock requirement.

        connector_endpoints carries the reserved `data_plane` key (what the
        runtime's turn executor and promoted-tool activities call today) plus
        one MCP door per connection (the richer surface it can move to)."""
        from sqlalchemy import select

        from convoy_core import ClockConfig, EnvironmentBinding, PermissionScope, ToolGrant

        from ..db.tables import Environment as EnvironmentRow

        if kind not in ("production", "sandbox"):
            raise PolicyDenied("unknown binding kind %s" % kind)
        if version is None:
            with self._policy._sf() as session:
                version = session.execute(
                    select(EnvironmentRow.version)
                    .where(EnvironmentRow.id == environment_id)
                    .order_by(EnvironmentRow.version.desc())
                    .limit(1)
                ).scalar_one_or_none()
            if version is None:
                raise PolicyDenied("unknown environment %s" % environment_id)
        snapshot = self._policy.load_environment(environment_id, version)
        env = snapshot.row
        grants = self._policy.allowed_tools(snapshot)

        if kind == "sandbox":
            clock = ClockConfig(mode="virtual", advance="manual")
        else:
            clock = ClockConfig(mode="real")

        base = base_url.rstrip("/")
        endpoints = {
            # Reserved key the runtime consumes today: plain-HTTP data plane,
            # environment-scoped so unauthenticated compose parity still
            # resolves policy correctly.
            "data_plane": "%s/%s/%s/%d"
            % (
                base,
                "simulated-data-plane" if kind == "sandbox" else "data-plane",
                environment_id,
                version,
            ),
        }
        for r in grants:
            endpoints[r.connection.id] = "%s/mcp/%s" % (base, r.connection.id)
        tool_registry = [
            ToolGrant(
                tool_id=r.spec.name,
                scope=PermissionScope(
                    resource="connector:%s" % r.connection.id,
                    actions=["write"] if r.side_effecting else ["read"],
                ),
                execution=r.execution,
                side_effecting=r.side_effecting,
            )
            for r in grants
        ]
        if env.sandbox_template:
            # Not a connector: the runtime routes sandbox_* tool ids by prefix
            # to its own SandboxProvider, so no connector_endpoints entry.
            # This grant derives from the environment's sandbox_template,
            # never from a connection allowlist.
            tool_registry.append(
                ToolGrant(
                    tool_id="sandbox_exec",
                    scope=PermissionScope(
                        resource="sandbox:" + env.sandbox_template, actions=["execute"]
                    ),
                    execution="promoted",
                    side_effecting=True,
                )
            )
        browser_policy = env.browser_policy or {}
        if env.sandbox_template and browser_policy.get("allowedDomains"):
            tool_registry.append(
                ToolGrant(
                    tool_id="sandbox_browser",
                    scope=PermissionScope(
                        resource="browser:" + ",".join(browser_policy["allowedDomains"]),
                        actions=["navigate", "interact"],
                    ),
                    execution="promoted",
                    side_effecting=True,
                )
            )
        return EnvironmentBinding(
            id="%s@%d/%s" % (environment_id, version, kind),
            tenant_id=env.workspace_id,
            kind=kind,
            tool_registry=tool_registry,
            connector_endpoints=endpoints,
            credential_scope="convoy-gateway:run-jwt:%s@%d" % (environment_id, version),
            data_namespace=env.data_namespace or "%s/%s" % (env.workspace_id, environment_id),
            sandbox_template=env.sandbox_template or "",
            clock=clock,
            browser=browser_policy,
        )

    async def call_effect(
        self,
        claims: RunClaims,
        tool: str,
        args: Dict[str, Any],
        idempotency_key: str,
        step_id: Optional[str] = None,
        connection_id: Optional[str] = None,
    ):
        """Promoted-call entry for the data-plane facade: returns
        (result, replayed) matching the runtime's PromotedToolOutcome
        expectations — replayed=True means the key had already executed and
        the recorded result was returned without touching the world."""
        recorded = self._recorded_result(claims, idempotency_key)
        if recorded is not None:
            return recorded.result, True
        result = await self.call_tool(
            claims,
            tool,
            args,
            step_id=step_id,
            idempotency_key=idempotency_key,
            connection_id=connection_id,
        )
        return result, False

    async def call_simulated_tool(
        self,
        claims: RunClaims,
        tool: str,
        args: Dict[str, Any],
        *,
        step_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        """Run a rehearsal without revealing the production credential.

        Managed Slack, Google, and GitHub calls use the exact production
        connector against provider-shaped stubs when the connector sandbox is
        configured. Other providers retain deterministic schema-shaped
        stand-ins until a provider sandbox is implemented for them.
        """

        base = self._base(claims, step_id)
        snapshot = self._policy.load_environment(claims.environment_id, claims.environment_version)
        try:
            resolution = self._policy.resolve(snapshot, tool)
        except PolicyDenied as denial:
            self._log.append(
                {
                    **base,
                    "type": "tool_denied",
                    "tool": tool,
                    "args": args,
                    "reason": denial.reason,
                },
                workspace_id=claims.workspace_id,
            )
            raise

        provider_sandbox = bool(
            self._sandbox_url and resolution.connection.provider in ("slack", "google", "github")
        )

        if resolution.execution == "inline":
            result = (
                await self._invoke_connector_sandbox(claims, resolution, tool, args)
                if provider_sandbox
                else simulate(resolution, tool, args)
            )
            self._log.append(
                {**base, "type": "tool_call", "tool": tool, "args": args, "result": result},
                workspace_id=claims.workspace_id,
            )
            return result

        key = idempotency_key or "%s:%s:%s" % (claims.run_id, tool, uuid.uuid4().hex[:12])
        recorded = self._recorded_result(claims, key)
        if recorded is not None:
            return recorded.result
        self._log.append(
            {**base, "type": "tool_intent", "tool": tool, "args": args, "idempotencyKey": key},
            workspace_id=claims.workspace_id,
        )
        self._log.append(
            {**base, "type": "tool_executed", "tool": tool, "args": args, "idempotencyKey": key},
            workspace_id=claims.workspace_id,
        )
        result = (
            await self._invoke_connector_sandbox(
                claims, resolution, tool, args, idempotency_key=key
            )
            if provider_sandbox
            else simulate(resolution, tool, args, idempotency_key=key)
        )
        self._log.append(
            {
                **base,
                "type": "simulated_effect",
                "tool": tool,
                "provider": resolution.connection.provider,
                "connectionId": resolution.connection.id,
                "args": args,
                "idempotencyKey": key,
                "result": result,
            },
            workspace_id=claims.workspace_id,
        )
        self._log.append(
            {**base, "type": "tool_result", "tool": tool, "idempotencyKey": key, "result": result},
            workspace_id=claims.workspace_id,
        )
        return result

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
        browser = snapshot.row.browser_policy or {}
        allowed = browser.get("allowedDomains") or []
        base = self._base(claims, None)
        if domain not in allowed:
            reason = "domain %s is not in the environment browser allowlist" % domain
            self._log.append(
                {
                    **base,
                    "type": "tool_denied",
                    "tool": "browser.request_login",
                    "args": {"domain": domain},
                    "reason": reason,
                },
                workspace_id=claims.workspace_id,
            )
            raise PolicyDenied(reason)
        for ec, conn in snapshot.connections:
            if conn.kind != "browser_identity" or conn.status != "active":
                continue
            if conn.manifest_hash != ec.manifest_hash:
                continue  # drifted identities contribute nothing
            domains = (conn.manifest or {}).get("domains") or []
            if domain in domains and conn.secret_ref:
                value = _json.loads(self._secrets.reveal(conn.secret_ref))
                self._log.append(
                    {
                        **base,
                        "type": "tool_call",
                        "tool": "browser.request_login",
                        "args": {"domain": domain, "connectionId": conn.id},
                        "result": {"leased": True},
                    },
                    workspace_id=claims.workspace_id,
                )
                return {
                    "domain": domain,
                    "connectionId": conn.id,
                    "username": value.get("username", ""),
                    "password": value.get("password", ""),
                }
        reason = "no browser identity covers %s in this environment" % domain
        self._log.append(
            {
                **base,
                "type": "tool_denied",
                "tool": "browser.request_login",
                "args": {"domain": domain},
                "reason": reason,
            },
            workspace_id=claims.workspace_id,
        )
        raise PolicyDenied(reason)
