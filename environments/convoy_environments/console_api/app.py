"""Console API — what apps/web talks to.

LEXICON (the deployed website's vocabulary wins on every user-facing
surface; the machine seam below the console is frozen and keeps its names):

    console "organization"  = db `workspaces` table    = wire `tenant_id`
    console "workspace"     = db `environments` table  = wire `environment_id`

An organization is the tenant (the customer). A workspace is a bundle of
system grants with production + rehearsal bindings — what the code and the
runtime call an environment. Python internals (tables, models, RBAC helpers,
GatewayService) deliberately keep the old names; only the HTTP paths and
JSON keys here speak the website's language. The runtime registry
(GET /environments/{id}, /internal/*, /data-plane/*, /mcp/*) is untouched.

Auth: WorkOS bearer at the edge, dev header auth behind a flag, and a
service-to-service mode (X-Convoy-Internal + X-Convoy-Acts-For: <email>) for
the website's server calling on behalf of its signed-in user — see auth.py.
Secrets are write-only: values go in on create and never come back out on
any read path. Gates render on the console first (settled decision #3) but
live with the runtime — see the note at the bottom.
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


class CreateOrganization(BaseModel):
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


class WorkspaceConnectionInput(BaseModel):
    connectionId: str
    toolAllowlist: List[str] = Field(default_factory=list)
    promoteOverrides: List[str] = Field(default_factory=list)  # escalate-only


class CreateWorkspaceSpec(BaseModel):
    """Body of workspace create AND new-version — a workspace version is a
    full respecification, not a patch. `purpose` maps to the environments
    table's `description` column (LEXICON above)."""

    name: str
    purpose: str = ""
    backingType: Literal["live", "hermetic"] = "live"  # kept for internal callers; not part of the website contract
    connections: List[WorkspaceConnectionInput] = Field(default_factory=list)
    browserPolicy: Optional[Dict[str, Any]] = None
    sandboxTemplate: str = ""  # sandbox template ref for the runtime's SandboxProvider
    dataNamespace: str = ""  # empty → derived <org_id>/<workspace_id>
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
    if not auth.internal_token:
        # One internal token per deployment: the provisioning secret also
        # authenticates the service-to-service acts-for mode (auth.py).
        auth.internal_token = provisioning

    def provisioning_dep(x_convoy_internal: str = Header(default="")) -> None:
        """Organization creation is a provisioning act (it corresponds to a
        stamped stack), not a self-serve signup — same fail-closed shared
        secret the gateway's internal surface uses."""
        if not provisioning or x_convoy_internal != provisioning:
            raise HTTPException(401, "organization creation requires the provisioning token")

    def user_dep(authorization: str = Header(default=""),
                 x_convoy_user: str = Header(default=""),
                 x_convoy_internal: str = Header(default=""),
                 x_convoy_acts_for: str = Header(default="")) -> str:
        with session_factory() as s:
            return auth.resolve_user(s, authorization, x_convoy_user,
                                     x_convoy_internal, x_convoy_acts_for)

    def _audit(session, org_id: str, actor: str, action: str, subject_type: str, subject_id: str,
               diff: Optional[dict] = None) -> None:
        # Flush domain rows first: with no relationship() constructs the unit
        # of work won't order audit_log after workspaces on its own, and
        # Postgres enforces the FK where SQLite silently didn't.
        session.flush()
        session.add(AuditLog(workspace_id=org_id, actor_user_id=actor, action=action,
                             subject_type=subject_type, subject_id=subject_id, diff=diff))

    # -- organizations (db: workspaces) ------------------------------------

    @app.post("/organizations")
    async def create_organization(req: CreateOrganization, _: None = Depends(provisioning_dep)):
        with session_factory() as s:
            org = Workspace(id=_id("ws"), name=req.name)
            user = s.query(User).filter_by(email=req.creatorEmail).one_or_none()
            if user is None:
                user = User(id=_id("usr"), email=req.creatorEmail)
                s.add(user)
            s.add(org)
            s.add(Membership(workspace_id=org.id, user_id=user.id, role="admin"))
            _audit(s, org.id, user.id, "workspace.create", "workspace", org.id)
            s.commit()
            return {"organizationId": org.id, "userId": user.id}

    # -- memberships -------------------------------------------------------

    @app.post("/organizations/{org_id}/memberships")
    async def upsert_membership(org_id: str, req: CreateMembership,
                                user: str = Depends(user_dep)):
        """Invite-by-email: creates (or reuses) the user row and attaches the
        organization role. The invitee gets access the moment they first log
        in through WorkOS with this email (auth.py links idp_subject then) —
        no email delivery in this layer; the website owns notifications."""
        with session_factory() as s:
            require_workspace_role(s, org_id, user, "admin")
            invitee = s.query(User).filter_by(email=req.email).one_or_none()
            if invitee is None:
                invitee = User(id=_id("usr"), email=req.email)
                s.add(invitee)
            existing = s.get(Membership, (org_id, invitee.id))
            if existing is not None:
                existing.role = req.role
            else:
                s.add(Membership(workspace_id=org_id, user_id=invitee.id, role=req.role))
            _audit(s, org_id, user, "membership.upsert", "membership",
                   "%s:%s" % (org_id, invitee.id), {"email": req.email, "role": req.role})
            s.commit()
            return {"userId": invitee.id, "email": invitee.email, "role": req.role,
                    "linked": invitee.idp_subject is not None}

    @app.get("/organizations/{org_id}/memberships")
    async def list_memberships(org_id: str, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_workspace_role(s, org_id, user, "member")
            rows = (s.query(Membership, User).join(User, User.id == Membership.user_id)
                    .filter(Membership.workspace_id == org_id).all())
            return [{"userId": u.id, "email": u.email, "role": m.role,
                     "linked": u.idp_subject is not None} for m, u in rows]

    # -- connections -------------------------------------------------------

    @app.post("/organizations/{org_id}/connections")
    async def create_connection(org_id: str, req: CreateConnection, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_workspace_role(s, org_id, user, "admin")
        secret_ref = None
        if req.secretValue is not None:
            secret_ref = secrets.create(org_id, "%s:%s" % (req.provider, req.displayName),
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
            row = ConnectionRow(id=_id("conn"), workspace_id=org_id, kind=req.kind,
                                provider=req.provider, display_name=req.displayName,
                                config=req.config, secret_ref=secret_ref,
                                manifest=manifest.model_dump(exclude_none=True),
                                manifest_hash=manifest.hash, status="active", created_by=user)
            s.add(row)
            _audit(s, org_id, user, "connection.create", "connection", row.id,
                   {"provider": req.provider, "manifestHash": manifest.hash})
            s.commit()
            return {"connectionId": row.id, "manifestHash": manifest.hash,
                    "tools": [t.name for t in manifest.tools], "domains": manifest.domains}

    @app.get("/organizations/{org_id}/connections")
    async def list_connections(org_id: str, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_workspace_role(s, org_id, user, "member")
            rows = s.query(ConnectionRow).filter_by(workspace_id=org_id).all()
            return [{"connectionId": r.id, "kind": r.kind, "provider": r.provider,
                     "displayName": r.display_name, "status": r.status,
                     "manifestHash": r.manifest_hash} for r in rows]

    # -- workspaces (db: environments) -------------------------------------

    def _build_env_rows(s, org_id: str, env_id: str, version: int,
                        req: CreateWorkspaceSpec, user: str):
        conn_rows = []
        for wc in req.connections:
            conn = s.get(ConnectionRow, wc.connectionId)
            if conn is None or conn.workspace_id != org_id:
                raise HTTPException(400, "unknown connection %s" % wc.connectionId)
            manifest = ConnectionManifest.model_validate(conn.manifest)
            unknown = [t for t in wc.toolAllowlist if manifest.tool(t) is None]
            if unknown:
                raise HTTPException(400, "tools not in %s manifest: %s" % (conn.id, unknown))
            conn_rows.append(
                EnvConnRow(environment_id=env_id, environment_version=version,
                           connection_id=conn.id, manifest_hash=conn.manifest_hash,
                           tool_allowlist=wc.toolAllowlist, promote_overrides=wc.promoteOverrides)
            )
        namespace = req.dataNamespace or "%s/%s" % (org_id, env_id)
        phash = policy_hash(
            req.backingType,
            [{"connectionId": r.connection_id, "manifestHash": r.manifest_hash,
              "toolAllowlist": r.tool_allowlist, "promoteOverrides": r.promote_overrides}
             for r in conn_rows],
            req.browserPolicy,
            sandbox_template=req.sandboxTemplate,
            data_namespace=namespace,
        )
        env = EnvironmentRow(id=env_id, version=version, workspace_id=org_id,
                             name=req.name, backing_type=req.backingType,
                             browser_policy=req.browserPolicy,
                             sandbox_template=req.sandboxTemplate, data_namespace=namespace,
                             policy_hash=phash, description=req.purpose, created_by=user)
        return env, conn_rows

    @app.post("/organizations/{org_id}/workspaces")
    async def create_workspace(org_id: str, req: CreateWorkspaceSpec, user: str = Depends(user_dep)):
        """A workspace IS the environment underneath (LEXICON): one id serves
        both the console (`workspaceId`) and the frozen runtime registry
        (`environmentId`), so the response carries it under both keys."""
        with session_factory() as s:
            require_workspace_role(s, org_id, user, "builder")
            env_id = _id("env")
            env, conn_rows = _build_env_rows(s, org_id, env_id, 1, req, user)
            s.add(env)
            s.add_all(conn_rows)
            s.add(EnvironmentGrant(environment_id=env_id, user_id=user, role="env_admin"))
            _audit(s, org_id, user, "environment.create", "environment",
                   "%s@1" % env_id, {"policyHash": env.policy_hash})
            s.commit()
            return {"workspaceId": env_id, "version": 1, "policyHash": env.policy_hash,
                    "environmentId": env_id}

    @app.post("/organizations/{org_id}/workspaces/{workspace_id}/versions")
    async def create_workspace_version(org_id: str, workspace_id: str,
                                       req: CreateWorkspaceSpec, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_env_role(s, org_id, workspace_id, user, "env_admin")
            latest = (s.query(EnvironmentRow).filter_by(id=workspace_id, workspace_id=org_id)
                      .order_by(EnvironmentRow.version.desc()).first())
            if latest is None:
                raise HTTPException(404, "unknown workspace")
            version = latest.version + 1
            env, conn_rows = _build_env_rows(s, org_id, workspace_id, version, req, user)
            s.add(env)
            s.add_all(conn_rows)
            _audit(s, org_id, user, "environment.new_version", "environment",
                   "%s@%d" % (workspace_id, version), {"policyHash": env.policy_hash})
            s.commit()
            return {"workspaceId": workspace_id, "version": version,
                    "policyHash": env.policy_hash, "environmentId": workspace_id}

    @app.get("/organizations/{org_id}/workspaces")
    async def list_workspaces(org_id: str, user: str = Depends(user_dep)):
        """Every workspace version visible to the caller.

        Binding vocabulary for the website: `environmentId` is the id the
        frozen runtime registry serves (== `workspaceId`), and
        `rehearsalEnvironmentId` is the string convention
        `<workspaceId>/sandbox` — the rehearsal binding itself is fetched
        from the frozen registry with `?kind=sandbox`. Clock semantics are
        per BINDING, not per row: `clockMode` describes the production
        binding and is always "wall"; `rehearsalClockMode` describes the
        rehearsal binding and is always "virtual"."""
        with session_factory() as s:
            role = require_workspace_role(s, org_id, user, "member")
            envs = (s.query(EnvironmentRow).filter_by(workspace_id=org_id)
                    .order_by(EnvironmentRow.id, EnvironmentRow.version).all())
            visible = []
            for env in envs:
                if role != "admin":
                    grant = s.get(EnvironmentGrant, (env.id, user))
                    if grant is None:
                        continue
                visible.append({"workspaceId": env.id, "version": env.version, "name": env.name,
                                "purpose": env.description, "policyHash": env.policy_hash,
                                "environmentId": env.id,
                                "rehearsalEnvironmentId": "%s/sandbox" % env.id,
                                "clockMode": "wall", "rehearsalClockMode": "virtual"})
            return visible

    @app.post("/organizations/{org_id}/workspaces/{workspace_id}/grants")
    async def create_grant(org_id: str, workspace_id: str, req: CreateGrant,
                           user: str = Depends(user_dep)):
        with session_factory() as s:
            require_env_role(s, org_id, workspace_id, user, "env_admin")
            existing = s.get(EnvironmentGrant, (workspace_id, req.userId))
            if existing is not None:
                existing.role = req.role
            else:
                s.add(EnvironmentGrant(environment_id=workspace_id, user_id=req.userId, role=req.role))
            _audit(s, org_id, user, "environment.grant", "environment_grant",
                   "%s:%s" % (workspace_id, req.userId), {"role": req.role})
            s.commit()
            return {"workspaceId": workspace_id, "userId": req.userId, "role": req.role}

    # Gates deliberately absent: human gates are plan-step-level and
    # runtime-owned (HumanGate + human_response signal, DESIGN §6/§7); gate
    # UX and notifications belong to website/ off gate_opened RunEvents.
    # This console covers only what environments/ owns: connections,
    # workspaces, grants.

    return app
