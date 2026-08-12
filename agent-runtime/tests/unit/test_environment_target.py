from convoy_runtime.config import RuntimeConfig
from convoy_runtime.control_plane.app import _environment_registry_target


def test_production_environment_reference_is_unchanged() -> None:
    assert _environment_registry_target("env_prod") == ("env_prod", "production")


def test_rehearsal_alias_selects_the_sandbox_binding() -> None:
    assert _environment_registry_target("env_prod/sandbox") == ("env_prod", "sandbox")


def test_empty_sandbox_prefix_is_not_treated_as_an_environment() -> None:
    assert _environment_registry_target("/sandbox") == ("/sandbox", "production")


def test_real_environment_registry_token_is_loaded(monkeypatch) -> None:
    monkeypatch.setenv("CONVOY_CODEC_KEY_B64", "Y29udm95LWRldi1jb2RlYy1rZXktMzItYnl0ZXMhISE=")
    monkeypatch.setenv("CONVOY_ENVIRONMENTS_INTERNAL_TOKEN", "registry-token")
    assert RuntimeConfig.from_env().environments_internal_token == "registry-token"
