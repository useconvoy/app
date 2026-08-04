import pytest
from convoy_core.binding import ClockConfig, EnvironmentBinding, PermissionScope, ToolGrant


def _binding(**overrides):
    kwargs = dict(
        id="env_a@3/production", tenant_id="ws_1", kind="production",
        tool_registry=[ToolGrant(tool_id="slack.post_message",
                                 scope=PermissionScope(connection_id="c1"),
                                 execution="promoted", side_effecting=True)],
        connector_endpoints={"c1": "https://gw.convoy.internal/gateway/mcp/c1"},
        credential_scope="convoy-gateway:run-jwt:env_a@3",
        data_namespace="ws_1/env_a", sandbox_template="sbx-v3",
    )
    kwargs.update(overrides)
    return EnvironmentBinding(**kwargs)


def test_binding_is_frozen():
    b = _binding()
    with pytest.raises(Exception):
        b.kind = "sandbox"
    with pytest.raises(Exception):
        b.tool_registry[0].side_effecting = False


def test_clock_defaults_real_and_sandbox_can_go_virtual():
    assert _binding().clock == ClockConfig(mode="real", advance="manual")
    sandbox = _binding(id="env_a@3/sandbox", kind="sandbox",
                       clock=ClockConfig(mode="virtual", advance="on_idle"))
    assert sandbox.clock.mode == "virtual"


def test_binding_json_roundtrip():
    b = _binding()
    assert EnvironmentBinding.model_validate(b.model_dump(mode="json")) == b


def test_unknown_fields_rejected():
    raw = _binding().model_dump(mode="json")
    raw["surprise"] = True
    with pytest.raises(Exception):
        EnvironmentBinding.model_validate(raw)


def test_tool_grant_shape_matches_design_s5():
    g = ToolGrant(tool_id="crm.update", scope=PermissionScope(connection_id="c9"),
                  execution="inline")
    assert g.side_effecting is False  # default matches DESIGN §5
