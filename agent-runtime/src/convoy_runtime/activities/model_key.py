"""provision_model_key activity — per-run virtual key at run start.

Creates the run's LiteLLM virtual key with a hard budget cap equal to the
run's dollar cap, before any turn executes. The key value is derived
worker-side from the proxy master key, so no secret ever crosses a Temporal
boundary; the activity returns nothing. Idempotent across retries.

Deployments without a model gateway (scripted-only) configure no provider and
the activity is a no-op.
"""

from decimal import Decimal

from temporalio import activity

from convoy_runtime.activities import names
from convoy_runtime.providers.model_keys import LiteLLMKeyProvider


class ModelKeyActivities:
    def __init__(self, provider: LiteLLMKeyProvider | None) -> None:
        self._provider = provider

    @activity.defn(name=names.PROVISION_MODEL_KEY)
    async def provision_model_key(self, run_id: str, cap_usd: Decimal) -> None:
        if self._provider is None:
            return
        await self._provider.ensure_run_key(run_id, cap_usd)
