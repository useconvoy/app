"""stub-env: stub environments/ registry for the compose stack (TESTING.md).

Serves `GET /environments/{id}?tenant_id=...` -> EnvironmentBinding JSON so the
control plane can resolve and pin a binding snapshot at run start.
Stdlib-only on purpose: the container needs no network at build time.

TODO(milestone-1): serve inline tool registry entries for grant intersection.
TODO(milestone-4): side-effect journal endpoints for the chaos suite.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8902


def binding(environment_id: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": environment_id,
        "tenant_id": tenant_id,
        "kind": "sandbox",
        "tool_registry": [],
        "connector_endpoints": {},
        "credential_scope": "stub:no-credentials",
        "data_namespace": f"{tenant_id}/stub",
        "sandbox_template": "stub",
        "clock": {"mode": "real", "advance": "manual", "ratio": None},
    }


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
