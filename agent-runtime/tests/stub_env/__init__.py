"""Stub environments/ implementation for tests.

The binding fixture used by test layers, mirroring what the compose stub-env
container serves (agent-runtime/docker/stub-env/server.py).
TODO: side-effect journal backing the chaos suite.
"""

from convoy_core import EnvironmentBinding


def stub_binding(
    environment_id: str = "stub-local", tenant_id: str = "tenant-test"
) -> EnvironmentBinding:
    return EnvironmentBinding(
        id=environment_id,
        tenant_id=tenant_id,
        kind="sandbox",
        tool_registry=[],
        connector_endpoints={},
        credential_scope="stub:no-credentials",
        data_namespace=f"{tenant_id}/stub",
        sandbox_template="stub",
    )
