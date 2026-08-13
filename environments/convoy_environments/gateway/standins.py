"""Deterministic connector stand-ins for rehearsal environment bindings."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .policy import ToolResolution


def _stable_id(tool: str, args: dict[str, Any], idempotency_key: str) -> str:
    material = json.dumps(
        {"tool": tool, "args": args, "key": idempotency_key},
        sort_keys=True,
        default=str,
    ).encode()
    return "sim-" + hashlib.sha256(material).hexdigest()[:16]


def _fixture(resolution: ToolResolution, tool: str) -> Any | None:
    fixtures = (resolution.connection.config or {}).get("rehearsalFixtures") or {}
    return fixtures.get(tool) if tool in fixtures else None


def simulate(
    resolution: ToolResolution,
    tool: str,
    args: dict[str, Any],
    *,
    idempotency_key: str = "",
) -> Any:
    """Return a provider-compatible deterministic result for one tool."""

    configured = _fixture(resolution, tool)
    if configured is not None:
        return configured

    effect_id = _stable_id(tool, args, idempotency_key or "read")
    if tool == "slack.list_channels":
        return {"ok": True, "channels": [], "response_metadata": {"next_cursor": ""}}
    if tool == "slack.read_messages":
        return {"ok": True, "messages": [], "has_more": False}
    if tool == "slack.post_message":
        return {
            "ok": True,
            "simulated": True,
            "channel": args.get("channel", ""),
            "ts": effect_id.removeprefix("sim-")[:10] + ".000000",
            "message": {"text": args.get("text", "")},
        }
    if tool == "github.list_issues":
        return []
    if tool == "github.get_file":
        return {
            "type": "file",
            "name": str(args.get("path", "")).rsplit("/", 1)[-1],
            "path": args.get("path", ""),
            "sha": effect_id.removeprefix("sim-"),
            "content": "",
            "encoding": "base64",
            "simulated": True,
        }
    if tool == "github.create_issue":
        return {
            "id": effect_id,
            "number": int(effect_id[-6:], 16) % 100_000,
            "title": args.get("title", ""),
            "body": args.get("body", ""),
            "state": "open",
            "html_url": "https://example.invalid/rehearsal/issues/" + effect_id,
            "simulated": True,
        }
    if tool == "google.drive_list_files":
        return {"files": [], "nextPageToken": None}
    if tool == "google.sheets_read_range":
        return {"range": args.get("range", ""), "majorDimension": "ROWS", "values": []}
    if tool == "google.docs_read":
        return {
            "documentId": args.get("documentId", effect_id),
            "title": "Rehearsal document",
            "text": "This is a rehearsal stand-in document. Its content is fixed.\n",
            "simulated": True,
        }
    if tool == "google.docs_update":
        return {
            "documentId": args.get("documentId", ""),
            "replies": [],
            "writeControl": {},
            "simulated": True,
            "effectId": effect_id,
        }
    if tool == "google.sheets_append_row":
        values = args.get("values") or []
        return {
            "spreadsheetId": args.get("spreadsheetId", ""),
            "tableRange": args.get("range", "A1"),
            "updates": {
                "updatedRows": 1,
                "updatedCells": len(values),
                "updatedRange": args.get("range", "A1"),
            },
            "simulated": True,
            "effectId": effect_id,
        }
    if tool == "notion.search":
        return {"object": "list", "results": [], "has_more": False, "next_cursor": None}
    if tool == "notion.get_page":
        return {
            "object": "page",
            "id": args.get("pageId", effect_id),
            "properties": {},
            "simulated": True,
        }
    if tool == "notion.create_page":
        return {
            "object": "page",
            "id": effect_id,
            "parent": args.get("parent", {}),
            "properties": args.get("properties", {}),
            "url": "https://example.invalid/rehearsal/notion/" + effect_id,
            "simulated": True,
        }
    return {
        "simulated": True,
        "effectId": effect_id,
        "tool": tool,
        "provider": resolution.connection.provider,
        "args": args,
    }
