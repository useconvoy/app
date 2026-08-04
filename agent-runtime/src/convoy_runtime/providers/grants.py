"""Tool grant validation and intersection.

An agent's effective permissions are the intersection of what it requested
(`AgentSpec.tools`) and what its environment offers
(`EnvironmentBinding.tool_registry`). Inline tools must be read-only
idempotent: a grant that is both inline and side-effecting is invalid and is
rejected before a run ever starts. Anything the model calls outside the
effective set is rejected at turn time and recorded in the transcript.
"""

from convoy_core import ToolGrant


class GrantValidationError(ValueError):
    """A tool request that can never be honored (unknown or invalid grant)."""


def validate_grants(grants: list[ToolGrant]) -> None:
    """Reject inline grants that claim side effects — inline execution retries
    freely inside the turn activity, so it is reserved for read-only tools."""
    for grant in grants:
        if grant.execution == "inline" and grant.side_effecting:
            raise GrantValidationError(
                f"tool {grant.tool_id!r} is side-effecting and cannot run inline; "
                "side-effecting tools must use promoted execution"
            )


def resolve_requested_tools(
    requested_tool_ids: list[str], registry: list[ToolGrant]
) -> list[ToolGrant]:
    """Resolve requested tool ids against the environment's registry at run
    creation. Unknown ids and invalid grants fail fast — a clean rejection at
    the API instead of a surprise mid-run."""
    by_id = {grant.tool_id: grant for grant in registry}
    unknown = [tool_id for tool_id in requested_tool_ids if tool_id not in by_id]
    if unknown:
        offered = sorted(by_id)
        raise GrantValidationError(
            f"unknown tool(s) {unknown!r}; this environment offers {offered!r}"
        )
    grants = [by_id[tool_id] for tool_id in requested_tool_ids]
    validate_grants(grants)
    return grants


def effective_inline_tools(
    agent_tools: list[ToolGrant], registry: list[ToolGrant]
) -> list[ToolGrant]:
    """The inline tools a turn may execute: granted to the agent AND offered
    by the environment, inline and read-only on both sides. The registry entry
    is authoritative for scope."""
    by_id = {grant.tool_id: grant for grant in registry}
    effective: list[ToolGrant] = []
    for requested in agent_tools:
        offered = by_id.get(requested.tool_id)
        if offered is None:
            continue
        if requested.execution != "inline" or offered.execution != "inline":
            continue
        if requested.side_effecting or offered.side_effecting:
            continue
        effective.append(offered)
    return effective
