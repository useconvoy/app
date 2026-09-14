"""Negative regressions for review round 3 (R13-fu, R15, R16-fu, R17-fu, R18-fu, R23, R26 server, R31)."""

from __future__ import annotations

import hashlib
import io
import tarfile
from datetime import timedelta

from conftest import WEB, login, make_user
from convoy_server.db import session_scope, write_txn
from convoy_server.ids import iso, utcnow
from fastapi.testclient import TestClient
from helpers import (
    deploy_success,
    enrolled_agent,
    eval_evidence,
    exec_sha,
    grant,
    heartbeat,
    probation_evidence,
    seed,
)


def test_r13_enrollment_issuance_with_revoked_session_in_flight(app, admin):
    """Barrier at the service level: the principal is loaded (authenticated), then its session is
    revoked, then the write proceeds -> must be refused."""
    from convoy_server.auth import load_principal
    from convoy_server.services import identity
    from fastapi import HTTPException, Request

    make_user(admin, "op@example.com", "operator")
    op = login(TestClient(app), "op@example.com", "password-123")
    cookie = op.cookies.get("convoy_session")
    scope = {
        "type": "http",
        "headers": [(b"cookie", f"convoy_session={cookie}".encode())],
        "method": "POST",
        "path": "/",
    }
    with session_scope() as db:
        p = load_principal(Request(scope), db)
        assert p is not None and p.via == "session"
        # in-flight: the account's password is reset by an admin (revokes sessions)
        uid = p.user.id
        assert (
            admin.patch(
                f"/api/v1/users/{uid}", json={"password": "another-password-1"}, headers=WEB
            ).status_code
            == 200
        )
        try:
            identity.create_enrollment(
                db, p, label="x", group_name="g", simulated=True, ttl_s=None, rebind_device_id=None
            )
            raise AssertionError("enrollment minted with a revoked session")
        except HTTPException as e:
            assert e.status_code == 401
        try:
            identity.create_api_token(db, p, "t", None)
            raise AssertionError("api token minted with a revoked session")
        except HTTPException as e:
            assert e.status_code == 401
    # nothing was persisted
    assert all(e["label"] != "x" for e in admin.get("/api/v1/enrollments").json())


def test_r4_in_flight_mutual_admin_race_at_service_level(app, admin):
    from convoy_server.auth import load_principal
    from convoy_server.models import User
    from convoy_server.services import identity
    from fastapi import HTTPException, Request
    from sqlalchemy import select

    other = make_user(admin, "admin2@example.com", "admin")
    a2 = login(TestClient(app), "admin2@example.com", "password-123")
    c1 = admin.cookies.get("convoy_session")
    c2 = a2.cookies.get("convoy_session")

    def principal(db, cookie):
        return load_principal(
            Request(
                {
                    "type": "http",
                    "headers": [(b"cookie", f"convoy_session={cookie}".encode())],
                    "method": "PATCH",
                    "path": "/",
                }
            ),
            db,
        )

    with session_scope() as db:
        p1 = principal(db, c1)
        p2 = principal(db, c2)  # both authenticated: barrier
        u1 = db.scalar(select(User).where(User.id == p1.user.id))
        u2 = db.scalar(select(User).where(User.id == other["id"]))
        identity.update_user(db, p2, u1, {"disabled": True})  # admin2 disables admin1
        try:
            identity.update_user(db, p1, u2, {"role": "viewer"})  # admin1's stale request must fail
            raise AssertionError("stale admin mutated the last remaining admin")
        except HTTPException as e:
            assert e.status_code == 401
        try:
            identity.update_user(db, p2, u2, {"disabled": True})
            raise AssertionError("last admin disabled")
        except identity.IdentityError as e:
            assert e.status == 409


def test_r12_reenable_without_new_password_is_refused_after_quarantine(app, admin):
    from convoy_server.services.identity import enter_quarantine, recover_admin

    u = make_user(admin, "op@example.com", "operator")
    with session_scope() as db:
        enter_quarantine(db, "test")
        recover_admin(db, "admin@example.com", "fresh-recovery-password")
    rec = login(TestClient(app), "admin@example.com", "fresh-recovery-password")
    assert rec.get("/api/v1/users").json() and any(
        x["password_reset_required"]
        for x in rec.get("/api/v1/users").json()
        if x["email"] == "op@example.com"
    )
    r = rec.patch(f"/api/v1/users/{u['id']}", json={"disabled": False}, headers=WEB)
    assert r.status_code == 409
    assert (
        TestClient(app)
        .post("/api/v1/auth/login", json={"email": "op@example.com", "password": "password-123"})
        .status_code
        == 401
    )
    assert (
        rec.patch(
            f"/api/v1/users/{u['id']}",
            json={"disabled": False, "password": "brand-new-password"},
            headers=WEB,
        ).status_code
        == 200
    )
    assert (
        TestClient(app)
        .post("/api/v1/auth/login", json={"email": "op@example.com", "password": "password-123"})
        .status_code
        == 401
    )
    assert (
        TestClient(app)
        .post("/api/v1/auth/login", json={"email": "op@example.com", "password": "brand-new-password"})
        .status_code
        == 200
    )


def test_r15_deploy_success_evidence_bindings_each_negative(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(a)
    g = grant(a, op).json()
    good_rt = {"binary_sha256": exec_sha(op), "build_info": "sim"}
    evr, _ = eval_evidence(a, op)  # R15 follow-up: the cited result must really be ingested for this op
    good_eval = {"verdict": "passed", "plan_digest": op["payload"]["plan_digest"], "eval_result_id": evr}
    good_prob = probation_evidence(a, op)
    cases = {
        "wrong binary": {
            "health": "ok",
            "cutover_ms": 1,
            "runtime": {"binary_sha256": "wrong", "build_info": ""},
            "eval": good_eval,
        },
        "empty build_info": {
            "health": "ok",
            "cutover_ms": 1,
            "runtime": {**good_rt, "build_info": ""},
            "eval": good_eval,
        },
        "wrong plan digest": {
            "health": "ok",
            "cutover_ms": 1,
            "runtime": good_rt,
            "eval": {**good_eval, "plan_digest": "wrong-plan"},
        },
        "no eval result id": {
            "health": "ok",
            "cutover_ms": 1,
            "runtime": good_rt,
            "eval": {**good_eval, "eval_result_id": ""},
        },
        "review R15 exact": {
            "health": "ok",
            "cutover_ms": 1,
            "runtime": {"binary_sha256": "wrong", "build_info": ""},
            "eval": {"verdict": "passed", "plan_digest": "wrong-plan"},
        },
        "unresolvable eval result id": {
            "health": "ok",
            "cutover_ms": 1,
            "runtime": good_rt,
            "eval": {**good_eval, "eval_result_id": "evr_x"},
            "probation": good_prob,
        },
        "no probation evidence": {"health": "ok", "cutover_ms": 1, "runtime": good_rt, "eval": good_eval},
    }
    for name, ev in cases.items():
        r = deploy_success(a, op, g, evidence=ev)
        assert r.status_code in (409, 422), (name, r.text)
        o = admin.get(f"/api/v1/operations/{op['id']}").json()
        assert o["status"] in ("granted", "running"), name
        assert admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] == op["id"], name
    assert (
        deploy_success(
            a,
            op,
            g,
            evidence={
                "health": "ok",
                "cutover_ms": 1,
                "runtime": good_rt,
                "eval": good_eval,
                "probation": good_prob,
            },
        ).status_code
        == 200
    )


def test_r15_physical_profile_requires_cuda_offload_evidence():
    from convoy_server.models import Operation
    from convoy_server.services.operations import OperationError, _validate_success

    op = Operation(
        id="op_p",
        device_id="d",
        type="deploy",
        payload={
            "target_release_id": "rel",
            "release_digest": "rd",
            "expected_active_release_id": None,
            "plan_id": None,
            "artifact_files": [{"path": "bin/llama-server", "sha256": "ab" * 32}],
            "simulated": False,
        },
        payload_digest="x",
        generation=1,
        grant_id="g1",
    )
    base = {
        "status": "succeeded",
        "grant_id": "g1",
        "grant_consumed_seq": 1,
        "result": {
            "active_release_id": "rel",
            "release_digest": "rd",
            "generation": 1,
            "recovery_release_id": None,
        },
    }
    for rt in (
        {"binary_sha256": "ab" * 32, "build_info": "b"},
        {
            "binary_sha256": "ab" * 32,
            "build_info": "b",
            "intended_backend_ok": False,
            "gpu_offloaded_layers": 29,
            "gpu_total_layers": 29,
        },
        {
            "binary_sha256": "ab" * 32,
            "build_info": "b",
            "intended_backend_ok": True,
            "gpu_offloaded_layers": 0,
            "gpu_total_layers": 29,
        },
        {
            "binary_sha256": "ab" * 32,
            "build_info": "b",
            "intended_backend_ok": True,
            "gpu_offloaded_layers": 10,
            "gpu_total_layers": 29,
        },
    ):
        try:
            _validate_success(
                None, op, {**base, "evidence": {"health": "ok", "cutover_ms": 1, "runtime": rt}}
            )
            raise AssertionError(f"accepted {rt}")
        except OperationError:
            pass
    _validate_success(
        None,
        op,
        {
            **base,
            "evidence": {
                "health": "ok",
                "cutover_ms": 1,
                "runtime": {
                    "binary_sha256": "ab" * 32,
                    "build_info": "b",
                    "intended_backend_ok": True,
                    "gpu_offloaded_layers": 29,
                    "gpu_total_layers": 29,
                },
            },
        },
    )


def test_r16_expired_grant_settles_without_worker(app, admin):
    from convoy_server.models import Grant

    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(a)
    g1 = grant(a, op, nonce="nonce-aaaaaaaa")
    assert g1.status_code == 200
    # an unexpired outstanding grant must be re-requested by nonce, not replaced
    assert grant(a, op, nonce="nonce-bbbbbbbb").status_code == 409
    assert admin.get(f"/api/v1/operations/{op['id']}").json()["status"] == "granted"
    with session_scope() as db:
        with write_txn(db):
            db.get(Grant, g1.json()["grant_id"]).expires_at = utcnow() - timedelta(seconds=1)
    # no worker sweep: the replacement request itself settles the uncertainty and is rejected
    assert grant(a, op, nonce="nonce-cccccccc").status_code == 409
    o = admin.get(f"/api/v1/operations/{op['id']}").json()
    assert o["status"] == "uncertain" and o["progress"].get("challenge")  # persisted, not rolled back
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] == op["id"]
    rep = heartbeat(a, generation=op["generation"])
    assert rep["reconcile_challenge"] == o["progress"]["challenge"]
    heartbeat(a, generation=op["generation"], kind="reconcile", challenge=rep["reconcile_challenge"])
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] is None
    assert admin.get(f"/api/v1/operations/{op['id']}").json()["status"] == "failed"


def test_r17_stale_nonce_and_buffered_history_cannot_become_live(app, admin, monkeypatch):
    from convoy_server.models import Device
    from convoy_server.services import operations as ops

    s = seed(admin)
    a = enrolled_agent(app, admin)
    rep = heartbeat(a)
    nonce = rep["live_nonce"]
    # hold the heartbeat for "hours": age the nonce server-side
    with session_scope() as db:
        with write_txn(db):
            db.get(Device, a.device_id).live_nonce_issued_at = utcnow() - timedelta(
                seconds=ops.LIVE_NONCE_TTL_S + 5
            )
    rep2 = heartbeat(a, health="failed", live_nonce=nonce)
    assert (
        rep2["applied"] is False and rep2["live_nonce"] != nonce
    )  # new challenge issued, buffered state not applied
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["observed_health"] == "ok"
    # a history record with a higher seq advances ingestion but never the live cursor; grants cite live_seq
    heartbeat(a)  # live again with the fresh nonce
    d = admin.get(f"/api/v1/devices/{a.device_id}").json()
    live_seq = d["live_seq"]
    heartbeat(a, kind="history", health="failed")
    d = admin.get(f"/api/v1/devices/{a.device_id}").json()
    assert d["last_report_seq"] == live_seq + 1 and d["live_seq"] == live_seq and d["observed_health"] == "ok"
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    assert grant(a, op, seq=d["last_report_seq"]).status_code == 409  # history seq is not a live context
    assert grant(a, op, seq=live_seq).status_code == 200


def test_r18_r23_eval_set_jsonl_nonfinite_and_scorer_object(app, admin):
    raw = '{"id":"x","prompt":"p","match":"json_field","field":"x","expected":1e309}'
    r = admin.post("/api/v1/eval-sets", json={"name": "inf", "version": "1", "cases_jsonl": raw}, headers=WEB)
    assert r.status_code == 400 and "non-finite" in r.json()["error"]
    assert all(e["name"] != "inf" for e in admin.get("/api/v1/eval-sets").json())
    nested = '{"id":"y","prompt":"p","match":"label","expected":"a","extra":{"deep":{"v":1e309}}}'
    assert (
        admin.post(
            "/api/v1/eval-sets", json={"name": "inf2", "version": "1", "cases_jsonl": nested}, headers=WEB
        ).status_code
        == 400
    )
    ok = '{"id":"a","prompt":"p","match":"label","expected":"yes"}'
    r = admin.post(
        "/api/v1/eval-sets", json={"name": "sc", "version": "1", "cases_jsonl": ok, "scorer": {}}, headers=WEB
    )
    assert r.status_code == 201 and r.json()["scorer"]["version"] == "1"
    r = admin.post(
        "/api/v1/eval-sets",
        json={"name": "sc", "version": "2", "cases_jsonl": ok, "scorer": {"warmup": 3}},
        headers=WEB,
    )
    assert r.status_code == 201 and r.json()["scorer"]["warmup"] == 3
    assert admin.get(f"/api/v1/eval-sets/{r.json()['id']}").status_code == 200
    assert (
        admin.post(
            "/api/v1/eval-sets",
            json={"name": "sc", "version": "3", "cases_jsonl": ok, "scorer": {"unknown": 1}},
            headers=WEB,
        ).status_code
        == 422
    )


def test_r26_server_rejects_aliased_duplicate_and_unlisted_members(app, admin):
    s = seed(admin)
    a_bytes, b_bytes = b"#!/bin/sh\nexit 0\n", b"#!/bin/sh\nexit 1\n"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("bin/llama-server", a_bytes), ("bin/./llama-server", b_bytes)):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    r = admin.post(
        "/api/v1/runtime-artifacts/upload",
        content=buf.getvalue(),
        headers={**WEB, "content-type": "application/gzip"},
    )
    assert r.status_code == 400 and "alias" in r.json()["error"]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("bin/llama-server", a_bytes), ("bin/extra.so", b_bytes)):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    up = admin.post(
        "/api/v1/runtime-artifacts/upload",
        content=buf.getvalue(),
        headers={**WEB, "content-type": "application/gzip"},
    ).json()
    receipt = {
        "archive_sha256": up["archive_sha256"],
        "archive_size": up["archive_size"],
        "files": [
            {"path": "bin/llama-server", "sha256": hashlib.sha256(a_bytes).hexdigest(), "size": len(a_bytes)}
        ],
        "provenance": {},
    }
    r = admin.post(
        "/api/v1/runtime-artifacts",
        json={"recipe_id": s["recipe_id"], "receipt": receipt, "scope": "fleet", "storage": "server"},
        headers=WEB,
    )
    assert r.status_code == 400 and "unlisted" in r.json()["error"]


def test_r31_budget_verdict_requires_fresh_measurement(app, admin):
    from convoy_server.models import Device

    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a, telemetry={"mem_total_mb": 7620.0, "mem_available_mb": 6000.0, "disk_free_mb": 20000.0})
    b = admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()
    assert b["verdict"] == "fit" and b["fresh"] is True and b["observed_age_s"] < 60
    with session_scope() as db:
        with write_txn(db):
            d = db.get(Device, a.device_id)
            d.observed_at = utcnow() - timedelta(hours=3)
            d.last_telemetry = {**d.last_telemetry, "at": iso(utcnow() - timedelta(hours=3))}
    b = admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()
    assert (
        b["verdict"] == "unknown"
        and b["fresh"] is False
        and "stale" in b["mem_source"]
        and b["observed_age_s"] > 3600
        and b["headroom_mb_historical"] > 0
    )
    # R31 follow-up: a valid LIVE report that omits telemetry refreshes the observation but NOT the
    # retained memory sample, so the budget stays stale/unknown until a new measurement arrives
    heartbeat(a, telemetry="omit")
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["observed_at"] > iso(
        utcnow() - timedelta(minutes=1)
    )
    b = admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()
    assert b["verdict"] == "unknown" and b["fresh"] is False and b["observed_age_s"] > 3600
    heartbeat(a, telemetry={"mem_total_mb": 7620.0, "mem_available_mb": 6000.0, "disk_free_mb": 20000.0})
    b = admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()
    assert b["verdict"] == "fit" and b["fresh"] is True
