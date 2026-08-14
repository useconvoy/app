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
import os
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

    # Hosted install (OAuth) metadata. Providers that support installing
    # the platform's app into a customer account declare the consent
    # screen, the exchange endpoint, and the scopes their tools need; the
    # console shows the install button only when this service also holds
    # the provider app's credentials (each connector reads its own from
    # this service's environment). Empty means paste-only.
    oauth_authorize_url: str = ""
    oauth_token_url: str = ""
    oauth_scopes: tuple = ()
    # How the authorize URL wants its scope list joined, and any extra
    # fixed query parameters the provider's consent screen requires.
    oauth_scope_delimiter: str = ","
    oauth_extra_params: dict = {}

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

    @classmethod
    def oauth_client_env(cls) -> tuple:
        """The provider app's client id and secret from this service's
        environment, or (None, None) while unconfigured. GitHub overrides
        the whole advert instead; most OAuth providers use this pair."""
        prefix = "CONVOY_OAUTH_%s_" % cls.provider.upper()
        client_id = os.environ.get(prefix + "CLIENT_ID", "")
        client_secret = os.environ.get(prefix + "CLIENT_SECRET", "")
        if not client_id or not client_secret:
            return (None, None)
        return (client_id, client_secret)

    @classmethod
    def oauth_directory_entry(cls) -> Optional[Dict[str, Any]]:
        """The public pieces of the hosted install for the provider
        directory, or None while the connector declares no install or the
        service holds no app credentials. Never includes any secret."""
        if not cls.oauth_authorize_url:
            return None
        client_id, _ = cls.oauth_client_env()
        if not client_id:
            return None
        entry: Dict[str, Any] = {
            "authorizeUrl": cls.oauth_authorize_url,
            "clientId": client_id,
            "scopes": list(cls.oauth_scopes),
        }
        if cls.oauth_scope_delimiter != ",":
            entry["scopeDelimiter"] = cls.oauth_scope_delimiter
        if cls.oauth_extra_params:
            entry["extraParams"] = dict(cls.oauth_extra_params)
        return entry

    async def exchange_oauth_code(self, code: str, redirect_uri: str) -> Dict[str, str]:
        """Exchange a hosted-install authorization code for the connection
        credential, using the app credentials this service holds. Returns
        {"secretValue": ..., "detail": ...} where detail is a plain phrase
        for the audit row (the installed workspace's name, say). Providers
        without a hosted install keep the default."""
        raise ConnectorError("%s has no hosted install" % (self.provider or "provider"))


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
