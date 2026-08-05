"""MCP termination — the agent-facing door.

The devbox speaks MCP (JSON-RPC 2.0 over streamable HTTP, plain-JSON
responses) to POST /mcp with its per-run bearer token. tools/list is the
environment's allowlisted union; tools/call dispatches through
GatewayService. A parked gate is returned as a *successful* tool result with
structuredContent {status: "parked", gateId, idempotencyKey} — the runtime
lands state and dies, and retries with the same idempotencyKey after console
approval. Also exposes POST /internal/run-tokens for the runtime (shared-
secret protected) to mint per-run JWTs.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..connectors import ConnectorError
from ..mcp_protocol import MCP_PROTOCOL_VERSION as PROTOCOL_VERSION
from .policy import PolicyDenied
from .service import GatewayService
from .tokens import RunClaims, TokenError, mint_run_token, verify_run_token


def _rpc_result(id: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id, "result": result})


def _rpc_error(id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})


def _tool_text_result(payload: Any, is_error: bool = False, structured: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
        "isError": is_error,
    }
    if structured is not None:
        out["structuredContent"] = structured
    return out


class LeaseRequest(BaseModel):
    domain: str


class DataPlaneToolCall(BaseModel):
    """Wire shape of the runtime's inline tool dispatch (pydantic_ai_turn)."""

    args: Dict[str, Any] = {}
    run_id: str = ""
    step_id: Optional[str] = None


class DataPlaneEffectCall(BaseModel):
    """Wire shape of the runtime's promoted-tool activity."""

    idempotency_key: str
    run_id: str = ""
    args: Dict[str, Any] = {}
    step_id: Optional[str] = None


class MintRequest(BaseModel):
    runId: str
    missionId: str
    workspaceId: str
    environmentId: str
    environmentVersion: int
    ttlSeconds: int = 3600


def build_app(service: GatewayService, gateway_secret: Optional[str] = None,
              internal_token: Optional[str] = None,
              public_url: Optional[str] = None,
              allow_anonymous_data_plane: Optional[bool] = None) -> FastAPI:
    app = FastAPI(title="convoy-gateway")
    internal = internal_token or os.environ.get("CONVOY_INTERNAL_TOKEN", "")
    base_url = (public_url or os.environ.get("CONVOY_GATEWAY_PUBLIC_URL",
                                             "http://127.0.0.1:8780/gateway")).rstrip("/")
    # Compose/stub parity only: the runtime's current data-plane clients send
    # no Authorization header (raised with Aneesh — should carry the run JWT).
    # Default is auth required; never enable in a deployment fronting real
    # customer credentials.
    if allow_anonymous_data_plane is None:
        allow_anonymous_data_plane = os.environ.get("CONVOY_DATA_PLANE_ALLOW_ANON", "") == "1"

    def internal_dep(x_convoy_internal: str = Header(default="")) -> None:
        if not internal or x_convoy_internal != internal:
            raise HTTPException(401, "invalid internal token")

    def claims_dep(authorization: str = Header(default="")) -> RunClaims:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer token")
        try:
            return verify_run_token(authorization[len("Bearer "):], secret=gateway_secret)
        except TokenError as err:
            raise HTTPException(401, str(err))

    @app.post("/mcp")
    async def mcp(request: Request, claims: RunClaims = Depends(claims_dep)):
        """Aggregate door: every tool the environment allows."""
        return await _mcp(request, claims, None)

    @app.post("/mcp/{connection_id}")
    async def mcp_scoped(connection_id: str, request: Request,
                         claims: RunClaims = Depends(claims_dep)):
        """Per-connection door — what EnvironmentBinding.connector_endpoints
        points at. Scopes discovery AND dispatch to one connection, so a tool
        name granted through connection A cannot be reached through B's door."""
        return await _mcp(request, claims, connection_id)

    async def _mcp(request: Request, claims: RunClaims, connection_id: Optional[str]):
        body = await request.json()
        rpc_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params") or {}

        if method == "initialize":
            return _rpc_result(rpc_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "convoy-gateway", "version": "0.1.0"},
            })
        if method.startswith("notifications/"):
            return JSONResponse(None, status_code=202)
        if method == "tools/list":
            tools = [
                {"name": r.spec.name, "description": r.spec.description,
                 "inputSchema": r.spec.inputSchema or {"type": "object"},
                 "annotations": {"readOnlyHint": not r.side_effecting}}
                for r in service.list_tools(claims, connection_id=connection_id)
            ]
            return _rpc_result(rpc_id, {"tools": tools})
        if method == "tools/call":
            tool = params.get("name", "")
            args = params.get("arguments") or {}
            meta = params.get("_meta") or {}
            try:
                result = await service.call_tool(
                    claims, tool, args,
                    step_id=meta.get("stepId"),
                    idempotency_key=meta.get("idempotencyKey"),
                    connection_id=connection_id,
                )
                return _rpc_result(rpc_id, _tool_text_result(result))
            except PolicyDenied as denial:
                return _rpc_result(rpc_id, _tool_text_result({"denied": denial.reason}, is_error=True))
            except ConnectorError as err:
                return _rpc_result(rpc_id, _tool_text_result({"error": str(err)}, is_error=True))
        return _rpc_error(rpc_id, -32601, "method not found: %s" % method)

    @app.post("/browser/credential-lease")
    async def credential_lease(req: LeaseRequest, claims: RunClaims = Depends(claims_dep)):
        """Fill-sidecar only. Returns the browser_identity credential for an
        allowlisted domain — values go to the sidecar's CDP fill, never into
        model context. The lease is recorded in the event log."""
        try:
            lease = service.browser_credential_lease(claims, req.domain)
        except PolicyDenied as denial:
            raise HTTPException(403, denial.reason)
        return lease

    # -- plain-HTTP data plane (what the runtime calls today) --------------

    def _data_plane_claims(environment_id: str, version: int, run_id: str,
                           authorization: str) -> RunClaims:
        """Claims for a data-plane call. Bearer run-JWT is authoritative (and
        must match the path's environment); anonymous is allowed only under
        the compose-parity flag, deriving tenant from the environment row."""
        if authorization.startswith("Bearer "):
            try:
                claims = verify_run_token(authorization[len("Bearer "):], secret=gateway_secret)
            except TokenError as err:
                raise HTTPException(401, str(err))
            if claims.environment_id != environment_id or claims.environment_version != version:
                raise HTTPException(403, "run token is scoped to a different environment")
            return claims
        if not allow_anonymous_data_plane:
            raise HTTPException(401, "data-plane calls require a run token")
        snapshot = service._policy.load_environment(environment_id, version)
        return RunClaims(run_id=run_id or "anonymous", mission_id=run_id or "anonymous",
                         workspace_id=snapshot.row.workspace_id,
                         environment_id=environment_id, environment_version=version)

    @app.post("/data-plane/{environment_id}/{version}/tools/{tool_id}")
    async def data_plane_tool(environment_id: str, version: int, tool_id: str,
                              req: DataPlaneToolCall,
                              authorization: str = Header(default="")):
        """Inline read dispatch — mirrors the stub-env `POST /tools/{tool_id}`
        contract: 200 {"result": ...} on success. Promoted/side-effecting
        tools are refused here; they must come through /effects with a key."""
        claims = _data_plane_claims(environment_id, version, req.run_id, authorization)
        snapshot = service._policy.load_environment(environment_id, version)
        try:
            resolution = service._policy.resolve(snapshot, tool_id)
        except PolicyDenied as denial:
            raise HTTPException(403, denial.reason)
        if resolution.execution != "inline":
            raise HTTPException(409, "tool %s is promoted; call /effects with an idempotency key" % tool_id)
        try:
            result = await service.call_tool(claims, tool_id, req.args, step_id=req.step_id)
        except PolicyDenied as denial:
            raise HTTPException(403, denial.reason)
        except ConnectorError as err:
            raise HTTPException(502, str(err))
        return {"result": result}

    @app.post("/data-plane/{environment_id}/{version}/effects/{tool_id}")
    async def data_plane_effect(environment_id: str, version: int, tool_id: str,
                                req: DataPlaneEffectCall,
                                authorization: str = Header(default="")):
        """Promoted/side-effecting dispatch — mirrors the stub-env
        `POST /effects/{tool_id}` contract: 200 {"result": ..., "replayed":
        bool}; a repeated idempotency key never fires the effect twice."""
        claims = _data_plane_claims(environment_id, version, req.run_id, authorization)
        try:
            result, replayed = await service.call_effect(
                claims, tool_id, req.args, idempotency_key=req.idempotency_key,
                step_id=req.step_id)
        except PolicyDenied as denial:
            raise HTTPException(403, denial.reason)
        except ConnectorError as err:
            raise HTTPException(502, str(err))
        return {"result": result, "replayed": replayed}

    @app.get("/environments/{environment_id}")
    async def registry_alias(environment_id: str, version: Optional[int] = None,
                             kind: str = "production", tenant_id: str = "",
                             _: None = Depends(internal_dep)):
        """Registry path the runtime's resolver expects (stub-env parity:
        GET /environments/{id}). Same fulfillment as the /internal binding
        route; tenant_id, when provided, must match the environment's."""
        try:
            binding = service.environment_binding(environment_id, version=version,
                                                  kind=kind, base_url=base_url)
        except PolicyDenied as denial:
            raise HTTPException(409 if "unmocked" in denial.reason else 404, denial.reason)
        if tenant_id and binding.tenant_id != tenant_id:
            raise HTTPException(404, "environment %s not found for tenant %s" % (environment_id, tenant_id))
        return binding.model_dump(mode="json")

    @app.get("/internal/environments/{environment_id}/binding")
    async def binding(environment_id: str, version: Optional[int] = None,
                      kind: str = "production", _: None = Depends(internal_dep)):
        """Registry endpoint for the runtime: one environment definition
        compiles into two immutable bindings — ?kind=production (default) or
        ?kind=sandbox (mock-validated, virtual-capable clock). Unpinned
        version resolves latest; the runtime pins the returned id as
        RunState.binding_ref."""
        try:
            result = service.environment_binding(environment_id, version=version,
                                                 kind=kind, base_url=base_url)
        except PolicyDenied as denial:
            status = 409 if "unmocked" in denial.reason else 404
            raise HTTPException(status, denial.reason)
        return result.model_dump(mode="json")

    @app.post("/internal/run-tokens")
    async def mint(req: MintRequest, _: None = Depends(internal_dep)):
        token = mint_run_token(
            RunClaims(run_id=req.runId, mission_id=req.missionId, workspace_id=req.workspaceId,
                      environment_id=req.environmentId, environment_version=req.environmentVersion),
            ttl_s=req.ttlSeconds, secret=gateway_secret,
        )
        return {"token": token}

    return app
