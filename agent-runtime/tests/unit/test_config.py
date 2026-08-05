"""Config loading honors the stack env-var contract: the infra-injected names
win, the legacy local spellings still work, and the codec key accepts both."""

import json

import pytest
from _support.common import TEST_CODEC_KEY, TEST_CODEC_KEY_B64
from temporalio.service import TLSConfig

from convoy_runtime.config import RuntimeConfig

TEST_CERT_PEM = "-----BEGIN CERTIFICATE-----\ntest-cert\n-----END CERTIFICATE-----"
TEST_KEY_PEM = "-----BEGIN PRIVATE KEY-----\ntest-key\n-----END PRIVATE KEY-----"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TEMPORAL_ADDRESS",
        "TEMPORAL_NAMESPACE",
        "TEMPORAL_TASK_QUEUE",
        "TEMPORAL_WORKER_BUILD_ID",
        "TEMPORAL_TLS_CERT_PEM",
        "TEMPORAL_TLS_KEY_PEM",
        "DATABASE_URL",
        "CONVOY_TEMPORAL_HOST",
        "CONVOY_TEMPORAL_NAMESPACE",
        "CONVOY_TASK_QUEUE",
        "CONVOY_PG_DSN",
        "CONVOY_ARTIFACT_BUCKET",
        "CONVOY_S3_BUCKET",
        "CONVOY_CODEC_KEY",
        "CONVOY_CODEC_KEY_B64",
        "CONVOY_MODEL_GATEWAY",
        "CONVOY_TURN_EXECUTOR",
        "CONVOY_SANDBOX_PROVIDER",
        "CONVOY_SANDBOX_CLUSTER",
        "CONVOY_SANDBOX_TASK_FAMILY",
        "CONVOY_SANDBOX_SUBNETS",
        "CONVOY_SANDBOX_SECURITY_GROUP",
        "LITELLM_BASE_URL",
        "LITELLM_MASTER_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVOY_CODEC_KEY_B64", TEST_CODEC_KEY_B64)


def test_infra_names_take_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMPORAL_ADDRESS", "stack.temporal.example:7233")
    monkeypatch.setenv("CONVOY_TEMPORAL_HOST", "legacy:7233")
    monkeypatch.setenv("TEMPORAL_NAMESPACE", "stack-ns")
    monkeypatch.setenv("TEMPORAL_TASK_QUEUE", "stack-queue")
    monkeypatch.setenv("DATABASE_URL", "postgresql://stack@db/convoy")
    monkeypatch.setenv("CONVOY_PG_DSN", "postgresql://legacy@db/convoy")
    monkeypatch.setenv("CONVOY_ARTIFACT_BUCKET", "stack-artifacts")
    monkeypatch.setenv("CONVOY_S3_BUCKET", "legacy-artifacts")
    config = RuntimeConfig.from_env()
    assert config.temporal_host == "stack.temporal.example:7233"
    assert config.temporal_namespace == "stack-ns"
    assert config.task_queue == "stack-queue"
    assert config.pg_dsn == "postgresql://stack@db/convoy"
    assert config.s3_bucket == "stack-artifacts"


def test_legacy_names_still_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONVOY_TEMPORAL_HOST", "legacy:7233")
    monkeypatch.setenv("CONVOY_PG_DSN", "postgresql://legacy@db/convoy")
    monkeypatch.setenv("CONVOY_S3_BUCKET", "legacy-artifacts")
    config = RuntimeConfig.from_env()
    assert config.temporal_host == "legacy:7233"
    assert config.pg_dsn == "postgresql://legacy@db/convoy"
    assert config.s3_bucket == "legacy-artifacts"


def test_codec_key_accepts_legacy_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONVOY_CODEC_KEY_B64", raising=False)
    monkeypatch.setenv("CONVOY_CODEC_KEY", TEST_CODEC_KEY_B64)
    assert RuntimeConfig.from_env().codec_key == TEST_CODEC_KEY


def test_gateway_config_parses_from_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CONVOY_MODEL_GATEWAY",
        json.dumps(
            {
                "endpoints": {},
                "approved_models": ["m1", "m2"],
                "fallback_chains": {"m1": ["m2"]},
            }
        ),
    )
    config = RuntimeConfig.from_env()
    assert config.model_gateway.approved_models == ["m1", "m2"]
    assert config.model_gateway.fallback_chains == {"m1": ["m2"]}


def test_default_gateway_matches_compose_models() -> None:
    config = RuntimeConfig.from_env()
    assert config.model_gateway.approved_models == ["mock-primary", "mock-fallback"]
    assert config.turn_executor == "scripted"


def test_invalid_executor_choice_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONVOY_TURN_EXECUTOR", "surprise")
    with pytest.raises(RuntimeError, match="CONVOY_TURN_EXECUTOR"):
        RuntimeConfig.from_env()


def test_worker_versioning_off_without_build_id() -> None:
    assert RuntimeConfig.from_env().temporal_worker_build_id == ""


def test_worker_build_id_consumed_from_deploy_pipeline_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEMPORAL_WORKER_BUILD_ID", "abc123def456")
    assert RuntimeConfig.from_env().temporal_worker_build_id == "abc123def456"


def test_temporal_connection_is_plaintext_without_tls_pems() -> None:
    assert RuntimeConfig.from_env().temporal_tls is False


def test_temporal_mtls_built_from_pem_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMPORAL_TLS_CERT_PEM", TEST_CERT_PEM)
    monkeypatch.setenv("TEMPORAL_TLS_KEY_PEM", TEST_KEY_PEM)
    tls = RuntimeConfig.from_env().temporal_tls
    assert isinstance(tls, TLSConfig)
    assert tls.client_cert == TEST_CERT_PEM.encode()
    assert tls.client_private_key == TEST_KEY_PEM.encode()


@pytest.mark.parametrize("present", ["TEMPORAL_TLS_CERT_PEM", "TEMPORAL_TLS_KEY_PEM"])
def test_half_configured_mtls_pair_fails_fast(
    monkeypatch: pytest.MonkeyPatch, present: str
) -> None:
    monkeypatch.setenv(present, TEST_CERT_PEM)
    with pytest.raises(RuntimeError, match="must be set together"):
        RuntimeConfig.from_env()


def test_sandbox_provider_defaults_to_local() -> None:
    config = RuntimeConfig.from_env()
    assert config.sandbox.provider == "local"
    assert config.sandbox_cluster == ""
    assert config.sandbox_subnets == ()


def test_sandbox_provider_ecs_reads_stack_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONVOY_SANDBOX_PROVIDER", "ecs")
    monkeypatch.setenv("CONVOY_SANDBOX_CLUSTER", "convoy-acme")
    monkeypatch.setenv("CONVOY_SANDBOX_TASK_FAMILY", "convoy-acme-sandbox")
    monkeypatch.setenv("CONVOY_SANDBOX_SUBNETS", "subnet-a, subnet-b")
    monkeypatch.setenv("CONVOY_SANDBOX_SECURITY_GROUP", "sg-123")
    config = RuntimeConfig.from_env()
    assert config.sandbox.provider == "ecs"
    assert config.sandbox_cluster == "convoy-acme"
    assert config.sandbox_task_family == "convoy-acme-sandbox"
    assert config.sandbox_subnets == ("subnet-a", "subnet-b")
    assert config.sandbox_security_group == "sg-123"


def test_unknown_sandbox_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONVOY_SANDBOX_PROVIDER", "e2b_hosted")
    with pytest.raises(RuntimeError, match="CONVOY_SANDBOX_PROVIDER"):
        RuntimeConfig.from_env()
