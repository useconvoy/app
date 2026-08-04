"""Credential fill service — TRUSTED SIDE, never inside the sandbox.

Sandboxes hold no credentials and no run tokens (runtime DESIGN §12). This
service runs beside the gateway in the trusted stack, holds the run token,
and drives the sandbox browser's CDP endpoint FROM OUTSIDE (the sandbox
exposes CDP inward to the stack; `cdp_url` points at it). Flow: a promoted
browser-login tool call reaches this service → it leases the credential from
the gateway (domain allowlist enforced, lease logged with values elided) →
fills the login fields over CDP. Credential values exist in this trusted
process and the page's DOM — never in the sandbox filesystem/env, never in
model context. The response is only {"status": "filled"}.

v1 fills and stops: the agent clicks submit itself via computer use, so the
model observes the outcome without observing the values. Screenshot masking
while a password field is focused is a follow-up.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# (js, cdp_url) → eval result. Injectable so tests (and future non-CDP
# drivers) replace the browser without touching lease logic.
CdpEval = Callable[[str, str], Awaitable[Any]]


async def cdp_eval(js: str, cdp_url: str) -> Any:  # pragma: no cover — needs a live browser
    """Evaluate JS in the first page target of a Chromium CDP endpoint."""
    import websockets

    async with httpx.AsyncClient() as client:
        targets = (await client.get(cdp_url.rstrip("/") + "/json")).json()
    page = next(t for t in targets if t.get("type") == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": js, "awaitPromise": True,
                                             "returnByValue": True}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                if "error" in msg or msg.get("result", {}).get("exceptionDetails"):
                    raise RuntimeError("cdp eval failed: %s" % json.dumps(msg)[:300])
                return msg["result"]["result"].get("value")


def _fill_script(username: str, password: str, username_selector: str, password_selector: str) -> str:
    """Set values the way a user would (native setter + input/change events)
    so framework-bound forms (React et al.) see the change."""
    return """
(() => {
  const setValue = (selector, value) => {
    const el = document.querySelector(selector);
    if (!el) return false;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  };
  const u = setValue(%s, %s);
  const p = setValue(%s, %s);
  return JSON.stringify({usernameFilled: u, passwordFilled: p});
})()
""" % (json.dumps(username_selector), json.dumps(username),
       json.dumps(password_selector), json.dumps(password))


class FillService:
    def __init__(self, gateway_url: str, run_token: str, cdp_url: str = "http://127.0.0.1:9222",
                 evaluator: Optional[CdpEval] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._gateway_url = gateway_url.rstrip("/")
        self._run_token = run_token
        self._cdp_url = cdp_url
        self._eval = evaluator or cdp_eval
        self._transport = transport

    async def _lease(self, domain: str) -> Dict[str, str]:
        kwargs: Dict[str, Any] = {"timeout": 30.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.post(
                self._gateway_url + "/browser/credential-lease",
                json={"domain": domain},
                headers={"Authorization": "Bearer %s" % self._run_token},
            )
        if resp.status_code == 403:
            raise HTTPException(403, resp.json().get("detail", "lease denied"))
        if resp.status_code != 200:
            raise HTTPException(502, "gateway lease failed: %d" % resp.status_code)
        return resp.json()

    async def fill(self, domain: str, username_selector: str, password_selector: str) -> Dict[str, Any]:
        lease = await self._lease(domain)
        script = _fill_script(lease["username"], lease["password"],
                              username_selector, password_selector)
        raw = await self._eval(script, self._cdp_url)
        outcome = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not outcome.get("usernameFilled") or not outcome.get("passwordFilled"):
            raise HTTPException(422, "selectors did not match login fields: %s" % json.dumps(outcome))
        return {"status": "filled", "domain": domain}


class FillRequest(BaseModel):
    domain: str
    usernameSelector: str = "input[type=email], input[name=username], input[type=text]"
    passwordSelector: str = "input[type=password]"


def build_sidecar_app(service: FillService) -> FastAPI:
    app = FastAPI(title="convoy-fill-sidecar")

    @app.post("/fill")
    async def fill(req: FillRequest):
        return await service.fill(req.domain, req.usernameSelector, req.passwordSelector)

    return app


def main() -> None:  # pragma: no cover — process entrypoint
    import os

    import uvicorn

    service = FillService(
        gateway_url=os.environ["CONVOY_GATEWAY_URL"],
        run_token=os.environ["CONVOY_RUN_TOKEN"],
        cdp_url=os.environ.get("CONVOY_CDP_URL", "http://127.0.0.1:9222"),
    )
    uvicorn.run(build_sidecar_app(service), host="127.0.0.1",
                port=int(os.environ.get("CONVOY_SIDECAR_PORT", "8781")))


if __name__ == "__main__":  # pragma: no cover
    main()
