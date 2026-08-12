"""Console API — what apps/web talks to.

LEXICON (the deployed website's vocabulary wins on every user-facing
surface; the machine seam below the console is frozen and keeps its names):

    console "organization"  = db `organizations` table = wire `tenant_id`
    console "workspace"     = db `environments` table  = wire `environment_id`

An organization is the tenant (the customer). A workspace is a bundle of
system grants with production + rehearsal bindings — what the code and the
runtime call an environment. The runtime registry
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

from ..connectors import ConnectorError, get_connector, registered_connectors
from ..db.tables import (
    AuditLog,
    Connection as ConnectionRow,
    Environment as EnvironmentRow,
    EnvironmentConnection as EnvConnRow,
    EnvironmentGrant,
    EventRule,
    Membership,
    Organization,
    User,
    PlatformConnector,
)
from ..schema import ConnectionManifest, policy_hash
from ..secrets import SecretsService
from .auth import ConsoleAuth
from .rbac import require_env_role, require_organization_role


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


class CreateExecutionEnvironment(BaseModel):
    """A named runtime configuration derived from one workspace.

    Connector grants come from the workspace and are copied immutably. The
    environment owns only execution concerns: compute template, browser
    policy, and its durable data namespace.
    """

    name: str
    purpose: str = ""
    sandboxTemplate: str = "convoy-devbox-python"
    browserPolicy: Optional[Dict[str, Any]] = None
    dataNamespace: str = ""


class AttachSecret(BaseModel):
    secretValue: str
    secretBackend: str = "builtin"


class SetWebhookSecret(BaseModel):
    """The provider's webhook *signing* secret — verification material the
    hooks door must read on every delivery, so it lives in the connection
    config, not the write-only credential vault."""

    secret: str = Field(min_length=8, max_length=256)


class CreateEventRule(BaseModel):
    connectionId: str
    eventType: str = Field(min_length=1, max_length=128)
    agentId: str
    enabled: bool = True
    # The frozen run template, resolved by the console at save time.
    goal: str = Field(min_length=1)
    environmentId: str
    budgetUsd: str
    tools: List[str] = Field(default_factory=list)
    instructions: List[str] = Field(default_factory=list)
    startedBy: Optional[str] = None


class UpsertPlatformConnector(BaseModel):
    provider: str = Field(min_length=2, max_length=64)
    displayName: str = Field(min_length=2, max_length=255)
    description: str = ""
    mcpUrl: str = Field(min_length=8, max_length=512)
    manifest: Dict[str, Any] = Field(default_factory=dict)
    credentialLabel: str = "Bearer token"
    credentialPlaceholder: str = ""
    credentialSteps: List[str] = Field(default_factory=list)
    enabled: bool = True


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
        # of work won't order audit_log after organizations on its own, and
        # Postgres enforces the FK where SQLite silently didn't.
        session.flush()
        session.add(AuditLog(organization_id=org_id, actor_user_id=actor, action=action,
                             subject_type=subject_type, subject_id=subject_id, diff=diff))

    # -- provider directory ------------------------------------------------

    @app.get("/providers")
    async def provider_directory(user: str = Depends(user_dep)):
        """The whole provider directory: code connectors (the platform's
        depth tier, listed straight from the connector registry so a new
        connector class appears here with no further wiring) plus enabled
        declarative platform connectors (the breadth tier, Convoy-hosted
        MCP servers stored as rows). Org-scoped custom connections are NOT
        here; they live on /organizations/{org}/connections."""
        entries: List[Dict[str, Any]] = []
        for provider, cls in sorted(registered_connectors().items()):
            if provider == "mcp_custom":
                continue  # the mechanism behind custom + declarative, not a provider
            manifest = await cls().manifest()
            entries.append({
                "provider": provider,
                "displayName": cls.display_name or provider,
                "description": cls.description,
                "kind": "code",
                "scope": "platform",
                "credential": {
                    "label": cls.credential_label,
                    "placeholder": cls.credential_placeholder,
                    "multiline": cls.credential_multiline,
                    "steps": list(cls.credential_steps),
                },
                "tools": [{"name": t.name, "execution": t.execution,
                           "sideEffecting": t.sideEffecting, "description": t.description}
                          for t in manifest.tools],
            })
        with session_factory() as s:
            rows = s.query(PlatformConnector).filter_by(enabled=True).all()
            for row in rows:
                entries.append({
                    "provider": row.provider,
                    "displayName": row.display_name,
                    "description": row.description,
                    "kind": "declarative",
                    "scope": "platform",
                    "mcpUrl": row.mcp_url,
                    "credential": {
                        "label": row.credential_label,
                        "placeholder": row.credential_placeholder,
                        "multiline": False,
                        "steps": list(row.credential_steps or []),
                    },
                    "tools": list((row.manifest or {}).get("tools", [])),
                })
        return entries

    @app.post("/platform-connectors")
    async def upsert_platform_connector(req: UpsertPlatformConnector,
                                        _: None = Depends(provisioning_dep)):
        """Team authoring door for the breadth tier, behind the provisioning
        token: publish (or update) a declarative platform connector. Takes
        effect in the directory immediately; existing org connections keep
        the config they were created with."""
        with session_factory() as s:
            row = s.get(PlatformConnector, req.provider)
            if row is None:
                row = PlatformConnector(provider=req.provider)
                s.add(row)
            row.display_name = req.displayName
            row.description = req.description
            row.mcp_url = req.mcpUrl
            row.manifest = req.manifest
            row.credential_label = req.credentialLabel
            row.credential_placeholder = req.credentialPlaceholder
            row.credential_steps = req.credentialSteps
            row.enabled = req.enabled
            s.commit()
        return {"provider": req.provider, "enabled": req.enabled}

    # -- organizations ----------------------------------------------------

    @app.post("/organizations")
    async def create_organization(req: CreateOrganization, _: None = Depends(provisioning_dep)):
        with session_factory() as s:
            org = Organization(id=_id("org"), name=req.name)
            user = s.query(User).filter_by(email=req.creatorEmail).one_or_none()
            if user is None:
                user = User(id=_id("usr"), email=req.creatorEmail)
                s.add(user)
            s.add(org)
            s.flush()  # org + user rows must precede the membership FK on Postgres
            s.add(Membership(organization_id=org.id, user_id=user.id, role="admin"))
            _audit(s, org.id, user.id, "organization.create", "organization", org.id)
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
            require_organization_role(s, org_id, user, "admin")
            invitee = s.query(User).filter_by(email=req.email).one_or_none()
            if invitee is None:
                invitee = User(id=_id("usr"), email=req.email)
                s.add(invitee)
                s.flush()  # user row must precede the membership FK on Postgres
            existing = s.get(Membership, (org_id, invitee.id))
            if existing is not None:
                existing.role = req.role
            else:
                s.add(Membership(organization_id=org_id, user_id=invitee.id, role=req.role))
            _audit(s, org_id, user, "membership.upsert", "membership",
                   "%s:%s" % (org_id, invitee.id), {"email": req.email, "role": req.role})
            s.commit()
            return {"userId": invitee.id, "email": invitee.email, "role": req.role,
                    "linked": invitee.idp_subject is not None}

    @app.get("/organizations/{org_id}/memberships")
    async def list_memberships(org_id: str, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_organization_role(s, org_id, user, "member")
            rows = (s.query(Membership, User).join(User, User.id == Membership.user_id)
                    .filter(Membership.organization_id == org_id).all())
            return [{"userId": u.id, "email": u.email, "role": m.role,
                     "linked": u.idp_subject is not None} for m, u in rows]

    # -- connections -------------------------------------------------------

    @app.post("/organizations/{org_id}/connections")
    async def create_connection(org_id: str, req: CreateConnection, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_organization_role(s, org_id, user, "admin")
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
            row = ConnectionRow(id=_id("conn"), organization_id=org_id, kind=req.kind,
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
            require_organization_role(s, org_id, user, "member")
            rows = s.query(ConnectionRow).filter_by(organization_id=org_id).all()
            return [{"connectionId": r.id, "kind": r.kind, "provider": r.provider,
                     "displayName": r.display_name, "status": r.status,
                     "manifestHash": r.manifest_hash} for r in rows]

    def _connection_or_404(s, org_id: str, connection_id: str) -> ConnectionRow:
        row = s.get(ConnectionRow, connection_id)
        if row is None or row.organization_id != org_id:
            raise HTTPException(404, "unknown connection %s" % connection_id)
        return row

    @app.get("/organizations/{org_id}/connections/{connection_id}")
    async def connection_detail(org_id: str, connection_id: str, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_organization_role(s, org_id, user, "member")
            row = _connection_or_404(s, org_id, connection_id)
            manifest = ConnectionManifest.model_validate(row.manifest or {})
            out = {"connectionId": row.id, "kind": row.kind, "provider": row.provider,
                   "displayName": row.display_name, "status": row.status,
                   "manifestHash": row.manifest_hash,
                   "hasCredential": row.secret_ref is not None,
                   "config": row.config or {},
                   "tools": [{"name": t.name, "execution": t.execution,
                              "sideEffecting": t.sideEffecting, "description": t.description}
                             for t in manifest.tools]}
            if manifest.domains:
                out["domains"] = manifest.domains
            return out

    @app.post("/organizations/{org_id}/connections/{connection_id}/secret")
    async def attach_connection_secret(org_id: str, connection_id: str, req: AttachSecret,
                                       user: str = Depends(user_dep)):
        """Attach (or replace) a connection's credential — the 'real
        credentials attached later' half of secretless registration. The
        value is stored write-only, then probed best-effort per provider
        (google: token exchange; mcp_custom: live tools/list reachability;
        others: no cheap safe probe → verified null). A failed probe keeps
        the stored value but flags the connection needs_reauth so the
        console can say exactly that."""
        with session_factory() as s:
            require_organization_role(s, org_id, user, "admin")
            row = _connection_or_404(s, org_id, connection_id)
            provider, config, secret_ref = row.provider, dict(row.config or {}), row.secret_ref
        if secret_ref is None:
            secret_ref = secrets.create(org_id, "%s:%s" % (provider, connection_id),
                                        req.secretValue, backend=req.secretBackend, created_by=user)
        else:
            secrets.rotate(secret_ref, req.secretValue, actor=user)

        verified: Optional[bool] = None
        failure: Optional[str] = None
        try:
            verified = await get_connector(provider, config=config).verify_credential(req.secretValue)
        except Exception as err:  # noqa: BLE001 — the probe is best-effort by design;
            # any failure (bad key material, unreachable server, auth reject)
            # means the same thing to the caller: not verified.
            verified, failure = False, str(err)

        status = "needs_reauth" if verified is False else "active"
        with session_factory() as s:
            row = _connection_or_404(s, org_id, connection_id)
            row.secret_ref = secret_ref
            row.status = status
            _audit(s, org_id, user, "connection.attach_secret", "connection", connection_id,
                   {"provider": provider, "verified": verified})
            s.commit()
        if failure is not None:
            raise HTTPException(400, "credential stored but verification failed: %s" % failure)
        return {"connectionId": connection_id, "status": status, "verified": verified}

    @app.post("/organizations/{org_id}/connections/{connection_id}/verify")
    async def verify_connection(org_id: str, connection_id: str,
                                user: str = Depends(user_dep)):
        """Re-run the provider probe on the stored credential, on demand.

        This is the console's health check: it reveals the credential only
        to probe the provider (the value never leaves the service — the
        gateway's per-call injection and this probe are the vault's two
        internal read paths) and moves the connection between active and
        needs_reauth accordingly. Providers without a cheap safe probe
        report verified null and the status stays put.
        """
        with session_factory() as s:
            require_organization_role(s, org_id, user, "builder")
            row = _connection_or_404(s, org_id, connection_id)
            provider, config = row.provider, dict(row.config or {})
            secret_ref, status = row.secret_ref, row.status
        if secret_ref is None:
            return {"connectionId": connection_id, "status": status, "verified": None,
                    "reason": "no credential stored"}

        verified: Optional[bool] = None
        try:
            value = secrets.reveal(secret_ref)
            verified = await get_connector(provider, config=config).verify_credential(value)
        except Exception:  # noqa: BLE001 — like the attach probe, any failure
            # (unreachable provider, auth reject, vault trouble) reads the
            # same to the caller: not verified.
            verified = False

        if verified is not None:
            status = "needs_reauth" if verified is False else "active"
            with session_factory() as s:
                row = _connection_or_404(s, org_id, connection_id)
                row.status = status
                _audit(s, org_id, user, "connection.verified", "connection", connection_id,
                       {"provider": provider, "verified": verified})
                s.commit()
        return {"connectionId": connection_id, "status": status, "verified": verified}

    # -- workspaces (db: environments) -------------------------------------

    def _build_env_rows(s, org_id: str, env_id: str, version: int,
                        req: CreateWorkspaceSpec, user: str,
                        parent_environment_id: Optional[str] = None):
        conn_rows = []
        for wc in req.connections:
            conn = s.get(ConnectionRow, wc.connectionId)
            if conn is None or conn.organization_id != org_id:
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
        env = EnvironmentRow(id=env_id, version=version, organization_id=org_id,
                             parent_environment_id=parent_environment_id,
                             name=req.name, backing_type=req.backingType,
                             browser_policy=req.browserPolicy,
                             sandbox_template=req.sandboxTemplate, data_namespace=namespace,
                             policy_hash=phash, description=req.purpose, created_by=user)
        return env, conn_rows

    @app.post("/organizations/{org_id}/workspaces")
    async def create_workspace(org_id: str, req: CreateWorkspaceSpec, user: str = Depends(user_dep)):
        """Create the workspace's compatibility binding.

        Existing callers still receive one registry environment id directly
        on the workspace. Named execution environments can then be created
        beneath it without breaking routines that use this fallback binding.
        """
        with session_factory() as s:
            require_organization_role(s, org_id, user, "builder")
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
            latest = (s.query(EnvironmentRow).filter_by(id=workspace_id, organization_id=org_id,
                                                        parent_environment_id=None)
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
            role = require_organization_role(s, org_id, user, "member")
            envs = (s.query(EnvironmentRow).filter_by(organization_id=org_id,
                                                      parent_environment_id=None)
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

    def _execution_environment_payload(env: EnvironmentRow) -> Dict[str, Any]:
        return {
            "environmentId": env.id,
            "workspaceId": env.parent_environment_id,
            "version": env.version,
            "name": env.name,
            "purpose": env.description,
            "policyHash": env.policy_hash,
            "sandboxTemplate": env.sandbox_template,
            "browserPolicy": env.browser_policy,
            "dataNamespace": env.data_namespace,
            "productionBindingId": env.id,
            "rehearsalBindingId": "%s/sandbox" % env.id,
            "createdAt": env.created_at.isoformat(),
        }

    @app.get("/organizations/{org_id}/workspaces/{workspace_id}/environments")
    async def list_execution_environments(org_id: str, workspace_id: str,
                                          user: str = Depends(user_dep)):
        with session_factory() as s:
            require_env_role(s, org_id, workspace_id, user, "viewer")
            rows = (s.query(EnvironmentRow)
                    .filter_by(organization_id=org_id, parent_environment_id=workspace_id)
                    .order_by(EnvironmentRow.id, EnvironmentRow.version.desc()).all())
            latest: Dict[str, EnvironmentRow] = {}
            for row in rows:
                latest.setdefault(row.id, row)
            return [_execution_environment_payload(row) for row in latest.values()]

    @app.post("/organizations/{org_id}/workspaces/{workspace_id}/environments")
    async def create_execution_environment(org_id: str, workspace_id: str,
                                            req: CreateExecutionEnvironment,
                                            user: str = Depends(user_dep)):
        with session_factory() as s:
            require_env_role(s, org_id, workspace_id, user, "env_admin")
            base = (s.query(EnvironmentRow)
                    .filter_by(id=workspace_id, organization_id=org_id,
                               parent_environment_id=None)
                    .order_by(EnvironmentRow.version.desc()).first())
            if base is None:
                raise HTTPException(404, "unknown workspace")
            base_connections = (s.query(EnvConnRow)
                                .filter_by(environment_id=base.id,
                                           environment_version=base.version).all())
            env_id = _id("env")
            spec = CreateWorkspaceSpec(
                name=req.name,
                purpose=req.purpose,
                backingType=base.backing_type,
                connections=[
                    WorkspaceConnectionInput(
                        connectionId=row.connection_id,
                        toolAllowlist=list(row.tool_allowlist or []),
                        promoteOverrides=list(row.promote_overrides or []),
                    )
                    for row in base_connections
                ],
                browserPolicy=req.browserPolicy,
                sandboxTemplate=req.sandboxTemplate,
                dataNamespace=req.dataNamespace,
            )
            env, connection_rows = _build_env_rows(
                s, org_id, env_id, 1, spec, user, parent_environment_id=workspace_id
            )
            s.add(env)
            s.add_all(connection_rows)
            s.add(EnvironmentGrant(environment_id=env_id, user_id=user, role="env_admin"))
            _audit(s, org_id, user, "execution_environment.create", "environment",
                   "%s@1" % env_id,
                   {"workspaceId": workspace_id, "policyHash": env.policy_hash})
            s.commit()
            return _execution_environment_payload(env)

    @app.get("/organizations/{org_id}/workspaces/{workspace_id}/environments/{environment_id}")
    async def execution_environment_detail(org_id: str, workspace_id: str,
                                           environment_id: str,
                                           user: str = Depends(user_dep)):
        with session_factory() as s:
            require_env_role(s, org_id, workspace_id, user, "viewer")
            row = (s.query(EnvironmentRow)
                   .filter_by(id=environment_id, organization_id=org_id,
                              parent_environment_id=workspace_id)
                   .order_by(EnvironmentRow.version.desc()).first())
            if row is None:
                raise HTTPException(404, "unknown environment")
            return _execution_environment_payload(row)

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

    # -- event triggers ----------------------------------------------------

    @app.post("/organizations/{org_id}/connections/{connection_id}/webhook-secret")
    async def set_webhook_secret(org_id: str, connection_id: str, req: SetWebhookSecret,
                                 user: str = Depends(user_dep)):
        with session_factory() as s:
            require_organization_role(s, org_id, user, "admin")
            row = _connection_or_404(s, org_id, connection_id)
            config = dict(row.config or {})
            config["webhookSecret"] = req.secret
            row.config = config
            _audit(s, org_id, user, "connection.webhook_secret_set", "connection",
                   connection_id, {"provider": row.provider})
            s.commit()
            return {"connectionId": connection_id, "hookPath": "/gateway/hooks/%s" % connection_id}

    @app.post("/organizations/{org_id}/event-rules")
    async def create_event_rule(org_id: str, req: CreateEventRule, user: str = Depends(user_dep)):
        with session_factory() as s:
            require_organization_role(s, org_id, user, "admin")
            _connection_or_404(s, org_id, req.connectionId)
            rule = EventRule(id=_id("rule"), organization_id=org_id,
                             connection_id=req.connectionId, event_type=req.eventType,
                             agent_id=req.agentId, enabled=req.enabled, goal=req.goal,
                             environment_id=req.environmentId, budget_usd=req.budgetUsd,
                             tools=req.tools, instructions=req.instructions,
                             started_by=req.startedBy or user, created_by=user)
            s.add(rule)
            _audit(s, org_id, user, "event_rule.create", "event_rule", rule.id,
                   {"connectionId": req.connectionId, "eventType": req.eventType,
                    "agentId": req.agentId})
            s.commit()
            return _event_rule_payload(rule)

    def _event_rule_payload(rule: EventRule) -> Dict[str, Any]:
        return {"ruleId": rule.id, "connectionId": rule.connection_id,
                "eventType": rule.event_type, "agentId": rule.agent_id,
                "enabled": rule.enabled, "goal": rule.goal,
                "environmentId": rule.environment_id, "budgetUsd": rule.budget_usd,
                "createdAt": rule.created_at.isoformat()}

    @app.get("/organizations/{org_id}/event-rules")
    async def list_event_rules(org_id: str, agent_id: Optional[str] = None,
                               user: str = Depends(user_dep)):
        with session_factory() as s:
            require_organization_role(s, org_id, user, "member")
            query = s.query(EventRule).filter_by(organization_id=org_id)
            if agent_id:
                query = query.filter_by(agent_id=agent_id)
            return [_event_rule_payload(rule) for rule in query.all()]

    @app.delete("/organizations/{org_id}/event-rules/{rule_id}")
    async def delete_event_rule(org_id: str, rule_id: str, user: str = Depends(user_dep)):
        """The registry's first removal endpoint: rules are pure config, so
        deleting one has no credential or version-pinning consequences."""
        with session_factory() as s:
            require_organization_role(s, org_id, user, "admin")
            rule = s.get(EventRule, rule_id)
            if rule is None or rule.organization_id != org_id:
                raise HTTPException(404, "unknown event rule %s" % rule_id)
            s.delete(rule)
            _audit(s, org_id, user, "event_rule.delete", "event_rule", rule_id)
            s.commit()
            return {"ruleId": rule_id, "deleted": True}

    # Gates deliberately absent: human gates are plan-step-level and
    # runtime-owned (HumanGate + human_response signal, DESIGN §6/§7); gate
    # UX and notifications belong to website/ off gate_opened RunEvents.
    # This console covers only what environments/ owns: connections,
    # workspaces, grants.

    return app
