"""Model gateway resolution: approved models and policy-constrained fallback.

Model routing is data, not code — a `ModelGatewayConfig` names the approved
models and the per-model fallback chains. Fallback may only permute within the
approved set; a config whose chains reference anything else is rejected at
construction so a bad deploy fails at boot, not mid-run.
"""

from convoy_core import ModelGatewayConfig


class ModelGatewayError(ValueError):
    """A model that is not approved for this deployment, or a bad config."""


class ModelGateway:
    def __init__(self, config: ModelGatewayConfig) -> None:
        approved = set(config.approved_models)
        for primary, chain in config.fallback_chains.items():
            if primary not in approved:
                raise ModelGatewayError(f"fallback chain key {primary!r} is not an approved model")
            rogue = [model for model in chain if model not in approved]
            if rogue:
                raise ModelGatewayError(
                    f"fallback chain for {primary!r} contains unapproved model(s) {rogue!r}; "
                    f"chains may only permute within approved_models"
                )
        self._config = config

    @property
    def approved_models(self) -> list[str]:
        return list(self._config.approved_models)

    def resolve_chain(self, model: str) -> list[str]:
        """The models a turn may try, in order: the requested model, then its
        fallbacks. Every member is approved by construction; duplicates are
        collapsed keeping first position."""
        if model not in self._config.approved_models:
            raise ModelGatewayError(
                f"model {model!r} is not in approved_models {self._config.approved_models!r}"
            )
        chain: list[str] = []
        for candidate in [model, *self._config.fallback_chains.get(model, [])]:
            if candidate not in chain:
                chain.append(candidate)
        return chain
