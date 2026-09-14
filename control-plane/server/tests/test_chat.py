from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from conftest import WEB, login, make_user
from convoy_server.db import session_scope, write_txn
from convoy_server.ids import utcnow
from convoy_server.models import Device, Installation, Release
from convoy_server.security import hash_token
from convoy_server.services import chat
from fastapi.testclient import TestClient


@pytest.fixture
def physical(app):
    secret = secrets.token_urlsafe(32)
    with session_scope() as db, write_txn(db):
        db.add(
            Release(
                id="rel_chat",
                name="Local Qwen",
                version="1",
                digest="d" * 64,
                spec={"config": {"ctx_size": 2048, "n_predict": 128}},
            )
        )
        db.add(
            Device(
                id="dev_chat",
                name="Jetson",
                credential_hash=hash_token(secret),
                simulated=False,
                binding_epoch=1,
                live_at=utcnow(),
                observed_health="ok",
                observed_active_release_id="rel_chat",
                expected_active_release_id="rel_failed",
                observed={
                    "gateway": {"mode": "production"},
                    "chat": {"supported": True, "protocol_version": 1},
                },
            )
        )
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer cvd_dev_chat_{secret}"})
    return client


def submit(client, **extra):
    body = {
        "request_id": str(uuid4()),
        "expected_release_id": "rel_chat",
        "messages": [{"role": "user", "content": "private prompt"}],
        "max_tokens": 128,
        **extra,
    }
    return client.post("/api/v1/devices/dev_chat/chat", json=body, headers=WEB), body


def claim(client):
    return client.post("/api/agent/v1/chat/claim", json={"active_release_id": "rel_chat"})


def finish(client, request, **extra):
    body = {
        "claim_token": request["claim_token"],
        "release_id": "rel_chat",
        "status": "succeeded",
        "content": "private response",
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        "metrics": {"latency_ms": 150.0, "queue_ms": 0.0, "ttft_ms": None},
        **extra,
    }
    return client.post(f"/api/agent/v1/chat/{request['id']}/result", json=body)


def test_chat_end_to_end_identity_bound_and_idempotent(admin, physical):
    info = admin.get("/api/v1/chat/devices").json()["devices"][0]
    assert info["eligible"] and info["context_window"] == 2048
    # Healthy observed baseline remains eligible even when desired release is a failed candidate.
    response, body = submit(admin)
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    repeat = admin.post("/api/v1/devices/dev_chat/chat", json=body, headers=WEB)
    assert repeat.json() == response.json()
    changed = {**body, "messages": [{"role": "user", "content": "different"}]}
    assert admin.post("/api/v1/devices/dev_chat/chat", json=changed, headers=WEB).status_code == 409
    job = claim(physical).json()["request"]
    assert job["messages"] == body["messages"]
    assert claim(physical).json() == {"request": None}
    assert finish(physical, job).status_code == 200
    assert finish(physical, job).status_code == 200
    assert finish(physical, job, content="changed").status_code == 409
    result = admin.get(f"/api/v1/devices/dev_chat/chat/{job['id']}").json()
    assert result["status"] == "succeeded" and result["content"] == "private response"
    # No prompt or result in the backed-up fleet database.
    from convoy_server.config import get_settings

    assert b"private prompt" not in (get_settings().data_dir / "convoy.db").read_bytes()
    assert b"private response" not in (get_settings().data_dir / "convoy.db").read_bytes()


def test_auth_csrf_and_creator_isolation(app, admin, physical):
    make_user(admin, "operator@example.com", "operator")
    make_user(admin, "other@example.com", "operator")
    make_user(admin, "viewer@example.com", "viewer")
    owner = login(TestClient(app), "operator@example.com", "password-123")
    other = login(TestClient(app), "other@example.com", "password-123")
    viewer = login(TestClient(app), "viewer@example.com", "password-123")
    r, body = submit(owner)
    assert r.status_code == 202
    assert submit(viewer)[0].status_code == 403
    assert submit(TestClient(app))[0].status_code == 401
    assert owner.post("/api/v1/devices/dev_chat/chat", json=body).status_code == 403
    url = f"/api/v1/devices/dev_chat/chat/{body['request_id']}"
    assert other.get(url).status_code == 404
    assert viewer.get(url).status_code == 404
    assert owner.get(url).status_code == 200
    assert admin.get(url).status_code == 200
    assert claim(admin).status_code == 401
    assert physical.post("/api/v1/devices/dev_chat/chat", json=body).status_code == 401


def test_claim_is_atomic_across_connections(admin, physical):
    response, _ = submit(admin)
    assert response.status_code == 202
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda _: claim(physical), range(8)))
    assert all(r.status_code == 200 for r in replies)
    assert sum(r.json()["request"] is not None for r in replies) == 1


@pytest.mark.parametrize("change", ["binding", "restore", "quarantine"])
def test_result_cannot_cross_identity_or_restore(admin, physical, change):
    submit(admin)
    job = claim(physical).json()["request"]
    with session_scope() as db, write_txn(db):
        if change == "binding":
            db.get(Device, "dev_chat").binding_epoch += 1
        elif change == "restore":
            db.get(Installation, 1).restored_from = {"backup_id": "new-restore"}
        else:
            db.get(Installation, 1).quarantined_at = utcnow()
    assert finish(physical, job).status_code == 409


def test_foreign_device_cannot_claim_result(admin, physical, app):
    submit(admin)
    job = claim(physical).json()["request"]
    secret = secrets.token_urlsafe(32)
    with session_scope() as db, write_txn(db):
        db.add(Device(id="dev_other", name="Other", credential_hash=hash_token(secret)))
    other = TestClient(app)
    other.headers.update({"Authorization": f"Bearer cvd_dev_other_{secret}"})
    assert finish(other, job).status_code == 404


def test_expiry_clears_content_but_keeps_idempotency(admin, physical, monkeypatch):
    response, body = submit(admin)
    job = claim(physical).json()["request"]
    now = chat.time.time()
    monkeypatch.setattr(chat.time, "time", lambda: now + 121)
    result = admin.get(f"/api/v1/devices/dev_chat/chat/{body['request_id']}").json()
    assert result["status"] == "expired" and result["content"] is None
    assert finish(physical, job).status_code == 404
    again = admin.post("/api/v1/devices/dev_chat/chat", json=body, headers=WEB)
    assert again.json()["status"] == "expired"
    assert claim(physical).json()["request"] is None
    with chat.transaction() as c:
        row = c.execute("SELECT * FROM requests").fetchone()
        assert row["payload"] is None and row["result"] is None and row["claim_token"] is None


def test_caps_and_no_tools(admin, physical, monkeypatch):
    assert submit(admin, max_tokens=129)[0].status_code == 422
    assert submit(admin, max_tokens=True)[0].status_code == 422
    assert submit(admin, tools=[{}])[0].status_code == 422
    assert submit(admin, messages=[{"role": "system", "content": "x"}])[0].status_code == 422
    assert submit(admin, messages=[{"role": "user", "content": "界" * 3000}])[0].status_code == 422
    monkeypatch.setattr(chat, "MAX_ROWS", 0)
    assert submit(admin)[0].status_code == 429


@pytest.mark.parametrize(
    "field,value",
    [
        ("simulated", True),
        ("observed_health", "failed"),
        ("active_operation_id", "op_busy"),
        ("live_at", None),
    ],
)
def test_not_ready_gates(admin, physical, field, value):
    with session_scope() as db, write_txn(db):
        setattr(db.get(Device, "dev_chat"), field, value)
    assert not admin.get("/api/v1/chat/devices").json()["devices"][0]["eligible"]
    assert submit(admin)[0].status_code == 409
    assert claim(physical).status_code == 409


def test_expected_release_and_response_limits(admin, physical):
    assert submit(admin, expected_release_id="stale-release")[0].status_code == 409
    submit(admin)
    job = claim(physical).json()["request"]
    assert finish(physical, job, metrics={"latency_ms": -1}).status_code == 422
    assert finish(physical, job, content="界" * 12000).status_code == 422
    assert (
        finish(
            physical, job, usage={"prompt_tokens": 1, "completion_tokens": 129, "total_tokens": 130}
        ).status_code
        == 422
    )


def test_relay_survives_new_api_app_without_redelivery(admin, physical, settings):
    from convoy_server.app import create_app

    submit(admin)
    job = claim(physical).json()["request"]
    other_app = create_app(settings, start_scheduler=False)
    other = TestClient(other_app)
    other.headers.update(physical.headers)
    assert claim(other).json()["request"] is None
    assert finish(other, job).status_code == 200


def test_completed_content_expires_and_results_do_not_fill_database(admin, physical, monkeypatch):
    response, body = submit(admin)
    job = claim(physical).json()["request"]
    assert finish(physical, job).status_code == 200
    now = chat.time.time()
    monkeypatch.setattr(chat.time, "time", lambda: now + 301)
    result = admin.get(f"/api/v1/devices/dev_chat/chat/{body['request_id']}").json()
    assert result["status"] == "expired" and result["content"] is None
    with chat.transaction() as c:
        row = c.execute("SELECT payload,result FROM requests").fetchone()
        assert tuple(row) == (None, None)


def test_body_limit_before_decode_including_chunked(admin, physical):
    response = admin.post("/api/v1/devices/dev_chat/chat", content=b"x" * (256 * 1024 + 1), headers=WEB)
    assert response.status_code == 413
    response = admin.post(
        "/api/v1/devices/dev_chat/chat", content=iter([b"x" * 200000, b"y" * 200000]), headers=WEB
    )
    assert response.status_code == 413


def test_storage_is_private_and_refuses_symlink(settings, tmp_path):
    import stat

    path = chat._path()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.unlink()
    target = tmp_path / "untouched"
    target.write_text("untouched")
    path.symlink_to(target)
    with pytest.raises(OSError):
        chat._path()
    assert target.read_text() == "untouched"
    path.unlink()


def test_capacity_reserves_bounded_response_space(admin, physical, monkeypatch):
    monkeypatch.setattr(chat, "MAX_BYTES", chat.MAX_RESULT_BYTES - 1)
    assert submit(admin)[0].status_code == 429


@pytest.mark.parametrize("change", ["binding", "restore"])
def test_previous_scope_content_cannot_be_read_or_replayed(admin, physical, change):
    response, body = submit(admin)
    job = claim(physical).json()["request"]
    assert finish(physical, job).status_code == 200
    url = f"/api/v1/devices/dev_chat/chat/{job['id']}"
    assert admin.get(url).json()["content"] == "private response"
    with session_scope() as db, write_txn(db):
        if change == "binding":
            db.get(Device, "dev_chat").binding_epoch += 1
        else:
            db.get(Installation, 1).restored_from = {"backup_id": "restored-main-database"}
    response = admin.get(url)
    assert response.status_code == 409 and "private response" not in response.text
    replay = admin.post("/api/v1/devices/dev_chat/chat", json=body, headers=WEB)
    assert replay.status_code == 409 and "private response" not in replay.text
