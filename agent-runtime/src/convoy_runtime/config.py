"""Runtime configuration for workers and the control plane.

Read from the environment at process start. Workflow code never touches this
module (no env reads in workflows/ — CLAUDE.md rule 1). In production these
values resolve from `DeploymentProfile`; TODO(milestone-5): profile-driven
resolution via the Terraform-stamped stack config.
"""

import os
from dataclasses import dataclass

from convoy_runtime.codec import codec_key_from_env

TASK_QUEUE = "agent-runtime"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class RuntimeConfig:
    temporal_host: str
    temporal_namespace: str
    task_queue: str
    pg_dsn: str
    s3_endpoint_url: str
    s3_region: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    stub_env_url: str
    dev_token: str
    codec_key: bytes
    scripted_turn_delay_seconds: float

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            temporal_host=_env("CONVOY_TEMPORAL_HOST", "localhost:7233"),
            temporal_namespace=_env("CONVOY_TEMPORAL_NAMESPACE", "default"),
            task_queue=_env("CONVOY_TASK_QUEUE", TASK_QUEUE),
            pg_dsn=_env(
                "CONVOY_PG_DSN",
                "postgresql://convoy_app:convoy_app@localhost:5433/convoy",
            ),
            s3_endpoint_url=_env("CONVOY_S3_ENDPOINT", "http://localhost:9000"),
            s3_region=_env("CONVOY_S3_REGION", "us-east-1"),
            s3_bucket=_env("CONVOY_S3_BUCKET", "convoy-artifacts"),
            s3_access_key=_env("CONVOY_S3_ACCESS_KEY", "convoy"),
            s3_secret_key=_env("CONVOY_S3_SECRET_KEY", "convoy-secret-key"),
            stub_env_url=_env("CONVOY_STUB_ENV_URL", "http://localhost:8902"),
            # M0 auth stub: static bearer token. TODO(milestone-5): WorkOS OIDC at the edge.
            dev_token=_env("CONVOY_DEV_TOKEN", "dev-token"),
            codec_key=codec_key_from_env(),
            # Test knob for e2e determinism (0 in production paths).
            scripted_turn_delay_seconds=float(_env("CONVOY_SCRIPTED_TURN_DELAY", "0")),
        )
