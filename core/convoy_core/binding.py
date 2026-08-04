"""EnvironmentBinding — the frozen runtime↔environments seam.

Transcribed from agent-runtime DESIGN.md §5 (Approved v1, Aug 3 2026). The
runtime resolves environment_id → binding at run start and pins the snapshot
as RunState.binding_ref for the run's lifetime; changing an environment mints
a new version, in-flight runs finish on the old one.

Trust obligation (SERVICE-CONTRACTS §2): the runtime *believes*
ToolGrant.execution and side_effecting. A side-effecting tool misflagged as
inline breaks retry safety and can double-fire real-world actions — flag
conservatively; unknown → promoted + side_effecting=True.

Fulfillment clauses (agreed): every connector_endpoints URL is a
gateway-terminated MCP door, never a direct connector server; `id` carries
the version ("env_x@3") and doubles as binding_ref. An environment
*definition* compiles into two immutable bindings — production (real
connectors, real clock) and sandbox (mocks, virtual-capable clock).

PermissionScope is not spelled out in DESIGN §5 — the shape below (the
connection the grant rides on, plus an opaque scope ref) is the environments/
proposal, flagged for Aneesh's sign-off before M0 transcription locks.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import AnyUrl, BaseModel, ConfigDict, Field
from typing_extensions import Literal


class PermissionScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_id: str
    scope_ref: str = ""  # provider-scope ref (OAuth scopes, session-policy name); opaque to the runtime


class ToolGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: str
    scope: PermissionScope
    execution: Literal["inline", "promoted"]  # static → replay-safe promotion
    side_effecting: bool = False  # inline requires False (runtime-enforced)


class ClockConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["real", "virtual"] = "real"  # virtual allowed only on sandbox-kind bindings
    advance: Literal["manual", "on_idle", "ratio"] = "manual"
    ratio: Optional[float] = None  # virtual seconds per real second


class EnvironmentBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # "env_<id>@<version>" — doubles as binding_ref
    tenant_id: str
    kind: Literal["sandbox", "production"]
    tool_registry: List[ToolGrant]  # what this environment offers
    connector_endpoints: Dict[str, AnyUrl]  # internal data-plane MCP servers (gateway doors)
    credential_scope: str  # IAM role / STS session-policy ref
    data_namespace: str
    sandbox_template: str
    clock: ClockConfig = Field(default_factory=ClockConfig)  # real for production; sandbox may go virtual
