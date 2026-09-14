"""WP8: ordered evidence lanes (replay never recounts, loss ranges explicit), ten-device burst,
eval-result validation against the plan's suite, retention; restore rehearsal end to end."""

from __future__ import annotations

import time

from conftest import WEB, FakeAgent, enrollment_token, login
from fastapi.testclient import TestClient
from helpers import enrolled_agent, heartbeat, seed


def test_spool_replay_never_recounts_and_gaps_need_loss_ranges(app, admin):
    a = enrolled_agent(app, admin)
    recs = [
        {"seq": i, "kind": "usage", "body": {"ts": time.time(), "inference_requests": 10, "tokens_out": 5}}
        for i in (1, 2, 3)
    ]
    r = a.client.post("/api/agent/v1/spool", json={"lane": "usage", "records": recs})
    assert r.status_code == 200 and r.json()["committed_seq"] == 3
    # lost ACK: the device replays the same batch -> nothing recounted
    r = a.client.post("/api/agent/v1/spool", json={"lane": "usage", "records": recs})
    assert r.json()["committed_seq"] == 3 and r.json()["accepted"] == 0
    u = admin.get("/api/v1/usage").json()
    assert u["totals"]["inference_requests"] == 30 and u["totals"]["tokens_out"] == 15
    # a gap without a loss range stops at the gap (cursor stays), with a loss range it advances
    r = a.client.post(
        "/api/agent/v1/spool",
        json={
            "lane": "usage",
            "records": [{"seq": 6, "kind": "usage", "body": {"ts": time.time(), "inference_requests": 1}}],
        },
    )
    assert r.json()["committed_seq"] == 3
    r = a.client.post(
        "/api/agent/v1/spool",
        json={
            "lane": "usage",
            "records": [{"seq": 6, "kind": "usage", "body": {"ts": time.time(), "inference_requests": 1}}],
            "loss_ranges": [{"from_seq": 4, "to_seq": 5, "reason": "quota_evicted"}],
        },
    )
    assert r.json()["committed_seq"] == 6
    cov = admin.get("/api/v1/usage").json()["coverage"]
    assert any(lr["from_seq"] == 4 and lr["to_seq"] == 5 for lr in cov["loss_ranges"]) and any(
        c["lane"] == "usage" and c["committed_seq"] == 6 for c in cov["lanes"]
    )
    # malformed record: rejected explicitly, cursor advances, loss recorded
    r = a.client.post(
        "/api/agent/v1/spool", json={"lane": "usage", "records": [{"seq": 7, "kind": "span", "body": {}}]}
    )
    assert r.json()["committed_seq"] == 7 and r.json()["rejected"]
    # wrong lane / non-finite -> 4xx, nothing stored
    assert a.client.post("/api/agent/v1/spool", json={"lane": "nope", "records": []}).status_code == 422
    assert (
        a.client.post(
            "/api/agent/v1/spool",
            content=b'{"lane":"telemetry","records":[{"seq":1,"kind":"telemetry","body":{"cpu_pct":1e999}}]}',
            headers={"content-type": "application/json"},
        ).status_code
        == 422
    )


def test_ten_device_reconnect_burst_and_retention(app, admin, settings):
    agents = [enrolled_agent(app, admin, f"burst-{i}") for i in range(10)]
    t0 = time.time()
    for a in agents:
        heartbeat(a)
        recs = [
            {
                "seq": i,
                "kind": "telemetry",
                "body": {
                    "ts": time.time() - 100 * 86400 if i % 2 else time.time(),
                    "mem_available_mb": 4000.0 + i,
                    "cpu_pct": 10.0,
                },
            }
            for i in range(1, 101)
        ]
        r = a.client.post("/api/agent/v1/spool", json={"lane": "telemetry", "records": recs})
        assert r.status_code == 200 and r.json()["committed_seq"] == 100
    elapsed = time.time() - t0
    assert elapsed < 60, f"burst ingest too slow: {elapsed:.1f}s"
    assert len(admin.get(f"/api/v1/devices/{agents[0].device_id}/telemetry?limit=500").json()) == 100
    from convoy_server.db import session_scope
    from convoy_server.services.evidence import apply_retention

    with session_scope() as db:
        out = apply_retention(db)
    assert out["telemetry"] == 500  # the half stamped 100 days ago is beyond the 7-day detail retention
    assert len(admin.get(f"/api/v1/devices/{agents[0].device_id}/telemetry?limit=500").json()) == 50


def test_eval_result_ingest_validates_suite_and_rescoring(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    plan = admin.get(f"/api/v1/plans/{s['plan_id']}").json()
    es = admin.get(f"/api/v1/eval-sets/{plan['eval_set_id']}").json()
    rel = admin.get(f"/api/v1/releases/{plan['release_id']}").json()
    from convoy_agent.evaluator import evaluate, summarize
    from convoy_agent.scoring import score_case

    def result(cases, summary_override=None, **over):
        timings = [{"case_id": c["id"], "status": c["status"], "latency_ms": 10.0} for c in cases]
        summary = summarize(cases, timings, [], expected_cases=len(es["cases"]))
        v, gates = evaluate(summary, plan["gates"])
        body = {
            "id": over.pop("id", "evr_t1"),
            "device_id": a.device_id,
            "release_id": rel["id"],
            "release_digest": rel["digest"],
            "plan_id": plan["id"],
            "plan_digest": plan["digest"],
            "eval_set_id": es["id"],
            "eval_set_digest": es["digest"],
            "stage": "eval",
            "device_verdict": v,
            "gates": gates,
            "summary": summary_override or summary,
            "cases": cases,
            "timings": timings,
            "coverage": {
                "expected": len(es["cases"]),
                "scored": len(cases),
                "completed": sum(1 for c in cases if c["status"] == "ok"),
                "complete": len(cases) == len(es["cases"]),
            },
            "provenance": {},
        }
        body.update(over)
        return body

    def send(body, seq):
        return a.client.post(
            "/api/agent/v1/spool",
            json={"lane": "critical", "records": [{"seq": seq, "kind": "eval_result", "body": body}]},
        ).json()

    good = [
        score_case(
            c,
            {
                "geo-1": "Paris",
                "geo-2": "Tokyo",
                "math-1": "56",
                "math-2": "42",
                "robot-stop": "STOP",
                "robot-json": '{"action":"dock"}',
                "color-1": "blue",
                "lang-1": "gracias",
            }[c["id"]],
        )
        for c in es["cases"]
    ]
    # device claims 'passed' but hashes say otherwise: server verdict is failed
    lying = [dict(c, passed=True, output_hash="0" * 64) for c in good]
    r = send(result(lying, device_verdict="passed", id="evr_lie"), 1)
    assert r["accepted"] == 1
    e = admin.get("/api/v1/evals/evr_lie").json()
    assert (
        e["device_verdict"] == "passed"
        and e["server_verdict"] == "failed"
        and all(
            c["server_method"] == "independently_rescored" for c in e["cases"] if c["match"] != "json_field"
        )
    )
    # partial coverage cannot pass; foreign case ids / wrong digests rejected
    r = send(result(good[:4], id="evr_partial"), 2)
    assert admin.get("/api/v1/evals/evr_partial").json()["server_verdict"] == "failed"
    r = send(result(good + [dict(good[0], id="not-in-suite")], id="evr_foreign"), 3)
    assert r["rejected"] and admin.get("/api/v1/evals/evr_foreign").status_code == 404
    r = send(result(good, plan_digest="0" * 64, id="evr_wrongplan"), 4)
    assert r["rejected"]
    r = send(result(good, id="evr_good"), 5)
    assert r["accepted"] == 1 and admin.get("/api/v1/evals/evr_good").json()["server_verdict"] == "passed"
    # idempotent replay of the same result id
    assert (
        send(result(good, id="evr_good"), 6)["accepted"] == 1
        and len(
            [x for x in admin.get(f"/api/v1/evals?device_id={a.device_id}").json() if x["id"] == "evr_good"]
        )
        == 1
    )
    cmp = admin.get("/api/v1/evals/compare?baseline=evr_good&candidate=evr_lie").json()
    assert cmp["comparable"] is True and abs(cmp["deltas"]["quality.pass_rate"]["delta"] - (-0.875)) < 1e-9
    # simulated evidence is labelled
    assert admin.get("/api/v1/evals/evr_good").json()["simulated"] is True


def test_restore_rehearsal_end_to_end(app, admin, settings, tmp_path):
    """Backup -> later changes (password reset, device revoked, new enrollment) -> restore the earlier
    snapshot -> quarantine -> console recovery -> rebind -> generation floor -> lift."""
    from convoy_server.db import session_scope
    from convoy_server.services.backup import restore_backup, take_backup
    from convoy_server.services.identity import recover_admin

    s = seed(admin)
    a = enrolled_agent(app, admin)
    rep = heartbeat(a, generation=0)
    with session_scope() as db:
        b = take_backup(db, str(tmp_path / "snap.db"))
    assert b["size"] > 0 and b["sha256"]
    # changes after the snapshot
    assert (
        admin.patch(
            f"/api/v1/users/{admin.get('/api/v1/auth/me').json()['user']['id']}",
            json={"password": "changed-after-backup"},
            headers=WEB,
        ).status_code
        == 200
    )
    admin = login(TestClient(app), "admin@example.com", "changed-after-backup")
    tok_after = admin.post(
        "/api/v1/enrollments", json={"label": "after", "simulated": True}, headers=WEB
    ).json()["token"]
    res = restore_backup(str(tmp_path / "snap.db"), confirm=False)
    assert res["ok"] is False  # requires --yes
    # R51: restore refuses while the service still holds the database; stop it (release the lock) first
    res = restore_backup(str(tmp_path / "snap.db"), confirm=True)
    assert res["ok"] is False and "locked" in res["error"]
    from convoy_server.db import reset_engine

    reset_engine()
    res = restore_backup(str(tmp_path / "snap.db"), confirm=True)
    assert res["ok"] is True
    # the app must reopen the restored DB: old and new passwords both dead, tokens dead, devices unbound
    c = TestClient(app)
    assert (
        c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin-password-1"}
        ).status_code
        == 401
    )
    assert (
        c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "changed-after-backup"}
        ).status_code
        == 401
    )
    assert a.client.post("/api/agent/v1/report", json={"seq": 99}).status_code == 401
    assert FakeAgent(app).enroll(tok_after).status_code == 401
    with session_scope() as db:
        recover_admin(db, "admin@example.com", "post-restore-password")
    rec = login(TestClient(app), "admin@example.com", "post-restore-password")
    me = rec.get("/api/v1/auth/me").json()
    assert me["installation"]["quarantined_at"] and me["installation"]["dispatch_paused_at"]
    assert rec.get("/api/v1/admin/installation").json()["restored_from"]["file"].endswith("snap.db")
    rebind = enrollment_token(rec, rebind_device_id=a.device_id)
    b2 = FakeAgent(app, "same")
    assert b2.enroll(rebind).status_code == 200 and b2.device_id == a.device_id
    # the device is ahead of the restored server: generation floor adopts its durable maximum
    rep = heartbeat(b2, generation=7)
    assert rep["quarantine"] is True
    assert rec.get(f"/api/v1/devices/{a.device_id}").json()["generation"] == 7
    assert rec.post("/api/v1/admin/quarantine/lift", headers=WEB).status_code == 200
    op = rec.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    assert op["generation"] == 8  # strictly higher than the device's durable maximum
