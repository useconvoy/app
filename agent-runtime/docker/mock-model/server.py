"""mock-model: deterministic OpenAI-compatible chat-completions stub.

Sits behind the LiteLLM proxy in the local stack so the real turn executor can
be exercised with zero real model calls. Behavior is fully scripted by
directives embedded in the conversation's system/user text, so tests control
the exact sequence of tool calls and the final answer:

    [[call:TOOL_ID {"json": "args"}]]  -> respond with that function tool call
    [[say:TEXT]]                       -> final text TEXT (step keeps going)
    [[done:TEXT]]                      -> final text TEXT + " STEP_DONE"

The Nth assistant response consumes the Nth directive; when directives run
out (or none exist) the response is "mock response STEP_DONE". Requests for a
model name containing "failing" always get HTTP 500 — that is how tests make
a primary model fail and force the runtime's fallback chain. Token usage is
fixed (12 prompt / 7 completion) so proxy-priced costs are reproducible.

Stdlib-only on purpose: the container needs no network at build time.
"""

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8901
DIRECTIVE = re.compile(r"\[\[(call|say|done):(.*?)\]\]", re.DOTALL)
STEP_DONE_MARKER = "STEP_DONE"


def _text_of(content: object) -> str:
    """Message content may be a plain string or a list of typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        return "\n".join(chunks)
    return ""


def _directives(messages: list[dict[str, object]]) -> list[tuple[str, str]]:
    """Directives in order, from system/user text only — never from tool
    results or retry prompts, so rejection messages cannot script behavior."""
    found: list[tuple[str, str]] = []
    for message in messages:
        if message.get("role") in ("system", "user"):
            for kind, body in DIRECTIVE.findall(_text_of(message.get("content"))):
                found.append((kind, body.strip()))
    return found


def _assistant_turns(messages: list[dict[str, object]]) -> int:
    return sum(1 for m in messages if m.get("role") == "assistant")


def _completion(model: str, messages: list[dict[str, object]]) -> dict[str, object]:
    turn = _assistant_turns(messages)
    directives = _directives(messages)
    message: dict[str, object]
    finish_reason = "stop"

    if turn < len(directives):
        kind, body = directives[turn]
        if kind == "call":
            tool_id, _, raw_args = body.partition(" ")
            arguments = raw_args.strip() or "{}"
            json.loads(arguments)  # scripted args must be valid JSON
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{turn}",
                        "type": "function",
                        "function": {"name": tool_id, "arguments": arguments},
                    }
                ],
            }
            finish_reason = "tool_calls"
        elif kind == "say":
            message = {"role": "assistant", "content": body}
        else:  # done
            message = {"role": "assistant", "content": f"{body} {STEP_DONE_MARKER}".strip()}
    else:
        message = {"role": "assistant", "content": f"mock response {STEP_DONE_MARKER}"}

    return {
        "id": f"mock-cmpl-{turn}",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self.path.endswith("/chat/completions"):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": {"message": "invalid JSON", "type": "invalid_request"}})
            return
        model = str(body.get("model", "mock-model-1"))
        if "failing" in model:
            self._json(
                500,
                {"error": {"message": "scripted upstream outage", "type": "server_error"}},
            )
            return
        messages = body.get("messages") or []
        self._json(200, _completion(model, messages))

    def _json(self, status: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
