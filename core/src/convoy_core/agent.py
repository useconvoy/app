"""Agent specification: identity, layer, capabilities, and model for one agent."""

from pydantic import BaseModel, Field

from convoy_core.artifacts import ArtifactRef
from convoy_core.tools import ToolGrant


class AgentSpec(BaseModel):
    id: str
    layer: int = Field(ge=0)  # depth cap is policy, not type — RunPolicy.max_depth
    parent_id: str | None = None
    max_children: int = Field(ge=0, le=10)
    model: str  # resolved via ModelGatewayConfig
    tools: list[ToolGrant]  # capabilities requested
    prompt_ref: ArtifactRef
