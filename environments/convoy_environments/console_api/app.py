"""Console API — what apps/web talks to.

White-glove v0 auth: the caller's user id arrives in X-Convoy-User (the
console fronts this with real session auth later; founder CLI sets it
directly). Secrets are write-only: values go in on create and never come
back out on any read path. Gates render on the console first (settled
decision #3): list open gates, resolve with attribution + typed reason —
resolutions land in the event log, which is what the runtime's scheduler
watches to resume parked missions.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from ..connectors import ConnectorError, get_connector
from ..db import SqlEventLog
from ..db.tables import (
    AuditLog,
    Connection as ConnectionRow,
    Environment as EnvironmentRow,
    EnvironmentConnection as EnvConnRow,
    EnvironmentGrant,
    Membership,
    User,
    Workspace,
)
from ..schema import ConnectionManifest, policy_hash
from ..secrets import SecretsService
from .rbac import require_env_role, require_workspace_role


def _id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:20]


class CreateWorkspace(BaseModel):
    name: str
    creatorEmail: str


class CreateConnection(BaseModel):
    kind: Literal["mcp_managed", "mcp_custom", "aws_role", "browser_identity"]
    provider: str
    displayName: str
    config: Dict[str, Any] = Field(default_factory=dict)
    secretValue: Optional[str] = None  # write-only; stored via SecretsService, never returned
    secretBackend: str = "builtin"
    manifest: Optional[Dict[str, Any]] = None  # browser_identity / aws_role declare theirs


class EnvConnectionInput(BaseModel):
    connectionId: str
    toolAllowlist: List[str] = Field(default_factory=list)
    gateOverrides: Dict[str, Literal["gated"]] = Field(default_factory=dict)


class CreateEnvironment(BaseModel):
    name: str
    description: str = ""
    backingType: Literal["live", "hermetic"] = "live"
    connections: List[EnvConnectionInput] = Field(default_factory=list)
    browserPolicy: Optional[Dict[str, Any]] = None
    budgetDefaults: Dict[str, Any] = Field(default_factory=dict)
    sandboxTemplate: str = ""  # E2B template id for the devbox image
    dataNamespace: str = ""  # empty → derived ws_<id>/env_<id>


class CreateGrant(BaseModel):
    userId: str
    role: Literal["viewer", "operator", "env_admin"]


class ResolveGate(BaseModel):
    resolution: Literal["approve", "reject", "edit_then_approve"]
    reason: Optional[str] = None
    patch: Any = None


def build_console_app(session_factory, secrets: SecretsService,
                      event_log: Optional[SqlEventLog] = None) -> FastAPI:
    app = FastAPI(title="convoy-console-api")
    log = event_log or SqlEventLog(session_factory)

    def user_dep(x_convoy_user: str = Header(default="")) -> str:
        if not x_convoy_user:
            raise HTTPException(401, "missing X-Convoy-User")
        return x_convoy_user

    def _audit(session, workspace_id: str, actor: str, action: str, subject_type: str, subject_id: str,
               diff: Optional[dict] = None) -> None:
        session.add(AuditLog(workspace_id=workspace_id, actor_user_id=actor, action=action,
                             subject_type=subject_type, subject_id=subject_id, diff=diff))

    # -- workspaces --------------------------------------------------------

    @app.post("/workspaces")
    async def create_workspace(req: CreateWorkspace):
        with session_factory() as s:
            ws = Workspace(id=_id("ws"), name=req.name)
            user = s.query(User).filter_by(email=req.creatorEmail).one_or_none()
            if user is None:
                user = User(id=_id("usr"), email=req.creatorEmail)
                s.add(user)
            s.add(ws)
            s.add(Membership(workspace_id=ws.id, user_id=user.id, role="admin"))
            _audit(s, ws.id, user.id, "workspace.create", "workspace", ws.id)
            s.commit()
            return {"workspaceId": ws.id, "userId": user.id}

    # -- connections -------------------------------------------------------

    @app.post("/workspaces/{workspace_id}/connections")
    async def create_connection(workspace_id: str, req: CreateConnection, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_workspace_role(s, workspace_id, user, "admin")
        secret_ref = None
        if req.secretValue is not None:
            secret_ref = secrets.create(workspace_id, "%s:%s" % (req.provider, req.displayName),
                                        req.secretValue, backend=req.secretBackend, created_by=user)
        if req.manifest is not None:
            manifest = ConnectionManifest.model_validate(req.manifest)
        else:
            try:
                connector = get_connector(req.provider, config=req.config)
                manifest = await connector.manifest(
                    secrets.reveal(secret_ref) if secret_ref else None
                )
            except ConnectorError as err:
                raise HTTPException(400, "could not build manifest: %s" % err)
        with session_factory() as s:
            row = ConnectionRow(id=_id("conn"), workspace_id=workspace_id, kind=req.kind,
                                provider=req.provider, display_name=req.displayName,
                                config=req.config, secret_ref=secret_ref,
                                manifest=manifest.model_dump(exclude_none=True),
                                manifest_hash=manifest.hash, status="active", created_by=user)
            s.add(row)
            _audit(s, workspace_id, user, "connection.create", "connection", row.id,
                   {"provider": req.provider, "manifestHash": manifest.hash})
            s.commit()
            return {"connectionId": row.id, "manifestHash": manifest.hash,
                    "tools": [t.name for t in manifest.tools], "domains": manifest.domains}

    @app.get("/workspaces/{workspace_id}/connections")
    async def list_connections(workspace_id: str, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_workspace_role(s, workspace_id, user, "member")
            rows = s.query(ConnectionRow).filter_by(workspace_id=workspace_id).all()
            return [{"connectionId": r.id, "kind": r.kind, "provider": r.provider,
                     "displayName": r.display_name, "status": r.status,
                     "manifestHash": r.manifest_hash} for r in rows]

    # -- environments ------------------------------------------------------

    def _build_env_rows(s, workspace_id: str, env_id: str, version: int,
                        req: CreateEnvironment, user: str):
        conn_rows = []
        for ec in req.connections:
            conn = s.get(ConnectionRow, ec.connectionId)
            if conn is None or conn.workspace_id != workspace_id:
                raise HTTPException(400, "unknown connection %s" % ec.connectionId)
            manifest = ConnectionManifest.model_validate(conn.manifest)
            unknown = [t for t in ec.toolAllowlist if manifest.tool(t) is None]
            if unknown:
                raise HTTPException(400, "tools not in %s manifest: %s" % (conn.id, unknown))
            conn_rows.append(
                EnvConnRow(environment_id=env_id, environment_version=version,
                           connection_id=conn.id, manifest_hash=conn.manifest_hash,
                           tool_allowlist=ec.toolAllowlist, gate_overrides=ec.gateOverrides)
            )
        namespace = req.dataNamespace or "%s/%s" % (workspace_id, env_id)
        phash = policy_hash(
            req.backingType,
            [{"connectionId": r.connection_id, "manifestHash": r.manifest_hash,
              "toolAllowlist": r.tool_allowlist, "gateOverrides": r.gate_overrides}
             for r in conn_rows],
            req.browserPolicy,
            sandbox_template=req.sandboxTemplate,
            data_namespace=namespace,
        )
        env = EnvironmentRow(id=env_id, version=version, workspace_id=workspace_id,
                             name=req.name, backing_type=req.backingType,
                             browser_policy=req.browserPolicy, budget_defaults=req.budgetDefaults,
                             sandbox_template=req.sandboxTemplate, data_namespace=namespace,
                             policy_hash=phash, description=req.description, created_by=user)
        return env, conn_rows

    @app.post("/workspaces/{workspace_id}/environments")
    async def create_environment(workspace_id: str, req: CreateEnvironment, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_workspace_role(s, workspace_id, user, "builder")
            env_id = _id("env")
            env, conn_rows = _build_env_rows(s, workspace_id, env_id, 1, req, user)
            s.add(env)
            s.add_all(conn_rows)
            s.add(EnvironmentGrant(environment_id=env_id, user_id=user, role="env_admin"))
            _audit(s, workspace_id, user, "environment.create", "environment",
                   "%s@1" % env_id, {"policyHash": env.policy_hash})
            s.commit()
            return {"environmentId": env_id, "version": 1, "policyHash": env.policy_hash}

    @app.post("/workspaces/{workspace_id}/environments/{environment_id}/versions")
    async def create_environment_version(workspace_id: str, environment_id: str,
                                         req: CreateEnvironment, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_env_role(s, workspace_id, environment_id, user, "env_admin")
            latest = (s.query(EnvironmentRow).filter_by(id=environment_id, workspace_id=workspace_id)
                      .order_by(EnvironmentRow.version.desc()).first())
            if latest is None:
                raise HTTPException(404, "unknown environment")
            version = latest.version + 1
            env, conn_rows = _build_env_rows(s, workspace_id, environment_id, version, req, user)
            s.add(env)
            s.add_all(conn_rows)
            _audit(s, workspace_id, user, "environment.new_version", "environment",
                   "%s@%d" % (environment_id, version), {"policyHash": env.policy_hash})
            s.commit()
            return {"environmentId": environment_id, "version": version, "policyHash": env.policy_hash}

    @app.get("/workspaces/{workspace_id}/environments")
    async def list_environments(workspace_id: str, user: str = Depends(user_dep)):
        with session_factory() as s:
            role = require_workspace_role(s, workspace_id, user, "member")
            envs = (s.query(EnvironmentRow).filter_by(workspace_id=workspace_id)
                    .order_by(EnvironmentRow.id, EnvironmentRow.version).all())
            visible = []
            for env in envs:
                if role != "admin":
                    grant = s.get(EnvironmentGrant, (env.id, user))
                    if grant is None:
                        continue
                visible.append({"environmentId": env.id, "version": env.version, "name": env.name,
                                "backingType": env.backing_type, "policyHash": env.policy_hash})
            return visible

    @app.post("/workspaces/{workspace_id}/environments/{environment_id}/grants")
    async def create_grant(workspace_id: str, environment_id: str, req: CreateGrant,
                           user: str = Depends(user_dep)):
        with session_factory() as s:
            require_env_role(s, workspace_id, environment_id, user, "env_admin")
            existing = s.get(EnvironmentGrant, (environment_id, req.userId))
            if existing is not None:
                existing.role = req.role
            else:
                s.add(EnvironmentGrant(environment_id=environment_id, user_id=req.userId, role=req.role))
            _audit(s, workspace_id, user, "environment.grant", "environment_grant",
                   "%s:%s" % (environment_id, req.userId), {"role": req.role})
            s.commit()
            return {"environmentId": environment_id, "userId": req.userId, "role": req.role}

    # -- gates (console-first) --------------------------------------------

    @app.get("/workspaces/{workspace_id}/gates")
    async def list_gates(workspace_id: str, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_workspace_role(s, workspace_id, user, "member")
        open_gates = log.open_gates(workspace_id=workspace_id)
        out = []
        with session_factory() as s:
            for g in open_gates:
                env_id = (g.get("payload") or {}).get("environmentId")
                try:
                    if env_id:
                        require_env_role(s, workspace_id, env_id, user, "viewer")
                    else:
                        require_workspace_role(s, workspace_id, user, "admin")
                except HTTPException:
                    continue
                out.append(g)
        return out

    @app.post("/workspaces/{workspace_id}/gates/{gate_id}/resolve")
    async def resolve_gate(workspace_id: str, gate_id: str, req: ResolveGate,
                           user: str = Depends(user_dep)):
        target = None
        for g in log.open_gates(workspace_id=workspace_id):
            if g["gateId"] == gate_id:
                target = g
                break
        if target is None:
            raise HTTPException(404, "gate %s is not open" % gate_id)
        env_id = (target.get("payload") or {}).get("environmentId")
        with session_factory() as s:
            if env_id:
                require_env_role(s, workspace_id, env_id, user, "operator")
            else:
                require_workspace_role(s, workspace_id, user, "admin")
        event = {"missionId": target["missionId"], "type": "gate_resolved", "gateId": gate_id,
                 "resolution": req.resolution, "resolvedBy": "user:%s" % user}
        if req.reason:
            event["reason"] = req.reason
        if req.patch is not None:
            event["patch"] = req.patch
        log.append(event, workspace_id=workspace_id)
        # Typed intervention rider — learning's label factory, live from day one.
        log.append({"missionId": target["missionId"], "type": "human_intervention",
                    "kind": "gate_resolution", "reason": req.reason,
                    "after": {"gateId": gate_id, "resolution": req.resolution}},
                   workspace_id=workspace_id)
        return {"gateId": gate_id, "resolution": req.resolution}

    return app
