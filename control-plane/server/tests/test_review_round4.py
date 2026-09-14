"""Independent review packet at 571ebe6/bbeaabb (server evidence, rollouts, scheduler, restore):
R43-R48, R50, R53, R54, R15 follow-up. Each test is the reviewer's exact negative/positive scenario;
every negative failed against the code before the fix (see docs/REVIEW_LOG.md)."""

from __future__ import annotations

import time
from datetime import timedelta

from conftest import WEB, login, make_user
from convoy_server.db import session_scope, write_txn
from convoy_server.ids import iso, utcnow
from fastapi.testclient import TestClient
from helpers import (
    deploy_success,
    enrolled_agent,
    eval_evidence,
    eval_record,
    exec_sha,
    full_deploy,
    grant,
    heartbeat,
    probation_evidence,
    seed,
    spool,
)
from test_rollouts_and_scheduler import _device_deploys, _tick


def _rollout(admin, plan_id, ids, canaries):
    r = admin.post(
        "/api/v1/rollouts",
        json={"name": "r", "plan_id": plan_id, "target": {"device_ids": ids}, "canary_device_ids": canaries},
        headers=WEB,
    )
    assert r.status_code == 201, r.text
    return admin.post(f"/api/v1/rollouts/{r.json()['id']}/start", headers=WEB).json()


def _op_of(admin, ro, did):
    return admin.get(f"/api/v1/operations/{ro['device_states'][did]['operation_id']}").json()


# ---------------------------------------------------------------- R43
def test_r43_restore_of_completed_rollout_receives_grants_and_tracks_outcomes(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    full_deploy(a, admin, s["release_id"], s["plan_id"])
    ro = _rollout(admin, s["candidate_plan_id"], [a.device_id], [a.device_id])
    _device_deploys(admin, a, _op_of(admin, ro, a.device_id), s["candidate_plan_id"])
    _tick()
    ro = admin.get(f"/api/v1/rollouts/{ro['id']}").json()
    assert ro["status"] == "completed"
    res = admin.post(f"/api/v1/rollouts/{ro['id']}/restore", headers=WEB).json()
    rec_id = res["results"][a.device_id]["operation_id"]
    ro = admin.get(f"/api/v1/rollouts/{ro['id']}").json()
    assert ro["status"] == "restoring"  # not 'restored' until the device acknowledges
    rec = admin.get(f"/api/v1/operations/{rec_id}").json()
    rep = heartbeat(
        a,
        active=s["candidate_release_id"],
        recovery=s["release_id"],
        stage="active",
        generation=rec["generation"] - 1,
    )
    assert any(o["id"] == rec_id for o in rep["operations"])
    g = grant(a, rec, active=s["candidate_release_id"], recovery=s["release_id"])
    assert g.status_code == 200, g.text  # was 423 "rollout is restored; no new grants"
    assert admin.post(f"/api/v1/rollouts/{ro['id']}/restore", headers=WEB).status_code == 409  # in progress
    r = a.client.post(
        f"/api/agent/v1/operations/{rec_id}/outcome",
        json={
            "status": "succeeded",
            "grant_id": g.json()["grant_id"],
            "grant_consumed_seq": a.seq,
            "result": {
                "active_release_id": s["release_id"],
                "release_digest": rec["payload"]["release_digest"],
                "generation": rec["generation"],
            },
            "evidence": {"health": "ok", "runtime": {"binary_sha256": exec_sha(rec), "build_info": "sim"}},
        },  # fmt: skip
    )
    assert r.status_code == 200, r.text
    _tick()
    ro = admin.get(f"/api/v1/rollouts/{ro['id']}").json()
    assert ro["status"] == "restored" and ro["device_states"][a.device_id]["restore_status"] == "succeeded"
    # a deploy operation of a finished rollout still gets no grant (expansion gating is unchanged)
    dep = _op_of(admin, ro, a.device_id)
    assert dep["status"] == "succeeded"


# ---------------------------------------------------------------- R44
def test_r44_eval_result_cannot_mutate_a_foreign_or_mismatched_operation(app, admin):
    s = seed(admin)
    a, b = enrolled_agent(app, admin, "a"), enrolled_agent(app, admin, "b")
    for x in (a, b):
        heartbeat(x)
    ops = {}
    for x in (a, b):
        ops[x.device_id] = admin.post(
            f"/api/v1/devices/{x.device_id}/deploy",
            json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
            headers=WEB,
        ).json()
        heartbeat(x)
    op_b = ops[b.device_id]
    # device A cites device B's operation: the whole batch is refused, nothing is written anywhere
    body = eval_record(a, {**op_b, "payload": op_b["payload"]}, "passed", id="evr_foreign")
    r = spool(a, "critical", "eval_result", body)
    assert r.status_code == 403, r.text
    assert admin.get("/api/v1/evals/evr_foreign").status_code == 404
    assert (admin.get(f"/api/v1/operations/{op_b['id']}").json()["progress"] or {}).get(
        "eval_result_id"
    ) is None
    assert not any(
        c["device_id"] == a.device_id for c in admin.get("/api/v1/usage").json()["coverage"]["lanes"]
    )
    # same device, wrong generation / wrong plan / wrong type binding: record rejected, projection untouched
    op_a = ops[a.device_id]
    for name, over in (
        ("generation", {"generation": op_a["generation"] + 1}),
        ("missing generation", {"generation": None}),
        ("unknown op", {"operation_id": "op_nope"}),
    ):
        body = eval_record(a, op_a, "passed", id=f"evr_{name[:4].strip()}", **over)
        r = spool(a, "critical", "eval_result", body)
        assert r.status_code == 200 and r.json()["rejected"], (name, r.text)
        assert (admin.get(f"/api/v1/operations/{op_a['id']}").json()["progress"] or {}).get(
            "eval_result_id"
        ) is None, name
    # a health operation cannot carry eval evidence either
    hop = admin.post(f"/api/v1/devices/{b.device_id}/health", headers=WEB).json()
    body = eval_record(
        b,
        {**hop, "payload": {**hop["payload"], "plan_id": s["plan_id"], "release_id": s["release_id"]}},
        "passed",
        id="evr_htype",
    )
    assert spool(b, "critical", "eval_result", body).json()["rejected"]
    # positive: the genuine binding is accepted and recorded on the operation
    evr, _ = eval_evidence(a, op_a)
    assert admin.get(f"/api/v1/operations/{op_a['id']}").json()["progress"]["eval_result_id"] == evr


# ---------------------------------------------------------------- R45
def test_r45_performance_aggregates_are_recomputed_from_structured_records(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    full_deploy(a, admin, s["release_id"], s["plan_id"])
    strict = admin.post(
        "/api/v1/plans",
        json={
            "name": "strict-latency",
            "release_id": s["release_id"],
            "gates": [
                {"metric": "quality.pass_rate", "op": "min", "limit": 0.8},
                {"metric": "latency.p95_ms", "op": "max", "limit": 50},
                {"metric": "memory.peak_used_mb", "op": "max", "limit": 6000},
            ],
        },
        headers=WEB,
    )
    assert strict.status_code == 201, strict.text
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/eval", json={"plan_id": strict.json()["id"]}, headers=WEB
    ).json()
    assert "payload" in op, op
    heartbeat(a, active=s["release_id"], stage="active", generation=op["generation"] - 1)
    # 1) cases carry latency 1000 ms while the supplied summary claims p95 = 1 ms: refused, nothing stored
    lie = eval_record(a, op, "passed", latency_ms=1000.0, id="evr_lie")
    lie["summary"]["latency"]["p95_ms"] = 1.0
    r = spool(a, "critical", "eval_result", lie)
    assert r.status_code == 200 and "contradicts" in r.json()["rejected"][0]["reason"], r.text
    assert admin.get("/api/v1/evals/evr_lie").status_code == 404
    # 2) honest slow timings: the server's own recomputation fails the latency gate
    slow = eval_record(a, op, "passed", latency_ms=1000.0, id="evr_slow")
    assert spool(a, "critical", "eval_result", slow).json()["accepted"] == 1
    e = admin.get("/api/v1/evals/evr_slow").json()
    lat = next(g for g in e["gates"] if g["metric"] == "latency.p95_ms")
    assert (
        e["server_verdict"] == "failed"
        and lat["status"] == "fail"
        and lat["evidence_method"] == "server_recomputed"
    )
    assert e["summary"]["latency"]["p95_ms"] == 1000.0 and e["coverage"]["timings"] == 8
    # 3) a supplied latency aggregate with NO timing records is an unsupported claim: refused
    nolat = eval_record(a, op, "passed", id="evr_nolat")
    nolat.pop("timings")
    r = spool(a, "critical", "eval_result", nolat)
    assert r.json()["rejected"] and "no structured evidence" in r.json()["rejected"][0]["reason"]
    # 4) no timings and no latency claim: the latency gate is unavailable -> inconclusive, never passed
    honest = eval_record(a, op, "passed", id="evr_noev")
    honest.pop("timings")
    honest["summary"].pop("latency")
    honest["summary"].pop("throughput")
    assert spool(a, "critical", "eval_result", honest).json()["accepted"] == 1
    e = admin.get("/api/v1/evals/evr_noev").json()
    lat = next(g for g in e["gates"] if g["metric"] == "latency.p95_ms")
    assert (
        e["server_verdict"] == "inconclusive"
        and lat["status"] == "unavailable"
        and lat["evidence_method"] == "unavailable"
    )
    # 5) memory recomputed from the transmitted sensor samples; a claimed peak below the samples is refused
    mem = eval_record(a, op, "passed", id="evr_mem")
    mem["summary"]["memory"]["peak_used_mb"] = 10.0
    assert "contradicts" in spool(a, "critical", "eval_result", mem).json()["rejected"][0]["reason"]
    good = eval_record(a, op, "passed", id="evr_good")
    assert spool(a, "critical", "eval_result", good).json()["accepted"] == 1
    e = admin.get("/api/v1/evals/evr_good").json()
    memg = next(g for g in e["gates"] if g["metric"] == "memory.peak_used_mb")
    assert (
        e["server_verdict"] == "passed"
        and memg["value"] == 3100.0
        and memg["evidence_method"] == "server_recomputed"
    )
    assert e["coverage"]["sensor_samples"] == 2
    # 6) bounded: too many timing records / mismatched timing identity are refused
    bad = eval_record(a, op, "passed", id="evr_tim")
    bad["timings"] = bad["timings"][:-1]
    assert "one-to-one" in spool(a, "critical", "eval_result", bad).json()["rejected"][0]["reason"]


# ---------------------------------------------------------------- R15 follow-up
def test_r15fu_success_must_cite_accepted_matching_eval_and_probation(app, admin):
    s = seed(admin)
    a, other = enrolled_agent(app, admin, "a"), enrolled_agent(app, admin, "o")
    heartbeat(a)
    heartbeat(other)
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(a)
    g = grant(a, op).json()
    rt = {"binary_sha256": exec_sha(op), "build_info": "sim"}
    plan_digest = op["payload"]["plan_digest"]
    good_prob = probation_evidence(a, op)
    # another device's genuinely accepted result for the same plan
    oop = admin.post(
        f"/api/v1/devices/{other.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(other)
    foreign_evr, _ = eval_evidence(other, oop)
    # a baseline-stage result of this very operation does not qualify a deploy
    base_evr, _ = eval_evidence(a, op, stage="baseline")
    evr, _ = eval_evidence(a, op)

    def attempt(eval_id, prob=good_prob):
        ev = {
            "health": "ok",
            "cutover_ms": 1,
            "runtime": rt,
            "eval": {"verdict": "passed", "plan_digest": plan_digest, "eval_result_id": eval_id},
        }
        if prob is not None:
            ev["probation"] = prob
        return deploy_success(a, op, g, evidence=ev)

    for name, r in (
        ("not ingested", attempt("evr_never_uploaded")),
        ("other device's result", attempt(foreign_evr)),
        ("baseline stage", attempt(base_evr)),
        ("no probation", attempt(evr, None)),
        ("probation too short", attempt(evr, {**good_prob, "elapsed_s": good_prob["min_s"] - 1})),
        ("too few requests", attempt(evr, {**good_prob, "requests_served": -1})),
        ("probation longer than the grant age", attempt(evr, {**good_prob, "elapsed_s": 3600.0})),
    ):
        assert r.status_code == 422, (name, r.text)
        o = admin.get(f"/api/v1/operations/{op['id']}").json()
        assert (
            o["status"] == "granted"
            and admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] == op["id"]
        ), name
    ok = attempt(evr)
    assert ok.status_code == 200, ok.text
    assert admin.get(f"/api/v1/operations/{op['id']}").json()["status"] == "succeeded"
    # eval operations: success must cite the accepted result of THIS operation
    heartbeat(a, active=s["release_id"], stage="active", generation=op["generation"])
    eop = admin.post(
        f"/api/v1/devices/{a.device_id}/eval", json={"plan_id": s["plan_id"]}, headers=WEB
    ).json()
    heartbeat(a, active=s["release_id"], stage="active", generation=eop["generation"] - 1)
    eg = grant(a, eop, active=s["release_id"]).json()

    def eval_success(eval_id, verdict="passed"):
        return a.client.post(
            f"/api/agent/v1/operations/{eop['id']}/outcome",
            json={
                "status": "succeeded",
                "grant_id": eg["grant_id"],
                "grant_consumed_seq": a.seq,
                "result": {"active_release_id": s["release_id"], "eval_result_id": eval_id},
                "evidence": {
                    "eval": {"verdict": verdict, "plan_digest": plan_digest, "eval_result_id": eval_id},
                    "runtime": rt,
                },
            },  # fmt: skip
        )

    assert eval_success("evr_unknown").status_code == 422
    assert eval_success(evr).status_code == 422  # belongs to the deploy operation, not this eval
    eevr, _ = eval_evidence(a, eop)
    assert eval_success(eevr, "failed").status_code == 422  # verdict must equal the server verdict
    assert eval_success(eevr).status_code == 200


def test_r15fu_rollout_qualification_is_bound_to_the_deploy_operation(app, admin):
    """An unrelated recent passing eval must not qualify a canary; the bound result must persist."""
    from convoy_server.models import EvalResult

    s = seed(admin)
    a = enrolled_agent(app, admin)
    full_deploy(a, admin, s["release_id"], s["plan_id"])
    ro = _rollout(admin, s["candidate_plan_id"], [a.device_id], [a.device_id])
    op = _op_of(admin, ro, a.device_id)
    _device_deploys(admin, a, op, s["candidate_plan_id"])
    bound = admin.get(f"/api/v1/operations/{op['id']}").json()["outcome"]["evidence"]["eval"][
        "eval_result_id"
    ]
    # an unrelated passing eval for the same release/plan exists (a later standalone eval operation)
    eop = admin.post(
        f"/api/v1/devices/{a.device_id}/eval", json={"plan_id": s["candidate_plan_id"]}, headers=WEB
    ).json()
    heartbeat(a, active=s["candidate_release_id"], stage="active", generation=eop["generation"] - 1)
    other_evr, _ = eval_evidence(a, eop)
    with session_scope() as db:
        with write_txn(db):
            db.delete(db.get(EvalResult, bound))  # the bound evidence is gone; the unrelated one remains
    _tick()
    ro = admin.get(f"/api/v1/rollouts/{ro['id']}").json()
    assert ro["status"] == "failed" and "bound to this deploy" in ro["device_states"][a.device_id]["reason"]
    assert other_evr and admin.get(f"/api/v1/evals/{other_evr}").status_code == 200


# ---------------------------------------------------------------- R46
def test_r46_history_uploads_never_keep_stale_canary_health_promotable(app, admin):
    from convoy_server.models import Device

    s = seed(admin)
    devs = [enrolled_agent(app, admin, f"d{i}") for i in range(2)]
    for d in devs:
        full_deploy(d, admin, s["release_id"], s["plan_id"])
    ids = [d.device_id for d in devs]
    ro = _rollout(admin, s["candidate_plan_id"], ids, [ids[0]])
    op = _op_of(admin, ro, ids[0])
    _device_deploys(admin, devs[0], op, s["candidate_plan_id"])
    _tick()
    assert admin.get(f"/api/v1/rollouts/{ro['id']}").json()["status"] == "awaiting_promotion"
    # the live observation expires; a history-only upload refreshes last_seen (transport receipt) but not liveness
    with session_scope() as db:
        with write_txn(db):
            db.get(Device, ids[0]).live_at = utcnow() - timedelta(minutes=10)
    heartbeat(
        devs[0],
        kind="history",
        active=s["candidate_release_id"],
        stage="active",
        generation=op["generation"],
        live_nonce=None,
    )
    d = admin.get(f"/api/v1/devices/{ids[0]}").json()
    assert d["status"] == "online" and d["observed_health"] == "ok"  # transport says online...
    r = admin.post(f"/api/v1/rollouts/{ro['id']}/promote", headers=WEB)
    assert r.status_code == 409 and "live" in r.text  # ...but promotion needs a live, challenged report
    # a live report at the wrong generation (older boot, pre-deploy) does not qualify either
    heartbeat(devs[0], active=s["candidate_release_id"], stage="active", generation=op["generation"] - 1)
    assert admin.post(f"/api/v1/rollouts/{ro['id']}/promote", headers=WEB).status_code == 409
    heartbeat(
        devs[0],
        active=s["candidate_release_id"],
        recovery=s["release_id"],
        stage="active",
        generation=op["generation"],
    )
    assert admin.post(f"/api/v1/rollouts/{ro['id']}/promote", headers=WEB).status_code == 200


# ---------------------------------------------------------------- R47
def test_r47_frozen_baseline_drift_blocks_expansion_instead_of_changing_the_transition(app, admin):
    s = seed(admin)
    devs = [enrolled_agent(app, admin, f"d{i}") for i in range(2)]
    for d in devs:
        full_deploy(d, admin, s["release_id"], s["plan_id"])
    ids = [d.device_id for d in devs]
    # a third release C, deployable with its own plan
    c = admin.post(
        "/api/v1/releases",
        json={
            "name": "sim-c",
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
            "config": {"seed": 3},
        },  # fmt: skip
        headers=WEB,
    ).json()
    cplan = admin.post(
        "/api/v1/plans",
        json={
            "name": "c",
            "release_id": c["id"],
            "sample_policy": {"probation_min_s": 0, "probation_min_requests": 0},
        },
        headers=WEB,
    ).json()
    ro = _rollout(admin, s["candidate_plan_id"], ids, [ids[0]])
    assert ro["previous"][ids[1]] == s["release_id"]  # frozen baseline A for the expansion target
    # during canary qualification, D is independently moved to C
    full_deploy(devs[1], admin, c["id"], cplan["id"], active=s["release_id"])
    assert admin.get(f"/api/v1/devices/{ids[1]}").json()["observed_active_release_id"] == c["id"]
    _device_deploys(admin, devs[0], _op_of(admin, ro, ids[0]), s["candidate_plan_id"])
    _tick()
    ro = admin.post(f"/api/v1/rollouts/{ro['id']}/promote", headers=WEB).json()
    st = ro["device_states"][ids[1]]
    assert st["status"] == "failed" and "baseline drift" in st["reason"] and "operation_id" not in st
    assert ro["status"] == "failed"
    assert admin.get(f"/api/v1/devices/{ids[1]}").json()["active_operation_id"] is None
    assert not [
        o for o in admin.get(f"/api/v1/devices/{ids[1]}/operations").json() if o.get("rollout_id") == ro["id"]
    ]


# ---------------------------------------------------------------- R48
def _batch(a, records, loss_ranges=None, lane="usage"):
    return a.client.post(
        "/api/agent/v1/spool", json={"lane": lane, "records": records, "loss_ranges": loss_ranges or []}
    ).json()


def _usage_rec(seq, n=1):
    return {"seq": seq, "kind": "usage", "body": {"ts": time.time(), "inference_requests": n}}


def _total(admin, device_id):
    u = admin.get("/api/v1/usage").json()
    return next(
        (d["metrics"].get("inference_requests", 0) for d in u["devices"] if d["device_id"] == device_id), 0
    )


def test_r48_spool_ack_advances_only_over_a_contiguous_union(app, admin):
    a = enrolled_agent(app, admin)
    # cursor 0 + record 100 + loss 1..1: 2..99 are neither received nor declared -> nothing acknowledged
    r = _batch(a, [_usage_rec(100)], [{"from_seq": 1, "to_seq": 1}])
    assert r["committed_seq"] == 1 and r["accepted"] == 0 and r["deferred"] == [100], r
    assert _total(admin, a.device_id) == 0
    # disjoint loss 100..100 on cursor 1 never jumps the cursor
    r = _batch(a, [], [{"from_seq": 100, "to_seq": 100}])
    assert r["committed_seq"] == 1
    # partial: loss 2..50 + record 52 -> cursor 50, record 52 deferred (51 unknown)
    r = _batch(a, [_usage_rec(52)], [{"from_seq": 2, "to_seq": 50}])
    assert r["committed_seq"] == 50 and r["deferred"] == [52] and r["accepted"] == 0
    # overlapping, unordered declared ranges that together cover 51..99 + record 100 -> cursor 100
    r = _batch(a, [_usage_rec(100, 5)], [{"from_seq": 70, "to_seq": 99}, {"from_seq": 40, "to_seq": 80}])
    assert r["committed_seq"] == 100 and r["accepted"] == 1 and r["deferred"] == []
    assert _total(admin, a.device_id) == 5
    cov = admin.get("/api/v1/usage").json()["coverage"]["loss_ranges"]
    mine = sorted((x["from_seq"], x["to_seq"]) for x in cov if x["device_id"] == a.device_id)
    assert mine == [(1, 1), (2, 50), (51, 80), (70, 99)]  # clipped to the segments actually advanced over
    # out-of-order records 103,101,102 -> all committed in order
    r = _batch(a, [_usage_rec(103), _usage_rec(101), _usage_rec(102)])
    assert r["committed_seq"] == 103 and r["accepted"] == 3
    # lost ACK: replay of the same batch recounts nothing and moves nothing
    r = _batch(a, [_usage_rec(103), _usage_rec(101), _usage_rec(102)])
    assert r["committed_seq"] == 103 and r["accepted"] == 0 and _total(admin, a.device_id) == 8
    # a deferred record re-sent later with the gap declared is counted exactly once
    r = _batch(a, [_usage_rec(105, 7)])
    assert r["deferred"] == [105] and _total(admin, a.device_id) == 8
    r = _batch(a, [_usage_rec(105, 7)], [{"from_seq": 104, "to_seq": 104}])
    assert r["committed_seq"] == 105 and r["accepted"] == 1 and _total(admin, a.device_id) == 15
    # duplicate seq inside one batch and an invalid range are rejected / ignored, never advance
    r = _batch(a, [_usage_rec(106), _usage_rec(106)], [{"from_seq": 9, "to_seq": 3}])
    assert (
        r["committed_seq"] == 106
        and r["accepted"] == 1
        and r["rejected"][0]["reason"] == "duplicate seq in batch"
    )
    # trailing declared loss extends the frontier only when it starts exactly at cursor+1
    r = _batch(a, [], [{"from_seq": 108, "to_seq": 110}])
    assert r["committed_seq"] == 106
    r = _batch(a, [], [{"from_seq": 107, "to_seq": 107}, {"from_seq": 108, "to_seq": 110}])
    assert r["committed_seq"] == 110


# ---------------------------------------------------------------- R50
def _due_schedule(admin, kind, target, payload=None, owner=None, **extra):
    client = owner or admin
    body = {
        "name": kind,
        "kind": kind,
        "cron": "0 * * * *",
        "timezone": "UTC",
        "target": target,
        "payload": payload or {},
        **extra,
    }
    r = client.post("/api/v1/schedules", json=body, headers=WEB)
    assert r.status_code == 201, r.text
    return r.json()


def _make_due(sid, ago_s=5, civil=None):
    from convoy_server.models import Schedule

    with session_scope() as db:
        with write_txn(db):
            row = db.get(Schedule, sid)
            due = utcnow() - timedelta(seconds=ago_s)
            row.next_run_at = due
            row.next_civil = civil or due.strftime("%Y-%m-%dT%H:%M")
            return row.revision


def _tick_schedules():
    from convoy_server.services import scheduler as sch

    with session_scope() as db:
        return sch.tick_schedules(db, None)


def test_r50_schedule_pause_edit_and_owner_revocation_block_grants(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    full_deploy(a, admin, s["release_id"], s["plan_id"])
    op_user = make_user(admin, "owner@example.com", "operator")
    owner = login(TestClient(app), "owner@example.com", "password-123")
    sch = _due_schedule(admin, "health", {"device_ids": [a.device_id]}, owner=owner)
    _make_due(sch["id"])
    fired = _tick_schedules()
    op_id = fired[0]["devices"][0]["operation_id"]
    op = admin.get(f"/api/v1/operations/{op_id}").json()
    heartbeat(a, active=s["release_id"], stage="active", generation=op["generation"])
    # paused schedule -> no new grants; resumed -> grant ok
    assert admin.post(f"/api/v1/schedules/{sch['id']}/pause", headers=WEB).status_code == 200
    r = grant(a, op, active=s["release_id"])
    assert r.status_code == 423 and "paused" in r.text, r.text
    assert admin.post(f"/api/v1/schedules/{sch['id']}/resume", headers=WEB).status_code == 200
    # edited after dispatch -> the occurrence carries stale intent -> no grant
    assert (
        admin.patch(f"/api/v1/schedules/{sch['id']}", json={"name": "renamed"}, headers=WEB).status_code
        == 200
    )
    r = grant(a, op, active=s["release_id"])
    assert r.status_code == 423 and "revision" in r.text
    # a second occurrence at the current revision: a demoted owner blocks NEW grants; once re-authorised
    # a grant is issued, and an already-consumed grant keeps its honest outcome even after revocation
    _make_due(sch["id"], civil="2031-01-01T00:00")
    op2_id = _tick_schedules()[0]["devices"][0]["operation_id"]
    op2 = admin.get(f"/api/v1/operations/{op2_id}").json()
    assert (
        admin.patch(f"/api/v1/users/{op_user['id']}", json={"role": "viewer"}, headers=WEB).status_code == 200
    )
    r = grant(a, op2, active=s["release_id"])
    assert r.status_code == 423 and "owner" in r.text, r.text
    assert (
        admin.patch(f"/api/v1/users/{op_user['id']}", json={"role": "operator"}, headers=WEB).status_code
        == 200
    )
    g = grant(a, op2, active=s["release_id"])
    assert g.status_code == 200, g.text
    assert (
        admin.patch(f"/api/v1/users/{op_user['id']}", json={"role": "viewer"}, headers=WEB).status_code == 200
    )
    r = a.client.post(
        f"/api/agent/v1/operations/{op2_id}/outcome",
        json={
            "status": "succeeded",
            "grant_id": g.json()["grant_id"],
            "grant_consumed_seq": a.seq,
            "result": {},
            "evidence": {"health": "ok"},
        },
    )
    assert r.status_code == 200, r.text  # the grant had already been consumed: honest completion is kept


def test_r50_dispatch_revalidates_selection_inside_the_fire_transaction(app, admin):
    from convoy_server.models import Occurrence, Schedule
    from convoy_server.services import scheduler as sch
    from sqlalchemy import select

    s = seed(admin)
    a = enrolled_agent(app, admin)
    full_deploy(a, admin, s["release_id"], s["plan_id"])
    row = _due_schedule(admin, "health", {"device_ids": [a.device_id]})
    rev = _make_due(row["id"])
    with session_scope() as db:
        srow = db.get(Schedule, row["id"])
        due, civil = srow.next_run_at, srow.next_civil
        # selected as due, then paused by an operator before the fire transaction
        assert admin.post(f"/api/v1/schedules/{row['id']}/pause", headers=WEB).status_code == 200
        res = sch.fire(db, None, srow, due, civil, expected_revision=rev)
        assert res.get("stale") and "paused" in res["stale"]
        assert admin.post(f"/api/v1/schedules/{row['id']}/resume", headers=WEB).status_code == 200
        rev2 = _make_due(row["id"])
        db.refresh(srow)
        due, civil = srow.next_run_at, srow.next_civil
        # edited between selection and fire: the old revision must not dispatch
        assert (
            admin.patch(f"/api/v1/schedules/{row['id']}", json={"window_s": 900}, headers=WEB).status_code
            == 200
        )
        res = sch.fire(db, None, srow, due, civil, expected_revision=rev2)
        assert res.get("stale") and "revision" in res["stale"]
        assert db.scalar(select(Occurrence).where(Occurrence.schedule_id == row["id"])) is None
        # the current revision, still due, dispatches
        rev3 = _make_due(row["id"])
        db.refresh(srow)
        res = sch.fire(db, None, srow, srow.next_run_at, srow.next_civil, expected_revision=rev3)
        assert res.get("occurrence_id")


# ---------------------------------------------------------------- R53
def test_r53_stalled_leader_cannot_advance_rollouts_or_expire_grants(app, admin):
    from convoy_server.models import Device, SchedulerLease
    from convoy_server.services import operations as ops
    from convoy_server.services import rollouts as rsvc
    from convoy_server.services.scheduler import FenceLost, Worker

    s = seed(admin)
    devs = [enrolled_agent(app, admin, f"d{i}") for i in range(3)]
    for d in devs:
        full_deploy(d, admin, s["release_id"], s["plan_id"])
    ids = [d.device_id for d in devs]
    ro = _rollout(admin, s["candidate_plan_id"], ids, [ids[0]])
    _device_deploys(admin, devs[0], _op_of(admin, ro, ids[0]), s["candidate_plan_id"])
    _tick()
    ro = admin.post(f"/api/v1/rollouts/{ro['id']}/promote", headers=WEB).json()
    assert ro["status"] == "expanding" and ro["device_states"][ids[1]]["status"] == "deploying"
    _device_deploys(admin, devs[1], _op_of(admin, ro, ids[1]), s["candidate_plan_id"])
    from convoy_server.models import Rollout

    def state(db, did):
        db.expire_all()
        return db.get(Rollout, ro["id"]).device_states[did]["status"]

    w1, w2 = Worker("w1"), Worker("w2")
    with session_scope() as db:
        assert state(db, ids[2]) == "queued"  # the next expansion step is pending
        assert w1.acquire(db)
        with write_txn(db):
            db.get(SchedulerLease, "scheduler").expires_at = utcnow() - timedelta(seconds=1)  # w1 stalls
        assert w2.acquire(db) and w2.fence == w1.fence + 1
        # the stalled ex-leader resumes: it must not create the next expansion operation
        for fn in (
            lambda: rsvc.advance_all(db, worker=w1),
            lambda: ops.expire_grants(db, worker=w1),
            lambda: ops.sweep_deadlines(db, worker=w1),
        ):
            try:
                fn()
                raise AssertionError("stale worker committed")
            except FenceLost:
                pass
        assert state(db, ids[2]) == "queued"
        assert db.get(Device, ids[2]).active_operation_id is None
        rsvc.advance_all(db, worker=w2)  # the current leader advances
        assert state(db, ids[2]) == "deploying"
    # the manual API path (no worker) is unaffected by the lease
    assert admin.post(f"/api/v1/rollouts/{ro['id']}/pause", headers=WEB).status_code == 200


# ---------------------------------------------------------------- R54
def test_r54_run_once_catch_up_gets_a_valid_execution_window(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    full_deploy(a, admin, s["release_id"], s["plan_id"])
    sch = _due_schedule(
        admin,
        "health",
        {"device_ids": [a.device_id]},
        missed_policy="run_once",
        catchup_age_s=3600,
        window_s=300,
    )
    _make_due(sch["id"], ago_s=600, civil="2030-06-01T02:00")
    fired = _tick_schedules()
    assert fired[0].get("catch_up") is True and fired[0]["late_s"] >= 600
    op = admin.get(f"/api/v1/operations/{fired[0]['devices'][0]['operation_id']}").json()
    # the occurrence keeps its civil identity but the execution window starts now
    occ = admin.get(f"/api/v1/schedules/{sch['id']}/occurrences").json()[0]
    assert occ["civil_key"] == "2030-06-01T02:00" and occ["results"]["catch_up"]["late_s"] >= 600
    assert op["deadline_at"] > iso(utcnow() + timedelta(seconds=250))
    heartbeat(a, active=s["release_id"], stage="active", generation=op["generation"])
    g = grant(a, op, active=s["release_id"])
    assert g.status_code == 200, g.text  # was 423 "maintenance window has ended"
