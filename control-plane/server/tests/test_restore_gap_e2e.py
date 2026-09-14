"""Restore-gap reconciliation, end to end with the REAL agent and the REAL server over HTTP.

Reproduces the reviewer's separate-volume rehearsal: backup B; later records on every lane are ACKed by
the server and therefore deleted on the device; the server is restored to B (its cursors fall behind the
device's durable ACK frontiers); the device is rebound with its SAME journal; a new eval/deploy runs.
Expected: the device declares exactly the unrecoverable pre-restore spans as visible loss, re-sends what
it still holds, every lane resumes exactly once, the new eval evidence ingests, the deploy settles, usage
totals equal the restored totals plus the post-restore traffic (nothing re-counted, nothing invented), and
repeated flushes neither double count nor extend the loss."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from conftest import WEB, enrollment_token, login
from convoy_agent.agent import Agent, enroll
from convoy_server.db import reset_engine, session_scope
from fastapi.testclient import TestClient
from helpers import seed


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(200):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return server, th
        except OSError:
            time.sleep(0.05)
    raise AssertionError("server did not start")


def _wait(fn, timeout=120, every=0.25, what="condition"):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise AssertionError(f"timeout waiting for {what}")


def _terminal(admin, op_id, timeout=150):
    return _wait(
        lambda: (lambda o: o if o["status"] in ("succeeded", "failed", "cancelled") else None)(
            admin.get(f"/api/v1/operations/{op_id}").json()
        ),
        timeout,
        what=f"operation {op_id} terminal",
    )


def _call(port, k) -> int | None:
    """One production request; returns the HTTP status (any answered status is a gateway-counted request)
    or None when the listener refused the connection (not counted anywhere)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps({"messages": [{"role": "user", "content": f"ping {k}"}], "max_tokens": 4}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return None


def _gw(port, n):
    for k in range(n):
        assert _call(port, k) == 200


def _lanes(admin, did):
    return {
        c["lane"]: c["committed_seq"]
        for c in admin.get("/api/v1/usage").json()["coverage"]["lanes"]
        if c["device_id"] == did
    }


def _local(a: Agent):
    j = a.journal
    with j._lock:
        rows = j.conn.execute("SELECT lane, committed_seq, next_seq FROM lane_cursor").fetchall()
        pend = dict(j.conn.execute("SELECT lane, COUNT(*) FROM lane GROUP BY lane").fetchall())
    return {
        r[0]: {"committed": int(r[1]), "next_seq": int(r[2]), "pending": int(pend.get(r[0], 0))} for r in rows
    }


def _losses(admin, did):
    return sorted(
        (x["lane"], x["from_seq"], x["to_seq"], x["reason"])
        for x in admin.get("/api/v1/usage").json()["coverage"]["loss_ranges"]
        if x["device_id"] == did
    )


@pytest.mark.timeout(420)
def test_restore_to_older_snapshot_reconciles_every_lane_exactly_once(settings, tmp_path: Path):
    from convoy_server.app import create_app
    from convoy_server.services.backup import restore_backup, take_backup
    from convoy_server.services.identity import recover_admin

    port = _free_port()
    settings.public_url = f"http://127.0.0.1:{port}"
    settings.heartbeat_interval_s = 1
    app = create_app(settings, start_scheduler=False)
    server, th = _serve(app, port)
    admin = login(TestClient(app))
    s = seed(admin)
    tok = admin.post("/api/v1/enrollments", json={"label": "gap", "simulated": True}, headers=WEB).json()[
        "token"
    ]
    d = tmp_path / "agent"
    res = enroll(d, server=settings.public_url, token=tok, name="gap-bot", simulate=True, seed=3)
    did = res["device_id"]
    a = Agent(d, robot_sim=False)
    ta = threading.Thread(target=a.run, daemon=True)
    ta.start()
    a2 = None
    ta2 = None
    try:
        _wait(lambda: admin.get(f"/api/v1/devices/{did}").json()["status"] == "online", 30, what="online")
        # ---- history BEFORE the backup: baseline deploy (critical seq 1), traffic (usage), telemetry ----
        op = admin.post(
            f"/api/v1/devices/{did}/deploy",
            json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
            headers=WEB,
        ).json()
        assert _terminal(admin, op["id"])["status"] == "succeeded"
        _wait(lambda: a.gw.mode == "production", 30, what="production")
        _gw(a.gw.port, 5)
        a._record_usage(force=True)
        _wait(
            lambda: _lanes(admin, did).get("usage", 0) >= 1 and _local(a)["usage"]["pending"] == 0,
            60,
            what="usage ACKed",
        )
        _wait(lambda: _lanes(admin, did).get("telemetry", 0) >= 2, 60, what="telemetry flowing")
        before_b = admin.get("/api/v1/usage").json()["totals"]
        with session_scope() as db:
            b = take_backup(db, str(tmp_path / "B.db"))
        lanes_b = _lanes(admin, did)
        # ---- history AFTER the backup: ACKed by this server, deleted on the device ----
        eop = admin.post(f"/api/v1/devices/{did}/eval", json={"plan_id": s["plan_id"]}, headers=WEB).json()
        assert _terminal(admin, eop["id"])["status"] == "succeeded"  # critical seq 2 ACKed
        _gw(a.gw.port, 7)
        a._record_usage(force=True)
        _wait(
            lambda: (
                _lanes(admin, did).get("usage", 0) > lanes_b["usage"]
                and _lanes(admin, did).get("critical", 0) > lanes_b["critical"]
                and _lanes(admin, did).get("telemetry", 0) > lanes_b["telemetry"] + 3
            ),
            60,
            what="post-backup ACKs",
        )
        _wait(lambda: all(v["pending"] == 0 for v in _local(a).values()), 30, what="device drained")
        # ---- outage: stop the agent first (its shutdown flush is ACKed by the still-running server) ----
        a.stop.set()
        ta.join(timeout=30)
        lanes_after = _lanes(admin, did)
        local_after = {k: {"committed": v} for k, v in _local_from_disk(d).items()}
        for lane in ("usage", "critical", "telemetry"):
            assert (
                local_after[lane]["committed"] == lanes_after[lane]
            )  # frontiers agree, records deleted locally
        server.should_exit = True
        th.join(timeout=10)
        reset_engine()
        assert restore_backup(b["path"], confirm=True)["ok"] is True
        with session_scope() as db:
            recover_admin(db, "admin@example.com", "post-restore-password")
        server, th = _serve(app, port)
        rec = login(TestClient(app), "admin@example.com", "post-restore-password")
        assert _lanes(rec, did) == lanes_b  # the server is back at B: behind the device on every lane
        rebind = enrollment_token(rec, rebind_device_id=did)
        res2 = enroll(d, server=settings.public_url, token=rebind, name="gap-bot", simulate=True, seed=3)
        assert res2["device_id"] == did
        assert _local_from_disk(d) == {k: v["committed"] for k, v in local_after.items()}  # journal preserved
        a2 = Agent(d, robot_sim=False)
        ta2 = threading.Thread(target=a2.run, daemon=True)
        ta2.start()
        _wait(
            lambda: rec.get(f"/api/v1/devices/{did}").json()["status"] == "online",
            60,
            what="online after rebind",
        )
        assert rec.post("/api/v1/admin/quarantine/lift", headers=WEB).status_code == 200
        # ---- the device declares EXACTLY the unrecoverable spans and the lanes resume ----
        expected = sorted(
            (lane, lanes_b[lane] + 1, local_after[lane]["committed"], "pre_restore_history_unrecoverable")
            for lane in ("usage", "critical", "telemetry")
            if local_after[lane]["committed"] > lanes_b[lane]
        )
        assert any(lane == "critical" for lane, *_ in expected) and any(
            lane == "usage" for lane, *_ in expected
        )
        # every lane, EMPTY ones included, is probed at first contact with the restored server, so all
        # three spans are declared eagerly (no new record, no second rebind needed)
        try:
            _wait(lambda: _losses(rec, did) == expected, 90, what=f"declared restore loss {expected}")
        except AssertionError as e:
            raise AssertionError(
                f"expected {expected}; got losses={_losses(rec, did)} server_lanes={_lanes(rec, did)} "
                f"local={_local(a2)} server_committed_seen={getattr(a2, '_server_committed', None)}"
            ) from e
        _wait(
            lambda: all(
                _lanes(rec, did).get(lane, 0) >= local_after[lane]["committed"]
                for lane in ("usage", "telemetry", "critical")
            ),
            90,
            what="server cursors caught up",
        )
        # ---- a new deploy: its eval evidence (critical seq 3) ingests once and the outcome settles ----
        _wait(
            lambda: rec.get(f"/api/v1/devices/{did}").json()["active_operation_id"] is None,
            60,
            what="unreserved",
        )
        # probation needs production traffic (candidate plan: probation_min_requests 1): pump requests
        pumped = {"answered": 0}  # every attempt the gateway answered (200 or 503) is one counted request
        pump_stop = threading.Event()

        def pump():
            k = 0
            while not pump_stop.is_set():
                if _call(a2.gw.port, k) is not None:
                    pumped["answered"] += 1
                k += 1
                pump_stop.wait(0.5)

        pump_thread = threading.Thread(target=pump, daemon=True)
        pump_thread.start()
        op2 = rec.post(
            f"/api/v1/devices/{did}/deploy",
            json={"release_id": s["candidate_release_id"], "plan_id": s["candidate_plan_id"]},
            headers=WEB,
        ).json()
        assert "id" in op2, op2
        try:
            done = _terminal(rec, op2["id"], 180)
        except AssertionError as e:
            o = rec.get(f"/api/v1/operations/{op2['id']}").json()
            raise AssertionError(
                f"deploy after restore did not settle: status={o['status']} progress={o.get('progress')} "
                f"outcome={o.get('outcome')} device={rec.get(f'/api/v1/devices/{did}').json()['observed_stage']} "
                f"local={_local(a2)} server_lanes={_lanes(rec, did)} losses={_losses(rec, did)} "
                f"pending_outcomes={getattr(a2, '_deferred', None)}"
            ) from e
        pump_stop.set()
        pump_thread.join(timeout=10)
        assert done["status"] == "succeeded", done
        evr = done["outcome"]["evidence"]["eval"]["eval_result_id"]
        assert rec.get(f"/api/v1/evals/{evr}").json()["operation_id"] == op2["id"]
        assert _losses(rec, did) == expected, _losses(rec, did)  # critical [2,2] declared with the new record
        assert _lanes(rec, did)["critical"] == 3
        # ---- accounting: restored totals + post-restore traffic only; convergence; idempotent replay ----
        _wait(lambda: a2.gw.mode == "production", 30, what="production after deploy")
        _gw(a2.gw.port, 3)
        a2._record_usage(force=True)

        def totals():
            return rec.get("/api/v1/usage").json()["totals"]

        _wait(
            lambda: totals().get("inference_requests", 0) >= before_b["inference_requests"] + 3,
            60,
            what="post-restore usage",
        )
        t = totals()
        # the 7 lost requests recorded between B and the restore are gone (declared, never re-counted);
        # the new deploy's eval requests are counted by the agent like any other served requests
        eval_requests = 9  # warmup 1 + 8 cases of the candidate plan's eval
        expected_total = before_b["inference_requests"] + eval_requests + pumped["answered"] + 3
        _wait(
            lambda: totals().get("inference_requests", 0) == expected_total,
            60,
            what=f"exact totals {expected_total}",
        )
        t = totals()
        assert t["inference_requests"] == expected_total, (t, before_b, pumped)
        _wait(lambda: all(v["pending"] == 0 for v in _local(a2).values()), 60, what="drained")
        assert {k: v["committed"] for k, v in _local(a2).items()} == {
            k: _lanes(rec, did)[k] for k in _local(a2)
        }
        for _ in range(3):
            a2.flush_spool(probe=True)
        assert _losses(rec, did) == expected  # never extended, never duplicated
        assert totals() == t
    finally:
        for ag, t_ in ((a, ta), (a2, ta2)):
            if ag is not None:
                ag.stop.set()
            if t_ is not None:
                t_.join(timeout=30)
        server.should_exit = True
        th.join(timeout=10)


def _local_from_disk(d: Path) -> dict[str, int]:
    import sqlite3

    con = sqlite3.connect(str(d / "journal.db"))
    try:
        return {r[0]: int(r[1]) for r in con.execute("SELECT lane, committed_seq FROM lane_cursor")}
    finally:
        con.close()
