"""Sandbox substrate pure types.

The `SandboxProvider` Protocol lives in the runtime (`convoy_runtime.providers`);
these are the data types it exchanges.
"""

from datetime import timedelta

from pydantic import BaseModel

from convoy_core.artifacts import ArtifactRef


class SandboxHandle(BaseModel):
    sandbox_id: str
    provider: str
    template: str


class SandboxJob(BaseModel):
    # hash(run_id, step_id, turn, call_index); retries must not double side effects
    idempotency_key: str
    command: list[str]
    env: dict[str, str] = {}
    inputs: list[ArtifactRef] = []
    timeout: timedelta | None = None


class SandboxJobResult(BaseModel):
    exit_code: int
    stdout_ref: ArtifactRef | None = None
    stderr_ref: ArtifactRef | None = None
    outputs: list[ArtifactRef] = []
