"""Model gateway: chains only permute within approved models, and resolution
rejects anything outside the approved set."""

import pytest

from convoy_core import ModelGatewayConfig
from convoy_runtime.providers.model_gateway import ModelGateway, ModelGatewayError


def config(approved: list[str], chains: dict[str, list[str]] | None = None) -> ModelGatewayConfig:
    return ModelGatewayConfig(endpoints={}, approved_models=approved, fallback_chains=chains or {})


def test_chain_resolution_orders_primary_then_fallbacks() -> None:
    gateway = ModelGateway(config(["a", "b", "c"], {"a": ["b", "c"]}))
    assert gateway.resolve_chain("a") == ["a", "b", "c"]
    assert gateway.resolve_chain("b") == ["b"]


def test_chain_deduplicates_keeping_first_position() -> None:
    gateway = ModelGateway(config(["a", "b"], {"a": ["b", "a", "b"]}))
    assert gateway.resolve_chain("a") == ["a", "b"]


def test_unapproved_model_is_rejected_at_resolution() -> None:
    gateway = ModelGateway(config(["a"]))
    with pytest.raises(ModelGatewayError, match="not in approved_models"):
        gateway.resolve_chain("rogue")


def test_chain_with_unapproved_member_is_rejected_at_construction() -> None:
    with pytest.raises(ModelGatewayError, match="unapproved model"):
        ModelGateway(config(["a"], {"a": ["rogue"]}))


def test_chain_keyed_by_unapproved_model_is_rejected_at_construction() -> None:
    with pytest.raises(ModelGatewayError, match="not an approved model"):
        ModelGateway(config(["a"], {"rogue": ["a"]}))
