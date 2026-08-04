import pytest
from convoy_core.binding import EnvironmentBinding, ToolGrant


def _binding():
    return EnvironmentBinding(
        id="env_a@3", tenant_id="ws_1", kind="production",
        tool_registry=[ToolGrant(tool="slack.post_message", connection_id="c1",
                                 effect_class="gated")],
        connector_endpoints={"c1": "https://gw.convoy.internal/gateway/mcp/c1"},
        credential_scope="convoy-gateway:run-jwt:env_a@3",
        data_namespace="ws_1/env_a", sandbox_template="e2b-v3",
    )


def test_binding_is_frozen():
    b = _binding()
    with pytest.raises(Exception):
        b.kind = "sandbox"
    with pytest.raises(Exception):
        b.tool_registry[0].effect_class = "read"


def test_binding_json_roundtrip():
    b = _binding()
    assert EnvironmentBinding.model_validate(b.model_dump(mode="json")) == b


def test_unknown_fields_rejected():
    raw = _binding().model_dump(mode="json")
    raw["surprise"] = True
    with pytest.raises(Exception):
        EnvironmentBinding.model_validate(raw)
