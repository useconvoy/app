"""Owner-scoped workspace documents across authenticated HTTP, both credentials and the SQLite upgrade."""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from datetime import timedelta

import pytest
from conftest import WEB, api_token_client, login, make_user
from convoy_server import chat_body_limit, migrations
from convoy_server.chat_body_limit import DOCUMENT_BODY
from convoy_server.db import make_engine, reset_engine, session_scope, write_txn
from convoy_server.ids import aware
from convoy_server.models import Base, Throttle
from convoy_server.platform_models import MutationReceipt
from convoy_server.services import workspace_documents as service
from convoy_server.services.workspace_documents import MAX_BODY_BYTES, MAX_DEPTH, MAX_DOCUMENTS
from convoy_server.workspace_models import WorkspaceDocument
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

ROOT = "/api/v1/workspace-documents"
SAMPLE = {
    "schemaVersion": 1,
    "configurations": [{"id": "sample-a", "robots": [{"name": "Sample bench", "latency_ms": None}]}],
}
CREATE = {"If-None-Match": "*"}
METADATA = ["name", "schema_version", "revision", "size_bytes", "updated_at"]


def put(client, name, body, key, *, revision=None, schema_version=1, headers=WEB):
    """Creates the document (If-None-Match: *) or, given a revision, replaces it (If-Match)."""
    precondition = CREATE if revision is None else {"If-Match": f'"{revision}"'}
    payload = {"schema_version": schema_version, "body": body}
    return client.put(
        f"{ROOT}/{name}", json=payload, headers={**headers, **precondition, "Idempotency-Key": key}
    )


def put_raw(client, name, content, key, *, headers=CREATE):
    return client.put(
        f"{ROOT}/{name}",
        content=content,
        headers={**WEB, "Content-Type": "application/json", "Idempotency-Key": key, **headers},
    )


def delete(client, name, key, *, revision=None, headers=WEB):
    precondition = {} if revision is None else {"If-Match": f'"{revision}"'}
    return client.delete(f"{ROOT}/{name}", headers={**headers, **precondition, "Idempotency-Key": key})


def user_client(app, admin, email, role):
    make_user(admin, email, role)
    return login(TestClient(app), email, "password-123")


def user_id(client) -> str:
    return client.get("/api/v1/auth/me").json()["user"]["id"]


def compact(body) -> str:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def compact_size(body) -> int:
    return len(compact(body).encode())


def nested(levels: int) -> dict:
    """A body whose containers (the body itself included) are nested `levels` deep."""
    body: dict = {}
    node = body
    for level in range(1, levels):
        child: dict | list = [] if level % 2 else {}
        if isinstance(node, dict):
            node["next"] = child
        else:
            node.append(child)
        node = child
    return body


def depth(value, level: int = 1) -> int:
    """Reference container depth: a plain walk over every value."""
    if isinstance(value, dict):
        return max([level, *(depth(child, level + 1) for child in value.values())])
    if isinstance(value, list):
        return max([level, *(depth(child, level + 1) for child in value)])
    return 0


def test_documents_are_owner_scoped_for_every_role_and_credential(app, admin):
    viewer = user_client(app, admin, "viewer@example.com", "viewer")
    operator = user_client(app, admin, "operator@example.com", "operator")
    created = put(viewer, "configurations", SAMPLE, "viewer-create")
    assert created.status_code == 200, created.text
    meta = created.json()
    assert list(meta) == METADATA  # a write answers with metadata, never the body it was sent
    assert meta["name"] == "configurations" and meta["schema_version"] == 1 and meta["revision"] == 1
    assert meta["size_bytes"] == compact_size(SAMPLE) and meta["updated_at"].endswith("Z")
    document = viewer.get(f"{ROOT}/configurations").json()
    assert list(document) == ["name", "schema_version", "revision", "body", "size_bytes", "updated_at"]
    assert document == {**meta, "body": SAMPLE}

    # Administrators and operators have their own namespace: they cannot list, read, replace or delete it.
    for other in (admin, operator):
        assert other.get(ROOT).json() == {"items": []}
        missing = other.get(f"{ROOT}/configurations")
        assert missing.status_code == 404 and missing.json() == {"error": "workspace document not found"}
        assert delete(other, "configurations", "not-yours").status_code == 404
        assert put(other, "configurations", {}, "not-yours", revision=1).status_code == 412
    own = put(admin, "configurations", {"owner": "admin"}, "admin-create")
    assert own.status_code == 200 and own.json()["revision"] == 1
    assert admin.get(f"{ROOT}/configurations").json()["body"] == {"owner": "admin"}
    assert viewer.get(f"{ROOT}/configurations").json() == document

    # An API token acts as its user without the browser header; a browser session needs it to write.
    token = api_token_client(app, viewer, name="workspace")
    assert token.get(f"{ROOT}/configurations").json() == document
    emptied = {"schemaVersion": 1, "configurations": []}
    replaced = put(token, "configurations", emptied, "token-save", revision=1, headers={})
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["revision"] == 2
    assert viewer.get(f"{ROOT}/configurations").json() == {**replaced.json(), "body": emptied}
    for response in (
        viewer.put(
            f"{ROOT}/configurations",
            json={"schema_version": 1, "body": {}},
            headers={"Idempotency-Key": "without-client-header", "If-Match": '"2"'},
        ),
        viewer.delete(f"{ROOT}/configurations", headers={"Idempotency-Key": "without-client-header"}),
    ):
        assert response.status_code == 403 and response.json() == {"error": "missing X-Convoy-Client header"}

    anonymous = TestClient(app)
    for response in (
        anonymous.get(ROOT),
        anonymous.get(f"{ROOT}/configurations"),
        put(anonymous, "configurations", {}, "anonymous"),
        delete(anonymous, "configurations", "anonymous"),
    ):
        assert response.status_code == 401 and response.json() == {"error": "authentication required"}

    assert delete(token, "configurations", "token-delete", headers={}).status_code == 204
    assert viewer.get(f"{ROOT}/configurations").status_code == 404
    assert admin.get(f"{ROOT}/configurations").json()["body"] == {"owner": "admin"}


def test_conditional_writes_create_replace_and_refuse_stale_revisions(admin):
    created = put(admin, "configurations", SAMPLE, "create")
    assert created.status_code == 200 and created.json()["revision"] == 1
    exists = put(admin, "configurations", {"other": True}, "create-again")
    assert exists.status_code == 412
    assert exists.json() == {"error": "workspace document already exists; replace it with If-Match and its revision"}

    replaced = put(admin, "configurations", {"step": 1}, "replace-1", revision=1)
    assert replaced.status_code == 200 and list(replaced.json()) == METADATA
    assert replaced.json()["revision"] == 2 and replaced.json()["updated_at"] >= created.json()["updated_at"]
    stale = put(admin, "configurations", {"stale": True}, "replace-stale", revision=1)
    assert stale.status_code == 412
    assert stale.json() == {"error": "workspace document is at revision 2, not 1; read it again first"}
    missing = put(admin, "absent", {}, "replace-missing", revision=1)
    assert missing.status_code == 412
    assert missing.json() == {"error": "workspace document does not exist; create it with If-None-Match: *"}
    assert admin.get(f"{ROOT}/absent").status_code == 404
    assert put(admin, "configurations", {"step": 2}, "replace-2", revision=2).json()["revision"] == 3
    current = admin.get(f"{ROOT}/configurations").json()
    assert current["revision"] == 3 and current["body"] == {"step": 2}
    assert admin.get(ROOT).json()["items"] == [{k: v for k, v in current.items() if k != "body"}]

    # The precondition is part of the idempotent payload: a retry replays its original answer even
    # though the revision has moved on, and the same key with another precondition is a different write.
    assert put(admin, "configurations", {"step": 1}, "replace-1", revision=1).json() == replaced.json()
    assert put(admin, "configurations", SAMPLE, "create").json() == created.json()
    reused = put(admin, "configurations", {"step": 1}, "replace-1", revision=3)
    assert reused.status_code == 409
    assert reused.json() == {"error": "Idempotency-Key was already used with a different payload"}

    # A write states exactly one well-formed precondition before anything else happens.
    url, payload = f"{ROOT}/configurations", {"schema_version": 1, "body": {}}
    unconditional = admin.put(url, json=payload, headers={**WEB, "Idempotency-Key": "unconditional"})
    assert unconditional.status_code == 428
    assert unconditional.json() == {
        "error": 'workspace document writes need a precondition: If-None-Match: * to create the document, '
        'or If-Match: "<revision>" to replace that revision'
    }
    both = admin.put(url, json=payload, headers={**WEB, **CREATE, "If-Match": '"3"', "Idempotency-Key": "both"})
    assert both.status_code == 422 and both.json() == {"error": "send either If-Match or If-None-Match, not both"}
    for value in ("3", 'W/"3"', '"03"', '"0"', '"-3"', '"three"', "*", '"3", "4"', '"' + "9" * 19 + '"'):
        malformed = admin.put(url, json=payload, headers={**WEB, "If-Match": value, "Idempotency-Key": "malformed"})
        assert malformed.status_code == 422, value
        assert malformed.json() == {"error": 'If-Match must be one quoted document revision, for example "3"'}
        assert delete(admin, "configurations", "malformed", headers={**WEB, "If-Match": value}).status_code == 422
    for value in ('"3"', "**", "W/*"):
        refused = admin.put(url, json=payload, headers={**WEB, "If-None-Match": value, "Idempotency-Key": "inm"})
        assert refused.status_code == 422 and refused.json() == {"error": "If-None-Match accepts only *"}
    assert admin.get(f"{ROOT}/configurations").json() == current

    # DELETE may name the revision it removes; a stale one leaves the document in place.
    stale_delete = delete(admin, "configurations", "delete-stale", revision=2)
    assert stale_delete.status_code == 412
    assert stale_delete.json() == {"error": "workspace document is at revision 3, not 2; read it again first"}
    assert admin.get(f"{ROOT}/configurations").json() == current
    assert delete(admin, "configurations", "delete", revision=3).status_code == 204
    assert delete(admin, "configurations", "delete", revision=3).status_code == 204  # replayed
    assert delete(admin, "configurations", "delete-again", revision=3).status_code == 404
    # A document created again starts over at revision 1.
    assert put(admin, "configurations", SAMPLE, "recreate").json()["revision"] == 1


def test_validation_errors_use_the_standard_envelope(admin):
    for name in ("Configurations", "-leading", "a" * 65, "under_score", "dot.name", "spa ce"):
        for response in (
            admin.get(f"{ROOT}/{name}"),
            put(admin, name, {}, "bad-name"),
            delete(admin, name, "bad-name"),
        ):
            assert response.status_code == 422, (name, response.text)
            assert response.json()["error"].startswith("invalid request: path.name: ")
    assert put(admin, "a" * 64, {}, "longest-name").status_code == 200
    assert put(admin, "0-sample-2", {}, "digits-and-dashes").status_code == 200

    payloads = [
        {"schema_version": 1, "body": []},
        {"schema_version": 1, "body": "text"},
        {"schema_version": 1, "body": None},
        {"schema_version": 1},
        {"body": {}},
        {"schema_version": 0, "body": {}},
        {"schema_version": "1", "body": {}},
        {"schema_version": 1.0, "body": {}},
        {"schema_version": True, "body": {}},
        {"schema_version": 2**31, "body": {}},
        {"schema_version": 1, "body": {}, "owner_user_id": "someone-else"},
        [{"schema_version": 1, "body": {}}],
    ]
    for index, payload in enumerate(payloads):
        response = admin.put(
            f"{ROOT}/configurations", json=payload, headers={**WEB, **CREATE, "Idempotency-Key": f"invalid-{index}"}
        )
        assert response.status_code == 422, (payload, response.text)
        assert response.json()["error"].startswith("invalid request: ") and response.json()["detail"]
    shaped = put(admin, "configurations", [], "list")
    assert shaped.json()["error"] == "invalid request: body: Input should be a valid dictionary"
    assert put(admin, "configurations", {}, "largest-version", schema_version=2**31 - 1).status_code == 200

    raw = {"nan": b'{"x":NaN}', "infinity": b'{"x":[-Infinity]}', "surrogate": b'{"x":"\\ud800"}'}
    for label, body in raw.items():
        response = put_raw(admin, f"raw-{label}", b'{"schema_version":1,"body":' + body + b"}", label)
        assert response.status_code == 422, (label, response.text)
        expected = "valid Unicode" if label == "surrogate" else "non-finite numbers are not accepted"
        assert expected in response.json()["error"]
    emoji = put_raw(admin, "raw-pair", b'{"schema_version":1,"body":{"x":"\\ud83d\\ude00"}}', "pair")
    assert emoji.status_code == 200 and admin.get(f"{ROOT}/raw-pair").json()["body"] == {"x": "\U0001f600"}
    for label, content in (("syntax", b'{"schema_version":1,"body":{'), ("encoding", b'{"x":"\xff"}')):
        response = put_raw(admin, f"raw-{label}", content, label)
        assert response.status_code == 422, (label, response.text)
        assert response.json()["error"].startswith("invalid request: ") and response.json()["detail"][0]["type"] == "json_invalid"
    text = put_raw(admin, "raw-text", b'{"schema_version":1,"body":{}}', "text", headers={**CREATE, "Content-Type": "text/plain"})
    assert text.status_code == 422
    assert text.json() == {"error": "the request body must be JSON (Content-Type: application/json)"}

    assert put(admin, "deep", nested(MAX_DEPTH), "deepest").status_code == 200
    too_deep = put(admin, "deeper", nested(MAX_DEPTH + 1), "too-deep")
    assert too_deep.status_code == 422 and too_deep.json() == {"error": f"body nesting exceeds {MAX_DEPTH} levels"}
    # Nesting far beyond what the decoder itself accepts is the same refusal, not a server error.
    abyss = b'{"schema_version":1,"body":{"a":' + b"[" * 50_000 + b"]" * 50_000 + b"}}"
    refused = put_raw(admin, "abyss", abyss, "abyss")
    assert refused.status_code == 422 and refused.json() == {"error": f"body nesting exceeds {MAX_DEPTH} levels"}
    # Brackets inside strings and keys are text, not nesting.
    brackets = {"[" * 40: "{" * 40, "list": ["]" * 40, '\\"[[[', {"\\": "\"{{{"}]}
    assert put(admin, "brackets", brackets, "brackets").status_code == 200
    assert admin.get(f"{ROOT}/brackets").json()["body"] == brackets

    missing = admin.put(f"{ROOT}/configurations", json={"schema_version": 1, "body": {}}, headers={**WEB, **CREATE})
    assert missing.status_code == 422 and "Idempotency-Key" in missing.json()["error"]
    for key in ("", "x" * 129, "has space"):
        assert put(admin, "configurations", {}, key).status_code == 422
        assert delete(admin, "configurations", key).status_code == 422
    assert admin.delete(f"{ROOT}/configurations", headers=WEB).status_code == 422


def test_nesting_is_measured_on_the_compact_encoding():
    """The bytes-level depth check agrees with a walk over every value, strings with brackets, quotes and
    escapes included, also across its internal slice boundaries."""
    tricky = ["[", "]", "{", "}", '"', "\\", '\\"', '"[', "\\[", "a\\", '\\\\"]]', "\n[", "é{", "\U0001f600]"]
    bodies = [
        {},
        {"a": []},
        {"[": "]"},
        {"k": tricky, "{": {"}": [tricky]}},
        nested(MAX_DEPTH),
        nested(MAX_DEPTH + 1),
        nested(MAX_DEPTH + 5),
        {"s": "x" * 65_530 + '"[' * 20, "t": [["]" * 70_000]]},
        {"s": "\\" * 65_535 + '"', "t": [[[{}]]]},
        {"k" * 65_534: [[]], "v": '"' * 70_000},
    ]
    rng = random.Random(20261001)
    alphabet = ["[", "]", "{", "}", '"', "\\", "a", "é", "\n", "\u0001", "\U0001f600"]

    def text():
        return "".join(rng.choice(alphabet) for _ in range(rng.randrange(6)))

    def value(level):
        kind = rng.random()
        if level < 8 and kind < 0.3:
            return [value(level + 1) for _ in range(rng.randrange(4))]
        if level < 8 and kind < 0.6:
            return {text(): value(level + 1) for _ in range(rng.randrange(4))}
        return text() if kind < 0.85 else rng.choice([0, -1.5, True, None])

    bodies += [{text(): value(1) for _ in range(rng.randrange(1, 4))} for _ in range(400)]
    for body in bodies:
        assert service.nesting(compact(body).encode()) == min(depth(body), MAX_DEPTH + 1), compact(body)[:200]


def test_lazy_body_bound_applies_only_while_the_route_reads(monkeypatch):
    """The document route's receive wrapper: nothing happens until the route reads, then the size bound
    and the deadline apply until the body is complete."""
    monkeypatch.setattr(chat_body_limit, "BODY_TIMEOUT_S", 0.05)

    def receiver(*messages):
        queue = list(messages)

        async def receive():
            if not queue:
                await asyncio.sleep(3600)
            return queue.pop(0)

        return receive

    async def scenario():
        chunk = {"type": "http.request", "body": b"x" * 6, "more_body": True}
        last = {"type": "http.request", "body": b"x" * 4, "more_body": False}
        bounded = chat_body_limit._bounded(receiver(chunk, last), 10, "application")
        assert await bounded() == chunk and await bounded() == last
        # Once the body is complete, later messages (a disconnect) pass through without a deadline.
        done = chat_body_limit._bounded(receiver(last, {"type": "http.disconnect"}), 10, "application")
        assert await done() == last
        await asyncio.sleep(0.1)
        assert await done() == {"type": "http.disconnect"}
        oversized = chat_body_limit._bounded(receiver(chunk, chunk), 10, "application")
        await oversized()
        with pytest.raises(HTTPException) as refused:
            await oversized()
        assert (refused.value.status_code, refused.value.detail) == (413, "application request body too large")
        stalled = chat_body_limit._bounded(receiver(chunk), 10, "application")
        await stalled()
        with pytest.raises(HTTPException) as late:
            await stalled()
        assert (late.value.status_code, late.value.detail) == (408, "application request body timeout")

    asyncio.run(scenario())


def test_size_and_document_count_limits(app, admin):
    # Exactly 2 MiB of compact JSON is accepted; one more byte is refused before storage.
    filler = MAX_BODY_BYTES - compact_size({"blob": ""})
    largest = put(admin, "largest", {"blob": "x" * filler}, "largest")
    assert largest.status_code == 200 and largest.json()["size_bytes"] == MAX_BODY_BYTES
    assert admin.get(f"{ROOT}/largest").json()["body"]["blob"] == "x" * filler
    for name, body in (("over", {"blob": "x" * (filler + 1)}), ("multibyte", {"blob": "é" * (filler // 2 + 1)})):
        refused = put(admin, name, body, name)
        assert refused.status_code == 413, refused.text
        assert refused.json() == {"error": f"workspace document body exceeds {MAX_BODY_BYTES} bytes as compact JSON"}
        assert admin.get(f"{ROOT}/{name}").status_code == 404

    # Formatting is accepted within the raw bound, and the stored size is the compact document's.
    rows = {"rows": [{"k": index} for index in range(20_000)]}
    pretty = json.dumps({"schema_version": 1, "body": rows}, indent=2).encode()
    assert compact_size(rows) < len(pretty) < DOCUMENT_BODY
    formatted = put_raw(admin, "formatted", pretty, "formatted")
    assert formatted.status_code == 200 and formatted.json()["size_bytes"] == compact_size(rows)
    # Larger raw requests are refused before decoding, with or without a declared length.
    assert MAX_BODY_BYTES < DOCUMENT_BODY <= MAX_BODY_BYTES + 64 * 1024
    for content in (b"x" * (DOCUMENT_BODY + 1), iter([b"x" * (DOCUMENT_BODY + 1)])):
        response = put_raw(admin, "oversized", content, "oversized")
        assert response.status_code == 413 and "too large" in response.json()["error"]
    # The larger allowance is exact: other management writes keep the ordinary bound.
    assert admin.post("/api/v1/projects", content=b"x" * (256 * 1024 + 1), headers=WEB).status_code == 413

    viewer = user_client(app, admin, "limits@example.com", "viewer")
    for index in range(MAX_DOCUMENTS):
        assert put(viewer, f"doc-{index:02d}", {"index": index}, f"create-{index}").status_code == 200
    refused = put(viewer, "one-more", {}, "one-more")
    assert refused.status_code == 409
    assert refused.json() == {"error": f"workspace document limit reached ({MAX_DOCUMENTS} per account); delete one first"}
    assert put(viewer, "doc-00", {"index": "replaced"}, "replace-at-limit", revision=1).status_code == 200
    assert delete(viewer, "doc-15", "free-one").status_code == 204
    # The refused attempt left no receipt, so the same key now performs the creation.
    assert put(viewer, "one-more", {}, "one-more").status_code == 200
    items = viewer.get(ROOT).json()["items"]
    assert [item["name"] for item in items] == [f"doc-{index:02d}" for index in range(15)] + ["one-more"]
    assert all(list(item) == METADATA for item in items)
    assert items[0]["size_bytes"] == compact_size({"index": "replaced"}) and items[0]["revision"] == 2


def test_bodies_are_stored_as_compact_utf8(admin):
    body = {"name": "Bänk — 東京 \U0001f916", "values": [1, 2.5, None, True, "\n\t\"\\"], "nested": {"a": []}}
    created = put(admin, "unicode", body, "unicode")
    assert created.status_code == 200 and created.json()["size_bytes"] == compact_size(body)
    # The largest document of multibyte text is stored at the limit, not at its ASCII-escaped size.
    filler = (MAX_BODY_BYTES - compact_size({"blob": ""})) // 3
    largest = {"blob": "東" * filler}
    assert put(admin, "multibyte", largest, "multibyte").json()["size_bytes"] == compact_size(largest)
    assert len(json.dumps(largest).encode()) > 1.9 * compact_size(largest)  # what a JSON column would keep
    with session_scope() as session:
        stored = dict(session.execute(select(WorkspaceDocument.name, WorkspaceDocument.body)).all())
        sizes = dict(session.execute(select(WorkspaceDocument.name, WorkspaceDocument.size_bytes)).all())
    assert stored == {"unicode": compact(body), "multibyte": compact(largest)}
    assert all(len(stored[name].encode()) == sizes[name] <= MAX_BODY_BYTES for name in stored)
    # Reads still return the body as a JSON object.
    assert admin.get(f"{ROOT}/unicode").json()["body"] == body
    assert admin.get(f"{ROOT}/multibyte").json()["body"] == largest


def test_writes_are_idempotent_audited_and_keep_compact_receipts(admin):
    first = put(admin, "configurations", SAMPLE, "save-1")
    assert first.status_code == 200
    again = put(admin, "configurations", SAMPLE, "save-1")
    assert again.status_code == 200 and again.json() == first.json()
    for changed in (put(admin, "configurations", {"changed": True}, "save-1"),
                    put(admin, "configurations", SAMPLE, "save-1", schema_version=2)):
        assert changed.status_code == 409
        assert changed.json() == {"error": "Idempotency-Key was already used with a different payload"}
    second = put(admin, "configurations", {"changed": True}, "save-2", revision=1, schema_version=2)
    assert second.status_code == 200 and second.json()["updated_at"] >= first.json()["updated_at"]
    # A late retry of the first write returns its original response and never overwrites newer content.
    assert put(admin, "configurations", SAMPLE, "save-1").json() == first.json()
    assert admin.get(f"{ROOT}/configurations").json() == {**second.json(), "body": {"changed": True}}

    # Keys are scoped to the owner and the document, and a DELETE cannot reuse a PUT key.
    assert put(admin, "other", SAMPLE, "save-1").status_code == 200
    assert delete(admin, "configurations", "save-2").status_code == 409
    assert delete(admin, "configurations", "remove").status_code == 204
    retried = delete(admin, "configurations", "remove")
    assert retried.status_code == 204 and retried.content == b""
    assert delete(admin, "configurations", "remove-again").json() == {"error": "workspace document not found"}
    assert admin.get(f"{ROOT}/configurations").status_code == 404
    assert [item["name"] for item in admin.get(ROOT).json()["items"]] == ["other"]

    with session_scope() as session:
        receipts = session.scalars(select(MutationReceipt).where(MutationReceipt.route.like(f"{ROOT}/%"))).all()
        assert len(receipts) == 4 and all("body" not in receipt.response for receipt in receipts)
        assert session.scalar(select(func.count()).select_from(WorkspaceDocument)) == 1

    entries = [e for e in admin.get("/api/v1/audit").json() if e["action"].startswith("workspace_document.")]
    entries.reverse()
    assert [(e["action"], e["details"]) for e in entries] == [
        ("workspace_document.put", {"name": "configurations", "schema_version": 1, "revision": 1, "size_bytes": compact_size(SAMPLE), "created": True}),
        ("workspace_document.put", {"name": "configurations", "schema_version": 2, "revision": 2, "size_bytes": compact_size({"changed": True}), "created": False}),
        ("workspace_document.put", {"name": "other", "schema_version": 1, "revision": 1, "size_bytes": compact_size(SAMPLE), "created": True}),
        ("workspace_document.delete", {"name": "configurations", "revision": 2}),
    ]  # fmt: skip
    assert all(e["actor_email"] == "admin@example.com" and e["target"].startswith("wsd_") for e in entries)
    assert entries[0]["target"] == entries[1]["target"] == entries[3]["target"] != entries[2]["target"]


def test_body_is_read_and_decoded_only_after_authentication_and_admission(app, admin, monkeypatch):
    """A refused PUT never consumes its upload, let alone decodes it: authentication, role, client header,
    name, idempotency key, precondition, content type and the write budget all come first."""
    decoded: list[int] = []
    original = service.decode
    monkeypatch.setattr(service, "decode", lambda raw: decoded.append(len(raw)) or original(raw))
    payload = json.dumps({"schema_version": 1, "body": {"blob": "x" * (MAX_BODY_BYTES - 11)}}).encode()
    assert MAX_BODY_BYTES < len(payload) <= DOCUMENT_BODY
    consumed: list[int] = []

    def upload():
        consumed.append(len(payload))
        yield payload

    def attempt(client, name="configurations", **headers):
        merged = {**WEB, **CREATE, "Content-Type": "application/json", "Idempotency-Key": "upload", **headers}
        sent = {key: value for key, value in merged.items() if value is not None}
        return client.put(f"{ROOT}/{name}", content=upload(), headers=sent)

    viewer = user_client(app, admin, "uploader@example.com", "viewer")
    stranger = TestClient(app)
    stranger.headers.update({"Authorization": "Bearer cva_not-a-token"})
    refusals = [
        (attempt(TestClient(app)), 401),
        (attempt(stranger), 401),
        (attempt(viewer, **{"X-Convoy-Client": None}), 403),
        (attempt(viewer, name="Not_A_Name"), 422),
        (attempt(viewer, **{"Idempotency-Key": None}), 422),
        (attempt(viewer, **{"If-None-Match": None}), 428),
        (attempt(viewer, **{"If-Match": '"1"'}), 422),
        (attempt(viewer, **{"Content-Type": "text/plain"}), 422),
    ]
    assert [response.status_code for response, _ in refusals] == [status for _, status in refusals]
    # A declared-length upload from an anonymous caller is refused without decoding as well.
    anonymous = TestClient(app).put(
        f"{ROOT}/configurations", content=payload, headers={**WEB, **CREATE, "Content-Type": "application/json"}
    )
    assert anonymous.status_code == 401
    assert consumed == [] and decoded == []

    accepted = attempt(viewer)
    assert accepted.status_code == 200 and accepted.json()["size_bytes"] == MAX_BODY_BYTES
    assert consumed == [len(payload)] and decoded == [len(payload)]

    monkeypatch.setattr(service, "WRITE_LIMIT", 1)
    throttled = attempt(viewer, **{"If-None-Match": None, "If-Match": '"1"', "Idempotency-Key": "again"})
    assert throttled.status_code == 429
    assert consumed == [len(payload)] and decoded == [len(payload)]


def test_writes_are_rate_limited_per_account(app, admin, monkeypatch):
    monkeypatch.setattr(service, "WRITE_LIMIT", 3)
    viewer = user_client(app, admin, "busy@example.com", "viewer")
    for index in range(3):
        assert put(viewer, f"doc-{index}", {"index": index}, f"create-{index}").status_code == 200
    for limited in (put(viewer, "doc-3", {}, "create-3"), delete(viewer, "doc-0", "delete-0")):
        assert limited.status_code == 429
        assert limited.json() == {"error": "too many workspace document writes (at most 3 per 10 minutes)"}
        assert 0 < int(limited.headers["Retry-After"]) <= service.WRITE_WINDOW_S
    # Reads are not limited, and every account has its own budget.
    assert [item["name"] for item in viewer.get(ROOT).json()["items"]] == ["doc-0", "doc-1", "doc-2"]
    assert put(admin, "configurations", SAMPLE, "admin-create").status_code == 200
    # Refused writes left no receipt; once the window has passed the same keys go through.
    with session_scope() as session, write_txn(session):
        throttle = session.get(Throttle, f"workspace-documents:{user_id(viewer)}")
        throttle.window_start -= timedelta(seconds=service.WRITE_WINDOW_S + 1)
    assert put(viewer, "doc-3", {}, "create-3").status_code == 200
    assert delete(viewer, "doc-0", "delete-0").status_code == 204


def test_document_receipts_expire_and_are_capped_per_account(app, admin, monkeypatch):
    monkeypatch.setattr(service, "MAX_RECEIPTS", 3)
    viewer = user_client(app, admin, "other@example.com", "viewer")
    assert put(viewer, "configurations", SAMPLE, "viewer-save").status_code == 200
    project = admin.post("/api/v1/projects", json={"name": "Receipts"}, headers={**WEB, "Idempotency-Key": "project"})
    assert project.status_code == 201
    assert put(admin, "configurations", SAMPLE, "save-1").status_code == 200
    for revision in range(1, 5):
        assert put(admin, "configurations", {"r": revision}, f"save-{revision + 1}", revision=revision).status_code == 200

    def keys(owner=None):
        with session_scope() as session:
            query = select(MutationReceipt.key).order_by(MutationReceipt.key)
            if owner is not None:
                query = query.where(MutationReceipt.owner_user_id == owner)
            return session.scalars(query).all()

    admin_id = user_id(admin)
    # The newest MAX_RECEIPTS document receipts are kept; other routes and accounts are untouched.
    assert keys(admin_id) == ["project", "save-3", "save-4", "save-5"]
    assert keys(user_id(viewer)) == ["viewer-save"]
    # Without its receipt, a late retry is decided by its precondition: no duplicate, no overwrite.
    late = put(admin, "configurations", SAMPLE, "save-1")
    assert late.status_code == 412
    assert admin.get(f"{ROOT}/configurations").json()["body"] == {"r": 4}
    assert put(admin, "configurations", {"r": 4}, "save-5", revision=4).json()["revision"] == 5  # still replayed

    # Receipts older than RECEIPT_TTL are removed by the account's next document write.
    with session_scope() as session, write_txn(session):
        for receipt in session.scalars(select(MutationReceipt)):
            receipt.created_at = aware(receipt.created_at) - service.RECEIPT_TTL - timedelta(minutes=1)
    assert delete(admin, "configurations", "remove", revision=5).status_code == 204
    assert keys(admin_id) == ["project", "remove"]
    assert keys(user_id(viewer)) == ["viewer-save"]


def test_existing_v5_database_gains_workspace_documents_at_startup(app, admin, settings):
    """ADDITIVE under schema version 5: startup completes a database that predates the table, without a
    version bump or rebuild, and `convoy-server migrate` afterwards reports nothing to do."""
    from convoy_server.app import create_app
    from convoy_server.migrations import migrate_database

    assert put(admin, "configurations", SAMPLE, "before-upgrade").status_code == 200
    reset_engine()
    path = settings.data_dir / "convoy.db"
    connection = sqlite3.connect(path)
    users = connection.execute("SELECT id, email, role FROM users ORDER BY id").fetchall()
    receipts = connection.execute("SELECT COUNT(*) FROM platform_mutation_receipts").fetchone()
    connection.execute("DROP TABLE workspace_documents")
    connection.commit()
    assert connection.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
    connection.close()

    engine = make_engine(settings)
    try:
        plan = migrations.plan_migration(engine)
        assert plan["add_tables"] == ["workspace_documents"] and not plan["add_columns"]
        assert not plan["needs_rebuild"] and not plan["needs_data_migration"]
        applied = migrations.ensure_schema(engine)
        assert applied["add_tables"] == ["workspace_documents"] and not applied["rebuild"]
        assert applied["user_version"] == migrations.user_version(engine) == migrations.SCHEMA_VERSION
    finally:
        engine.dispose()
    assert migrate_database(settings)["applied"] == {"unchanged": True, "user_version": migrations.SCHEMA_VERSION}
    assert not list(settings.data_dir.glob("convoy.pre-migrate-*.db"))

    connection = sqlite3.connect(path)
    assert connection.execute("SELECT id, email, role FROM users ORDER BY id").fetchall() == users
    assert connection.execute("SELECT COUNT(*) FROM platform_mutation_receipts").fetchone() == receipts
    columns = {row[1]: (row[2], row[3]) for row in connection.execute("PRAGMA table_info(workspace_documents)")}
    assert columns["revision"] == ("INTEGER", 1) and columns["body"] == ("TEXT", 1)
    indexes = {row[1]: row[2] for row in connection.execute("PRAGMA index_list(workspace_documents)")}
    assert indexes["ix_workspace_documents_owner_user_id"] == 0
    unique = [
        [column[2] for column in connection.execute(f'PRAGMA index_info("{name}")')]
        for name, flag in indexes.items()
        if flag
    ]
    assert ["owner_user_id", "name"] in unique
    assert connection.execute("PRAGMA foreign_key_list(workspace_documents)").fetchone()[2:5] == (
        "users", "owner_user_id", "id"
    )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()

    with TestClient(create_app(settings, start_scheduler=False)) as restarted:
        login(restarted)
        assert restarted.get(f"{ROOT}/configurations").status_code == 404
        assert put(restarted, "configurations", SAMPLE, "after-upgrade").status_code == 200


def test_services_starting_together_both_complete_the_additive_upgrade(settings):
    """Deploys recreate the api and evaluations services back to back on one database. Both may plan the
    same additive table before either commits; the second must finish instead of failing on it."""
    engine = make_engine(settings)
    try:
        migrations.ensure_schema(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE workspace_documents")
    finally:
        engine.dispose()
    api, evaluations = make_engine(settings), make_engine(settings)
    try:
        plans = [migrations.plan_migration(api), migrations.plan_migration(evaluations)]
        assert [plan["add_tables"] for plan in plans] == [["workspace_documents"]] * 2
        migrations.apply_migration(api, plans[0], allow_rebuild=False)
        finished = migrations.apply_migration(evaluations, plans[1], allow_rebuild=False)
        assert finished["user_version"] == migrations.user_version(evaluations) == migrations.SCHEMA_VERSION
        assert migrations.plan_migration(evaluations)["empty"]
    finally:
        api.dispose()
        evaluations.dispose()


def test_previous_release_starts_on_a_database_with_workspace_documents(admin, settings, monkeypatch):
    """Rollback keeps the data directory: the release before workspace documents (schema 5, no workspace
    table in its metadata) must accept the upgraded database without refusal, rebuild or data loss. A
    later schema bump fails here on purpose: it ends rollback to that release and needs a decision."""
    assert put(admin, "configurations", SAMPLE, "before-rollback").status_code == 200
    reset_engine()
    previous = {name: table for name, table in Base.metadata.tables.items() if name != "workspace_documents"}
    monkeypatch.setattr(migrations, "_model_tables", lambda: previous)
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 5)
    engine = make_engine(settings)
    try:
        assert migrations.ensure_schema(engine) == {"user_version": 5, "unchanged": True}
    finally:
        engine.dispose()
    connection = sqlite3.connect(settings.data_dir / "convoy.db")
    stored = connection.execute("SELECT name, schema_version, revision, body FROM workspace_documents").fetchall()
    connection.close()
    assert stored == [("configurations", 1, 1, compact(SAMPLE))]
