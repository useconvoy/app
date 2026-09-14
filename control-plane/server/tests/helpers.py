"""Shared helpers for API-level tests: seed the simulator catalog and drive a fake device."""

from __future__ import annotations

import secrets
from typing import Any

from conftest import WEB, FakeAgent, enrollment_token
from convoy_server.ids import iso, utcnow


def seed(admin) -> dict[str, Any]:
    r = admin.post("/api/v1/sim/seed", headers=WEB)
    assert r.status_code == 200, r.text
    return r.json()


def enrolled_agent(app, admin, name="sim-1") -> FakeAgent:
    a = FakeAgent(app, name)
    assert a.enroll(enrollment_token(admin)).status_code == 200
    return a


def heartbeat(
    a: FakeAgent,
    *,
    active=None,
    recovery=None,
    stage="idle",
    health="ok",
    generation=0,
    operation_id=None,
    kind="heartbeat",
    latch=None,
    challenge=None,
    telemetry=None,
    source_ts=None,
    seq=None,
    runtime=None,
    hardware=None,
    live_nonce="auto",
) -> dict[str, Any]:
    a.seq = seq if seq is not None else a.seq + 1
    nonce = getattr(a, "live_nonce", None) if live_nonce == "auto" else live_nonce
    body = {
        "seq": a.seq, "boot_id": a.boot_id, "kind": kind, "source_ts": source_ts or iso(utcnow()), "agent_version": "test",
        "observed": {"active_release_id": active, "recovery_release_id": recovery, "stage": stage, "health": health, "generation": generation, "operation_id": operation_id, "failed_generation_latch": latch, "runtime": runtime},
        "telemetry": (None if telemetry == "omit" else telemetry) if telemetry is not None else {"mem_total_mb": 7620.0, "mem_available_mb": 5000.0, "disk_free_mb": 20000.0},
        "time_confidence": "ntp",
        "challenge": challenge,
        "hardware": hardware,
        "live_nonce": nonce,
    }  # fmt: skip
    r = a.client.post("/api/agent/v1/report", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    if out.get("live_nonce"):
        a.live_nonce = out["live_nonce"]
    if out.get("applied"):
        a.live_seq = a.seq
    return out


def grant(a: FakeAgent, op: dict[str, Any], *, active=None, recovery=None, nonce=None, **override):
    p = op["payload"]
    body = {
        "nonce": nonce or secrets.token_hex(8),
        "boot_id": a.boot_id,
        "seq": a.seq,
        "active_release_id": active,
        "recovery_release_id": recovery,
        "release_digest": p.get("release_digest"),
        "plan_digest": p.get("plan_digest"),
        "artifact_sha256": p.get("artifact_sha256"),
    }
    body.update(override)
    return a.client.post(f"/api/agent/v1/operations/{op['id']}/grant", json=body)


def exec_sha(op: dict[str, Any]) -> str | None:
    for f in op["payload"].get("artifact_files") or []:
        if f["path"].endswith(("llama-server", "llama-server.sim")):
            return f["sha256"]
    return None


ANSWERS = {
    "geo-1": "Paris",
    "geo-2": "Tokyo",
    "math-1": "56",
    "math-2": "42",
    "robot-stop": "STOP",
    "robot-json": '{"action":"dock"}',
    "color-1": "blue",
    "lang-1": "gracias",
}


def eval_record(
    a: FakeAgent, op: dict[str, Any], verdict="passed", *, stage="eval", latency_ms=20.0, **override
) -> dict[str, Any]:
    """Build a structurally complete eval result for `op` from the agent-facing plan/release manifests:
    per-case records, one timing per case, sensor samples, generation and a summary computed with the
    shared evaluator from exactly those structured records (R45)."""
    from convoy_agent.evaluator import evaluate, summarize
    from convoy_agent.scoring import score_case

    p = op["payload"]
    plan = a.client.get(f"/api/agent/v1/plans/{p['plan_id']}").json()
    rel = a.client.get(f"/api/agent/v1/releases/{p['release_id']}").json()
    es = plan["eval_set"]
    cases = [score_case(c, ANSWERS[c["id"]] if verdict == "passed" else "wrong") for c in es["cases"]]
    timings = [
        {
            "case_id": c["id"],
            "status": "ok",
            "latency_ms": latency_ms,
            "ttft_ms": 5.0,
            "queue_ms": 0.5,
            "tok_s": 30.0,
        }
        for c in es["cases"]
    ]
    sensors = [
        {
            "ts": 1.0,
            "mem_used_mb": 3000.0,
            "mem_available_mb": 4000.0,
            "temp_max_c": 50.0,
            "power_w": 6.0,
            "gpu_pct": 10.0,
            "clock_confidence": "ntp",
        },
        {
            "ts": 2.0,
            "mem_used_mb": 3100.0,
            "mem_available_mb": 3900.0,
            "temp_max_c": 51.0,
            "power_w": 6.5,
            "gpu_pct": 12.0,
            "clock_confidence": "ntp",
        },
    ]
    summary = summarize(
        cases, timings, sensors, runtime_props={"backend": "simulated"}, expected_cases=len(es["cases"])
    )
    v, gates = evaluate(summary, plan["gates"])
    body = {
        "id": f"evr_{op['id'][-8:]}{verdict[:1]}{stage[:1]}",
        "device_id": a.device_id,
        "operation_id": op["id"],
        "generation": op["generation"],
        "release_id": rel["release_id"],
        "release_digest": rel["digest"],
        "plan_id": plan["id"],
        "plan_digest": plan["digest"],
        "eval_set_id": es["id"],
        "eval_set_digest": es["digest"],
        "stage": stage,
        "device_verdict": v,
        "gates": gates,
        "summary": summary,
        "cases": cases,
        "timings": timings,
        "sensor_samples": sensors,
        "coverage": {
            "expected": len(es["cases"]),
            "scored": len(cases),
            "completed": len(cases),
            "cancelled": False,
            "complete": True,
        },
        "provenance": {"simulated": True, "runtime": {"backend": "simulated"}},
    }
    body.update(override)
    return body


def spool(a: FakeAgent, lane: str, kind: str, body: dict[str, Any], **extra):
    a.spool_seq = getattr(a, "spool_seq", 0) + 1
    r = a.client.post(
        "/api/agent/v1/spool",
        json={"lane": lane, "records": [{"seq": a.spool_seq, "kind": kind, "body": body}], **extra},
    )
    if r.status_code >= 400:
        a.spool_seq -= 1  # the batch was refused as a whole: the device keeps and re-sends the record
    return r


def eval_evidence(a: FakeAgent, op: dict[str, Any], verdict="passed", **override) -> tuple[str, str]:
    """Ingest a genuine eval result for `op` through the critical lane; returns (id, device verdict)."""
    body = eval_record(a, op, verdict, **override)
    r = spool(a, "critical", "eval_result", body)
    assert r.status_code == 200 and r.json()["accepted"] == 1, r.text
    return body["id"], body["device_verdict"]


def probation_evidence(a: FakeAgent, op: dict[str, Any]) -> dict[str, Any]:
    plan = a.client.get(f"/api/agent/v1/plans/{op['payload']['plan_id']}").json()
    sp = plan.get("sample_policy") or {}
    return {
        "elapsed_s": float(sp.get("probation_min_s", 60)),
        "requests_served": int(sp.get("probation_min_requests", 0)),
        "health_checks": 3,
        "min_s": float(sp.get("probation_min_s", 60)),
        "min_requests": int(sp.get("probation_min_requests", 0)),
    }


def deploy_success(a: FakeAgent, op: dict[str, Any], g: dict[str, Any], *, eval_verdict="passed", **override):
    """Post a deploy success. For plan-qualified deploys the eval result is ingested first (deterministic
    id, idempotent replay) and probation evidence follows the plan policy, unless `evidence` overrides."""
    p = op["payload"]
    evidence: dict[str, Any] = {
        "health": "ok",
        "cutover_ms": 1234,
        "runtime": {"binary_sha256": exec_sha(op), "build_info": "sim"},
    }
    if p.get("plan_id") and "evidence" not in override:
        evr, _ = eval_evidence(a, op, eval_verdict)
        evidence["eval"] = {
            "verdict": eval_verdict,
            "plan_digest": p.get("plan_digest"),
            "eval_result_id": evr,
        }
        evidence["probation"] = probation_evidence(a, op)
    body = {
        "status": "succeeded", "grant_id": g["grant_id"], "grant_consumed_seq": max(a.seq, 1), "trace_id": "tr-" + op["id"],
        "result": {"active_release_id": p["target_release_id"], "release_digest": p["release_digest"], "generation": op["generation"], "recovery_release_id": p["expected_active_release_id"]},
        "evidence": evidence,
    }  # fmt: skip
    body.update(override)
    return a.client.post(f"/api/agent/v1/operations/{op['id']}/outcome", json=body)


def full_deploy(
    a: FakeAgent, admin, release_id: str, plan_id: str | None = None, *, bootstrap=False, active=None
) -> dict[str, Any]:
    """Drive a deploy end to end from the device side: heartbeat -> op delivered -> grant -> success -> heartbeat."""
    body: dict[str, Any] = {"release_id": release_id, "plan_id": plan_id}
    if bootstrap:
        body["bootstrap"] = True
    r = admin.post(f"/api/v1/devices/{a.device_id}/deploy", json=body, headers=WEB)
    assert r.status_code == 201, r.text
    op = r.json()
    rep = heartbeat(a, active=active, stage="active" if active else "idle", generation=op["generation"] - 1)
    assert any(o["id"] == op["id"] for o in rep["operations"])
    g = grant(
        a, op, active=active, recovery=None if active is None else op["payload"].get("recovery_release_id")
    )
    assert g.status_code == 200, g.text
    o = deploy_success(a, op, g.json())
    assert o.status_code == 200, o.text
    heartbeat(a, active=release_id, recovery=active, stage="active", generation=op["generation"])
    return op
