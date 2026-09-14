"""Restore protocol follow-ups: bounded batches of ordinary records always drain, a single refused record
gets an explicit disposition, device-facing writes re-validate the admitted binding inside their own
transaction, declared losses carry the restoration context."""

from __future__ import annotations

import time

import pytest
from conftest import WEB, FakeAgent, enrollment_token, login
from fastapi.testclient import TestClient
from helpers import enrolled_agent, heartbeat, seed


def _telemetry(seq):
    return {"seq": seq, "kind": "telemetry", "body": {"ts": time.time(), "mem_total_mb": 7620.0, "mem_available_mb": 5000.0, "cpu_pct": 10.0, "gpu_pct": 0.0, "power_w": 6.0, "temp_max_c": 40.0, "disk_free_mb": 20000.0, "runtime_state": "running", "clock_confidence": "ntp"}}  # fmt: skip


def test_full_batch_of_ordinary_records_exceeding_the_global_key_cap_drains(app, admin):
    a = enrolled_agent(app, admin)
    records = [_telemetry(i) for i in range(1, 201)]
    spans = [
        {"seq": 200 + i, "kind": "span", "body": {"trace_id": f"tr{i}", "span_id": f"s{i}", "name": "infer", "kind": "server", "status": "ok", "start_ts": time.time(), "duration_ms": 12.5, "attrs": {f"k{j}": j for j in range(20)}}}
        for i in range(1, 201)
    ]  # fmt: skip
    keys = sum(len(r["body"]) + 3 for r in records + spans)
    assert keys > 2000  # the old global cap rejected this ordinary backlog as a whole
    r = a.client.post("/api/agent/v1/spool", json={"lane": "telemetry", "records": records})
    assert r.status_code == 200 and r.json()["committed_seq"] == 200 and r.json()["accepted"] == 200, r.text
    r = a.client.post("/api/agent/v1/spool", json={"lane": "telemetry", "records": spans})
    assert r.status_code == 200 and r.json()["committed_seq"] == 400 and r.json()["accepted"] == 200, r.text
    # per-record safeguards stay: one non-finite value still refuses the batch (the agent isolates it)
    bad = [_telemetry(401)]
    bad[0]["body"]["cpu_pct"] = 1e999
    r = a.client.post(
        "/api/agent/v1/spool",
        content=b'{"lane":"telemetry","records":[{"seq":401,"kind":"telemetry","body":{"cpu_pct":1e999}}]}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422


def test_agent_isolates_a_refused_record_and_the_lane_keeps_draining(app, admin, tmp_path, settings):
    """Real Agent journal + flush against the real server: a record the server refuses outright is
    bisected out, disposed of explicitly (local loss `rejected_by_server:…`), and the rest drains."""
    import socket
    import threading

    import uvicorn
    from convoy_agent.agent import Agent, enroll
    from convoy_server.app import create_app

    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        port = s_.getsockname()[1]
    settings.public_url = f"http://127.0.0.1:{port}"
    app2 = create_app(settings, start_scheduler=False)
    server = uvicorn.Server(uvicorn.Config(app2, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    try:
        tok = admin.post("/api/v1/enrollments", json={"label": "iso", "simulated": True}, headers=WEB).json()[
            "token"
        ]
        d = tmp_path / "iso"
        enroll(d, server=settings.public_url, token=tok, name="iso", simulate=True, seed=1)
        ag = Agent(d, robot_sim=False)
        for i in range(5):
            body = {"ts": time.time(), "mem_total_mb": 7620.0, "mem_available_mb": 5000.0}
            if i == 2:
                body["attrs"] = {f"k{j}": j for j in range(2500)}  # exceeds the per-record cap: refused
            ag.emit("telemetry", "telemetry", body)
        done = ag.flush_spool(lanes=("telemetry",), max_batches=8)
        assert done["telemetry"] is True
        with ag.journal._lock:
            pending = ag.journal.conn.execute("SELECT COUNT(*) FROM lane WHERE lane='telemetry'").fetchone()[
                0
            ]
            loss = ag.journal.conn.execute(
                "SELECT from_seq, to_seq, reason, reported FROM loss WHERE lane='telemetry'"
            ).fetchall()
        assert (
            pending == 0
            and [(x[0], x[1]) for x in loss] == [(3, 3)]
            and loss[0][2].startswith("rejected_by_server")
        )
        cov = admin.get("/api/v1/usage").json()["coverage"]
        lane = next(c for c in cov["lanes"] if c["device_id"] == ag.device_id and c["lane"] == "telemetry")
        assert lane["committed_seq"] == 5
        assert any(
            x["device_id"] == ag.device_id
            and (x["from_seq"], x["to_seq"]) == (3, 3)
            and x["reason"].startswith("rejected_by_server")
            for x in cov["loss_ranges"]
        )
        ag.journal.close() if hasattr(ag.journal, "close") else None
    finally:
        server.should_exit = True
        th.join(timeout=5)


def test_stale_binding_admitted_before_a_rebind_mutates_nothing(app, admin):
    """Session A is admitted with the old credential; a rebind commits a new credential/epoch; A's
    already-admitted request must then refuse to write (spool, report, grant)."""
    from convoy_server.auth import assert_admitted_binding
    from convoy_server.db import session_scope

    a = enrolled_agent(app, admin)
    heartbeat(a)
    assert (
        a.client.post(
            "/api/agent/v1/spool",
            json={
                "lane": "usage",
                "records": [
                    {"seq": 1, "kind": "usage", "body": {"ts": time.time(), "inference_requests": 1}}
                ],
            },
        ).json()["committed_seq"]
        == 1
    )
    # model the race: capture the admitted binding, rebind, then run the write-side re-validation
    from convoy_server.auth import StaleBinding
    from convoy_server.models import Device

    with session_scope() as db:
        old = db.get(Device, a.device_id)
        old.__dict__["_admitted"] = {
            "credential_hash": old.credential_hash,
            "binding_epoch": old.binding_epoch or 1,
        }
        b = FakeAgent(app, "same")
        assert b.enroll(enrollment_token(admin, rebind_device_id=a.device_id)).status_code == 200
        try:
            assert_admitted_binding(db, old)
            raise AssertionError("stale binding accepted")
        except StaleBinding:
            pass
    # over HTTP the old credential is simply refused, and the cursor/loss state is untouched
    r = a.client.post(
        "/api/agent/v1/spool",
        json={
            "lane": "usage",
            "records": [],
            "loss_ranges": [{"from_seq": 2, "to_seq": 500, "reason": "pre_restore_history_unrecoverable"}],
        },
    )
    assert r.status_code == 401
    cov = admin.get("/api/v1/usage").json()["coverage"]
    assert (
        next(c for c in cov["lanes"] if c["device_id"] == a.device_id and c["lane"] == "usage")[
            "committed_seq"
        ]
        == 1
    )
    assert not [x for x in cov["loss_ranges"] if x["device_id"] == a.device_id]
    # the rebound device carries the binding context on the losses it declares
    r = b.client.post(
        "/api/agent/v1/spool",
        json={
            "lane": "usage",
            "records": [],
            "loss_ranges": [{"from_seq": 2, "to_seq": 3, "reason": "pre_restore_history_unrecoverable"}],
        },
    )
    assert r.status_code == 200 and r.json()["committed_seq"] == 3
    lr = next(
        x
        for x in admin.get("/api/v1/usage").json()["coverage"]["loss_ranges"]
        if x["device_id"] == a.device_id
    )
    assert lr["context"]["binding_epoch"] == 2 and lr["context"]["credential_issued_at"]


def test_isolation_never_infers_acceptance_from_http_200(app, admin, tmp_path, settings):
    """Reviewer's exact case: old local ACK 2 (a pre-restore server acknowledged seq 2), restored server
    cursor 1, retained valid seq 3 and a bad seq 4. Isolating the bad record must never retire the
    valid retained record before the server actually committed it; the restore gap [2,2] is declared
    first, seq 3 is committed by the server, only seq 4 is disposed of."""
    import socket
    import threading

    import uvicorn
    from convoy_agent.agent import Agent, enroll
    from convoy_server.app import create_app

    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        port = s_.getsockname()[1]
    settings.public_url = f"http://127.0.0.1:{port}"
    app2 = create_app(settings, start_scheduler=False)
    server = uvicorn.Server(uvicorn.Config(app2, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    try:
        tok = admin.post(
            "/api/v1/enrollments", json={"label": "iso2", "simulated": True}, headers=WEB
        ).json()["token"]
        d = tmp_path / "iso2"
        enroll(d, server=settings.public_url, token=tok, name="iso2", simulate=True, seed=1)
        ag = Agent(d, robot_sim=False)
        ag.emit("telemetry", "telemetry", {"ts": time.time(), "mem_total_mb": 7620.0})  # seq 1
        assert ag.flush_spool(lanes=("telemetry",))["telemetry"] is True  # server cursor 1, local ACK 1
        # a pre-restore server had acknowledged seq 2 as well (deleted locally); this server is at 1
        with ag.journal.txn() as c:
            c.execute("UPDATE lane_cursor SET committed_seq=2, next_seq=3 WHERE lane='telemetry'")
        ag.emit(
            "telemetry", "telemetry", {"ts": time.time(), "mem_total_mb": 7620.0, "cpu_pct": 3.0}
        )  # seq 3 valid
        ag.emit(
            "telemetry", "telemetry", {"ts": time.time(), "attrs": {f"k{j}": j for j in range(2500)}}
        )  # seq 4 bad
        done = ag.flush_spool(lanes=("telemetry",), max_batches=8)
        assert done["telemetry"] is True
        with ag.journal._lock:
            pending = ag.journal.conn.execute(
                "SELECT seq FROM lane WHERE lane='telemetry' ORDER BY seq"
            ).fetchall()
            loss = ag.journal.conn.execute(
                "SELECT from_seq, to_seq, reason FROM loss WHERE lane='telemetry' ORDER BY from_seq"
            ).fetchall()
            cur = ag.journal.conn.execute(
                "SELECT committed_seq FROM lane_cursor WHERE lane='telemetry'"
            ).fetchone()[0]
        assert pending == [] and cur == 4
        assert (
            [(x[0], x[1]) for x in loss] == [(2, 2), (4, 4)]
            and loss[0][2] == "pre_restore_history_unrecoverable"
            and loss[1][2].startswith("rejected_by_server")
        )
        cov = admin.get("/api/v1/usage").json()["coverage"]
        assert (
            next(c for c in cov["lanes"] if c["device_id"] == ag.device_id and c["lane"] == "telemetry")[
                "committed_seq"
            ]
            == 4
        )
        mine = sorted(
            (x["from_seq"], x["to_seq"]) for x in cov["loss_ranges"] if x["device_id"] == ag.device_id
        )
        assert mine == [(2, 2), (4, 4)]
        # the valid retained record 3 really reached the server (never dropped by an inferred ACK)
        tel = admin.get(f"/api/v1/devices/{ag.device_id}/telemetry").json()
        rows = tel if isinstance(tel, list) else tel.get("samples") or tel.get("rows") or []
        assert any(abs(float(r.get("cpu_pct") or -1) - 3.0) < 1e-9 for r in rows), rows[:3]
    finally:
        server.should_exit = True
        th.join(timeout=5)


def test_large_restore_gap_is_declared_in_retained_chunks(tmp_path):
    from convoy_agent.journal import Journal

    j = Journal(tmp_path / "j.db")
    for lo, hi in ((1, 1_000_000), (1_000_001, 1_000_003)):
        assert j.declare_loss("usage", lo, hi, "pre_restore_history_unrecoverable") is not None
    _, losses = j.pending("usage")
    assert [(x["from_seq"], x["to_seq"]) for x in losses] == [(1, 1_000_000), (1_000_001, 1_000_003)]
    # a re-declaration of a changed overlapping span replaces only the overlapping chunk
    j.declare_loss("usage", 1_000_001, 1_000_005, "pre_restore_history_unrecoverable")
    _, losses = j.pending("usage")
    assert [(x["from_seq"], x["to_seq"]) for x in losses] == [(1, 1_000_000), (1_000_001, 1_000_005)]


def test_usage_ingest_keys_populations_on_the_schema_discriminator(app, admin):
    """The producer's `schema` field, not the presence of a metric, decides where a value lands:
    unversioned records keep their minutes under explicit MIXED names (meaning not attributable),
    schema-2 records feed the measured populations, and a schema-2 record that still carries an
    unversioned name is refused rather than guessed. Nothing is ever aliased or summed across."""
    a = enrolled_agent(app, admin)
    old = {"ts": time.time(), "inference_requests": 1, "online_minutes": 40.0, "active_minutes": 40.0}
    interim = {  # unversioned but already carrying connected_minutes: still not attributable
        "ts": time.time(),
        "inference_requests": 1,
        "connected_minutes": 7.0,
        "online_minutes": 7.0,
        "active_minutes": 0.25,
    }
    new = {
        "schema": 2,
        "ts": time.time(),
        "inference_requests": 2,
        "contact_minutes": 10.0,
        "inference_minutes": 0.5,
        "runtime_up_minutes": 9.0,
        "agent_up_minutes": 10.0,
        "contacts": 5,
        "reconnects": 1,
        "unknown_coverage_s": 3.0,
    }
    recs = [{"seq": i + 1, "kind": "usage", "body": b} for i, b in enumerate((old, interim, new))]
    r = a.client.post("/api/agent/v1/spool", json={"lane": "usage", "records": recs})
    assert r.status_code == 200 and r.json()["accepted"] == 3
    t = admin.get("/api/v1/usage").json()["totals"]
    assert t["mixed_online_minutes"] == 47.0 and t["mixed_active_minutes"] == 40.25
    assert t["contact_minutes"] == 10.0 and t["inference_minutes"] == 0.5 and t["unknown_coverage_s"] == 3.0
    assert t["contacts"] == 5 and t["reconnects"] == 1 and t["inference_requests"] == 4
    for absent in ("online_minutes", "active_minutes", "connected_minutes", "legacy_online_minutes"):
        assert absent not in t
    # refused (disposed with a reason), not guessed: a schema-2 record with an unversioned name, and an
    # unknown schema
    bads = ({**new, "active_minutes": 1.0}, {**new, "connected_minutes": 1.0}, {**new, "schema": 3})
    for i, bad in enumerate(bads):
        r = a.client.post(
            "/api/agent/v1/spool",
            json={"lane": "usage", "records": [{"seq": 4 + i, "kind": "usage", "body": bad}]},
        )
        assert r.status_code == 200 and r.json()["accepted"] == 0, r.text
        assert "schema" in r.json()["rejected"][0]["reason"]
    assert admin.get("/api/v1/usage").json()["totals"] == t


def test_populated_upgrade_relabels_mixed_usage_history_idempotently(settings, app, admin):
    """Upgrade of an EXISTING database whose usage_daily rows were written by earlier servers under
    `active_minutes` / `online_minutes` / the interim `legacy_*` names: startup refuses (a data step
    with rows to touch needs `convoy-server migrate`), the migration preserves every value under the
    MIXED names (summing collisions instead of picking one), a second run is a no-op, and a schema-2
    record ingested afterwards lands in the distinct measured population."""
    from convoy_server import migrations
    from convoy_server.db import make_engine, reset_engine, session_scope
    from convoy_server.models import UsageDaily

    a = enrolled_agent(app, admin)
    did = a.device_id
    with session_scope() as db, db.begin():
        for metric, value in (
            ("active_minutes", 40.0),  # pre-rework: runtime loaded
            ("legacy_active_minutes", 2.0),  # interim server label for the same unversioned producers
            ("mixed_active_minutes", 0.5),  # already relabelled (a partially upgraded install)
            ("online_minutes", 30.0),
            ("legacy_online_minutes", 1.0),
            ("connected_minutes", 3.0),  # interim producers: loosely bounded contact time
            ("inference_requests", 9.0),
        ):
            db.add(UsageDaily(day="2026-01-02", device_id=did, metric=metric, value=value, simulated=True))
        db.add(
            UsageDaily(day="2026-01-03", device_id=did, metric="active_minutes", value=5.0, simulated=True)
        )
    reset_engine()  # release the shared lock; the services are "stopped"
    engine = make_engine(settings)
    with engine.connect() as c:
        c.execute(migrations.text("PRAGMA user_version=2"))
        c.commit()
    plan = migrations.plan_migration(engine)
    assert plan["needs_data_migration"] and plan["data_steps"][0]["rows"] == 6 and not plan["empty"]
    with pytest.raises(migrations.SchemaError, match="convoy-server migrate"):
        migrations.ensure_schema(engine)  # startup refuses and names the command
    engine.dispose()

    res = migrations.migrate_database(settings)
    assert res["ok"] and res["pre_migration_copy"]
    steps = res["applied"]["data_steps"]["usage_daily_mixed_populations"]
    assert steps["active_minutes"] == {"merged_into_existing": 1, "renamed": 1}
    assert steps["legacy_active_minutes"] == {"merged_into_existing": 1, "renamed": 0}

    def rows():
        eng = make_engine(settings)
        try:
            with eng.connect() as c:
                return sorted(
                    tuple(r)
                    for r in c.execute(
                        migrations.text("SELECT day, metric, value FROM usage_daily WHERE device_id=:d"),
                        {"d": did},
                    )
                )
        finally:
            eng.dispose()

    expected = [
        ("2026-01-02", "inference_requests", 9.0),
        ("2026-01-02", "mixed_active_minutes", 42.5),
        ("2026-01-02", "mixed_online_minutes", 34.0),
        ("2026-01-03", "mixed_active_minutes", 5.0),
    ]
    assert rows() == expected
    again = migrations.migrate_database(settings)
    assert again["ok"] and again["applied"] == {"unchanged": True, "user_version": migrations.SCHEMA_VERSION}
    assert rows() == expected and migrations.user_version(make_engine(settings)) == migrations.SCHEMA_VERSION

    # the services start again on the migrated database and a schema-2 record stays distinct
    from convoy_server.db import init_engine

    init_engine(settings)
    body = {"schema": 2, "ts": time.time(), "inference_minutes": 0.5, "contact_minutes": 1.0}
    r = a.client.post(
        "/api/agent/v1/spool", json={"lane": "usage", "records": [{"seq": 1, "kind": "usage", "body": body}]}
    )
    assert r.status_code == 200, r.text
    t = admin.get("/api/v1/usage", params={"from": "2026-01-01", "to": "2099-01-01"}).json()["totals"]
    assert t["mixed_active_minutes"] == 47.5 and t["inference_minutes"] == 0.5 and "active_minutes" not in t
    assert t["mixed_online_minutes"] == 34.0 and t["contact_minutes"] == 1.0 and "connected_minutes" not in t


def test_migration_backfills_a_json_default_column_on_populated_rows(app, admin, settings):
    """Upgrade path for an existing schema-2 database that predates loss_ranges.context: the column is
    added with a safe '{}' default and the populated loss rows stay readable."""
    import sqlite3

    from convoy_server.db import init_engine, reset_engine
    from convoy_server.migrations import migrate_database

    a = enrolled_agent(app, admin)
    r = a.client.post(
        "/api/agent/v1/spool",
        json={
            "lane": "usage",
            "records": [{"seq": 3, "kind": "usage", "body": {"ts": time.time(), "inference_requests": 1}}],
            "loss_ranges": [{"from_seq": 1, "to_seq": 2, "reason": "quota_evicted"}],
        },
    )
    assert r.json()["committed_seq"] == 3
    reset_engine()
    live = settings.data_dir / "convoy.db"
    con = sqlite3.connect(str(live))
    con.execute("ALTER TABLE loss_ranges DROP COLUMN context")
    con.commit()
    assert con.execute("SELECT count(*) FROM loss_ranges").fetchone()[0] == 1
    con.close()
    plan = migrate_database(settings, dry_run=True)
    assert plan["ok"] is True and any(
        c["column"] == "context" and c["default"] == "'{}'" for c in plan["plan"]["add_columns"]
    ), plan
    res = migrate_database(settings)
    assert res["ok"] is True and "loss_ranges.context" in res["applied"]["add_columns"], res
    init_engine(settings)
    cov = admin.get("/api/v1/usage").json()["coverage"]["loss_ranges"]
    mine = [x for x in cov if x["device_id"] == a.device_id]
    assert mine and mine[0]["context"] == {} and (mine[0]["from_seq"], mine[0]["to_seq"]) == (1, 2)


def test_repeated_restore_of_the_same_backup_rebinds_and_recontextualises(app, admin, settings, tmp_path):
    """Restoring the SAME backup twice: after each restore the previous device credential is dead, a
    fresh rebind (new epoch) is required, and the losses declared afterwards carry that restoration's
    own context (a later restored_at, a higher binding epoch) — never the previous one."""
    from conftest import FakeAgent, enrollment_token
    from convoy_server.db import reset_engine, session_scope
    from convoy_server.services.backup import restore_backup, take_backup
    from convoy_server.services.identity import recover_admin

    seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    assert (
        a.client.post(
            "/api/agent/v1/spool",
            json={
                "lane": "usage",
                "records": [
                    {"seq": 1, "kind": "usage", "body": {"ts": time.time(), "inference_requests": 1}}
                ],
            },
        ).json()["committed_seq"]
        == 1
    )
    with session_scope() as db:
        b = take_backup(db, str(tmp_path / "B.db"))
    contexts = []
    prev = a
    for round_ in (1, 2):
        reset_engine()
        assert restore_backup(b["path"], confirm=True)["ok"] is True
        with session_scope() as db:
            recover_admin(db, "admin@example.com", f"pw-after-restore-{round_}")
        rec = login(TestClient(app), "admin@example.com", f"pw-after-restore-{round_}")
        assert (
            prev.client.post("/api/agent/v1/spool", json={"lane": "usage", "records": []}).status_code == 401
        )
        cur = FakeAgent(app, f"same{round_}")
        assert cur.enroll(enrollment_token(rec, rebind_device_id=a.device_id)).status_code == 200
        assert rec.post("/api/v1/admin/quarantine/lift", headers=WEB).status_code == 200
        # the device declares the (same) unrecoverable span against THIS restoration
        r = cur.client.post(
            "/api/agent/v1/spool",
            json={
                "lane": "usage",
                "records": [
                    {"seq": 4, "kind": "usage", "body": {"ts": time.time(), "inference_requests": 1}}
                ],
                "loss_ranges": [{"from_seq": 2, "to_seq": 3, "reason": "pre_restore_history_unrecoverable"}],
            },
        )
        assert r.status_code == 200 and r.json()["committed_seq"] == 4, r.text
        lr = [
            x
            for x in rec.get("/api/v1/usage").json()["coverage"]["loss_ranges"]
            if x["device_id"] == a.device_id
        ]
        assert len(lr) == 1  # each restore starts from B: exactly one declaration is visible per restoration
        contexts.append(lr[0]["context"])
        prev = cur
        admin = rec
    assert contexts[0]["restored_at"] != contexts[1]["restored_at"]
    assert (
        contexts[0]["binding_epoch"] == 2 and contexts[1]["binding_epoch"] == 2
    )  # B is at epoch 1 each time
    assert contexts[0]["credential_issued_at"] != contexts[1]["credential_issued_at"]
