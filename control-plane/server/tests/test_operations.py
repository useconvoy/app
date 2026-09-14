from __future__ import annotations

from conftest import WEB
from helpers import deploy_success, enrolled_agent, exec_sha, full_deploy, grant, heartbeat, seed


def test_deploy_lifecycle_and_reservation_conflicts(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    dev = a.device_id
    op = admin.post(
        f"/api/v1/devices/{dev}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    assert op["status"] == "pending" and op["generation"] == 1
    d = admin.get(f"/api/v1/devices/{dev}").json()
    assert d["active_operation_id"] == op["id"] and d["expected_active_release_id"] == s["release_id"]
    # conflicting manual mutation -> 409 naming the active op; no latent queue, generation unchanged
    r = admin.post(
        f"/api/v1/devices/{dev}/deploy",
        json={"release_id": s["candidate_release_id"], "plan_id": s["candidate_plan_id"]},
        headers=WEB,
    )
    assert r.status_code == 409 and r.json()["error"]["active_operation"]["id"] == op["id"]
    assert admin.get(f"/api/v1/devices/{dev}").json()["generation"] == 1
    assert admin.get(f"/api/v1/devices/{dev}").json()["expected_active_release_id"] == s["release_id"]
    # delivery on report
    rep = heartbeat(a)
    assert [o["id"] for o in rep["operations"]] == [op["id"]]
    assert admin.get(f"/api/v1/operations/{op['id']}").json()["status"] == "delivered"
    g = grant(a, op)
    assert g.status_code == 200
    assert (
        a.client.post(
            f"/api/agent/v1/operations/{op['id']}/outcome",
            json={"status": "running", "progress": {"stage": "cutover"}, "trace_id": "tr-1"},
        ).status_code
        == 200
    )
    assert admin.get(f"/api/v1/operations/{op['id']}").json()["status"] == "running"
    assert deploy_success(a, op, g.json(), trace_id="tr-1").status_code == 200
    o = admin.get(f"/api/v1/operations/{op['id']}").json()
    assert o["status"] == "succeeded" and o["trace_id"] == "tr-1"
    heartbeat(a, active=s["release_id"], stage="active", generation=1)
    d = admin.get(f"/api/v1/devices/{dev}").json()
    assert (
        d["active_operation_id"] is None
        and d["observed_active_release_id"] == s["release_id"]
        and d["status"] == "online"
    )
    # second deploy carries expected active + recovery bindings
    op2 = admin.post(
        f"/api/v1/devices/{dev}/deploy",
        json={"release_id": s["candidate_release_id"], "plan_id": s["candidate_plan_id"]},
        headers=WEB,
    ).json()
    assert op2["generation"] == 2 and op2["expected_active_release_id"] == s["release_id"]
    assert op2["payload"]["recovery_release_id"] is None  # device reported none yet
    ops = admin.get(f"/api/v1/operations?device_id={dev}").json()
    assert len(ops) == 2


def test_cancel_before_grant_and_after_grant(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    dev = a.device_id
    op = admin.post(
        f"/api/v1/devices/{dev}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    r = admin.post(f"/api/v1/operations/{op['id']}/cancel", headers=WEB)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert admin.get(f"/api/v1/devices/{dev}").json()["active_operation_id"] is None
    # after a grant, cancellation is only requested; the device may already be activating
    op = admin.post(
        f"/api/v1/devices/{dev}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(a)
    g = grant(a, op)
    assert g.status_code == 200
    r = admin.post(f"/api/v1/operations/{op['id']}/cancel", headers=WEB)
    assert (
        r.status_code == 200
        and r.json()["status"] == "granted"
        and "activation may be in progress" in r.json()["note"]
    )
    assert grant(a, op).status_code == 409  # no new grants once cancellation is requested
    assert (
        deploy_success(a, op, g.json()).status_code == 200
    )  # consumption raced cancellation: honest success


def test_abandon_keeps_slot_blocked_until_reconciled(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    dev = a.device_id
    op = admin.post(
        f"/api/v1/devices/{dev}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(a)
    r = admin.post(f"/api/v1/operations/{op['id']}/abandon", json={"reason": "stuck"}, headers=WEB)
    assert r.status_code == 200 and r.json()["status"] == "abandoned_unconfirmed"
    assert admin.get(f"/api/v1/devices/{dev}").json()["active_operation_id"] == op["id"]
    assert (
        admin.post(
            f"/api/v1/devices/{dev}/deploy",
            json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
            headers=WEB,
        ).status_code
        == 409
    )
    rep = heartbeat(a, generation=1)
    heartbeat(a, generation=1, kind="reconcile", challenge=rep["reconcile_challenge"])
    assert admin.get(f"/api/v1/devices/{dev}").json()["active_operation_id"] is None
    assert (
        admin.post(
            f"/api/v1/devices/{dev}/deploy",
            json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
            headers=WEB,
        ).status_code
        == 201
    )


def test_offline_device_keeps_pending_work_and_status(app, admin, settings):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    dev = a.device_id
    op = admin.post(
        f"/api/v1/devices/{dev}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    from datetime import timedelta

    from convoy_server.db import session_scope, write_txn
    from convoy_server.ids import utcnow
    from convoy_server.models import Device

    with session_scope() as db:
        with write_txn(db):
            db.get(Device, dev).last_seen_at = utcnow() - timedelta(seconds=settings.offline_after_s + 5)
    d = admin.get(f"/api/v1/devices/{dev}").json()
    assert (
        d["status"] == "offline"
        and d["active_operation_id"] == op["id"]
        and d["expected_active_release_id"] == s["release_id"]
    )
    rep = heartbeat(a)
    assert [o["id"] for o in rep["operations"]] == [op["id"]]
    assert admin.get(f"/api/v1/devices/{dev}").json()["status"] == "online"


def test_dispatch_pause_blocks_delivery_and_grants(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    assert (
        admin.post(
            "/api/v1/admin/dispatch", json={"paused": True, "reason": "maintenance"}, headers=WEB
        ).status_code
        == 200
    )
    rep = heartbeat(a)
    assert rep["operations"] == [] and rep["dispatch_paused"] == "maintenance"
    assert grant(a, op).status_code == 423
    assert admin.post("/api/v1/admin/dispatch", json={"paused": False}, headers=WEB).status_code == 200
    assert grant(a, op).status_code == 200


def test_recover_operation_success_contract(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    full_deploy(a, admin, s["release_id"], s["plan_id"])
    full_deploy(a, admin, s["candidate_release_id"], s["candidate_plan_id"], active=s["release_id"])
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/restore", json={"reason": "bad behaviour"}, headers=WEB
    ).json()
    heartbeat(
        a,
        active=s["candidate_release_id"],
        recovery=s["release_id"],
        stage="active",
        generation=op["generation"] - 1,
    )
    g = grant(a, op, active=s["candidate_release_id"], recovery=s["release_id"])
    assert g.status_code == 200, g.text
    body = {
        "status": "succeeded",
        "grant_id": g.json()["grant_id"],
        "grant_consumed_seq": a.seq,
        "result": {
            "active_release_id": s["release_id"],
            "release_digest": op["payload"]["release_digest"],
            "generation": op["generation"],
        },
        "evidence": {"health": "ok", "runtime": {"binary_sha256": exec_sha(op), "build_info": "sim"}},
    }
    assert a.client.post(f"/api/agent/v1/operations/{op['id']}/outcome", json=body).status_code == 200
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["expected_active_release_id"] == s["release_id"]
