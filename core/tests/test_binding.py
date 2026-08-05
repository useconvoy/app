"""Sanity on the merged DESIGN §5 seam types as the environments registry
consumes them (the types themselves are runtime-transcribed; deep coverage
lives in agent-runtime's suites)."""

from convoy_core import ClockConfig, EnvironmentBinding, PermissionScope, ToolGrant


def test_binding_roundtrip_with_clock_and_scope():
    binding = EnvironmentBinding(
        id="env_a@3/production", tenant_id="ws_1", kind="production",
        tool_registry=[ToolGrant(tool_id="slack.post_message",
                                 scope=PermissionScope(resource="connector:c1", actions=["write"]),
                                 execution="promoted", side_effecting=True)],
        connector_endpoints={"data_plane": "https://gw.convoy.internal/gateway/data-plane/env_a/3"},
        credential_scope="convoy-gateway:run-jwt:env_a@3",
        data_namespace="ws_1/env_a", sandbox_template="sbx-v3",
        clock=ClockConfig(mode="virtual", advance="on_idle"),
    )
    assert EnvironmentBinding.model_validate(binding.model_dump(mode="json")) == binding


def test_tool_grant_defaults_match_design_s5():
    grant = ToolGrant(tool_id="kb_lookup",
                      scope=PermissionScope(resource="kb:*", actions=["read"]),
                      execution="inline")
    assert grant.side_effecting is False
    assert ClockConfig().mode == "real"
