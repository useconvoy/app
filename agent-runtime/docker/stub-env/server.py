"""stub-env: stub environments/ registry + data plane for the compose stack.

Two seams in one process:

  GET  /environments/{id}?tenant_id=...  -> EnvironmentBinding JSON, including
       the inline tool registry the runtime intersects agent grants against
       and the connector endpoint tool calls are sent to.
  POST /tools/{tool_id}                  -> executes one read-only tool call
       against fixture data; deterministic and idempotent by construction.

The registry deliberately offers a side-effecting promoted tool (kb_delete)
that must never become an inline tool, and an invalid inline+side-effecting
entry (audit_write) so grant validation rejection paths can be exercised.

Stdlib-only on purpose: the container needs no network at build time.

TODO: side-effect journal endpoints for the crash/chaos durability suite.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8902

# Where the runtime should send tool calls. Advertised inside the binding; the
# default matches the compose port mapping as seen from the host-run worker.
DATA_PLANE_URL = os.environ.get("DATA_PLANE_URL", "http://localhost:8902")

KNOWLEDGE_BASE = {
    "q3-revenue": "Q3 revenue was $1.2M, up 8% quarter over quarter.",
    "q3-headcount": "Headcount at the end of Q3 was 42.",
    "runway": "Runway is 19 months at the current burn rate.",
}

TOOL_REGISTRY = [
    {
        "tool_id": "kb_lookup",
        "scope": {"resource": "kb:*", "actions": ["read"]},
        "execution": "inline",
        "side_effecting": False,
    },
    {
        "tool_id": "kb_search",
        "scope": {"resource": "kb:*", "actions": ["read"]},
        "execution": "inline",
        "side_effecting": False,
    },
    {
        "tool_id": "kb_delete",
        "scope": {"resource": "kb:*", "actions": ["write"]},
        "execution": "promoted",
        "side_effecting": True,
    },
    {
        # Invalid on purpose: inline tools must be read-only. Requesting this
        # grant must be rejected at run creation.
        "tool_id": "audit_write",
        "scope": {"resource": "audit:*", "actions": ["write"]},
        "execution": "inline",
        "side_effecting": True,
    },
]


def binding(environment_id: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": environment_id,
        "tenant_id": tenant_id,
        "kind": "sandbox",
        "tool_registry": TOOL_REGISTRY,
        "connector_endpoints": {"data_plane": DATA_PLANE_URL},
        "credential_scope": "stub:no-credentials",
        "data_namespace": f"{tenant_id}/stub",
        "sandbox_template": "stub",
        "clock": {"mode": "real", "advance": "manual", "ratio": None},
    }


def kb_lookup(args: dict[str, object]) -> dict[str, object]:
    key = str(args.get("key", ""))
    return {"key": key, "value": KNOWLEDGE_BASE.get(key)}


def kb_search(args: dict[str, object]) -> dict[str, object]:
    query = str(args.get("query", "")).lower()
    results = [
        {"key": key, "snippet": text}
        for key, text in sorted(KNOWLEDGE_BASE.items())
        if query and (query in key.lower() or query in text.lower())
    ]
    return {"query": query, "results": results}


TOOL_HANDLERS = {"kb_lookup": kb_lookup, "kb_search": kb_search}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if parsed.path == "/health":
            self._json(200, {"ok": True})
        elif len(parts) == 2 and parts[0] == "environments":
            tenant = parse_qs(parsed.query).get("tenant_id", ["unknown"])[0]
            self._json(200, binding(parts[1], tenant))
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if len(parts) != 2 or parts[0] != "tools":
            self._json(404, {"error": "not found"})
            return
        tool_id = parts[1]
        handler = TOOL_HANDLERS.get(tool_id)
        if handler is None:
            self._json(404, {"error": f"unknown tool {tool_id!r}"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON body"})
            return
        args = body.get("args") if isinstance(body, dict) else {}
        self._json(
            200, {"tool_id": tool_id, "result": handler(args if isinstance(args, dict) else {})}
        )

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
