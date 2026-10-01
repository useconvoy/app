"""Bound chat and application HTTP bodies before JSON decoding, including chunked requests."""

from __future__ import annotations

import asyncio
import re

from starlette.responses import JSONResponse

MAX_BODY = 256 * 1024
RECORDING_BODY = 4 * 1024 * 1024
RECORDING_COMMAND = re.compile(r"/api/agent/v1/robots/[^/]+/missions/[^/]+/recording/commands/[0-9]+")
# A workspace document is at most 2 MiB of compact JSON (checked exactly after decoding). The raw PUT
# may add its envelope and some formatting; anything larger is refused before it is decoded, which
# happens before authentication.
DOCUMENT_BODY = 2 * 1024 * 1024 + 64 * 1024
DOCUMENT = re.compile(r"/api/v1/workspace-documents/[^/]+")


class ChatBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        document = DOCUMENT.fullmatch(path) is not None
        application = document or path.startswith((
            "/api/agent/v1/robots/", "/api/v1/projects", "/api/v1/robots", "/api/v1/applications",
            "/api/v1/deployments", "/api/v1/missions/", "/api/v1/evaluations", "/api/v1/evaluation-suites",
        ))
        limited = (
            path.startswith("/api/agent/v1/chat/") or path.startswith("/api/v1/devices/") and "/chat" in path
        ) or application
        label = "application" if application else "chat"
        # Camera observations and workspace documents exceed the ordinary management-body limit. Keep
        # these exceptions exact and bounded, including requests without a length.
        maximum = MAX_BODY
        if RECORDING_COMMAND.fullmatch(path):
            maximum = RECORDING_BODY
        elif document:
            maximum = DOCUMENT_BODY
        method = "PUT" if document else "POST"
        if scope["type"] != "http" or scope.get("method") != method or not limited:
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
            async with asyncio.timeout(15):
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
