"""EnvironmentBinding — the frozen runtime↔environments seam.

The runtime resolves environment_id → binding at run start (registry endpoint
in environments/) and pins the resolved snapshot for the run's lifetime as
RunState.binding_ref: a run's environment cannot drift mid-flight. Changing
an environment means a new binding version; existing runs land against the
version they started with.

This type is co-owned and FROZEN (agreed Aug 2026): environments/ serves and
fulfills it; the runtime consumes it. Field changes need both founders, like
events.py. Two fulfillment clauses agreed alongside the type:
  1. Every URL in connector_endpoints is a GATEWAY door (MCP terminated at
     the governed gateway) — never a direct connector server. Credential
     injection, allowlists, two-phase events, and gates all live behind it.
  2. `id` carries the version ("env_x@3") — binding_ref is that string, and
     equal ids imply byte-identical bindings.
"""

from __future__ import annotations

from typing import Dict, List

from pydantic import AnyUrl, BaseModel, ConfigDict, Field
from typing_extensions import Literal


class ToolGrant(BaseModel):
    """One tool this environment offers, with its enforcement class."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    connection_id: str
    effect_class: Literal["read", "effectful", "gated"]
    description: str = ""
    input_schema: Dict = Field(default_factory=dict)


class EnvironmentBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # "env_<id>@<version>" — doubles as binding_ref
    tenant_id: str
    kind: Literal["sandbox", "production"]
    tool_registry: List[ToolGrant]  # what this environment offers
    connector_endpoints: Dict[str, AnyUrl]  # gateway-terminated MCP doors, keyed by connection_id
    credential_scope: str  # ref the runtime presents when minting run tokens
    data_namespace: str
    sandbox_template: str
