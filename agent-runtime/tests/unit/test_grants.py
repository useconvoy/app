"""Grant validation and intersection: inline tools must be read-only, unknown
requests fail fast, effective tools are the two-sided intersection, and
delegation to a subagent can only ever narrow what the parent holds."""

import pytest

from convoy_core import PermissionScope, ToolGrant
from convoy_runtime.providers.grants import (
    GrantValidationError,
    effective_inline_tools,
    intersect_grants,
    resolve_requested_tools,
    validate_grants,
)


def grant(
    tool_id: str,
    *,
    execution: str = "inline",
    side_effecting: bool = False,
    resource: str = "kb:*",
) -> ToolGrant:
    return ToolGrant.model_validate(
        {
            "tool_id": tool_id,
            "scope": PermissionScope(resource=resource, actions=["read"]),
            "execution": execution,
            "side_effecting": side_effecting,
        }
    )


REGISTRY = [
    grant("kb_lookup"),
    grant("kb_search"),
    grant("kb_delete", execution="promoted", side_effecting=True),
    grant("audit_write", execution="inline", side_effecting=True),  # invalid combo
]


def test_validate_rejects_inline_side_effecting() -> None:
    with pytest.raises(GrantValidationError, match="side-effecting"):
        validate_grants([grant("audit_write", side_effecting=True)])


def test_validate_allows_promoted_side_effecting() -> None:
    validate_grants([grant("kb_delete", execution="promoted", side_effecting=True)])


def test_resolve_requested_tools_maps_registry_entries() -> None:
    grants = resolve_requested_tools(["kb_lookup", "kb_search"], REGISTRY)
    assert [g.tool_id for g in grants] == ["kb_lookup", "kb_search"]
    assert all(g.execution == "inline" and not g.side_effecting for g in grants)


def test_resolve_requested_tools_rejects_unknown() -> None:
    with pytest.raises(GrantValidationError, match="unknown tool"):
        resolve_requested_tools(["kb_lookup", "not_a_tool"], REGISTRY)


def test_resolve_requested_tools_rejects_invalid_registry_grant() -> None:
    # The registry offers it, but inline + side-effecting can never be valid.
    with pytest.raises(GrantValidationError, match="audit_write"):
        resolve_requested_tools(["audit_write"], REGISTRY)


def test_effective_inline_tools_is_the_intersection() -> None:
    agent_tools = [grant("kb_lookup"), grant("nonexistent")]
    effective = effective_inline_tools(agent_tools, REGISTRY)
    assert [g.tool_id for g in effective] == ["kb_lookup"]


def test_effective_inline_tools_excludes_promoted_and_side_effecting() -> None:
    # Agent asks for everything; only clean inline registry entries survive.
    agent_tools = [
        grant("kb_lookup"),
        grant("kb_search"),
        grant("kb_delete"),  # registry says promoted+side-effecting
        grant("audit_write"),  # registry says side-effecting
    ]
    effective = effective_inline_tools(agent_tools, REGISTRY)
    assert [g.tool_id for g in effective] == ["kb_lookup", "kb_search"]


def test_effective_inline_tools_respects_agent_side_declaration() -> None:
    # The agent's own grant marks the tool promoted; it must not run inline.
    agent_tools = [grant("kb_lookup", execution="promoted")]
    assert effective_inline_tools(agent_tools, REGISTRY) == []


def test_intersect_grants_drops_tools_the_parent_lacks() -> None:
    parent = [grant("kb_lookup"), grant("kb_search")]
    requested = [grant("kb_search"), grant("kb_delete"), grant("payments_send")]
    delegated = intersect_grants(requested, parent)
    assert [g.tool_id for g in delegated] == ["kb_search"]


def test_intersect_grants_parent_entry_is_authoritative() -> None:
    # The child re-describes a tool as a harmless inline read; the parent's
    # promoted side-effecting grant is what gets delegated — a child can
    # never widen (or re-classify) a capability by asking differently.
    parent = [grant("kb_delete", execution="promoted", side_effecting=True, resource="kb:archive")]
    requested = [grant("kb_delete", execution="inline", side_effecting=False, resource="kb:*")]
    delegated = intersect_grants(requested, parent)
    assert len(delegated) == 1
    assert delegated[0].execution == "promoted"
    assert delegated[0].side_effecting is True
    assert delegated[0].scope.resource == "kb:archive"


def test_intersect_grants_dedupes_repeated_requests() -> None:
    parent = [grant("kb_lookup")]
    requested = [grant("kb_lookup"), grant("kb_lookup")]
    assert [g.tool_id for g in intersect_grants(requested, parent)] == ["kb_lookup"]


def test_intersect_grants_empty_parent_delegates_nothing() -> None:
    assert intersect_grants([grant("kb_lookup")], []) == []
