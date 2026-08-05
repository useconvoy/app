"""EcsSandboxProvider against the shared SandboxProvider contract battery.

This is the stamped-stack manual gate: it needs real AWS (the stack's sandbox
task family, subnets, security group, and artifact bucket) plus ambient AWS
credentials able to RunTask and presign, so it skips unless the stack wiring
variables are present. Point it at a stack with the same variables the worker
consumes — no code edits:

    CONVOY_SANDBOX_CLUSTER=<ecs_cluster_name> \\
    CONVOY_SANDBOX_TASK_FAMILY=<sandbox_task_family> \\
    CONVOY_SANDBOX_SUBNETS=<private_subnet_ids, comma-separated> \\
    CONVOY_SANDBOX_SECURITY_GROUP=<sandbox_security_group_id> \\
    CONVOY_ARTIFACT_BUCKET=<artifact_bucket_name> \\
    AWS_REGION=<stack region> \\
    uv run pytest agent-runtime/tests/contracts/test_ecs_sandbox.py -v

It is the identical battery the local provider passes in the fast lane; only
the harness differs.
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sandbox_battery import SandboxHarness, SandboxProviderBattery

from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.sandbox_ecs import EcsSandboxProvider

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not os.environ.get("CONVOY_SANDBOX_CLUSTER"),
        reason=(
            "ECS sandbox battery is the stamped-stack manual gate: set "
            "CONVOY_SANDBOX_CLUSTER / CONVOY_SANDBOX_TASK_FAMILY / "
            "CONVOY_SANDBOX_SUBNETS / CONVOY_SANDBOX_SECURITY_GROUP / "
            "CONVOY_ARTIFACT_BUCKET plus AWS credentials to run it"
        ),
    ),
]


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required to run the ECS sandbox battery")
    return value


class TestEcsSandboxProvider(SandboxProviderBattery):
    @pytest.fixture
    async def harness(self) -> AsyncIterator[SandboxHarness]:
        store = ArtifactStore(
            bucket=_required("CONVOY_ARTIFACT_BUCKET"),
            region=os.environ.get("CONVOY_S3_REGION")
            or os.environ.get("AWS_REGION")
            or "us-east-1",
        )
        provider = EcsSandboxProvider(
            store,
            cluster=_required("CONVOY_SANDBOX_CLUSTER"),
            task_family=_required("CONVOY_SANDBOX_TASK_FAMILY"),
            subnets=[
                subnet.strip()
                for subnet in _required("CONVOY_SANDBOX_SUBNETS").split(",")
                if subnet.strip()
            ],
            security_group=_required("CONVOY_SANDBOX_SECURITY_GROUP"),
            # Battery artifacts live under their own prefix per invocation so
            # repeated gate runs never collide in the stack bucket.
            key_prefix=f"contract-battery/{uuid.uuid4().hex[:12]}",
        )
        yield SandboxHarness(provider=provider, store=store)
