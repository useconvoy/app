"""Local gateway transport. Gateway identity is checked inside its inference slot."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .protocol import completion_request, parse_decision


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GatewayBackend:
    def __init__(self, url: str):
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"} or
                parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}):
            raise ValueError("planner backend must be the local loopback gateway (or an explicit local tunnel)")
        self.url = url.rstrip("/")
        self.opener = urllib.request.build_opener(NoRedirect())

    def _call(self, path: str, body=None, timeout_s=3):
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        with self.opener.open(req, timeout=timeout_s) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("gateway response exceeds bound")
            return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))

    def inspect(self) -> dict:
        return self._call("/v1/runtime-identity")

    def propose(self, identity: dict, budget_s: float) -> dict:
        result = self._call("/v1/bound-completions", {"request": completion_request(),
                            "runtime_identity": identity, "deadline_s": budget_s}, timeout_s=budget_s)
        if (result.get("convoy", {}).get("runtime_identity") != identity or
                result.get("convoy", {}).get("simulated") is not False):
            raise ValueError("gateway returned a different or simulated runtime identity")
        choices = result.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or choices[0].get("finish_reason") != "stop":
            raise ValueError("planner completion is incomplete or ambiguous")
        return parse_decision(choices[0]["message"]["content"])
