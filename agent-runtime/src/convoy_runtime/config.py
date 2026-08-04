"""Runtime configuration for workers and the control plane.

Read from the environment at process start; workflow code never touches this
module. Variable names follow the stack contract the infrastructure module
injects into every service (TEMPORAL_*, DATABASE_URL, LITELLM_*, CONVOY_*);
the older CONVOY_-prefixed spellings remain as fallbacks for local tooling.
"""

import json
import os
from dataclasses import dataclass
from typing import Literal

from convoy_core import ModelGatewayConfig
from convoy_runtime.carry import (
    DEFAULT_MIDSTEP_COMPACTION_TOKENS,
    DEFAULT_TURN_LIMIT,
)
from convoy_runtime.codec import codec_key_from_env

TASK_QUEUE = "agent-runtime"

# Default gateway shape matches the local compose stack: both models route to
# the LiteLLM proxy, which maps them onto the mock-model container. Real
# deployments override this with CONVOY_MODEL_GATEWAY (a full JSON config).
_DEFAULT_GATEWAY: dict[str, object] = {
    "endpoints": {},
    "approved_models": ["mock-primary", "mock-fallback"],
    "fallback_chains": {"mock-primary": ["mock-fallback"]},
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_first(*names: str, default: str) -> str:
    """First set variable wins; lets the infra names take precedence over the
    legacy local spellings."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


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
    turn_executor: Literal["scripted", "pydantic_ai"]
    default_model: str
    litellm_base_url: str
    litellm_master_key: str
    model_gateway: ModelGatewayConfig
    turn_limit: int
    midstep_compaction_tokens: int
    sandbox_dir: str
    promoted_tool_delay_seconds: float

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        executor = _env("CONVOY_TURN_EXECUTOR", "scripted")
        if executor not in ("scripted", "pydantic_ai"):
            raise RuntimeError(
                f"CONVOY_TURN_EXECUTOR must be 'scripted' or 'pydantic_ai', got {executor!r}"
            )
        gateway_json = _env("CONVOY_MODEL_GATEWAY", "")
        gateway_raw = json.loads(gateway_json) if gateway_json else _DEFAULT_GATEWAY
        return cls(
            temporal_host=_env_first(
                "TEMPORAL_ADDRESS", "CONVOY_TEMPORAL_HOST", default="localhost:7233"
            ),
            temporal_namespace=_env_first(
                "TEMPORAL_NAMESPACE", "CONVOY_TEMPORAL_NAMESPACE", default="default"
            ),
            task_queue=_env_first("TEMPORAL_TASK_QUEUE", "CONVOY_TASK_QUEUE", default=TASK_QUEUE),
            pg_dsn=_env_first(
                "DATABASE_URL",
                "CONVOY_PG_DSN",
                default="postgresql://convoy_app:convoy_app@localhost:5433/convoy",
            ),
            s3_endpoint_url=_env("CONVOY_S3_ENDPOINT", "http://localhost:9000"),
            s3_region=_env("CONVOY_S3_REGION", "us-east-1"),
            s3_bucket=_env_first(
                "CONVOY_ARTIFACT_BUCKET", "CONVOY_S3_BUCKET", default="convoy-artifacts"
            ),
            s3_access_key=_env("CONVOY_S3_ACCESS_KEY", "convoy"),
            s3_secret_key=_env("CONVOY_S3_SECRET_KEY", "convoy-secret-key"),
            stub_env_url=_env("CONVOY_STUB_ENV_URL", "http://localhost:8902"),
            # Static bearer token for local auth. TODO: WorkOS OIDC at the
            # edge, with the tenant derived from the verified identity.
            dev_token=_env("CONVOY_DEV_TOKEN", "dev-token"),
            codec_key=codec_key_from_env(),
            # Test knob for e2e determinism (0 in production paths).
            scripted_turn_delay_seconds=float(_env("CONVOY_SCRIPTED_TURN_DELAY", "0")),
            turn_executor=executor,
            default_model=_env("CONVOY_DEFAULT_MODEL", "scripted-echo-1"),
            litellm_base_url=_env("LITELLM_BASE_URL", ""),
            litellm_master_key=_env("LITELLM_MASTER_KEY", ""),
            model_gateway=ModelGatewayConfig.model_validate(gateway_raw),
            # Durability tuning delivered to workflows as recorded input, so
            # replay always sees the limits the run actually started with.
            turn_limit=int(_env("CONVOY_TURN_LIMIT", str(DEFAULT_TURN_LIMIT))),
            midstep_compaction_tokens=int(
                _env("CONVOY_MIDSTEP_COMPACTION_TOKENS", str(DEFAULT_MIDSTEP_COMPACTION_TOKENS))
            ),
            # Workspace root for the local sandbox provider; workspaces are
            # cache, snapshots in the artifact store are truth.
            sandbox_dir=_env("CONVOY_SANDBOX_DIR", "/tmp/convoy-sandboxes"),
            # Test knob widening the crash window after a promoted side
            # effect lands (0 in production paths).
            promoted_tool_delay_seconds=float(_env("CONVOY_PROMOTED_TOOL_DELAY", "0")),
        )
