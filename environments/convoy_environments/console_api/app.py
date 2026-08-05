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

import os
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from ..connectors import ConnectorError, get_connector
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
from .auth import ConsoleAuth
from .rbac import require_env_role, require_workspace_role


def _id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:20]


class CreateWorkspace(BaseModel):
    name: str
    creatorEmail: str


class CreateMembership(BaseModel):
    email: str
    role: Literal["admin", "builder", "member"]


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
    promoteOverrides: List[str] = Field(default_factory=list)  # escalate-only


class CreateEnvironment(BaseModel):
    name: str
    description: str = ""
    backingType: Literal["live", "hermetic"] = "live"
    connections: List[EnvConnectionInput] = Field(default_factory=list)
    browserPolicy: Optional[Dict[str, Any]] = None
    sandboxTemplate: str = ""  # sandbox template ref for the runtime's SandboxProvider
    dataNamespace: str = ""  # empty → derived ws_<id>/env_<id>
    # budgets are runtime-owned (RunPolicy / BudgetState) — no budget fields here


class CreateGrant(BaseModel):
    userId: str
    role: Literal["viewer", "operator", "env_admin"]


def build_console_app(session_factory, secrets: SecretsService,
                      auth: Optional[ConsoleAuth] = None,
                      provisioning_token: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="convoy-console-api")
    auth = auth or ConsoleAuth()
    provisioning = provisioning_token if provisioning_token is not None else os.environ.get(
        "CONVOY_INTERNAL_TOKEN", "")

    def provisioning_dep(x_convoy_internal: str = Header(default="")) -> None:
        """Workspace creation is a provisioning act (it corresponds to a
        stamped stack), not a self-serve signup — same fail-closed shared
        secret the gateway's internal surface uses."""
        if not provisioning or x_convoy_internal != provisioning:
            raise HTTPException(401, "workspace creation requires the provisioning token")

    def user_dep(authorization: str = Header(default=""),
                 x_convoy_user: str = Header(default="")) -> str:
        with session_factory() as s:
            return auth.resolve_user(s, authorization, x_convoy_user)

    def _audit(session, workspace_id: str, actor: str, action: str, subject_type: str, subject_id: str,
               diff: Optional[dict] = None) -> None:
        session.add(AuditLog(workspace_id=workspace_id, actor_user_id=actor, action=action,
                             subject_type=subject_type, subject_id=subject_id, diff=diff))

    # -- workspaces --------------------------------------------------------

    @app.post("/workspaces")
    async def create_workspace(req: CreateWorkspace, _: None = Depends(provisioning_dep)):
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

    # -- memberships -------------------------------------------------------

    @app.post("/workspaces/{workspace_id}/memberships")
    async def upsert_membership(workspace_id: str, req: CreateMembership,
                                user: str = Depends(user_dep)):
        """Invite-by-email: creates (or reuses) the user row and attaches the
        workspace role. The invitee gets access the moment they first log in
        through WorkOS with this email (auth.py links idp_subject then) — no
        email delivery in this layer; the website owns notifications."""
        with session_factory() as s:
            require_workspace_role(s, workspace_id, user, "admin")
            invitee = s.query(User).filter_by(email=req.email).one_or_none()
            if invitee is None:
                invitee = User(id=_id("usr"), email=req.email)
                s.add(invitee)
            existing = s.get(Membership, (workspace_id, invitee.id))
            if existing is not None:
                existing.role = req.role
            else:
                s.add(Membership(workspace_id=workspace_id, user_id=invitee.id, role=req.role))
            _audit(s, workspace_id, user, "membership.upsert", "membership",
                   "%s:%s" % (workspace_id, invitee.id), {"email": req.email, "role": req.role})
            s.commit()
            return {"userId": invitee.id, "email": invitee.email, "role": req.role,
                    "linked": invitee.idp_subject is not None}

    @app.get("/workspaces/{workspace_id}/memberships")
    async def list_memberships(workspace_id: str, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_workspace_role(s, workspace_id, user, "member")
            rows = (s.query(Membership, User).join(User, User.id == Membership.user_id)
                    .filter(Membership.workspace_id == workspace_id).all())
            return [{"userId": u.id, "email": u.email, "role": m.role,
                     "linked": u.idp_subject is not None} for m, u in rows]

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
                           tool_allowlist=ec.toolAllowlist, promote_overrides=ec.promoteOverrides)
            )
        namespace = req.dataNamespace or "%s/%s" % (workspace_id, env_id)
        phash = policy_hash(
            req.backingType,
            [{"connectionId": r.connection_id, "manifestHash": r.manifest_hash,
              "toolAllowlist": r.tool_allowlist, "promoteOverrides": r.promote_overrides}
             for r in conn_rows],
            req.browserPolicy,
            sandbox_template=req.sandboxTemplate,
            data_namespace=namespace,
        )
        env = EnvironmentRow(id=env_id, version=version, workspace_id=workspace_id,
                             name=req.name, backing_type=req.backingType,
                             browser_policy=req.browserPolicy,
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

    # Gates deliberately absent: human gates are plan-step-level and
    # runtime-owned (HumanGate + human_response signal, DESIGN §6/§7); gate
    # UX and notifications belong to website/ off gate_opened RunEvents.
    # This console covers only what environments/ owns: connections,
    # environments, grants.

    return app
