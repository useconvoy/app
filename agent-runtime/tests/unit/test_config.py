"""Config loading honors the stack env-var contract: the infra-injected names
win, the legacy local spellings still work, and the codec key accepts both."""

import json

import pytest
from _support.common import TEST_CODEC_KEY, TEST_CODEC_KEY_B64

from convoy_runtime.config import RuntimeConfig


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TEMPORAL_ADDRESS",
        "TEMPORAL_NAMESPACE",
        "TEMPORAL_TASK_QUEUE",
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
