"""stub-env: stub environments/ registry + data plane for the compose stack.

Three seams in one process:

  GET  /environments/{id}?tenant_id=...  -> EnvironmentBinding JSON, including
       the inline tool registry the runtime intersects agent grants against
       and the connector endpoint tool calls are sent to. Ids ending in
       "-virtual" serve a sandbox binding with a manual virtual clock;
       "prod-local" serves a production-kind binding (which must refuse
       clock advances); everything else is a real-clock sandbox binding.
  POST /tools/{tool_id}                  -> executes one read-only tool call
       against fixture data; deterministic and idempotent by construction.
  POST /effects/{tool_id}                -> executes one side-effecting call,
       journaled by idempotency key: a repeated key is a no-op that returns
       the recorded result. GET /journal exposes the journal so crash/chaos
       suites can prove a side effect fired exactly once.

The registry deliberately offers side-effecting promoted tools (kb_delete,
sandbox_exec) that must never become inline tools, and an invalid
inline+side-effecting entry (audit_write) so grant validation rejection
paths can be exercised.

Stdlib-only on purpose: the container needs no network at build time.
"""

import hashlib
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

# Mutable KB the side-effecting tools act on; the fixture above stays pristine
# for the read-only tools.
MUTABLE_KB = dict(KNOWLEDGE_BASE)

# Side-effect journal: idempotency key -> record. `executions` counts real
# firings (must stay 1 per key); `requests` counts every arrival including
# replays, which is what proves a retry hit the journal instead of the world.
JOURNAL: dict[str, dict[str, object]] = {}

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
        "tool_id": "sandbox_exec",
        "scope": {"resource": "sandbox:*", "actions": ["execute"]},
        "execution": "promoted",
        "side_effecting": True,
    },
    {
        "tool_id": "google.drive_list_files",
        "scope": {"resource": "google:drive:*", "actions": ["read"]},
        "execution": "inline",
        "side_effecting": False,
    },
    {
        "tool_id": "google.sheets_read_range",
        "scope": {"resource": "google:sheets:*", "actions": ["read"]},
        "execution": "inline",
        "side_effecting": False,
    },
    {
        "tool_id": "google.sheets_append_row",
        "scope": {"resource": "google:sheets:*", "actions": ["write"]},
        "execution": "promoted",
        "side_effecting": True,
    },
    {
        "tool_id": "slack.list_channels",
        "scope": {"resource": "slack:*", "actions": ["read"]},
        "execution": "inline",
        "side_effecting": False,
    },
    {
        "tool_id": "slack.read_messages",
        "scope": {"resource": "slack:*", "actions": ["read"]},
        "execution": "inline",
        "side_effecting": False,
    },
    {
        "tool_id": "slack.post_message",
        "scope": {"resource": "slack:*", "actions": ["write"]},
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
    kind = "production" if environment_id == "prod-local" else "sandbox"
    clock: dict[str, object] = {"mode": "real", "advance": "manual", "ratio": None}
    if kind == "sandbox" and environment_id.endswith("-virtual"):
        clock = {"mode": "virtual", "advance": "manual", "ratio": None}
    endpoint = DATA_PLANE_URL.rstrip("/")
    if kind == "sandbox":
        endpoint += "/simulated-data-plane"
    return {
        "id": environment_id,
        "tenant_id": tenant_id,
        "kind": kind,
        "tool_registry": TOOL_REGISTRY,
        "connector_endpoints": {"data_plane": endpoint},
        "credential_scope": "stub:no-credentials",
        "data_namespace": f"{tenant_id}/stub",
        "sandbox_template": "stub",
        "clock": clock,
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


def google_drive_list_files(_: dict[str, object]) -> dict[str, object]:
    return {"files": [], "nextPageToken": None}


def google_sheets_read_range(args: dict[str, object]) -> dict[str, object]:
    return {
        "range": args.get("range", ""),
        "majorDimension": "ROWS",
        "values": [],
    }


def slack_list_channels(_: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "channels": [], "response_metadata": {"next_cursor": ""}}


def slack_read_messages(_: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "messages": [], "has_more": False}


TOOL_HANDLERS = {
    "kb_lookup": kb_lookup,
    "kb_search": kb_search,
    "google.drive_list_files": google_drive_list_files,
    "google.sheets_read_range": google_sheets_read_range,
    "slack.list_channels": slack_list_channels,
    "slack.read_messages": slack_read_messages,
}


def kb_delete_effect(args: dict[str, object]) -> dict[str, object]:
    key = str(args.get("key", "")) or "runway"
    existed = key in MUTABLE_KB
    MUTABLE_KB.pop(key, None)
    return {"deleted": key, "existed": existed, "remaining": len(MUTABLE_KB)}


def generic_effect(tool_id: str, args: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "tool_id": tool_id, "args": args}


def stand_in_effect(
    tool_id: str, idempotency_key: str, args: dict[str, object]
) -> dict[str, object]:
    material = json.dumps(
        {"tool": tool_id, "key": idempotency_key, "args": args}, sort_keys=True
    ).encode()
    effect_id = "sim-" + hashlib.sha256(material).hexdigest()[:16]
    if tool_id == "slack.post_message":
        return {
            "ok": True,
            "simulated": True,
            "channel": args.get("channel", ""),
            "ts": effect_id.removeprefix("sim-")[:10] + ".000000",
            "message": {"text": args.get("text", "")},
        }
    if tool_id == "google.sheets_append_row":
        values = args.get("values")
        return {
            "spreadsheetId": args.get("spreadsheetId", ""),
            "tableRange": args.get("range", "A1"),
            "updates": {
                "updatedRows": 1,
                "updatedCells": len(values) if isinstance(values, list) else 0,
                "updatedRange": args.get("range", "A1"),
            },
            "simulated": True,
            "effectId": effect_id,
        }
    return generic_effect(tool_id, args)


def run_effect(tool_id: str, idempotency_key: str, args: dict[str, object]) -> dict[str, object]:
    """Journaled side effect: a repeated key never fires twice."""
    record = JOURNAL.get(idempotency_key)
    if record is not None:
        record["requests"] = int(str(record.get("requests", 1))) + 1
        return {"tool_id": tool_id, "result": record["result"], "replayed": True}
    if tool_id == "kb_delete":
        result = kb_delete_effect(args)
    elif tool_id in {"slack.post_message", "google.sheets_append_row"}:
        result = stand_in_effect(tool_id, idempotency_key, args)
    else:
        result = generic_effect(tool_id, args)
    JOURNAL[idempotency_key] = {
        "idempotency_key": idempotency_key,
        "tool_id": tool_id,
        "args": args,
        "result": result,
        "executions": 1,
        "requests": 1,
    }
    return {"tool_id": tool_id, "result": result, "replayed": False}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if parsed.path == "/health":
            self._json(200, {"ok": True})
        elif parsed.path == "/journal":
            self._json(200, {"entries": list(JOURNAL.values())})
        elif len(parts) == 2 and parts[0] == "environments":
            tenant = parse_qs(parsed.query).get("tenant_id", ["unknown"])[0]
            self._json(200, binding(parts[1], tenant))
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if parts and parts[0] == "simulated-data-plane":
            parts = parts[1:]
        if len(parts) != 2 or parts[0] not in ("tools", "effects"):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON body"})
            return
        if not isinstance(body, dict):
            body = {}
        raw_args = body.get("args")
        args = raw_args if isinstance(raw_args, dict) else {}
        tool_id = parts[1]
        if parts[0] == "effects":
            key = str(body.get("idempotency_key", ""))
            if not key:
                self._json(422, {"error": "idempotency_key is required for side effects"})
                return
            self._json(200, run_effect(tool_id, key, args))
            return
        handler = TOOL_HANDLERS.get(tool_id)
        if handler is None:
            self._json(404, {"error": f"unknown tool {tool_id!r}"})
            return
        self._json(200, {"tool_id": tool_id, "result": handler(args)})

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
