"""Bound chat and application HTTP bodies before JSON decoding, including chunked requests."""

from __future__ import annotations

import asyncio
import re

from fastapi import HTTPException
from starlette.responses import JSONResponse

MAX_BODY = 256 * 1024
RECORDING_BODY = 4 * 1024 * 1024
RECORDING_COMMAND = re.compile(r"/api/agent/v1/robots/[^/]+/missions/[^/]+/recording/commands/[0-9]+")
# A workspace document is at most 2 MiB of compact JSON (checked exactly after decoding). The raw PUT
# may add its envelope and some formatting; anything larger is refused before it is decoded. The route
# reads its body only after authenticating the caller, so this bound applies as the body is consumed
# instead of buffering it up front.
DOCUMENT_BODY = 2 * 1024 * 1024 + 64 * 1024
DOCUMENT = re.compile(r"/api/v1/workspace-documents/[^/]+")
BODY_TIMEOUT_S = 15
# Offline evaluations read their bodies after authenticating the caller too. An episode upload carries
# its frames (at most 16 MiB in one request), so it may take longer to arrive than a management write.
OFFLINE_EVALUATIONS = re.compile(r"/api/v1/offline-evaluations")
OFFLINE_EPISODES = re.compile(r"/api/v1/offline-evaluations/[^/]+/episodes")
OFFLINE_EPISODE_BODY = 16 * 1024 * 1024
OFFLINE_EPISODE_TIMEOUT_S = 120
# Routes that consume their own body: (path, method, maximum, deadline in seconds or None for the default).
LAZY = (
    (DOCUMENT, "PUT", DOCUMENT_BODY, None),
    (OFFLINE_EVALUATIONS, "POST", MAX_BODY, None),
    (OFFLINE_EPISODES, "POST", OFFLINE_EPISODE_BODY, OFFLINE_EPISODE_TIMEOUT_S),
)


class ChatBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        lazy = next((route for route in LAZY if route[0].fullmatch(path) and scope.get("method") == route[1]), None)
        if scope["type"] == "http" and lazy is not None:
            # Refuse a declared oversize up front; otherwise bound the body while the route reads it.
            _, _, maximum, deadline = lazy
            if not _declared_within(scope, maximum):
                await JSONResponse({"error": "application request body too large or invalid"}, status_code=413)(
                    scope, receive, send
                )
                return
            await self.app(scope, _bounded(receive, maximum, "application", deadline), send)
            return
        application = path.startswith((
            "/api/agent/v1/robots/", "/api/v1/projects", "/api/v1/robots", "/api/v1/applications",
            "/api/v1/deployments", "/api/v1/missions/", "/api/v1/evaluations", "/api/v1/evaluation-suites",
            "/api/v1/offline-evaluations",
        ))
        limited = (
            path.startswith("/api/agent/v1/chat/") or path.startswith("/api/v1/devices/") and "/chat" in path
        ) or application
        label = "application" if application else "chat"
        # Camera observations exceed the ordinary management-body limit. Keep this exception exact and
        # bounded, including requests without a length.
        maximum = RECORDING_BODY if RECORDING_COMMAND.fullmatch(path) else MAX_BODY
        if scope["type"] != "http" or scope.get("method") != "POST" or not limited:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = -1
        if length < 0 or length > maximum:
            await JSONResponse({"error": f"{label} request body too large or invalid"}, status_code=413)(
                scope, receive, send
            )
            return
        body = bytearray()
        try:
            async with asyncio.timeout(BODY_TIMEOUT_S):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > maximum:
                        await JSONResponse({"error": f"{label} request body too large"}, status_code=413)(
                            scope, receive, send
                        )
                        return
                    if not message.get("more_body"):
                        break
        except TimeoutError:
            await JSONResponse({"error": f"{label} request body timeout"}, status_code=408)(scope, receive, send)
            return
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def _declared_within(scope, maximum: int) -> bool:
    headers = dict(scope.get("headers", []))
    try:
        length = int(headers.get(b"content-length", b"0"))
    except ValueError:
        return False
    return 0 <= length <= maximum


def _bounded(receive, maximum: int, label: str, timeout_s: float | None = None):
    """`receive` for a route that reads its body only after authenticating: the same size bound and
    deadline as buffering, raised as HTTP errors while the route consumes the body. Nothing is read
    when the route refuses the request first."""
    size = 0
    deadline: float | None = None
    complete = False

    async def bounded():
        nonlocal size, deadline, complete
        if complete:
            return await receive()
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + (BODY_TIMEOUT_S if timeout_s is None else timeout_s)
        try:
            async with asyncio.timeout_at(deadline):
                message = await receive()
        except TimeoutError:
            raise HTTPException(408, f"{label} request body timeout") from None
        if message["type"] == "http.request":
            size += len(message.get("body", b""))
            if size > maximum:
                raise HTTPException(413, f"{label} request body too large")
            complete = not message.get("more_body", False)
        return message

    return bounded
