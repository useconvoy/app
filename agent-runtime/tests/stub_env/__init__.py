"""Stub environments/ implementation for tests (TESTING.md section 3).

M0 scope: the binding fixture used by test layers, mirroring what the compose
stub-env container serves (agent-runtime/docker/stub-env/server.py).
TODO(milestone-1): stub tool registry entries for grant-intersection tests.
TODO(milestone-4): side-effect journal backing the chaos suite.
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
