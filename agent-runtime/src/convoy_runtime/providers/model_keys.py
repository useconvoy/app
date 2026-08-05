"""Per-run LiteLLM virtual keys — the infra-side budget backstop.

Every run gets its own proxy key with a hard dollar cap equal to the run's
budget; even if workflow-level accounting failed entirely, the proxy would
stop the spend. The key VALUE is derived deterministically from the proxy
master key and the run id, so any worker can reconstruct it locally — the
secret never rides through Temporal, projections, or logs. Creation is
idempotent: retries and concurrent provisioning converge on the same key.
"""

import hashlib
import hmac
from decimal import Decimal

import httpx


class ModelKeyError(RuntimeError):
    """Virtual key provisioning failed in a way retries may fix."""


def derive_run_key(master_key: str, run_id: str) -> str:
    """Deterministic per-run key value: an HMAC of the run id under the master
    key. 40 hex chars keeps it comfortably above the proxy's minimum length."""
    digest = hmac.new(
        master_key.encode(), f"convoy-run:{run_id}".encode(), hashlib.sha256
    ).hexdigest()
    return f"sk-run-{digest[:40]}"


class LiteLLMKeyProvider:
    def __init__(
        self,
        *,
        base_url: str,
        master_key: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not base_url or not master_key:
            raise ValueError("LiteLLM base URL and master key are both required")
        self._base_url = base_url.rstrip("/")
        self._master_key = master_key
        self._timeout = timeout_seconds
        self._provisioned: set[str] = set()

    def run_key(self, run_id: str) -> str:
        return derive_run_key(self._master_key, run_id)

    async def ensure_run_key(self, run_id: str, cap_usd: Decimal) -> str:
        """Create the run's virtual key with a hard budget cap, if it does not
        already exist. Safe to call repeatedly and from multiple processes."""
        key = self.run_key(run_id)
        if run_id in self._provisioned:
            return key
        headers = {"Authorization": f"Bearer {self._master_key}"}
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            info = await http.get(
                f"{self._base_url}/key/info", params={"key": key}, headers=headers
            )
            if info.status_code == 200:
                self._provisioned.add(run_id)
                return key
            response = await http.post(
                f"{self._base_url}/key/generate",
                headers=headers,
                json={
                    "key": key,
                    "key_alias": f"run-{run_id}",
                    "max_budget": float(cap_usd),
                    "metadata": {"run_id": run_id},
                },
            )
            if response.status_code == 200:
                self._provisioned.add(run_id)
                return key
            # A duplicate means another worker won the race — same derived key.
            if response.status_code == 400 and "already exists" in response.text:
                self._provisioned.add(run_id)
                return key
        raise ModelKeyError(
            f"virtual key provisioning for run {run_id} failed: HTTP {response.status_code}"
        )
