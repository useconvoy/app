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
from .policy import PolicyDenied
from .service import GatewayService, Parked
from .tokens import RunClaims, TokenError, mint_run_token, verify_run_token

PROTOCOL_VERSION = "2025-06-18"


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


class MintRequest(BaseModel):
    runId: str
    missionId: str
    workspaceId: str
    environmentId: str
    environmentVersion: int
    ttlSeconds: int = 3600


def build_app(service: GatewayService, gateway_secret: Optional[str] = None,
              internal_token: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="convoy-gateway")
    internal = internal_token or os.environ.get("CONVOY_INTERNAL_TOKEN", "")

    def claims_dep(authorization: str = Header(default="")) -> RunClaims:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer token")
        try:
            return verify_run_token(authorization[len("Bearer "):], secret=gateway_secret)
        except TokenError as err:
            raise HTTPException(401, str(err))

    @app.post("/mcp")
    async def mcp(request: Request, claims: RunClaims = Depends(claims_dep)):
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
                 "annotations": {"readOnlyHint": r.effect_class == "read"}}
                for r in service.list_tools(claims)
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
                )
                return _rpc_result(rpc_id, _tool_text_result(result))
            except Parked as parked:
                return _rpc_result(rpc_id, _tool_text_result(
                    {"status": "parked", "gateId": parked.gate_id},
                    structured={"status": "parked", "gateId": parked.gate_id,
                                "idempotencyKey": parked.idempotency_key},
                ))
            except PolicyDenied as denial:
                return _rpc_result(rpc_id, _tool_text_result({"denied": denial.reason}, is_error=True))
            except ConnectorError as err:
                return _rpc_result(rpc_id, _tool_text_result({"error": str(err)}, is_error=True))
        return _rpc_error(rpc_id, -32601, "method not found: %s" % method)

    @app.post("/internal/run-tokens")
    async def mint(req: MintRequest, x_convoy_internal: str = Header(default="")):
        if not internal or x_convoy_internal != internal:
            raise HTTPException(401, "invalid internal token")
        token = mint_run_token(
            RunClaims(run_id=req.runId, mission_id=req.missionId, workspace_id=req.workspaceId,
                      environment_id=req.environmentId, environment_version=req.environmentVersion),
            ttl_s=req.ttlSeconds, secret=gateway_secret,
        )
        return {"token": token}

    return app
