"""Readiness recovery never seizes admitted authority or strands expired queued work."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from convoy_server.db import session_scope, write_txn
from convoy_server.ids import utcnow
from convoy_server.platform_models import Mission
from test_platform_lifecycle import claim, post, start
from test_platform_lifecycle import pipeline as _pipeline

pipeline = _pipeline


def readiness(p, state):
    return p["agent"].client.post(f"{p['base']}/deployments/{p['deployment']['id']}/report", json={
        "generation": 1, "state": state, "release_digest": p["release"]["digest"],
        "detail": "temporary component outage" if state == "blocked" else "",
    })


def expire(mission):
    with session_scope() as db, write_txn(db):
        db.get(Mission, mission["id"]).expires_at = utcnow() - timedelta(seconds=1)


def test_desired_and_late_claim_settle_expired_queued_mission_once(pipeline):
    p = pipeline
    mission = start(p)
    assert readiness(p, "blocked").status_code == 200
    expire(mission)
    # Both routes may notice expiry, but only one terminal episode may be written.
    barrier = Barrier(2)

    def reconcile():
        barrier.wait(timeout=5)
        return p["agent"].client.get(f"{p['base']}/desired")

    def claim_late():
        barrier.wait(timeout=5)
        return p["agent"].client.post(f"{p['base']}/missions/{mission['id']}/claim", json={
            "boot_id": "boot", "incarnation": "late-coordinator", "authority_epoch": 1,
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        desired, late_claim = pool.submit(reconcile), pool.submit(claim_late)
        snapshot, rejected = desired.result(timeout=10), late_claim.result(timeout=10)
    assert snapshot.status_code == 200, snapshot.text
    assert rejected.status_code in {409, 410}, rejected.text
    failed = snapshot.json()["mission"]
    assert failed["state"] == "failed" and failed["identity"] is None
    assert failed["episode_id"]
    assert p["agent"].client.get(f"{p['base']}/desired").json()["mission"] == failed
    episodes = p["admin"].get("/api/v1/episodes", params={"mission_id": mission["id"]}).json()
    assert len(episodes) == 1 and episodes[0]["id"] == failed["episode_id"]
    assert episodes[0]["summary"] == {"reason": "authorization_expired"}
    assert readiness(p, "ready").status_code == 200
    following = start(p, key="after-expiry")
    assert following["id"] != mission["id"]
    assert claim(p, following)[0]["identity"]["mission_id"] == following["id"]


def test_pending_mission_can_regain_readiness_without_replacing_authority(pipeline):
    p = pipeline
    mission = start(p)
    assert readiness(p, "blocked").status_code == 200
    recovered = readiness(p, "ready")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["generation"] == mission["generation"] == 1
    retained = p["agent"].client.get(f"{p['base']}/desired").json()["mission"]
    assert retained == mission  # same id, seed, expiry and unclaimed identity
    claimed, _ = claim(p, mission)
    assert claimed["mission"]["expires_at"] == mission["expires_at"]
    assert readiness(p, "ready").status_code == 200  # unchanged acknowledgement remains idempotent
    assert readiness(p, "blocked").status_code == 200
    assert readiness(p, "ready").status_code == 409  # authority has now been admitted


@pytest.mark.parametrize("state", ["starting", "running", "unknown", "cancel_requested", "unclaimed_cancel"])
def test_reconciliation_preserves_admitted_or_cancelled_authority(pipeline, state):
    p = pipeline
    mission = start(p)
    identity = None
    if state != "unclaimed_cancel":
        identity = claim(p, mission)[0]["identity"]
    if state in {"running", "unknown"}:
        response = p["agent"].client.post(f"{p['base']}/missions/{mission['id']}/report", json={
            "identity": identity, "state": state,
        })
        assert response.status_code == 200, response.text
    if state in {"cancel_requested", "unclaimed_cancel"}:
        post(p["admin"], f"/api/v1/missions/{mission['id']}/cancel", {}, expected=200)
    assert readiness(p, "blocked").status_code == 200
    expire(mission)
    response = p["agent"].client.get(f"{p['base']}/desired")
    assert response.status_code == 200, response.text
    retained = response.json()["mission"]
    assert retained["state"] == ("cancel_requested" if state == "unclaimed_cancel" else state)
    assert retained["identity"] == identity and retained["episode_id"] is None
    assert readiness(p, "ready").status_code == 409
