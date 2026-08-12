"""Connector interface.

A connector adapts one provider to the gateway: it declares a manifest (tools
with per-tool effect classes — the hashable source of truth for gating) and
executes calls with an injected credential. Connectors are the ONLY code that
ever sees a secret value besides the fill sidecar lease path, and they receive
it per-call from the gateway — never stored, never returned.

Managed connectors are deliberately thin allowlisted surfaces, not full API
mirrors: every tool added here is a tool an agent can be granted, so the
default posture is a small wedge-driven set that grows by explicit decision.
"""

from __future__ import annotations

import abc
from typing import Any, Dict, Optional, Type

import httpx

from ..schema import ConnectionManifest


class ConnectorError(Exception):
    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class Connector(abc.ABC):
    provider: str = ""

    # Directory metadata: how the console presents this provider before any
    # connection exists. Code connectors are platform-scoped by definition.
    display_name: str = ""
    description: str = ""
    credential_label: str = "Credential"
    credential_placeholder: str = ""
    credential_multiline: bool = False
    credential_steps: tuple = ()

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.config = config or {}
        self._transport = transport

    def _client(self, **kwargs) -> httpx.AsyncClient:
        # The gateway uses this internal-only hook to pass an idempotency key
        # through the normal connector transport to a provider-shaped
        # sandbox. Production connection configuration never needs it.
        request_headers = self.config.get("_requestHeaders") or {}
        if request_headers:
            kwargs["headers"] = {**request_headers, **(kwargs.get("headers") or {})}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        kwargs.setdefault("timeout", 30.0)
        return httpx.AsyncClient(**kwargs)

    def _config_url(self, default: str, *keys: str) -> str:
        """Resolve an optional provider endpoint override.

        Production connections use the provider default. Hermetic/rehearsal
        connections can point the same connector implementation at a Convoy
        sandbox without replacing its transport or tool semantics.
        """
        for key in keys:
            configured = self.config.get(key)
            if configured:
                return str(configured).rstrip("/")
        return default.rstrip("/")

    @abc.abstractmethod
    async def manifest(self, credential: Optional[str] = None) -> ConnectionManifest: ...

    @abc.abstractmethod
    async def invoke(self, tool: str, args: Dict[str, Any], credential: str) -> Any: ...

    async def verify_credential(self, credential: str) -> Optional[bool]:
        """Best-effort, side-effect-free probe of a credential. Returns True
        on a positive check, raises ConnectorError on a definite failure, and
        returns None when the provider has no cheap safe probe (the console
        then reports the credential as stored-but-unverified)."""
        return None


_REGISTRY: Dict[str, Type[Connector]] = {}


def register(cls: Type[Connector]) -> Type[Connector]:
    _REGISTRY[cls.provider] = cls
    return cls


def registered_connectors() -> Dict[str, Type[Connector]]:
    """The code connectors, keyed by provider, for the console directory."""
    return dict(_REGISTRY)


def get_connector(
    provider: str,
    config: Optional[Dict[str, Any]] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> Connector:
    try:
        cls = _REGISTRY[provider]
    except KeyError:
        raise ConnectorError("unknown connector provider: %s" % provider)
    return cls(config=config, transport=transport)


async def raise_for_status(resp: httpx.Response, provider: str) -> None:
    if resp.status_code == 401:
        raise ConnectorError("%s: credential rejected (needs_reauth)" % provider)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise ConnectorError(
            "%s: upstream unavailable (%d)" % (provider, resp.status_code), retryable=True
        )
    if resp.status_code >= 400:
        raise ConnectorError("%s: %d %s" % (provider, resp.status_code, resp.text[:300]))
