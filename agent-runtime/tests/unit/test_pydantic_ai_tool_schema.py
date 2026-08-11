"""Runtime-owned model tool schemas must reject no-op sandbox calls."""

from convoy_core import PermissionScope, ToolGrant
from convoy_runtime.providers.pydantic_ai_turn import PydanticAITurnExecutor


def _grant(tool_id: str) -> ToolGrant:
    return ToolGrant(
        tool_id=tool_id,
        scope=PermissionScope(resource="workspace", actions=["execute"]),
        execution="promoted",
        side_effecting=True,
    )


def test_sandbox_exec_requires_a_nonempty_command() -> None:
    schema = PydanticAITurnExecutor._tool_schema(_grant("sandbox_exec"))
    assert schema["required"] == ["command"]
    assert schema["properties"]["command"]["minItems"] == 1
    assert schema["additionalProperties"] is False


def test_connector_schema_stays_open_until_registry_schemas_land() -> None:
    assert PydanticAITurnExecutor._tool_schema(_grant("slack.post_message")) == {
        "type": "object",
        "additionalProperties": True,
    }
