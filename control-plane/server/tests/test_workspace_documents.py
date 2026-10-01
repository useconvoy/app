"""Owner-scoped workspace documents across authenticated HTTP, both credentials and the SQLite upgrade."""

from __future__ import annotations

import json
import sqlite3

from conftest import WEB, api_token_client, login, make_user
from convoy_server import migrations
from convoy_server.chat_body_limit import DOCUMENT_BODY
from convoy_server.db import make_engine, reset_engine, session_scope
from convoy_server.models import Base
from convoy_server.platform_models import MutationReceipt
from convoy_server.services.workspace_documents import MAX_BODY_BYTES, MAX_DEPTH, MAX_DOCUMENTS
from convoy_server.workspace_models import WorkspaceDocument
from fastapi.testclient import TestClient
from sqlalchemy import func, select

ROOT = "/api/v1/workspace-documents"
SAMPLE = {
    "schemaVersion": 1,
    "configurations": [{"id": "sample-a", "robots": [{"name": "Sample bench", "latency_ms": None}]}],
}


def put(client, name, body, key, *, schema_version=1, headers=WEB):
    payload = {"schema_version": schema_version, "body": body}
    return client.put(f"{ROOT}/{name}", json=payload, headers={**headers, "Idempotency-Key": key})


def delete(client, name, key, *, headers=WEB):
    return client.delete(f"{ROOT}/{name}", headers={**headers, "Idempotency-Key": key})


def user_client(app, admin, email, role):
    make_user(admin, email, role)
    return login(TestClient(app), email, "password-123")


def compact_size(body) -> int:
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())


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


def test_documents_are_owner_scoped_for_every_role_and_credential(app, admin):
    viewer = user_client(app, admin, "viewer@example.com", "viewer")
    operator = user_client(app, admin, "operator@example.com", "operator")
    created = put(viewer, "configurations", SAMPLE, "viewer-create")
    assert created.status_code == 200, created.text
    document = created.json()
    assert list(document) == ["name", "schema_version", "body", "size_bytes", "updated_at"]
    assert document["name"] == "configurations" and document["schema_version"] == 1
    assert document["body"] == SAMPLE and document["size_bytes"] == compact_size(SAMPLE)
    assert document["updated_at"].endswith("Z")
    assert viewer.get(f"{ROOT}/configurations").json() == document

    # Administrators and operators have their own namespace: they cannot list, read or delete it.
    for other in (admin, operator):
        assert other.get(ROOT).json() == {"items": []}
        missing = other.get(f"{ROOT}/configurations")
        assert missing.status_code == 404 and missing.json() == {"error": "workspace document not found"}
        assert delete(other, "configurations", "not-yours").status_code == 404
    own = put(admin, "configurations", {"owner": "admin"}, "admin-create")
    assert own.status_code == 200 and own.json()["body"] == {"owner": "admin"}
    assert viewer.get(f"{ROOT}/configurations").json() == document

    # An API token acts as its user without the browser header; a browser session needs it to write.
    token = api_token_client(app, viewer, name="workspace")
    assert token.get(f"{ROOT}/configurations").json() == document
    replaced = put(token, "configurations", {"schemaVersion": 1, "configurations": []}, "token-save", headers={})
    assert replaced.status_code == 200, replaced.text
    assert viewer.get(f"{ROOT}/configurations").json() == replaced.json()
    for response in (
        viewer.put(
            f"{ROOT}/configurations",
            json={"schema_version": 1, "body": {}},
            headers={"Idempotency-Key": "without-client-header"},
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
    ]
    for index, payload in enumerate(payloads):
        response = admin.put(
            f"{ROOT}/configurations", json=payload, headers={**WEB, "Idempotency-Key": f"invalid-{index}"}
        )
        assert response.status_code == 422, (payload, response.text)
        assert response.json()["error"].startswith("invalid request: ") and response.json()["detail"]
    shaped = admin.put(
        f"{ROOT}/configurations", json={"schema_version": 1, "body": []}, headers={**WEB, "Idempotency-Key": "list"}
    )
    assert shaped.json()["error"] == "invalid request: body: Input should be a valid dictionary"
    assert put(admin, "configurations", {}, "largest-version", schema_version=2**31 - 1).status_code == 200

    raw = {"nan": b'{"x":NaN}', "infinity": b'{"x":[-Infinity]}', "surrogate": b'{"x":"\\ud800"}'}
    for label, body in raw.items():
        response = admin.put(
            f"{ROOT}/raw-{label}",
            content=b'{"schema_version":1,"body":' + body + b"}",
            headers={**WEB, "Idempotency-Key": label, "Content-Type": "application/json"},
        )
        assert response.status_code == 422, (label, response.text)
        expected = "valid Unicode" if label == "surrogate" else "non-finite numbers are not accepted"
        assert expected in response.json()["error"]
    emoji = admin.put(
        f"{ROOT}/raw-pair",
        content=b'{"schema_version":1,"body":{"x":"\\ud83d\\ude00"}}',
        headers={**WEB, "Idempotency-Key": "pair", "Content-Type": "application/json"},
    )
    assert emoji.status_code == 200 and emoji.json()["body"] == {"x": "\U0001f600"}

    assert put(admin, "deep", nested(MAX_DEPTH), "deepest").status_code == 200
    too_deep = put(admin, "deeper", nested(MAX_DEPTH + 1), "too-deep")
    assert too_deep.status_code == 422 and too_deep.json() == {"error": f"body nesting exceeds {MAX_DEPTH} levels"}

    missing = admin.put(f"{ROOT}/configurations", json={"schema_version": 1, "body": {}}, headers=WEB)
    assert missing.status_code == 422 and "Idempotency-Key" in missing.json()["error"]
    for key in ("", "x" * 129, "has space"):
        assert put(admin, "configurations", {}, key).status_code == 422
        assert delete(admin, "configurations", key).status_code == 422
    assert admin.delete(f"{ROOT}/configurations", headers=WEB).status_code == 422


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
    formatted = admin.put(
        f"{ROOT}/formatted",
        content=pretty,
        headers={**WEB, "Idempotency-Key": "formatted", "Content-Type": "application/json"},
    )
    assert formatted.status_code == 200 and formatted.json()["size_bytes"] == compact_size(rows)
    # Larger raw requests are refused before decoding, with or without a declared length.
    assert MAX_BODY_BYTES < DOCUMENT_BODY <= MAX_BODY_BYTES + 64 * 1024
    for content in (b"x" * (DOCUMENT_BODY + 1), iter([b"x" * (DOCUMENT_BODY + 1)])):
        response = admin.put(
            f"{ROOT}/oversized", content=content, headers={**WEB, "Idempotency-Key": "oversized"}
        )
        assert response.status_code == 413 and "too large" in response.json()["error"]
    # The larger allowance is exact: other management writes keep the ordinary bound.
    assert admin.post("/api/v1/projects", content=b"x" * (256 * 1024 + 1), headers=WEB).status_code == 413

    viewer = user_client(app, admin, "limits@example.com", "viewer")
    for index in range(MAX_DOCUMENTS):
        assert put(viewer, f"doc-{index:02d}", {"index": index}, f"create-{index}").status_code == 200
    refused = put(viewer, "one-more", {}, "one-more")
    assert refused.status_code == 409
    assert refused.json() == {"error": f"workspace document limit reached ({MAX_DOCUMENTS} per account); delete one first"}
    assert put(viewer, "doc-00", {"index": "replaced"}, "replace-at-limit").status_code == 200
    assert delete(viewer, "doc-15", "free-one").status_code == 204
    # The refused attempt left no receipt, so the same key now performs the creation.
    assert put(viewer, "one-more", {}, "one-more").status_code == 200
    items = viewer.get(ROOT).json()["items"]
    assert [item["name"] for item in items] == [f"doc-{index:02d}" for index in range(15)] + ["one-more"]
    assert all(list(item) == ["name", "schema_version", "size_bytes", "updated_at"] for item in items)
    assert items[0]["size_bytes"] == compact_size({"index": "replaced"})


def test_writes_are_idempotent_audited_and_keep_compact_receipts(admin):
    first = put(admin, "configurations", SAMPLE, "save-1")
    assert first.status_code == 200
    again = put(admin, "configurations", SAMPLE, "save-1")
    assert again.status_code == 200 and again.json() == first.json()
    for changed in (put(admin, "configurations", {"changed": True}, "save-1"),
                    put(admin, "configurations", SAMPLE, "save-1", schema_version=2)):
        assert changed.status_code == 409
        assert changed.json() == {"error": "Idempotency-Key was already used with a different payload"}
    second = put(admin, "configurations", {"changed": True}, "save-2", schema_version=2)
    assert second.status_code == 200 and second.json()["updated_at"] >= first.json()["updated_at"]
    # A late retry of the first write returns its original response and never overwrites newer content.
    assert put(admin, "configurations", SAMPLE, "save-1").json() == first.json()
    assert admin.get(f"{ROOT}/configurations").json() == second.json()

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
        ("workspace_document.put", {"name": "configurations", "schema_version": 1, "size_bytes": compact_size(SAMPLE), "created": True}),
        ("workspace_document.put", {"name": "configurations", "schema_version": 2, "size_bytes": compact_size({"changed": True}), "created": False}),
        ("workspace_document.put", {"name": "other", "schema_version": 1, "size_bytes": compact_size(SAMPLE), "created": True}),
        ("workspace_document.delete", {"name": "configurations"}),
    ]  # fmt: skip
    assert all(e["actor_email"] == "admin@example.com" and e["target"].startswith("wsd_") for e in entries)
    assert entries[0]["target"] == entries[1]["target"] == entries[3]["target"] != entries[2]["target"]


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
    stored = connection.execute("SELECT name, schema_version, body FROM workspace_documents").fetchall()
    connection.close()
    assert [(name, version, json.loads(body)) for name, version, body in stored] == [("configurations", 1, SAMPLE)]
