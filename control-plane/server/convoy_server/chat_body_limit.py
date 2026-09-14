"""Bound chat HTTP bodies before JSON decoding, including chunked requests."""

from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse

MAX_BODY = 256 * 1024


class ChatBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        limited = (
            path.startswith("/api/agent/v1/chat/") or path.startswith("/api/v1/devices/") and "/chat" in path
        )
        if scope["type"] != "http" or scope.get("method") != "POST" or not limited:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            await JSONResponse({"error": "chat request body too large or invalid"}, status_code=413)(
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
                    if len(body) > MAX_BODY:
                        await JSONResponse({"error": "chat request body too large"}, status_code=413)(
                            scope, receive, send
                        )
                        return
                    if not message.get("more_body"):
                        break
        except TimeoutError:
            await JSONResponse({"error": "chat request body timeout"}, status_code=408)(scope, receive, send)
            return
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)
