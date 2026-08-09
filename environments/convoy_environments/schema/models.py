"""Control-plane models for the environments layer.

Field names are camelCase to match the co-signed wire convention in
convoy_core.events. These are the API/config shapes; the SQLAlchemy tables in
convoy_environments.db mirror them in snake_case columns.

The two-object rule (spec §1.2): a Connection is an admin-owned authenticated
link to one external system — expensive, workspace-level. An Environment is a
cheap, versioned-immutable policy bundle that subsets connections. Never merge
them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .hashing import manifest_hash as _manifest_hash
from .hashing import policy_hash as _policy_hash

WorkspaceRole = Literal["admin", "builder", "member"]
EnvironmentRole = Literal["viewer", "operator", "env_admin"]

ConnectionKind = Literal["mcp_managed", "mcp_custom", "aws_role", "browser_identity"]
ConnectionStatus = Literal["active", "needs_reauth", "revoked"]
EnvironmentBacking = Literal["live", "hermetic"]

# Per-tool annotations in the connection manifest, in the runtime's frozen
# vocabulary (DESIGN §5 ToolGrant; SERVICE-CONTRACTS §2 trust obligation):
#   execution="inline"   → runs inside run_turn; MUST be read-only idempotent
#   execution="promoted" → own activity, runtime idempotency key, retry-safe
#   side_effecting=True  → requires promoted; we flag conservatively — a
#                          misflagged tool can double-fire real-world actions.
ToolExecution = Literal["inline", "promoted"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolSpec(_Model):
    """One tool in a connection manifest. Hash-relevant: any change to any
    field (including the schema) changes manifest_hash and voids certification.

    Defaults are the conservative corner (promoted + side-effecting): a tool
    must *earn* inline by being declared read-only idempotent."""

    name: str
    description: str = ""
    inputSchema: Dict[str, Any] = Field(default_factory=dict)
    execution: ToolExecution = "promoted"
    sideEffecting: bool = True

    @property
    def is_read(self) -> bool:
        return self.execution == "inline" and not self.sideEffecting


class ConnectionManifest(_Model):
    """What a connection can do. For tool connections: the MCP tool list.
    For browser identities: the domain set the identity may log into."""

    tools: List[ToolSpec] = Field(default_factory=list)
    domains: List[str] = Field(default_factory=list)

    @property
    def hash(self) -> str:
        return _manifest_hash(self)

    def tool(self, name: str) -> Optional[ToolSpec]:
        for t in self.tools:
            if t.name == name:
                return t
        return None


class Connection(_Model):
    """Admin-owned authenticated link to one external system. `config` holds
    only non-secret material (instance URL, role ARN, MCP URL); the secret
    itself is a reference into a secrets backend, never a value."""

    connectionId: str
    workspaceId: str
    kind: ConnectionKind
    provider: str
    displayName: str
    config: Dict[str, Any] = Field(default_factory=dict)
    secretRef: Optional[str] = None
    manifest: ConnectionManifest
    status: ConnectionStatus = "active"

    @property
    def manifestHash(self) -> str:
        return self.manifest.hash


class EnvironmentConnection(_Model):
    """One connection's grant inside an environment. The allowlist is explicit
    — no wildcard-by-default. promoteOverrides escalates specific tools to
    promoted + side-effecting beyond their manifest annotation (escalate only,
    never downgrade — an env admin can distrust a manifest, not relax it)."""

    connectionId: str
    manifestHash: str
    toolAllowlist: List[str] = Field(default_factory=list)
    promoteOverrides: List[str] = Field(default_factory=list)


class BrowserPolicy(_Model):
    allowedDomains: List[str] = Field(default_factory=list)
    persistProfile: bool = True


class Environment(_Model):
    """Versioned-immutable policy bundle. Edits create a new version; missions
    pin (environmentId, version), so certification is voided explicitly, never
    silently."""

    environmentId: str
    workspaceId: str
    parentEnvironmentId: Optional[str] = None
    name: str
    version: int = Field(ge=1)
    backingType: EnvironmentBacking = "live"
    connections: List[EnvironmentConnection] = Field(default_factory=list)
    browserPolicy: Optional[BrowserPolicy] = None
    sandboxTemplate: str = ""  # sandbox image/template ref the runtime's SandboxProvider instantiates
    dataNamespace: str = ""  # defaults to ws_<id>/env_<id> when unset
    # Budgets deliberately absent: dollar caps live in the runtime
    # (RunPolicy/BudgetState + LiteLLM virtual keys), not the environment.
    description: str = ""

    @property
    def policyHash(self) -> str:
        return _policy_hash(self.backingType, self.connections, self.browserPolicy,
                            self.sandboxTemplate, self.dataNamespace)

    def connection_policy(self, connection_id: str) -> Optional[EnvironmentConnection]:
        for ec in self.connections:
            if ec.connectionId == connection_id:
                return ec
        return None
