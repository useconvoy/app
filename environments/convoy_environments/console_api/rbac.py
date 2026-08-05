"""RBAC checks. Two levels (spec §7): workspace roles gate who manages
connections and creates environments; per-environment grants gate who views,
operates (triggers/approves), and administers each environment. The split is
deliberate: the operator who approves a gated payment is not the env_admin
who can rewire which connection the environment uses."""

from __future__ import annotations

from fastapi import HTTPException

from ..db.tables import EnvironmentGrant, Membership

_WORKSPACE_ORDER = {"member": 0, "builder": 1, "admin": 2}
_ENV_ORDER = {"viewer": 0, "operator": 1, "env_admin": 2}


def workspace_role(session, workspace_id: str, user_id: str) -> str:
    row = session.get(Membership, (workspace_id, user_id))
    if row is None:
        raise HTTPException(403, "not a member of workspace %s" % workspace_id)
    return row.role


def require_workspace_role(session, workspace_id: str, user_id: str, at_least: str) -> str:
    role = workspace_role(session, workspace_id, user_id)
    if _WORKSPACE_ORDER[role] < _WORKSPACE_ORDER[at_least]:
        raise HTTPException(403, "requires workspace role %s" % at_least)
    return role


def require_env_role(session, workspace_id: str, environment_id: str, user_id: str, at_least: str) -> str:
    """Workspace admins pass every environment check; everyone else needs an
    explicit grant of sufficient rank."""
    if workspace_role(session, workspace_id, user_id) == "admin":
        return "env_admin"
    grant = session.get(EnvironmentGrant, (environment_id, user_id))
    if grant is None or _ENV_ORDER[grant.role] < _ENV_ORDER[at_least]:
        raise HTTPException(403, "requires %s on environment %s" % (at_least, environment_id))
    return grant.role
