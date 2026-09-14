"""End-to-end: the REAL agent (simulated hardware/runtime) against the REAL server over HTTP.

Covers: enrollment via CLI path, live reports, deploy with grant -> cutover -> eval -> probation ->
success, evidence upload (eval result re-scored server-side, spans, usage), a candidate whose eval gate
fails -> rollback to the retained release + failed-generation latch, restart recovery of an interrupted
cutover, and a lost-outcome retry."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from conftest import WEB, login
from convoy_agent.agent import Agent, enroll
from fastapi.testclient import TestClient
from helpers import seed


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_server(settings):
    port = _free_port()
    settings.public_url = f"http://127.0.0.1:{port}"
    settings.heartbeat_interval_s = 1
    from convoy_server.app import create_app

    app = create_app(settings, start_scheduler=False)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    yield app, f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(timeout=5)


def _wait(fn, timeout=60, every=0.2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise AssertionError("timeout waiting")


def _run_agent(a: Agent):
    th = threading.Thread(target=a.run, daemon=True)
    th.start()
    return th


@pytest.mark.timeout(180)
def test_full_deploy_eval_rollback_and_restart(live_server, tmp_path: Path):
    app, base = live_server
    admin = login(TestClient(app))
    s = seed(admin)
    tok = admin.post("/api/v1/enrollments", json={"label": "e2e", "simulated": True}, headers=WEB).json()[
        "token"
    ]
    d = tmp_path / "agent"
    res = enroll(d, server=base, token=tok, name="e2e-1", simulate=True, seed=3)
    dev = res["device_id"]
    # lost-response replay: the agent persisted its identity before the claim; if the response was lost
    # (device_id never recorded) the retry with the same request id + secret hash returns the same device
    cfg = json.loads((d / "agent.json").read_text())
    lost = dict(cfg)
    lost.pop("device_id")
    (d / "agent.json").write_text(json.dumps(lost))
    assert enroll(d, server=base, token=tok, name="e2e-1", simulate=True, seed=3)["device_id"] == dev
    cfg = json.loads((d / "agent.json").read_text())
    assert cfg["device_id"] == dev and (d / "credential").stat().st_mode & 0o777 == 0o600
    agent = Agent(d, robot_sim=True)
    th = _run_agent(agent)
    try:
        _wait(lambda: admin.get(f"/api/v1/devices/{dev}").json()["status"] == "online")
        dj = admin.get(f"/api/v1/devices/{dev}").json()
        assert (
            dj["live_seq" if "live_seq" in dj else "last_report_seq"] >= 1
            and dj["last_telemetry"]["mem_available_mb"]
            and dj["hardware"]["simulated"] is True
        )
        # bootstrap deploy with the plan
        op = admin.post(
            f"/api/v1/devices/{dev}/deploy",
            json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
            headers=WEB,
        ).json()
        final = _wait(
            lambda: (lambda o: o if o["status"] in ("succeeded", "failed") else None)(
                admin.get(f"/api/v1/operations/{op['id']}").json()
            ),
            timeout=120,
        )
        assert final["status"] == "succeeded", final
        ev = final["outcome"]["evidence"]
        assert (
            ev["health"] == "ok"
            and ev["cutover_ms"] >= 0
            and ev["eval"]["verdict"] == "passed"
            and ev["probation"]["elapsed_s"] >= 2
        )
        assert (
            ev["runtime"]["simulated"] is True and ev["runtime"]["intended_backend_ok"] is None
        )  # no GPU claims from the simulator
        dj = _wait(
            lambda: (
                lambda x: (
                    x
                    if x["observed_active_release_id"] == s["release_id"]
                    and x["active_operation_id"] is None
                    and x["observed_stage"] == "active"
                    else None
                )
            )(admin.get(f"/api/v1/devices/{dev}").json())
        )
        assert (
            dj["observed_health"] == "ok"
            and dj["observed_stage"] == "active"
            and dj["expected_active_release_id"] == s["release_id"]
        )
        # evidence arrived through the spool and was re-scored by the server
        evals = _wait(lambda: admin.get(f"/api/v1/evals?device_id={dev}").json() or None)
        e = admin.get(f"/api/v1/evals/{evals[0]['id']}").json()
        assert (
            e["server_verdict"] == "passed"
            and e["device_verdict"] == "passed"
            and e["simulated"] is True
            and e["plan_id"] == s["plan_id"]
        )
        assert (
            all(c["server_method"] in ("independently_rescored", "structured_rescored") for c in e["cases"])
            and len(e["cases"]) == 8
        )
        assert (
            e["summary"]["latency"]["p95_ms"] is not None
            and e["summary"]["thermal"]["max_c"] is not None
            and e["summary"]["memory"]["peak_used_mb"] is not None
        )
        assert ev["eval"]["eval_result_id"] == e["id"]
        spans = _wait(
            lambda: (
                [x for x in admin.get(f"/api/v1/devices/{dev}/spans").json() if x["status"] == "ok"] or None
            )
        )
        assert (
            spans[0]["kind"] == "inference"
            and spans[0]["attrs"]["tokens_out"] >= 1
            and spans[0]["attrs"]["ttft_ms"] is not None
        )
        # robot sim traffic reached the gateway in production mode
        _wait(lambda: agent.gw.stats["requests"] > 8 and agent.gw.mode == "production", timeout=30)
        # candidate whose eval gate fails (wrong answers) -> rollback + latch
        bad = admin.post(
            "/api/v1/releases",
            json={
                "name": "sim-bad",
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
                "config": {"sim": {"wrong_every": 2}},
            },
            headers=WEB,
        )
        assert bad.status_code == 201, bad.text
        bad_plan = admin.post(
            "/api/v1/plans",
            json={
                "name": "bad plan",
                "release_id": bad.json()["id"],
                "sample_policy": {
                    "probation_min_s": 1,
                    "probation_min_requests": 0,
                    "fresh_eval_max_age_s": 3600,
                },
            },
            headers=WEB,
        ).json()
        op2 = admin.post(
            f"/api/v1/devices/{dev}/deploy",
            json={"release_id": bad.json()["id"], "plan_id": bad_plan["id"]},
            headers=WEB,
        ).json()
        assert (
            op2["expected_active_release_id"] == s["release_id"] and op2["generation"] == op["generation"] + 1
        )
        final2 = _wait(
            lambda: (lambda o: o if o["status"] in ("succeeded", "failed") else None)(
                admin.get(f"/api/v1/operations/{op2['id']}").json()
            ),
            timeout=120,
        )
        assert final2["status"] == "failed" and final2["outcome"]["failure"]["code"] == "EVAL_FAILED"
        assert final2["outcome"]["failure"]["details"]["recovery"]["recovered"] is True
        dj = _wait(
            lambda: (
                lambda x: (
                    x
                    if x["observed_active_release_id"] == s["release_id"]
                    and x["observed_latch_generation"] == op2["generation"]
                    and x["observed_stage"] == "active"
                    else None
                )
            )(admin.get(f"/api/v1/devices/{dev}").json())
        )
        assert dj["observed_health"] == "ok" and dj["active_operation_id"] is None
        f = admin.get(f"/api/v1/devices/{dev}/failures").json()
        assert any(x["code"] == "EVAL_FAILED" for x in f)
        failed_eval = [
            x for x in admin.get(f"/api/v1/evals?device_id={dev}").json() if x["server_verdict"] == "failed"
        ]
        assert failed_eval and failed_eval[0]["release_id"] == bad.json()["id"]
        # gateway kept serving the restored release
        assert agent.journal.get("active_release_id") == s["release_id"] and agent.gw.mode == "production"
    finally:
        agent.stop.set()
        th.join(timeout=30)
    # ---- restart recovery: simulate a crash mid-cutover by writing the journal state, then start again ----
    from convoy_agent.journal import Journal

    j = Journal(d / "journal.db")
    j.begin_operation(
        {"id": "op_crash", "type": "deploy", "payload": {"target_release_id": "rel_x"}, "generation": 99}
    )
    j.set_stage("op_crash", "Cutover", grant={"grant_id": "g"}, grant_consumed_seq=1, started_monotonic=0.0)
    j.set_many({"active_release_id": None, "recovery_release_id": s["release_id"]})
    j.close()
    agent2 = Agent(d, robot_sim=False)
    th2 = _run_agent(agent2)
    try:
        _wait(
            lambda: (
                agent2.journal.get("active_release_id") == s["release_id"] and agent2.sup.state() == "running"
            ),
            timeout=60,
        )
        note = agent2._restart_note
        assert note and note["action"] == "recovered" and note["result"]["recovered"] is True
        assert agent2.journal.get("failed_generation_latch") == 99
        _wait(
            lambda: any(
                x["code"] == "INTERRUPTED" for x in admin.get(f"/api/v1/devices/{dev}/failures").json()
            ),
            timeout=30,
        )
        usage = _wait(
            lambda: (lambda u: u if u["totals"] else None)(admin.get("/api/v1/usage").json()), timeout=90
        )
        assert usage["totals"].get("inference_requests", 0) >= 1 and usage["devices"][0]["simulated"] is True
    finally:
        agent2.stop.set()
        th2.join(timeout=30)
