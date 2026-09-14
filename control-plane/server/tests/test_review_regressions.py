"""Regression tests for independent review findings R1-R12 (docs/REVIEW_LOG.md)."""

from __future__ import annotations

from conftest import WEB, FakeAgent, enrollment_token, login, make_user
from fastapi.testclient import TestClient
from helpers import deploy_success, enrolled_agent, exec_sha, full_deploy, grant, heartbeat, seed


def test_r1_nonfinite_telemetry_rejected_and_none_allowed(app, admin):
    a = enrolled_agent(app, admin)
    r = a.client.post(
        "/api/agent/v1/report",
        content=b'{"seq":1,"boot_id":"b","telemetry":{"mem_available_mb":1e309}}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    r = a.client.post(
        "/api/agent/v1/report",
        json={"seq": 1, "boot_id": "b", "telemetry": {"mem_available_mb": None, "power_w": 0.0}},
    )
    assert r.status_code == 200
    d = admin.get(f"/api/v1/devices/{a.device_id}").json()
    assert d["last_telemetry"]["mem_available_mb"] is None and d["last_telemetry"]["power_w"] == 0.0
    assert admin.get("/api/v1/devices").status_code == 200
    # nested evidence in outcomes is checked too
    r = a.client.post(
        "/api/agent/v1/operations/op_x/outcome",
        content=b'{"status":"failed","failure":{"code":"X","details":{"v":NaN}}}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code in (404, 422)


def test_r2_seq_validation_and_duplicate_idempotent(app, admin):
    a = enrolled_agent(app, admin)
    assert a.client.post("/api/agent/v1/report", json={"seq": 0, "boot_id": "b"}).status_code == 422
    assert a.client.post("/api/agent/v1/report", json={"seq": 1, "boot_id": "b"}).status_code == 200
    assert (
        a.client.post("/api/agent/v1/report", json={"seq": 1, "boot_id": "b"}).status_code == 200
    )  # exact retry
    assert (
        a.client.post("/api/agent/v1/report", json={"seq": 2, "source_ts": "not-a-time"}).status_code == 422
    )
    assert (
        a.client.post(
            "/api/agent/v1/report", json={"seq": 2, "observed": {"generation": "seven"}}
        ).status_code
        == 422
    )
    assert (
        a.client.post("/api/agent/v1/report", json={"seq": 2, "observed": {"generation": -1}}).status_code
        == 422
    )
    assert admin.get(f"/api/v1/devices/{a.device_id}/reports").status_code == 200


def test_r3_r12_quarantine_revokes_everything_and_console_recovery(app, admin, settings):
    from convoy_server.db import session_scope
    from convoy_server.services.identity import enter_quarantine, recover_admin

    a = enrolled_agent(app, admin)
    op_user = make_user(admin, "op@example.com", "operator")
    open_tok = enrollment_token(admin)
    rebind_tok = enrollment_token(admin, rebind_device_id=a.device_id)
    api = admin.post("/api/v1/tokens", json={"name": "t"}, headers=WEB).json()["token"]
    # snapshot restored: quarantine
    with session_scope() as db:
        enter_quarantine(db, "restore test", {"file": "backup.db"})
    # sessions, API tokens, device credentials, enrollment tokens and passwords are all dead
    assert admin.get("/api/v1/auth/me").status_code == 401
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {api}"})
    assert c.get("/api/v1/auth/me").status_code == 401
    assert a.client.post("/api/agent/v1/report", json={"seq": 5}).status_code == 401
    assert FakeAgent(app).enroll(open_tok).status_code == 401
    assert FakeAgent(app).enroll(rebind_tok).status_code == 401
    assert (
        TestClient(app)
        .post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin-password-1"})
        .status_code
        == 401
    )
    assert (
        TestClient(app)
        .post("/api/v1/auth/login", json={"email": "op@example.com", "password": "password-123"})
        .status_code
        == 401
    )
    # bootstrap env credentials do not bypass recovery
    from convoy_server.services.bootstrap import bootstrap

    with session_scope() as db:
        bootstrap(db, settings)
    assert (
        TestClient(app)
        .post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin-password-1"})
        .status_code
        == 401
    )
    # console recovery sets a NEW password for one admin; dispatch stays paused
    with session_scope() as db:
        recover_admin(db, "admin@example.com", "fresh-recovery-password")
    rec = login(TestClient(app), "admin@example.com", "fresh-recovery-password")
    me = rec.get("/api/v1/auth/me").json()
    assert me["installation"]["quarantined_at"] and me["installation"]["dispatch_paused_at"]
    assert (
        TestClient(app)
        .post("/api/v1/auth/login", json={"email": "op@example.com", "password": "password-123"})
        .status_code
        == 401
    )
    # history preserved; device rebinds with a fresh post-restore token and reports its generation
    d = rec.get(f"/api/v1/devices/{a.device_id}").json()
    assert d["status"] == "credential_revoked" and d["credential_revoked_reason"] == "quarantine"
    new_rebind = enrollment_token(rec, rebind_device_id=a.device_id)
    b = FakeAgent(app, "same-robot")
    assert b.enroll(new_rebind).status_code == 200 and b.device_id == a.device_id
    rep = heartbeat(b, generation=17)
    assert rep["quarantine"] is True and rep["operations"] == []
    assert rec.get(f"/api/v1/devices/{a.device_id}").json()["generation"] == 17
    assert rec.post("/api/v1/admin/quarantine/lift", headers=WEB).status_code == 200
    assert (
        rec.patch(
            f"/api/v1/users/{op_user['id']}",
            json={"disabled": False, "password": "new-password-99"},
            headers=WEB,
        ).status_code
        == 200
    )
    assert (
        login(TestClient(app), "op@example.com", "new-password-99").get("/api/v1/auth/me").status_code == 200
    )


def test_r4_last_admin_invariant_two_admins(app, admin):
    other = make_user(admin, "admin2@example.com", "admin")
    a2 = login(TestClient(app), "admin2@example.com", "password-123")
    me = admin.get("/api/v1/auth/me").json()["user"]
    # admin2 disables admin1 (allowed: admin2 remains) ...
    assert a2.patch(f"/api/v1/users/{me['id']}", json={"disabled": True}, headers=WEB).status_code == 200
    # ... admin1's in-flight request to demote admin2 must now fail: actor no longer an enabled admin
    assert admin.patch(f"/api/v1/users/{other['id']}", json={"role": "viewer"}, headers=WEB).status_code in (
        401,
        403,
    )
    # admin2 cannot remove themselves as the last enabled admin
    assert a2.patch(f"/api/v1/users/{other['id']}", json={"role": "viewer"}, headers=WEB).status_code == 409
    assert a2.patch(f"/api/v1/users/{other['id']}", json={"disabled": True}, headers=WEB).status_code == 409


def test_r5_login_after_password_reset_race(app, admin):
    """Deterministic: verify with the old hash, reset in between, then the session creation step must refuse."""
    from convoy_server.auth import start_session
    from convoy_server.db import session_scope
    from convoy_server.models import User
    from fastapi import HTTPException, Response
    from sqlalchemy import select

    u = make_user(admin, "victim@example.com", "operator")
    with session_scope() as db:
        row = db.scalar(select(User).where(User.email == "victim@example.com"))
        old_hash = row.password_hash
        # attacker's login verified the old password here (paused) ...
        assert (
            admin.patch(
                f"/api/v1/users/{u['id']}", json={"password": "brand-new-password"}, headers=WEB
            ).status_code
            == 200
        )
        # ... and resumes: session creation must fail because the verified hash changed
        try:
            start_session(db, row, Response(), verified_hash=old_hash)
            raise AssertionError("session was created after a password reset")
        except HTTPException as e:
            assert e.status_code == 401
    assert (
        TestClient(app)
        .post("/api/v1/auth/login", json={"email": "victim@example.com", "password": "password-123"})
        .status_code
        == 401
    )


def test_r6_negative_limits_rejected(app, admin):
    a = enrolled_agent(app, admin)
    for path in (
        "/api/v1/operations?limit=-1",
        f"/api/v1/devices/{a.device_id}/logs?limit=0",
        f"/api/v1/devices/{a.device_id}/reports?limit=999999",
        "/api/v1/audit?limit=-5",
    ):
        assert admin.get(path).status_code == 422, path


def test_r7_recover_keeps_expected_active_as_current(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    full_deploy(a, admin, s["release_id"], s["plan_id"])
    full_deploy(a, admin, s["candidate_release_id"], s["candidate_plan_id"], active=s["release_id"])
    r = admin.post(f"/api/v1/devices/{a.device_id}/restore", json={"reason": "test"}, headers=WEB)
    assert r.status_code == 201, r.text
    op = r.json()
    assert op["expected_active_release_id"] == s["candidate_release_id"]  # B is current
    assert op["payload"]["target_release_id"] == s["release_id"]  # A is the target
    assert op["payload"]["recovery_release_id"] == s["release_id"]


def test_r8_reference_coherence_and_explicit_bootstrap(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    dev = a.device_id
    # deploy without plan and without bootstrap -> 422; missing plan -> 404; plan for other release -> 422
    assert (
        admin.post(
            f"/api/v1/devices/{dev}/deploy", json={"release_id": s["release_id"]}, headers=WEB
        ).status_code
        == 422
    )
    assert (
        admin.post(
            f"/api/v1/devices/{dev}/deploy",
            json={"release_id": s["release_id"], "plan_id": "plan_missing"},
            headers=WEB,
        ).status_code
        == 404
    )
    assert (
        admin.post(
            f"/api/v1/devices/{dev}/deploy",
            json={"release_id": s["release_id"], "plan_id": s["candidate_plan_id"]},
            headers=WEB,
        ).status_code
        == 422
    )
    # eval needs the device to run the plan's release
    assert (
        admin.post(f"/api/v1/devices/{dev}/eval", json={"plan_id": s["plan_id"]}, headers=WEB).status_code
        == 409
    )
    # build_required release is never deployable
    rel = admin.post(
        "/api/v1/releases",
        json={
            "name": "src-only",
            "version": "1",
            "model": {
                "source": "fixture",
                "repo": "convoy-sim/qwen2.5-1.5b-instruct-gguf",
                "revision": "0" * 40,
                "files": ["qwen2.5-1.5b-instruct-q4_k_m.sim.gguf"],
            },
            "recipe_id": s["recipe_id"],
            "profile_id": "simulated-host",
        },
        headers=WEB,
    )
    assert (
        rel.status_code == 201
        and rel.json()["build_status"] == "build_required"
        and rel.json()["deployable"] is False
    )
    r = admin.post(
        f"/api/v1/devices/{dev}/deploy", json={"release_id": rel.json()["id"], "bootstrap": True}, headers=WEB
    )
    assert r.status_code == 409 and "build required" in r.json()["error"]["message"]
    # explicit bootstrap is recorded
    op = admin.post(
        f"/api/v1/devices/{dev}/deploy", json={"release_id": s["release_id"], "bootstrap": True}, headers=WEB
    ).json()
    assert op["payload"]["qualification"] == "bootstrap_no_plan"


def test_r9_grant_bindings(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(a)
    assert grant(a, op, boot_id="other-boot").status_code == 409
    assert grant(a, op, seq=a.seq - 1).status_code == 409  # stale context
    assert grant(a, op, active="rel_wrong").status_code == 409
    assert grant(a, op, release_digest="0" * 64).status_code == 409
    assert grant(a, op, plan_digest="0" * 64).status_code == 409
    assert grant(a, op, artifact_sha256="0" * 64).status_code == 409
    # missing boot id is a schema error
    r = a.client.post(f"/api/agent/v1/operations/{op['id']}/grant", json={"nonce": "n" * 12, "seq": a.seq})
    assert r.status_code == 422
    g = grant(a, op, nonce="nonce-1234567")
    assert g.status_code == 200
    body = g.json()
    assert (
        body["operation_id"] == op["id"]
        and body["generation"] == op["generation"]
        and body["boot_id"] == a.boot_id
        and body["target_release_id"] == s["release_id"]
    )
    assert body["release_digest"] == op["payload"]["release_digest"] and body["ttl_s"] <= 30
    # idempotent re-request with the same nonce; a new nonce supersedes
    assert grant(a, op, nonce="nonce-1234567").json()["grant_id"] == body["grant_id"]
    # a foreign device gets 404, not a hint
    b = enrolled_agent(app, admin, "other")
    heartbeat(b)
    assert grant(b, op).status_code == 404


def test_r10_bare_success_rejected_and_pre_grant_failure_allowed(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(a)
    r = a.client.post(f"/api/agent/v1/operations/{op['id']}/outcome", json={"status": "succeeded"})
    assert r.status_code == 409  # no grant yet
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] == op["id"]
    g = grant(a, op).json()
    for bad in (
        {"result": {"active_release_id": "rel_other"}},
        {
            "evidence": {
                "health": "degraded",
                "cutover_ms": 1,
                "runtime": {"binary_sha256": "x" * 64, "build_info": "b"},
                "eval": {"verdict": "passed", "plan_digest": op["payload"]["plan_digest"]},
            }
        },
        {
            "evidence": {
                "health": "ok",
                "cutover_ms": 1,
                "runtime": {"binary_sha256": "x" * 64, "build_info": "b"},
                "eval": {"verdict": "failed", "plan_digest": op["payload"]["plan_digest"]},
            }
        },
        {"grant_id": "grant_other"},
    ):
        r = deploy_success(a, op, g, **bad)
        assert r.status_code in (409, 422), (bad, r.text)
        assert admin.get(f"/api/v1/operations/{op['id']}").json()["status"] in ("granted", "running")
    assert deploy_success(a, op, g).status_code == 200
    assert deploy_success(a, op, g).json()["duplicate"] is True  # exact duplicate retry
    r = deploy_success(
        a,
        op,
        g,
        evidence={
            "health": "ok",
            "cutover_ms": 999,
            "runtime": {"binary_sha256": exec_sha(op), "build_info": "sim"},
            "eval": {"verdict": "passed", "plan_digest": op["payload"]["plan_digest"]},
        },
    )
    assert r.status_code == 409  # conflicting duplicate
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] is None
    # pre-grant failure (e.g. preflight) is allowed and records a structured failure
    heartbeat(a, active=s["release_id"], stage="active", generation=op["generation"])
    op2 = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["candidate_release_id"], "plan_id": s["candidate_plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(a, active=s["release_id"], stage="active", generation=op["generation"])
    r = a.client.post(
        f"/api/agent/v1/operations/{op2['id']}/outcome",
        json={
            "status": "failed",
            "failure": {
                "code": "PREFLIGHT_MEMORY",
                "stage": "preflight",
                "message": "token hf_abcdefghijklmnopqrstuvwxyz leaked?",
            },
        },
    )
    assert r.status_code == 200
    f = admin.get(f"/api/v1/devices/{a.device_id}/failures").json()[0]
    assert f["code"] == "PREFLIGHT_MEMORY" and "hf_abcdefghijklmnop" not in f["message"]
    assert (
        a.client.post(
            f"/api/agent/v1/operations/{op2['id']}/outcome",
            json={"status": "failed", "failure": {"code": "OTHER"}},
        ).status_code
        == 409
    )


def test_r11_history_reports_do_not_refresh_and_reconcile_needs_challenge(app, admin, settings):
    from convoy_server.db import session_scope
    from convoy_server.services.operations import expire_grants

    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a, health="ok")
    # a delayed buffered report with a higher sequence but old observation must not refresh health
    old_ts = "2020-01-01T00:00:00Z"
    rep = heartbeat(a, health="failed", kind="history", source_ts=old_ts)
    assert rep["applied"] is False
    d = admin.get(f"/api/v1/devices/{a.device_id}").json()
    assert d["observed_health"] == "ok" and d["last_report_seq"] == a.seq
    # a heartbeat that does not echo the server's live nonce is not roundtrip-bound: stored, not applied
    assert heartbeat(a, health="failed", live_nonce="stale-or-missing")["applied"] is False
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["observed_health"] == "ok"
    # a slow device clock (old source_ts) with a proper nonce echo IS live: clock is metadata only (R17)
    assert heartbeat(a, health="ok", source_ts=old_ts)["applied"] is True
    # uncertain reservation: grant expires; a plain higher-seq heartbeat must NOT free the slot
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(a)
    g = grant(a, op)
    assert g.status_code == 200
    with session_scope() as db:
        from datetime import timedelta

        from convoy_server.db import write_txn
        from convoy_server.ids import utcnow
        from convoy_server.models import Grant

        with write_txn(db):
            row = db.get(Grant, g.json()["grant_id"])
            row.expires_at = utcnow() - timedelta(seconds=1)
        assert expire_grants(db) == 1
    assert admin.get(f"/api/v1/operations/{op['id']}").json()["status"] == "uncertain"
    rep = heartbeat(a, operation_id=None, generation=op["generation"])
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] == op["id"]
    assert rep["reconcile_challenge"]
    # wrong challenge, or history kind, does nothing
    heartbeat(a, generation=op["generation"], kind="reconcile", challenge="bogus")
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] == op["id"]
    heartbeat(a, generation=op["generation"], kind="history", challenge=rep["reconcile_challenge"])
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] == op["id"]
    # correct challenged live reconcile at sufficient generation frees the slot and fails the op honestly
    heartbeat(a, generation=op["generation"], kind="reconcile", challenge=rep["reconcile_challenge"])
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] is None
    o = admin.get(f"/api/v1/operations/{op['id']}").json()
    assert o["status"] == "failed" and o["outcome"]["failure"]["code"] == "GRANT_UNCONFIRMED"
