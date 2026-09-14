"""WP6/WP7 acceptance: canary rollouts with explicit promotion, failed canary blocks, abort/restore
semantics; fenced scheduler with two workers + stalled leader, civil occurrence dedupe, DST gap/fold,
missed-window policies, overlap/no_change/offline handling, owner revocation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import WEB, make_user
from convoy_server.db import session_scope, write_txn
from convoy_server.ids import utcnow
from helpers import (
    deploy_success,
    enrolled_agent,
    eval_evidence,
    exec_sha,
    full_deploy,
    grant,
    heartbeat,
    probation_evidence,
    seed,
)


def _device_deploys(admin, a, op, plan_id, verdict="passed"):
    """Simulate the device completing a rollout deploy op with real evidence."""
    heartbeat(
        a,
        active=op["payload"]["expected_active_release_id"],
        stage="active" if op["payload"]["expected_active_release_id"] else "idle",
        generation=op["generation"] - 1,
    )
    g = grant(
        a,
        op,
        active=op["payload"]["expected_active_release_id"],
        recovery=op["payload"].get("recovery_release_id"),
    )
    assert g.status_code == 200, g.text
    if verdict == "passed":
        evr, v = eval_evidence(a, op, "passed")
        r = deploy_success(
            a,
            op,
            g.json(),
            evidence={
                "health": "ok",
                "cutover_ms": 100,
                "runtime": {"binary_sha256": exec_sha(op), "build_info": "sim"},
                "eval": {
                    "verdict": "passed",
                    "plan_digest": op["payload"]["plan_digest"],
                    "eval_result_id": evr,
                },
                "probation": probation_evidence(a, op),
            },
        )
        assert r.status_code == 200, r.text
        heartbeat(
            a,
            active=op["payload"]["target_release_id"],
            recovery=op["payload"]["expected_active_release_id"],
            stage="active",
            generation=op["generation"],
        )
    else:
        eval_evidence(a, op, "failed")
        r = a.client.post(
            f"/api/agent/v1/operations/{op['id']}/outcome",
            json={
                "status": "failed",
                "grant_id": g.json()["grant_id"],
                "grant_consumed_seq": a.seq,
                "failure": {
                    "code": "EVAL_FAILED",
                    "stage": "evaluating",
                    "message": "gate failed",
                    "details": {"recovery": {"recovered": True}},
                },
            },
        )
        assert r.status_code == 200, r.text
        heartbeat(
            a,
            active=op["payload"]["expected_active_release_id"],
            stage="active",
            generation=op["generation"],
            latch=op["generation"],
        )


def _tick(app=None):
    from convoy_server.services import rollouts

    with session_scope() as db:
        rollouts.advance_all(db)


def test_canary_rollout_promotion_serial_expansion_and_failed_canary_blocks(app, admin):
    s = seed(admin)
    devs = [enrolled_agent(app, admin, f"sim-{i}") for i in range(3)]
    for a in devs:
        full_deploy(a, admin, s["release_id"], s["plan_id"])
    ids = [a.device_id for a in devs]
    r = admin.post(
        "/api/v1/rollouts",
        json={
            "name": "r1",
            "plan_id": s["candidate_plan_id"],
            "target": {"device_ids": ids},
            "canary_device_ids": [ids[0]],
        },
        headers=WEB,
    )
    assert r.status_code == 201, r.text
    ro = r.json()
    assert ro["status"] == "draft" and ro["targets"] == ids and ro["previous"][ids[0]] == s["release_id"]
    assert admin.post(f"/api/v1/rollouts/{ro['id']}/promote", headers=WEB).status_code == 409
    ro = admin.post(f"/api/v1/rollouts/{ro['id']}/start", headers=WEB).json()
    assert (
        ro["status"] == "canary"
        and ro["device_states"][ids[0]]["status"] == "deploying"
        and ro["device_states"][ids[1]]["status"] == "queued"
    )
    op = admin.get(f"/api/v1/operations/{ro['device_states'][ids[0]]['operation_id']}").json()
    assert op["rollout_id"] == ro["id"] and op["plan_id"] == s["candidate_plan_id"]
    # promotion is refused until the canary has passed; the canary device completes with real evidence
    assert admin.post(f"/api/v1/rollouts/{ro['id']}/promote", headers=WEB).status_code == 409
    _device_deploys(admin, devs[0], op, s["candidate_plan_id"])
    _tick()
    ro = admin.get(f"/api/v1/rollouts/{ro['id']}").json()
    assert (
        ro["status"] == "awaiting_promotion"
        and ro["device_states"][ids[0]]["status"] == "passed"
        and ro["device_states"][ids[0]]["eval_result_id"]
    )
    # promotion re-checks LIVE health (R46): an old live report -> refused; a fresh live report -> ok
    from convoy_server.models import Device

    with session_scope() as db:
        with write_txn(db):
            db.get(Device, ids[0]).live_at = utcnow() - timedelta(hours=1)
    assert admin.post(f"/api/v1/rollouts/{ro['id']}/promote", headers=WEB).status_code == 409
    heartbeat(
        devs[0], active=s["candidate_release_id"], recovery=s["release_id"], stage="active", generation=2
    )
    ro = admin.post(f"/api/v1/rollouts/{ro['id']}/promote", headers=WEB).json()
    assert (
        ro["status"] == "expanding"
        and ro["device_states"][ids[1]]["status"] == "deploying"
        and ro["device_states"][ids[2]]["status"] == "queued"
    )  # serial: max_in_flight 1
    op1 = admin.get(f"/api/v1/operations/{ro['device_states'][ids[1]]['operation_id']}").json()
    _device_deploys(admin, devs[1], op1, s["candidate_plan_id"])
    _tick()
    ro = admin.get(f"/api/v1/rollouts/{ro['id']}").json()
    assert (
        ro["device_states"][ids[1]]["status"] == "passed"
        and ro["device_states"][ids[2]]["status"] == "deploying"
    )
    op2 = admin.get(f"/api/v1/operations/{ro['device_states'][ids[2]]['operation_id']}").json()
    _device_deploys(admin, devs[2], op2, s["candidate_plan_id"])
    _tick()
    assert admin.get(f"/api/v1/rollouts/{ro['id']}").json()["status"] == "completed"
    # a second rollout whose canary fails is blocked and the device rolled back
    bad = admin.post(
        "/api/v1/releases",
        json={
            "name": "sim-bad2",
            "version": "1",
            "model": {
                "source": "fixture",
                "repo": "convoy-sim/qwen2.5-1.5b-instruct-gguf",
                "revision": "0" * 40,
                "files": ["qwen2.5-1.5b-instruct-q4_k_m.sim.gguf"],
            },
            "recipe_id": s["recipe_id"],
            "runtime_artifact_id": s["artifact_id"],
            "profile_id": "simulated-host",
            "eval_set_id": s["eval_set_id"],
            "config": {"seed": 9},
        },
        headers=WEB,
    ).json()
    bplan = admin.post("/api/v1/plans", json={"name": "bad", "release_id": bad["id"]}, headers=WEB).json()
    ro2 = admin.post(
        "/api/v1/rollouts",
        json={
            "name": "r2",
            "plan_id": bplan["id"],
            "target": {"device_ids": ids},
            "canary_device_ids": [ids[0]],
        },
        headers=WEB,
    ).json()
    ro2 = admin.post(f"/api/v1/rollouts/{ro2['id']}/start", headers=WEB).json()
    opc = admin.get(f"/api/v1/operations/{ro2['device_states'][ids[0]]['operation_id']}").json()
    _device_deploys(admin, devs[0], opc, bplan["id"], verdict="failed")
    _tick()
    ro2 = admin.get(f"/api/v1/rollouts/{ro2['id']}").json()
    assert (
        ro2["status"] == "failed"
        and "blocked" in ro2["message"]
        and ro2["device_states"][ids[1]]["status"] == "queued"
    )
    assert admin.post(f"/api/v1/rollouts/{ro2['id']}/promote", headers=WEB).status_code == 409
    assert (
        admin.get(f"/api/v1/devices/{ids[0]}").json()["observed_active_release_id"]
        == s["candidate_release_id"]
    )


def test_rollout_pause_abort_restore_and_offline_denominator(app, admin):
    s = seed(admin)
    devs = [enrolled_agent(app, admin, f"sim-{i}") for i in range(2)]
    for a in devs:
        full_deploy(a, admin, s["release_id"], s["plan_id"])
    ids = [a.device_id for a in devs]
    ro = admin.post(
        "/api/v1/rollouts",
        json={
            "name": "r",
            "plan_id": s["candidate_plan_id"],
            "target": {"device_ids": ids},
            "canary_device_ids": [ids[0]],
        },
        headers=WEB,
    ).json()
    ro = admin.post(f"/api/v1/rollouts/{ro['id']}/start", headers=WEB).json()
    op = admin.get(f"/api/v1/operations/{ro['device_states'][ids[0]]['operation_id']}").json()
    # pause: no new grants for rollout operations
    assert admin.post(f"/api/v1/rollouts/{ro['id']}/pause", headers=WEB).json()["status"] == "paused"
    heartbeat(devs[0], active=s["release_id"], stage="active", generation=op["generation"] - 1)
    assert grant(devs[0], op, active=s["release_id"]).status_code == 423
    assert admin.post(f"/api/v1/rollouts/{ro['id']}/resume", headers=WEB).json()["status"] == "canary"
    g = grant(devs[0], op, active=s["release_id"])
    assert g.status_code == 200
    # abort after grant: cancellation requested, device not silently restored
    ro = admin.post(f"/api/v1/rollouts/{ro['id']}/abort", headers=WEB).json()
    assert (
        ro["status"] == "aborted"
        and "NOT restored" in ro["message"]
        and "cancellation requested" in ro["device_states"][ids[0]]["note"]
    )
    assert admin.get(f"/api/v1/operations/{op['id']}").json()["cancel_requested_at"]
    # the device had consumed the grant: honest success is still accepted, then restore issues a recover op
    evr, _ = eval_evidence(devs[0], op)
    assert (
        deploy_success(
            devs[0],
            op,
            g.json(),
            evidence={
                "health": "ok",
                "cutover_ms": 5,
                "runtime": {"binary_sha256": exec_sha(op), "build_info": "sim"},
                "eval": {
                    "verdict": "passed",
                    "plan_digest": op["payload"]["plan_digest"],
                    "eval_result_id": evr,
                },
                "probation": probation_evidence(devs[0], op),
            },
        ).status_code
        == 200
    )
    heartbeat(
        devs[0],
        active=s["candidate_release_id"],
        recovery=s["release_id"],
        stage="active",
        generation=op["generation"],
    )
    res = admin.post(f"/api/v1/rollouts/{ro['id']}/restore", headers=WEB).json()
    assert res["results"][ids[0]]["operation_id"] and res["results"][ids[1]]["skipped"]
    rec = admin.get(f"/api/v1/operations/{res['results'][ids[0]]['operation_id']}").json()
    assert rec["type"] == "recover" and rec["payload"]["target_release_id"] == s["release_id"]
    assert admin.get(f"/api/v1/rollouts/{ro['id']}").json()["status"] == "restoring"
    # offline target stays in the denominator of a new rollout
    from convoy_server.models import Device

    with session_scope() as db:
        with write_txn(db):
            db.get(Device, ids[1]).last_seen_at = utcnow() - timedelta(hours=2)
    ro3 = admin.post(
        "/api/v1/rollouts",
        json={
            "name": "r3",
            "plan_id": s["candidate_plan_id"],
            "target": {"device_ids": ids},
            "canary_device_ids": [ids[1]],
        },
        headers=WEB,
    ).json()
    assert len(ro3["targets"]) == 2 and ids[1] in ro3["targets"]


def test_scheduler_fence_two_workers_and_stalled_leader(app, admin):
    from convoy_server.models import SchedulerLease
    from convoy_server.services.scheduler import FenceLost, Worker

    w1, w2 = Worker("w1"), Worker("w2")
    with session_scope() as db:
        assert w1.acquire(db) is True and w2.acquire(db) is False
        with write_txn(db):
            w1.fenced(db)  # ok
        # w1 stalls: lease expires; w2 takes over with a higher fence
        with write_txn(db):
            db.get(SchedulerLease, "scheduler").expires_at = utcnow() - timedelta(seconds=1)
        assert w2.acquire(db) is True and w2.fence == w1.fence + 1
        # the stalled w1 wakes up and tries to commit: owner/token/fence/expiry check fails
        try:
            with write_txn(db):
                w1.fenced(db)
            raise AssertionError("stale worker committed")
        except FenceLost:
            pass
        # even with no replacement, an expired holder cannot write
        with write_txn(db):
            db.get(SchedulerLease, "scheduler").expires_at = utcnow() - timedelta(seconds=1)
        try:
            with write_txn(db):
                w2.fenced(db)
            raise AssertionError("expired holder committed")
        except FenceLost:
            pass


def test_schedule_validation_preview_and_dst(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    body = {
        "name": "nightly",
        "kind": "eval",
        "cron": "30 2 * * *",
        "timezone": "America/Los_Angeles",
        "target": {"device_ids": [a.device_id]},
        "payload": {"plan_id": s["plan_id"]},
    }
    assert (
        admin.post("/api/v1/schedules", json={**body, "cron": "*/2 * * * *"}, headers=WEB).status_code == 400
    )  # min interval
    assert (
        admin.post("/api/v1/schedules", json={**body, "timezone": "Mars/Olympus"}, headers=WEB).status_code
        == 400
    )
    assert admin.post("/api/v1/schedules", json={**body, "cron": "30 2 * *"}, headers=WEB).status_code in (
        400,
        422,
    )
    assert (
        admin.post("/api/v1/schedules", json={**body, "payload": {}}, headers=WEB).status_code == 400
    )  # eval needs a pinned plan
    r = admin.post("/api/v1/schedules", json=body, headers=WEB)
    assert r.status_code == 201, r.text
    sch = r.json()
    assert (
        sch["next_civil"]
        and sch["next_run_at"]
        and sch["revision"] == 1
        and sch["payload"]["release_id"] == s["release_id"]
    )
    prev = admin.get(f"/api/v1/schedules/{sch['id']}/preview").json()["next"]
    assert len(prev) == 5 and all(p["civil"] for p in prev)
    from convoy_server.services.scheduler import next_occurrences

    gap = next_occurrences(
        "30 2 * * *", "America/Los_Angeles", datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc), 3
    )
    assert (
        gap[0]["note"].startswith("skipped_nonexistent")
        and gap[0]["utc"] is None
        and gap[1]["civil"] == "2026-03-09T02:30"
    )
    fold = next_occurrences(
        "30 1 * * *", "America/Los_Angeles", datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc), 2
    )
    assert (
        fold[0]["civil"] == "2026-11-01T01:30"
        and fold[0]["offset"] == "-0700"
        and fold[0]["note"] == "fold_0"
        and fold[1]["civil"] == "2026-11-02T01:30"
    )
    hourly = next_occurrences(
        "15 * * * *", "America/Los_Angeles", datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc), 3
    )
    assert [h["civil"] for h in hourly] == [
        "2026-11-01T01:15",
        "2026-11-01T02:15",
        "2026-11-01T03:15",
    ]  # 01:15 fires once
    # edits create a new revision
    r = admin.patch(f"/api/v1/schedules/{sch['id']}", json={"cron": "0 3 * * *"}, headers=WEB)
    assert r.status_code == 200 and r.json()["revision"] == 2


def test_schedule_fire_dedupe_overlap_no_change_offline_missed_and_owner_revocation(app, admin):
    from convoy_server.models import Schedule
    from convoy_server.services import scheduler as sch

    s = seed(admin)
    online = enrolled_agent(app, admin, "on")
    busy = enrolled_agent(app, admin, "busy")
    offline = enrolled_agent(app, admin, "off")
    for a in (online, busy, offline):
        full_deploy(a, admin, s["release_id"], s["plan_id"])
    # busy device has an unsettled deploy; offline device stops reporting
    bop = admin.post(
        f"/api/v1/devices/{busy.device_id}/deploy",
        json={"release_id": s["candidate_release_id"], "plan_id": s["candidate_plan_id"]},
        headers=WEB,
    ).json()
    from convoy_server.models import Device

    with session_scope() as db:
        with write_txn(db):
            db.get(Device, offline.device_id).last_seen_at = utcnow() - timedelta(hours=1)
    ids = [online.device_id, busy.device_id, offline.device_id]
    dep = admin.post(
        "/api/v1/schedules",
        json={
            "name": "deploy",
            "kind": "deploy",
            "cron": "0 * * * *",
            "timezone": "UTC",
            "target": {"device_ids": ids},
            "payload": {"plan_id": s["plan_id"]},
        },
        headers=WEB,
    ).json()
    ev = admin.post(
        "/api/v1/schedules",
        json={
            "name": "eval",
            "kind": "eval",
            "cron": "0 * * * *",
            "timezone": "UTC",
            "target": {"device_ids": ids},
            "payload": {"plan_id": s["plan_id"]},
        },
        headers=WEB,
    ).json()
    due = utcnow() - timedelta(seconds=5)
    with session_scope() as db:
        with write_txn(db):
            for sid in (dep["id"], ev["id"]):
                row = db.get(Schedule, sid)
                row.next_run_at = due
                row.next_civil = due.strftime("%Y-%m-%dT%H:%M")
        fired = sch.tick_schedules(db, None)
        assert len(fired) == 2
        # tick again: the same civil occurrence is deduped (unique civil identity), nothing fires twice
        with write_txn(db):
            for sid in (dep["id"], ev["id"]):
                row = db.get(Schedule, sid)
                row.next_run_at = due
                row.next_civil = due.strftime("%Y-%m-%dT%H:%M")
        again = sch.tick_schedules(db, None)
        assert all(x.get("deduped") for x in again)
    occ = admin.get(f"/api/v1/schedules/{ev['id']}/occurrences").json()
    assert len(occ) == 1
    per = {d["device_id"]: d for d in occ[0]["results"]["devices"]}
    assert (
        per[online.device_id].get("operation_id")
        and per[busy.device_id]["skipped"] == "busy"
        and per[offline.device_id]["skipped"] == "offline_deferred"
    )
    docc = admin.get(f"/api/v1/schedules/{dep['id']}/occurrences").json()[0]
    dper = {d["device_id"]: d for d in docc["results"]["devices"]}
    assert dper[online.device_id]["skipped"] == "no_change"  # already running the pinned release, healthy
    assert docc["status"] == "completed"
    # the eval occurrence is 'dispatched' until its child operation settles
    assert occ[0]["status"] == "dispatched"
    op = admin.get(f"/api/v1/operations/{per[online.device_id]['operation_id']}").json()
    assert op["occurrence_id"] == occ[0]["id"] and op["type"] == "eval" and op["deadline_at"]
    # missed window: skip policy records the miss and advances; run_once within catch-up fires late
    with session_scope() as db:
        with write_txn(db):
            row = db.get(Schedule, ev["id"])
            row.next_run_at = utcnow() - timedelta(hours=3)
            row.next_civil = "2020-01-01T00:00"
        fired = sch.tick_schedules(db, None)
        assert fired[0].get("missed") and fired[0]["policy"] == "skip"
        with write_txn(db):
            row = db.get(Schedule, ev["id"])
            row.missed_policy = "run_once"
            row.catchup_age_s = 4 * 3600
            row.next_run_at = utcnow() - timedelta(hours=3)
            row.next_civil = "2020-01-01T01:00"
        fired = sch.tick_schedules(db, None)
        assert fired[0].get("catch_up") is True and fired[0]["late_s"] > 3600
    # owner revocation pauses dispatch
    op_user = make_user(admin, "sched-owner@example.com", "operator")
    from conftest import login
    from fastapi.testclient import TestClient

    owner = login(TestClient(app), "sched-owner@example.com", "password-123")
    h = owner.post(
        "/api/v1/schedules",
        json={
            "name": "health",
            "kind": "health",
            "cron": "0 * * * *",
            "timezone": "UTC",
            "target": {"device_ids": [online.device_id]},
        },
        headers=WEB,
    ).json()
    assert (
        admin.patch(f"/api/v1/users/{op_user['id']}", json={"role": "viewer"}, headers=WEB).status_code == 200
    )
    with session_scope() as db:
        with write_txn(db):
            row = db.get(Schedule, h["id"])
            row.next_run_at = utcnow() - timedelta(seconds=1)
            row.next_civil = utcnow().strftime("%Y-%m-%dT%H:%M")
        fired = sch.tick_schedules(db, None)
        assert fired[0].get("paused") == "owner_revoked"
    hs = admin.get(f"/api/v1/schedules/{h['id']}").json()
    assert hs["paused_at"] and hs["pause_reason"] == "owner_revoked"
    _ = bop
