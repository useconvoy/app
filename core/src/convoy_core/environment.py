"""Environment binding — the seam between the runtime and environments/.

The runtime pins the resolved binding as an immutable snapshot for the run's
lifetime (`RunState.binding_ref`): a run's environment cannot drift mid-flight,
which replay determinism and the audit trail both require.
"""

from typing import Literal

from pydantic import AnyUrl, BaseModel

from convoy_core.tools import ToolGrant


class ClockConfig(BaseModel):  # how a run experiences time (real or virtual)
    mode: Literal["real", "virtual"] = "real"  # virtual allowed only on sandbox-kind bindings
    advance: Literal["manual", "on_idle", "ratio"] = "manual"
    ratio: float | None = None  # virtual seconds per real second


class EnvironmentBinding(BaseModel):  # the environments/ seam
    id: str
    tenant_id: str
    kind: Literal["sandbox", "production"]
    tool_registry: list[ToolGrant]  # what this environment offers
    connector_endpoints: dict[str, AnyUrl]  # internal data-plane MCP servers
    credential_scope: str  # IAM role / STS session-policy ref
    data_namespace: str
    sandbox_template: str
    clock: ClockConfig = ClockConfig()  # real for production; sandbox may go virtual
