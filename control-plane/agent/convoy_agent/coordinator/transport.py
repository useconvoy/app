"""Bounded JSON HTTP; no redirects or implicit inference retries."""

from __future__ import annotations

import http.client
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request


class RemoteError(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"remote HTTP {status}")


class TransportError(Exception):
    """A bounded local classification; never retains endpoint or response text."""

    def __init__(self, category: str):
        if category not in {"deadline", "transport", "protocol"}:
            raise ValueError("unsupported transport failure category")
        self.category = category
        super().__init__(category)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JsonHTTP:
    def __init__(self, base_url: str, token: str, *, timeout_s: float = 3, ca_file: str | None = None):
        parts = urllib.parse.urlsplit(base_url)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("endpoint must not contain credentials, query or fragment")
        if parts.scheme != "https" and not (
            parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("HTTP is allowed only for the local simulator; use HTTPS elsewhere")
        self.base_url, self.token, self.timeout_s = base_url.rstrip("/"), token, timeout_s
        context = ssl.create_default_context()
        if ca_file:
            context.load_verify_locations(cafile=ca_file)
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def call(self, method: str, path: str, payload=None, *, token=None, timeout_s=None):
        body = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        request = urllib.request.Request(
            self.base_url + path, data=body, method=method,
            headers={"Authorization": "Bearer " + (self.token if token is None else token),
                     "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=timeout_s or self.timeout_s) as response:
                declared = response.headers.get("Content-Length")
                length = int(declared) if declared is not None else None
                if length is not None and not 0 <= length <= 131072:
                    raise TransportError("protocol")
                content = response.read(131073)
                if len(content) > 131072 or (length is not None and len(content) != length):
                    raise TransportError("protocol")
                return json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        except urllib.error.HTTPError as error:
            error.close()
            raise RemoteError(error.code) from None
        except (TimeoutError, urllib.error.URLError) as error:
            timed_out = isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError)
            raise TransportError("deadline" if timed_out else "transport") from None
        except OSError:
            raise TransportError("transport") from None
        except (ValueError, http.client.HTTPException):
            raise TransportError("protocol") from None

    def get(self, path: str):
        return self.call("GET", path)

    def post(self, path: str, payload: dict):
        return self.call("POST", path, payload)


class WorkerHTTP(JsonHTTP):
    def probe(self, release_digest: str, profile: str) -> dict:
        return self.post("/v1/probe", {"release_digest": release_digest, "profile": profile})

    def start_session(self, identity: dict, grant: str) -> dict:
        return self.call("POST", "/v1/sessions/start", {"identity": identity}, token=grant)

    def end_session(self, identity: dict, grant: str) -> dict:
        return self.call("POST", "/v1/sessions/end", {"identity": identity}, token=grant)

    def decide(self, request: dict, grant: str) -> dict:
        return self.call("POST", "/v1/decisions", request, token=grant, timeout_s=request["budget_ms"] / 1000)


class PlannerHTTP(WorkerHTTP):
    def propose(self, request: dict, grant: str) -> dict:
        return self.call("POST", "/v1/plans", request, token=grant, timeout_s=request["budget_ms"] / 1000)
