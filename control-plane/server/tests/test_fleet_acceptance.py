"""Fleet acceptance harness: TEN real agents (simulated hardware/runtime) against the REAL server over HTTP.

Run independently with (opt-in; about five minutes)
    CONVOY_FLEET_ACCEPTANCE=1 uv run --package convoy-server pytest server/tests/test_fleet_acceptance.py -q -p no:cacheprovider
Optional sustained phase (all devices under continuous traffic, then every lane must drain):
    CONVOY_FLEET_SUSTAIN_S=1800 CONVOY_FLEET_MIN_ATTEMPTS=1000 CONVOY_FLEET_MIN_FAILURES=100 (defaults 0 / 0 / 0)

What it proves (all simulated, orchestration only; no hardware claim):
1. ten devices enroll, report live, and take a baseline deploy each (grant -> cutover -> eval -> probation);
2. usage arithmetic: the harness drives a known number of accepted and rejected requests through every
   device's loopback gateway; request counts are the harness's own tally, token counts are the runtime's
   usage fields echoed by the gateway (downstream accounting consistency, not an independent tokenizer);
   the server totals must equal that tally to the request and token, never an estimate;
3. a duplicate ACK (replayed batch) recounts nothing; a server outage (stop + restart on the same port)
   loses nothing and double-counts nothing once the agents reconnect;
4. a ten-target canary rollout with two canaries: explicit promotion, serial expansion (max_in_flight 1)
   proven from operation timestamps, every device ends on the candidate release;
5. scheduled work dispatched by the fenced worker: a health occurrence over all ten devices completes,
   and a deploy occurrence pinned to the already-current release is recorded as no_change for all ten."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from conftest import WEB, login
from convoy_agent.agent import Agent, enroll
from convoy_server.db import session_scope, write_txn
from convoy_server.ids import utcnow
from fastapi.testclient import TestClient
from helpers import seed

N = 10
PROMPTS = ["Say hello.", "Name a colour.", "Count to three.", "What is 2+2?", "Describe rain in five words."]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(fn, timeout=120, every=0.25, what="condition"):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise AssertionError(f"timeout waiting for {what}")


def _serve(app, port: int):
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


def _terminal(admin, op_id, timeout=150):
    return _wait(
        lambda: (lambda o: o if o["status"] in ("succeeded", "failed", "cancelled") else None)(
            admin.get(f"/api/v1/operations/{op_id}").json()
        ),
        timeout,
        what=f"operation {op_id} terminal",
    )


def _gateway_call(port: int, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _usage(admin):
    return admin.get("/api/v1/usage").json()


def _local_lanes(a: Agent) -> dict[str, dict[str, int]]:
    """The agent's durable lane frontiers: committed (ACKed by the server) and next_seq (allocated)."""
    j = a.journal
    with j._lock:
        rows = j.conn.execute("SELECT lane, committed_seq, next_seq FROM lane_cursor").fetchall()
        pending = {
            r[0]: r[1] for r in j.conn.execute("SELECT lane, COUNT(*) FROM lane GROUP BY lane").fetchall()
        }
    return {
        r[0]: {"committed": int(r[1]), "next_seq": int(r[2]), "pending": int(pending.get(r[0], 0))}
        for r in rows
    }


def _converged(admin, agents, ids) -> bool:
    """Every lane on every device: nothing pending locally and the server cursor equals the local
    committed frontier (the definition of 'all lanes drained')."""
    lanes = {(c["device_id"], c["lane"]): c["committed_seq"] for c in _usage(admin)["coverage"]["lanes"]}
    for a, did in zip(agents, ids, strict=True):
        for lane, st in _local_lanes(a).items():
            if st["pending"] or st["next_seq"] - 1 != st["committed"]:
                return False
            if lanes.get((did, lane), 0) != st["committed"]:
                return False
    return True


class Pump:
    """Continuous production traffic to every device gateway (probation needs served requests). Every
    attempt is tallied per device so the accounting stays exact; errors during cutover are expected."""

    def __init__(self, agents, ids, oracle, period_s=0.5):
        self.agents, self.ids, self.oracle, self.period_s = agents, ids, oracle, period_s
        self.stop = threading.Event()
        self.latencies: list[float] = []
        self.attempts = 0
        self.failures = 0
        self.by_status: dict[int, int] = {}  # every gateway-answered attempt by HTTP status
        self.deliberate_invalid = 0  # attempts the pump sent invalid on purpose (max_tokens=0 -> 400)
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _one(self, a, did, k):
        bad = k % 7 == 6  # roughly one rejected attempt in seven, so failures are exercised continuously
        body = {
            "messages": [{"role": "user", "content": PROMPTS[k % len(PROMPTS)]}],
            "max_tokens": 0 if bad else 8,
        }
        t0 = time.monotonic()
        try:
            code, resp = _gateway_call(a.gw.port, body)
        except Exception:
            return  # listener closed during cutover: not an attempt the gateway counted
        lat = (time.monotonic() - t0) * 1000
        with self.lock:
            self.by_status[code] = self.by_status.get(code, 0) + 1
            if bad:
                self.deliberate_invalid += 1
            if code == 200:
                self.oracle[did]["requests"] += 1
                self.oracle[did]["served"] += 1
                self.oracle[did]["tokens_in"] += int(resp["usage"]["prompt_tokens"])
                self.oracle[did]["tokens_out"] += int(resp["usage"]["completion_tokens"])
                self.latencies.append(lat)
                self.attempts += 1
            elif code == 400:
                self.oracle[did]["requests"] += 1
                self.oracle[did]["rejected"] += 1
                self.attempts += 1
                self.failures += 1
            elif code in (503, 504):  # closed/eval/overloaded: the gateway counts these as requests too
                self.oracle[did]["requests"] += 1
                self.attempts += 1
                self.failures += 1

    def _run(self):
        k = 0
        while not self.stop.is_set():
            for a, did in zip(self.agents, self.ids, strict=True):
                if self.stop.is_set():
                    return  # stop is honoured between requests, not only between sweeps
                self._one(a, did, k)
                k += 1
            self.stop.wait(self.period_s)

    def start(self):
        self.thread.start()
        return self

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=60)
        assert not self.thread.is_alive(), "traffic pump did not stop"

    def percentiles(self):
        xs = sorted(self.latencies)
        if not xs:
            return {}
        return {p: xs[min(len(xs) - 1, int(len(xs) * p / 100))] for p in (50, 95, 99)}


def _device_metrics(admin, device_id) -> dict:
    return next((d["metrics"] for d in _usage(admin)["devices"] if d["device_id"] == device_id), {})


@pytest.mark.skipif(
    os.environ.get("CONVOY_FLEET_ACCEPTANCE") != "1",
    reason="opt-in (CONVOY_FLEET_ACCEPTANCE=1): ~5 min, ten in-process agents; canary qualification under ten "
    "concurrent simulated agents currently times out and is being diagnosed (see docs/STATUS.md)",
)
@pytest.mark.timeout(900 + int(os.environ.get("CONVOY_FLEET_SUSTAIN_S", "0")) + 600)  # setup + phases + drain
def test_fleet_acceptance_ten_devices(settings, tmp_path: Path):
    port = _free_port()
    settings.public_url = f"http://127.0.0.1:{port}"
    settings.heartbeat_interval_s = 1
    from convoy_server.app import create_app

    app = create_app(settings, start_scheduler=False)
    server, th = _serve(app, port)
    admin = login(TestClient(app))
    s = seed(admin)
    agents: list[Agent] = []
    threads: list[threading.Thread] = []
    ids: list[str] = []
    pump: Pump | None = None
    try:
        # ------------------------------------------------------------------ 1. enrol + baseline deploy
        for i in range(1, N + 1):
            tok = admin.post(
                "/api/v1/enrollments", json={"label": f"fleet-{i}", "simulated": True}, headers=WEB
            ).json()["token"]
            d = tmp_path / f"fleet-{i}"
            res = enroll(d, server=settings.public_url, token=tok, name=f"fleet-{i}", simulate=True, seed=i)
            a = Agent(d, robot_sim=False)  # the harness owns ALL production traffic for an exact oracle
            t = threading.Thread(target=a.run, daemon=True, name=f"agent-{i}")
            t.start()
            agents.append(a)
            threads.append(t)
            ids.append(res["device_id"])
        for did in ids:
            _wait(
                lambda did=did: admin.get(f"/api/v1/devices/{did}").json()["status"] == "online",
                60,
                what="online",
            )
        ops = [
            admin.post(
                f"/api/v1/devices/{d}/deploy",
                json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
                headers=WEB,
            ).json()
            for d in ids
        ]
        assert all(o.get("status") == "pending" for o in ops), ops
        for o in ops:
            done = _terminal(admin, o["id"])
            assert done["status"] == "succeeded", (
                done["id"],
                done["status"],
                (done.get("outcome") or {}).get("failure"),
            )
            assert done["outcome"]["evidence"]["probation"]["elapsed_s"] >= 2
        for did in ids:
            dev = _wait(
                lambda did=did: (
                    lambda d: (
                        d
                        if d["observed_active_release_id"] == s["release_id"]
                        and d["observed_stage"] == "active"
                        else None
                    )
                )(admin.get(f"/api/v1/devices/{did}").json()),
                60,
                what="baseline active",
            )
            assert dev["observed_generation"] == 1 and dev["active_operation_id"] is None
        for a in agents:
            _wait(lambda a=a: a.gw.mode == "production", 30, what="gateway production mode")
        # ------------------------------------------------------------------ 2. exact usage oracle
        # the qualification eval itself went through the gateway: exactly warmup(1) + 8 cases, all served
        oracle = {}
        for a, did in zip(agents, ids, strict=True):
            st = dict(a.gw.stats)
            assert (st["requests"], st["served"], st["rejected"]) == (9, 9, 0), (did, st)
            oracle[did] = {
                "requests": 9, "served": 9, "rejected": 0,
                "tokens_in": st["served_tokens_in"], "tokens_out": st["served_tokens_out"],
            }  # fmt: skip
        for i, (a, did) in enumerate(zip(agents, ids, strict=True)):
            for k in range(i + 1):  # 1..10 accepted requests per device: 55 in total
                code, body = _gateway_call(
                    a.gw.port,
                    {"messages": [{"role": "user", "content": PROMPTS[k % len(PROMPTS)]}], "max_tokens": 8},
                )
                assert code == 200, (did, code, body)
                u = body["usage"]
                oracle[did]["requests"] += 1
                oracle[did]["served"] += 1
                oracle[did]["tokens_in"] += int(u["prompt_tokens"])
                oracle[did]["tokens_out"] += int(u["completion_tokens"])
            for _ in range(2):  # two rejected attempts per device: counted as attempts, never as tokens
                code, _ = _gateway_call(
                    a.gw.port, {"messages": [{"role": "user", "content": "x"}], "max_tokens": 0}
                )
                assert code == 400
                oracle[did]["requests"] += 1
                oracle[did]["rejected"] += 1
        for a, did in zip(agents, ids, strict=True):
            st = a.gw.stats
            assert (st["requests"], st["served"], st["rejected"]) == (
                oracle[did]["requests"],
                oracle[did]["served"],
                oracle[did]["rejected"],
            ), (did, st, oracle[did])
            assert (st["served_tokens_in"], st["served_tokens_out"]) == (
                oracle[did]["tokens_in"],
                oracle[did]["tokens_out"],
            )
            a._record_usage(force=True)  # close the counting interval now instead of at the 60 s mark
        total = {k: sum(o[k] for o in oracle.values()) for k in ("requests", "tokens_in", "tokens_out")}
        assert total["requests"] == 9 * N + 55 + 2 * N

        def settled():
            u = _usage(admin)["totals"]
            return u if u.get("inference_requests") == total["requests"] else None

        u = _wait(settled, 90, what="usage totals to equal the oracle")
        assert u["tokens_in"] == total["tokens_in"] and u["tokens_out"] == total["tokens_out"], (u, total)
        for did in ids:
            m = _device_metrics(admin, did)
            assert (m["inference_requests"], m["tokens_in"], m["tokens_out"]) == (
                oracle[did]["requests"],
                oracle[did]["tokens_in"],
                oracle[did]["tokens_out"],
            ), (did, m, oracle[did])
        # ------------------------------------------------------------------ 3. duplicate ACK + reconnect
        lanes = {c["device_id"]: c for c in _usage(admin)["coverage"]["lanes"] if c["lane"] == "usage"}
        victim, vdid = agents[0], ids[0]
        committed = lanes[vdid]["committed_seq"]
        assert committed >= 1
        # a replayed record at an already-committed sequence (lost ACK) is ignored, never recounted
        replay = victim.client.post(
            "/api/agent/v1/spool",
            {
                "lane": "usage",
                "records": [
                    {
                        "seq": committed,
                        "kind": "usage",
                        "body": {"ts": time.time(), "inference_requests": 999, "tokens_out": 999},
                    }
                ],
            },
        )
        assert replay["committed_seq"] == committed and replay["accepted"] == 0
        assert _device_metrics(admin, vdid)["inference_requests"] == oracle[vdid]["requests"]
        # the control plane goes away; devices keep serving; nothing is lost when it returns on the same port
        server.should_exit = True
        th.join(timeout=10)
        assert not th.is_alive()  # the listener thread has really exited...
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()  # ...and the port refuses
        offline = {}
        for a, did in list(zip(agents, ids, strict=True))[:3]:
            extra = 0
            for k in range(3):
                code, body = _gateway_call(
                    a.gw.port, {"messages": [{"role": "user", "content": PROMPTS[k]}], "max_tokens": 8}
                )
                assert code == 200
                oracle[did]["requests"] += 1
                oracle[did]["tokens_in"] += int(body["usage"]["prompt_tokens"])
                oracle[did]["tokens_out"] += int(body["usage"]["completion_tokens"])
                extra += 1
            a._record_usage(force=True)  # spooled locally; the flush fails while the server is down
            offline[did] = extra
        time.sleep(2.0)  # let at least one failed flush attempt happen while offline
        for a in agents[:3]:
            assert _local_lanes(a)["usage"]["pending"] >= 1  # still held locally: the flush really failed
        server, th = _serve(app, port)
        total = {k: sum(o[k] for o in oracle.values()) for k in ("requests", "tokens_in", "tokens_out")}
        u = _wait(settled, 120, what="usage totals after reconnect")
        assert u["tokens_in"] == total["tokens_in"] and u["tokens_out"] == total["tokens_out"], (u, total)
        for did in ids[:3]:
            assert _device_metrics(admin, did)["inference_requests"] == oracle[did]["requests"]
        assert not _usage(admin)["coverage"]["loss_ranges"], "no loss may be declared: nothing was evicted"
        # ------------------------------------------------------------------ 3b. optional sustained phase
        sustain_s = int(os.environ.get("CONVOY_FLEET_SUSTAIN_S", "0"))
        pump = Pump(agents, ids, oracle).start()
        if sustain_s > 0:
            t_end = time.monotonic() + sustain_s
            while time.monotonic() < t_end:
                time.sleep(5)
            pump.finish()
            for a in agents:
                a._record_usage(force=True)
            total = {k: sum(o[k] for o in oracle.values()) for k in ("requests", "tokens_in", "tokens_out")}
            u = _wait(settled, 300, what="sustained usage totals to equal the tally")
            assert u["tokens_in"] == total["tokens_in"] and u["tokens_out"] == total["tokens_out"], (u, total)
            _wait(lambda: _converged(admin, agents, ids), 300, 1.0, "all lanes drained within five minutes")
            report = {
                "duration_s": sustain_s,
                "attempts": pump.attempts,
                "failures": pump.failures,  # deliberate 400 rejections + unavailable/overloaded answers
                "by_status": {str(k): v for k, v in sorted(pump.by_status.items())},
                "deliberate_invalid_attempts": pump.deliberate_invalid,  # validation failures the pump injected
                "availability_failures": sum(v for k, v in pump.by_status.items() if k in (503, 504)),
                "latency_ms_successful_requests_only": pump.percentiles(),
            }
            (tmp_path / "sustained-report.json").write_text(json.dumps(report, indent=2))
            print("sustained phase:", json.dumps(report))
            assert pump.attempts >= int(os.environ.get("CONVOY_FLEET_MIN_ATTEMPTS", "0")), report
            assert pump.failures >= int(os.environ.get("CONVOY_FLEET_MIN_FAILURES", "0")), report
            pump = Pump(agents, ids, oracle).start()  # production traffic continues for probation below
        # ------------------------------------------------------------------ 4. ten-target serial rollout
        ro = admin.post(
            "/api/v1/rollouts",
            json={
                "name": "fleet candidate",
                "plan_id": s["candidate_plan_id"],
                "target": {"device_ids": ids},
                "canary_device_ids": ids[:2],
            },
            headers=WEB,
        ).json()
        assert ro["status"] == "draft" and ro["previous"] == {d: s["release_id"] for d in ids}
        ro = admin.post(f"/api/v1/rollouts/{ro['id']}/start", headers=WEB).json()
        assert ro["status"] == "canary" and all(
            ro["device_states"][d]["status"] == "deploying" for d in ids[:2]
        )
        assert all(ro["device_states"][d]["status"] == "queued" for d in ids[2:])

        def diag():
            x = admin.get(f"/api/v1/rollouts/{ro['id']}").json()
            ops_ = admin.get(f"/api/v1/operations?rollout_id={ro['id']}").json()
            devs_ = [admin.get(f"/api/v1/devices/{d}").json() for d in ids[:2]]
            return {
                "status": x["status"],
                "states": {d: x["device_states"][d] for d in ids[:2]},
                "ops": [
                    (o["id"], o["status"], o["progress"], (o.get("outcome") or {}).get("failure"))
                    for o in ops_
                ],
                "devices": [
                    (
                        d["id"],
                        d["observed_stage"],
                        d["observed_health"],
                        d["observed_generation"],
                        d["live_at"],
                    )
                    for d in devs_
                ],
            }

        try:
            r = _wait(
                lambda: (
                    lambda x: x if x["status"] in ("awaiting_promotion", "failed", "completed") else None
                )(admin.get(f"/api/v1/rollouts/{ro['id']}").json()),
                180,
                0.5,
                "canaries to qualify",
            )
        except AssertionError as e:
            raise AssertionError(f"canaries did not qualify: {diag()}") from e
        assert r["status"] == "awaiting_promotion", diag()
        assert all(
            r["device_states"][d]["status"] == "queued" for d in ids[2:]
        )  # nothing expanded before promotion
        r = admin.post(f"/api/v1/rollouts/{ro['id']}/promote", headers=WEB).json()
        assert r["status"] == "expanding" and r["device_states"][ids[2]]["status"] == "deploying"
        assert all(r["device_states"][d]["status"] == "queued" for d in ids[3:])
        r = _wait(
            lambda: (lambda x: x if x["status"] in ("completed", "failed") else None)(
                admin.get(f"/api/v1/rollouts/{ro['id']}").json()
            ),
            420,
            1.0,
            "serial expansion to complete",
        )
        assert r["status"] == "completed", r
        assert all(r["device_states"][d]["status"] == "passed" for d in ids)
        exp = [
            admin.get(f"/api/v1/operations/{r['device_states'][d]['operation_id']}").json() for d in ids[2:]
        ]
        for prev, nxt in zip(exp, exp[1:], strict=False):
            assert prev["terminal_at"] <= nxt["created_at"], (
                prev["id"],
                prev["terminal_at"],
                nxt["id"],
                nxt["created_at"],
            )
        for did in ids:
            _wait(
                lambda did=did: (
                    admin.get(f"/api/v1/devices/{did}").json()["observed_active_release_id"]
                    == s["candidate_release_id"]
                ),
                60,
                what="candidate active",
            )
        # ------------------------------------------------------------------ 5. scheduled work (fenced worker)
        from convoy_server.models import Schedule
        from convoy_server.services.scheduler import Worker
        from convoy_server.services.worker import run_tick

        health = admin.post(
            "/api/v1/schedules",
            json={
                "name": "fleet health",
                "kind": "health",
                "cron": "0 * * * *",
                "timezone": "UTC",
                "target": {"device_ids": ids},
            },
            headers=WEB,
        ).json()
        nochange = admin.post(
            "/api/v1/schedules",
            json={
                "name": "keep current",
                "kind": "deploy",
                "cron": "0 * * * *",
                "timezone": "UTC",
                "target": {"device_ids": ids},
                "payload": {"plan_id": s["candidate_plan_id"]},
            },
            headers=WEB,
        ).json()
        with session_scope() as db:
            with write_txn(db):
                for sid in (health["id"], nochange["id"]):
                    row = db.get(Schedule, sid)
                    row.next_run_at = utcnow()
                    row.next_civil = "2030-01-01T00:00"
        w = Worker("fleet-worker")
        out = run_tick(w)
        assert out["leader"] is True and len(out["fired"]) == 2, out
        hocc = admin.get(f"/api/v1/schedules/{health['id']}/occurrences").json()[0]
        hops = [d["operation_id"] for d in hocc["results"]["devices"] if "operation_id" in d]
        assert len(hops) == N and hocc["status"] == "dispatched"
        for op_id in hops:
            assert _terminal(admin, op_id, 120)["status"] == "succeeded"
        _wait(
            lambda: (
                run_tick(w)["occurrences"] >= 1
                or admin.get(f"/api/v1/schedules/{health['id']}/occurrences").json()[0]["status"]
                == "completed"
            ),
            30,
            what="occurrence finalized",
        )
        hocc = admin.get(f"/api/v1/schedules/{health['id']}/occurrences").json()[0]
        assert hocc["status"] == "completed" and len(hocc["results"]["operations"]) == N
        docc = admin.get(f"/api/v1/schedules/{nochange['id']}/occurrences").json()[0]
        assert docc["status"] == "completed" and all(
            d.get("skipped") == "no_change" for d in docc["results"]["devices"]
        )
        assert len(docc["results"]["devices"]) == N
        # every device is still online, unreserved and on the candidate at the end
        for did in ids:
            d = admin.get(f"/api/v1/devices/{did}").json()
            assert (
                d["status"] == "online"
                and d["active_operation_id"] is None
                and d["observed_active_release_id"] == s["candidate_release_id"]
            )
        # ------------------------------------------------------------------ 6. exact accounting + convergence
        pump.finish()
        for a in agents:
            a._record_usage(force=True)
        # request counts are fully independent: the harness tally plus exactly one qualification eval per
        # device for the rollout (warmup 1 + 8 cases); token counts are the runtime's usage fields, so the
        # server must agree with the gateway counters to the token (downstream consistency)
        expected_requests = sum(o["requests"] for o in oracle.values()) + 9 * N
        gw = {
            k: sum(a.gw.stats[k] for a in agents)
            for k in ("requests", "served_tokens_in", "served_tokens_out")
        }
        assert gw["requests"] == expected_requests, (gw, expected_requests)
        u = _wait(
            lambda: (lambda t: t if t.get("inference_requests") == expected_requests else None)(
                _usage(admin)["totals"]
            ),
            180,
            what="final usage totals to equal the independent request tally",
        )
        assert (u["tokens_in"], u["tokens_out"]) == (gw["served_tokens_in"], gw["served_tokens_out"]), (u, gw)
        _wait(lambda: _converged(admin, agents, ids), 300, 1.0, "all three lanes drained on all ten devices")
        assert not _usage(admin)["coverage"]["loss_ranges"]
    finally:
        if pump is not None and pump.thread.is_alive():
            pump.stop.set()
            pump.thread.join(timeout=60)
        for a in agents:
            a.stop.set()
        for t in threads:
            t.join(timeout=30)
        server.should_exit = True
        th.join(timeout=10)
