from __future__ import annotations

from conftest import WEB, FakeAgent, api_token_client, enrollment_token, login, make_user


def test_login_me_logout(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    login(client)
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    assert r.json()["installation"]["simulator"] is True
    r = client.post("/api/v1/auth/logout")
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_throttle(client, settings):
    for _ in range(settings.login_throttle):
        assert (
            client.post(
                "/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong"}
        ).status_code
        == 429
    )


def test_roles_enforced_server_side(app, admin):
    make_user(admin, "viewer@example.com", "viewer")
    make_user(admin, "op@example.com", "operator")
    from fastapi.testclient import TestClient

    viewer = login(TestClient(app), "viewer@example.com", "password-123")
    assert viewer.get("/api/v1/users").status_code == 200
    assert (
        viewer.post("/api/v1/enrollments", json={"label": "x", "simulated": True}, headers=WEB).status_code
        == 403
    )
    assert (
        viewer.post(
            "/api/v1/users",
            json={"email": "z@example.com", "role": "admin", "password": "password-123"},
            headers=WEB,
        ).status_code
        == 403
    )
    op = login(TestClient(app), "op@example.com", "password-123")
    assert (
        op.post("/api/v1/enrollments", json={"label": "x", "simulated": True}, headers=WEB).status_code == 201
    )
    assert (
        op.post(
            "/api/v1/users",
            json={"email": "z@example.com", "role": "admin", "password": "password-123"},
            headers=WEB,
        ).status_code
        == 403
    )


def test_csrf_header_required_for_browser_sessions(app, admin):
    r = admin.post("/api/v1/enrollments", json={"label": "x", "simulated": True})
    assert r.status_code == 403 and "X-Convoy-Client" in r.json()["error"]
    tok = api_token_client(app, admin)
    # API tokens are not cookie sessions: no CSRF header needed
    assert tok.post("/api/v1/enrollments", json={"label": "x", "simulated": True}).status_code == 201


def test_api_token_revocation_and_disable_user(app, admin):
    u = make_user(admin, "op@example.com", "operator")
    from fastapi.testclient import TestClient

    op = login(TestClient(app), "op@example.com", "password-123")
    tokc = api_token_client(app, op)
    assert tokc.get("/api/v1/auth/me").status_code == 200
    tid = op.get("/api/v1/tokens").json()[0]["id"]
    assert op.delete(f"/api/v1/tokens/{tid}", headers=WEB).status_code == 200
    assert tokc.get("/api/v1/auth/me").status_code == 401
    # disabling a user revokes their sessions
    assert admin.patch(f"/api/v1/users/{u['id']}", json={"disabled": True}, headers=WEB).status_code == 200
    assert op.get("/api/v1/auth/me").status_code == 401


def test_enrollment_one_use_and_lost_response_replay(app, admin):
    tok = enrollment_token(admin)
    a = FakeAgent(app)
    r = a.enroll(tok)
    assert r.status_code == 200 and r.json()["replay"] is False
    dev = a.device_id
    # lost response: identical retry returns the same device, never a new credential
    r2 = a.enroll(tok)
    assert r2.status_code == 200 and r2.json() == {**r2.json(), "device_id": dev, "replay": True}
    # a different agent replaying the consumed token is rejected
    b = FakeAgent(app, "impostor")
    assert b.enroll(tok).status_code == 409
    # same request id but different secret hash is rejected too
    c = FakeAgent(app, "impostor2")
    c.request_id = a.request_id
    assert c.enroll(tok).status_code == 409
    # the enrolled device authenticates; the impostor does not
    assert admin.get(f"/api/v1/devices/{dev}").status_code == 200
    assert (
        a.client.post(
            "/api/agent/v1/report", json={"seq": 1, "boot_id": a.boot_id, "kind": "heartbeat", "observed": {}}
        ).status_code
        == 200
    )
    assert (
        b.client.post(
            "/api/agent/v1/report", json={"seq": 1, "kind": "heartbeat", "observed": {}}
        ).status_code
        == 401
    )


def test_enrollment_expiry_and_revocation(app, admin):
    r = admin.post("/api/v1/enrollments", json={"label": "t", "simulated": True, "ttl_s": 60}, headers=WEB)
    enr = r.json()
    assert admin.delete(f"/api/v1/enrollments/{enr['id']}", headers=WEB).status_code == 200
    assert FakeAgent(app).enroll(enr["token"]).status_code == 401
    # simulated flag mismatch
    tok = enrollment_token(admin, simulated=True)
    assert FakeAgent(app).enroll(tok, simulated=False).status_code == 409


def test_revoke_credential_then_rebind_keeps_device_id(app, admin):
    tok = enrollment_token(admin)
    a = FakeAgent(app)
    assert a.enroll(tok).status_code == 200
    dev = a.device_id
    assert (
        admin.post(
            f"/api/v1/devices/{dev}/revoke-credential", json={"reason": "lost"}, headers=WEB
        ).status_code
        == 200
    )
    assert (
        a.client.post(
            "/api/agent/v1/report", json={"seq": 2, "kind": "heartbeat", "observed": {}}
        ).status_code
        == 401
    )
    rebind = enrollment_token(admin, rebind_device_id=dev)
    b = FakeAgent(app, "same-robot")
    assert b.enroll(rebind).status_code == 200 and b.device_id == dev
    assert (
        b.client.post(
            "/api/agent/v1/report", json={"seq": 3, "kind": "heartbeat", "observed": {}}
        ).status_code
        == 200
    )
    d = admin.get(f"/api/v1/devices/{dev}").json()
    assert d["rebound_at"] and d["credential_revoked_at"] is None
