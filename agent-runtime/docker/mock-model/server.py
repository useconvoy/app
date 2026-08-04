"""mock-model: canned OpenAI-compatible completion endpoint for the compose stack.

M0 ships the container so the stack shape is final; nothing calls it yet.
TODO(milestone-1): wire behind LiteLLM for the Pydantic AI TurnExecutor lane.
Stdlib-only on purpose: the container needs no network at build time.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8901


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self._json(
            200,
            {
                "id": "mock-cmpl-1",
                "object": "chat.completion",
                "model": "mock-model-1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "mock response"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
            },
        )

    def _json(self, status: int, body: dict[str, object]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
