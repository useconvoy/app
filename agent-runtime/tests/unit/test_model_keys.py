"""Per-run virtual key derivation: deterministic, run-scoped, never trivially
guessable from the run id alone."""

from convoy_runtime.providers.model_keys import derive_run_key


def test_derivation_is_deterministic() -> None:
    assert derive_run_key("master", "run-1") == derive_run_key("master", "run-1")


def test_derivation_varies_by_run_and_master() -> None:
    keys = {
        derive_run_key("master", "run-1"),
        derive_run_key("master", "run-2"),
        derive_run_key("other-master", "run-1"),
    }
    assert len(keys) == 3


def test_key_shape_meets_proxy_requirements() -> None:
    key = derive_run_key("master", "run-1")
    assert key.startswith("sk-run-")
    # Proxy requires at least 16 characters; ours is prefix + 40 hex chars.
    assert len(key) == len("sk-run-") + 40


def test_key_does_not_embed_the_run_id_or_master_key() -> None:
    key = derive_run_key("super-secret-master", "run-visible-id")
    assert "run-visible-id" not in key.removeprefix("sk-run-")
    assert "super-secret-master" not in key
