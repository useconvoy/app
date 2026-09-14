"""Review packet (operations side): R49 scheduler successors, R55 partial PATCH, R51 staged/exclusive
restore, R52 configured database path, R63 schema migration, R66 CLI exit codes, R71 receipt/recipe
compatibility, R64 verify-artifacts, R73 worker-health."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import WEB, login
from convoy_server import config
from convoy_server.db import reset_engine, session_scope, write_txn
from convoy_server.ids import utcnow
from fastapi.testclient import TestClient
from helpers import enrolled_agent, heartbeat, seed

UTC = timezone.utc


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------- R49
def test_r49_ordinary_successors_and_dst_edges_strictly_advance():
    from convoy_server.services.scheduler import compute_next, next_occurrences

    after = _dt("2026-09-14T12:00:00Z")
    assert compute_next("*/5 * * * *", "UTC", after)[0] == _dt("2026-09-14T12:05:00Z")
    assert compute_next("0 * * * *", "UTC", after)[0] == _dt("2026-09-14T13:00:00Z")
    daily2 = next_occurrences("0 6,18 * * *", "UTC", after, 3)
    assert [o["civil"] for o in daily2] == ["2026-09-14T18:00", "2026-09-15T06:00", "2026-09-15T18:00"]
    # exact match at `after` is not "after": strictly later
    assert compute_next("0 12 * * *", "UTC", after)[0] == _dt("2026-09-15T12:00:00Z")
    # spring-forward gap: 02:30 does not exist on 2026-03-08 in Los Angeles -> skipped visibly, never shifted
    gap = next_occurrences("30 2 * * *", "America/Los_Angeles", _dt("2026-03-08T00:00:00Z"), 2)
    assert gap[0]["civil"] == "2026-03-08T02:30" and gap[0]["utc"] is None and "skipped" in gap[0]["note"]
    assert gap[1]["civil"] == "2026-03-09T02:30" and gap[1]["utc"] == "2026-03-09T09:30:00Z"
    # fall-back fold: 01:30 happens twice on 2026-11-01; it fires once, at fold 0 (08:30Z)
    fold = next_occurrences("30 1 * * *", "America/Los_Angeles", _dt("2026-11-01T07:00:00Z"), 2)
    assert fold[0]["utc"] == "2026-11-01T08:30:00Z" and fold[0]["note"] == "fold_0"
    assert fold[1]["civil"] == "2026-11-02T01:30" and fold[1]["utc"] == "2026-11-02T09:30:00Z"
    # the reviewer's probe: after 09:10Z (01:10 PST, second pass through 01:xx) the fold-0 instant is PAST
    nxt, civil = compute_next("30 1 * * *", "America/Los_Angeles", _dt("2026-11-01T09:10:00Z"))
    assert nxt == _dt("2026-11-02T09:30:00Z") and civil == "2026-11-02T01:30"
    # hourly across the fold: 01:15 once, then 02:15 PST
    hourly = next_occurrences("15 * * * *", "America/Los_Angeles", _dt("2026-11-01T07:30:00Z"), 3)
    assert [h["utc"] for h in hourly] == [
        "2026-11-01T08:15:00Z",
        "2026-11-01T10:15:00Z",
        "2026-11-01T11:15:00Z",
    ]
    # every-5-minutes through both DST edges: the chain strictly advances and never repeats a civil key
    for start in ("2026-11-01T07:50:00Z", "2026-03-08T09:50:00Z"):
        t = _dt(start)
        seen = set()
        for _ in range(40):
            nxt, civil = compute_next("*/5 * * * *", "America/Los_Angeles", t)
            assert nxt is not None and nxt > t and civil not in seen, (start, t, nxt, civil)
            seen.add(civil)
            t = nxt


# ---------------------------------------------------------------- R55
def test_r55_partial_schedule_patch_over_http(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    body = {
        "name": "nightly",
        "kind": "eval",
        "cron": "30 2 * * *",
        "timezone": "Asia/Kolkata",
        "target": {"device_ids": [a.device_id]},
        "payload": {"plan_id": s["plan_id"]},
    }
    sch = admin.post("/api/v1/schedules", json=body, headers=WEB).json()
    r = admin.patch(f"/api/v1/schedules/{sch['id']}", json={"name": "renamed"}, headers=WEB)
    assert r.status_code == 200, r.text
    assert (
        r.json()["timezone"] == "Asia/Kolkata"
        and r.json()["cron"] == "30 2 * * *"
        and r.json()["revision"] == 2
    )
    r = admin.patch(f"/api/v1/schedules/{sch['id']}", json={"enabled": False}, headers=WEB)
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["timezone"] == "Asia/Kolkata"
    assert (
        admin.patch(f"/api/v1/schedules/{sch['id']}", json={"timezone": None}, headers=WEB).status_code == 400
    )
    assert admin.patch(f"/api/v1/schedules/{sch['id']}", json={"cron": None}, headers=WEB).status_code == 400
    assert (
        admin.patch(f"/api/v1/schedules/{sch['id']}", json={"kind": "health"}, headers=WEB).status_code == 422
    )
    assert (
        admin.patch(
            f"/api/v1/schedules/{sch['id']}", json={"missed_policy": "later"}, headers=WEB
        ).status_code
        == 400
    )
    assert admin.get(f"/api/v1/schedules/{sch['id']}").json()["revision"] == 3


# ---------------------------------------------------------------- R51 / R52
def _snapshot(admin, tmp_path):
    from convoy_server.services.backup import take_backup

    with session_scope() as db:
        return take_backup(db, str(tmp_path / "snap.db"))


def test_r51_restore_refuses_active_service_and_survives_crash_on_either_side(app, admin, settings, tmp_path):
    from convoy_server.services.backup import restore_backup
    from convoy_server.services.identity import recover_admin

    seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    b = _snapshot(admin, tmp_path)
    live = settings.data_dir / "convoy.db"
    assert b["size"] > 0 and live.exists()
    # 1) the api still holds the database: refused, nothing changes
    res = restore_backup(b["path"], confirm=True)
    assert res["ok"] is False and "locked" in res["error"]
    assert login(TestClient(app)).get("/api/v1/auth/me").status_code == 200
    # service stopped (lock released)
    reset_engine()
    # 2) crash BEFORE publication: live database untouched, staging cleaned up, old credentials still valid
    with pytest.raises(RuntimeError):
        restore_backup(b["path"], confirm=True, _fault="before_publish")
    assert not (settings.data_dir / "convoy.db.restore-staging").exists()
    c = login(TestClient(app))
    assert c.get("/api/v1/auth/me").json()["installation"]["quarantined_at"] is None
    assert a.client.post("/api/agent/v1/report", json={"seq": 5}).status_code != 401
    reset_engine()
    # 3) crash AFTER publication: what is live is the snapshot, already quarantined and invalidated
    with pytest.raises(RuntimeError):
        restore_backup(b["path"], confirm=True, _fault="after_publish")
    c = TestClient(app)
    assert (
        c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin-password-1"}
        ).status_code
        == 401
    )
    assert a.client.post("/api/agent/v1/report", json={"seq": 6}).status_code == 401
    with session_scope() as db:
        recover_admin(db, "admin@example.com", "post-restore-password")
    rec = login(TestClient(app), "admin@example.com", "post-restore-password")
    inst = rec.get("/api/v1/auth/me").json()["installation"]
    assert inst["quarantined_at"] and inst["dispatch_paused_at"]
    # a non-Convoy sqlite file is refused before anything is staged
    junk = tmp_path / "junk.db"
    sqlite3.connect(str(junk)).execute("create table t(x)").connection.close()
    reset_engine()
    assert restore_backup(str(junk), confirm=True)["ok"] is False


def test_r52_backup_and_restore_honor_database_url(tmp_path):
    from convoy_server.db import init_engine
    from convoy_server.services.backup import BackupError, is_convoy_db, restore_backup, take_backup

    other = tmp_path / "elsewhere" / "fleet.sqlite"
    other.parent.mkdir(parents=True)
    s = config.Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{other.as_posix()}",
        simulator=True,
        scheduler_inprocess=False,
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="admin-password-1",
        public_url="http://testserver",
    )
    reset_engine()
    config.set_settings(s)
    try:
        from convoy_server.app import create_app

        app = create_app(s, start_scheduler=False)
        with TestClient(app) as c:
            assert (
                c.post(
                    "/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin-password-1"}
                ).status_code
                == 200
            )
        assert other.exists() and not (tmp_path / "data" / "convoy.db").exists()
        with session_scope() as db:
            b = take_backup(db, str(tmp_path / "snap.db"))
        assert is_convoy_db(Path(b["path"])) and not (tmp_path / "data" / "convoy.db").exists()
        reset_engine()
        res = restore_backup(b["path"], confirm=True)
        assert res["ok"] is True and not (tmp_path / "data" / "convoy.db").exists()
        assert is_convoy_db(other)
        # the configured database is the one that was quarantined
        con = sqlite3.connect(f"file:{other}?mode=ro", uri=True)
        assert con.execute("select quarantined_at from installation").fetchone()[0] is not None
        con.close()
        # a missing configured source is an error, never created as a side effect
        s2 = config.Settings(
            data_dir=tmp_path / "d2", database_url=f"sqlite:///{(tmp_path / 'missing.sqlite').as_posix()}"
        )
        config.set_settings(s2)
        reset_engine()
        with pytest.raises(BackupError):
            with session_scope() as db:  # noqa: SIM117
                init_engine()  # creates the (empty) configured db for the session...
                (tmp_path / "missing.sqlite").unlink()  # ...which we remove to model a wrong path
                take_backup(db, str(tmp_path / "never.db"))
        assert not (tmp_path / "never.db").exists() and not (tmp_path / "missing.sqlite").exists()
    finally:
        reset_engine()


# ---------------------------------------------------------------- R63
def _downgrade_schema(path: Path) -> None:
    """Produce the pre-epoch schema (571ebe6): no devices.binding_epoch, reports unique on (device, seq)."""
    con = sqlite3.connect(str(path))
    con.execute("ALTER TABLE devices DROP COLUMN binding_epoch")
    con.execute(
        "CREATE TABLE reports_old (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id VARCHAR(32), seq INTEGER, "
        "boot_id VARCHAR(64), kind VARCHAR(16), source_ts DATETIME, receipt_ts DATETIME, applied BOOLEAN, body JSON, "
        "CONSTRAINT uq_report_seq UNIQUE (device_id, seq))"
    )
    con.execute(
        "INSERT INTO reports_old (id, device_id, seq, boot_id, kind, source_ts, receipt_ts, applied, body) "
        "SELECT id, device_id, seq, boot_id, kind, source_ts, receipt_ts, applied, body FROM reports"
    )
    con.execute("DROP TABLE reports")
    con.execute("ALTER TABLE reports_old RENAME TO reports")
    con.execute("PRAGMA user_version=0")
    con.commit()
    con.close()


def test_r63_upgrade_from_older_schema_refuses_then_migrates(app, admin, settings):
    from convoy_server.db import init_engine
    from convoy_server.migrations import SCHEMA_VERSION, SchemaError, migrate_database, user_version
    from convoy_server.models import Report

    seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    live = settings.data_dir / "convoy.db"
    # a live service blocks the migration command
    assert "locked" in migrate_database(settings)["error"]
    reset_engine()
    _downgrade_schema(live)
    con = sqlite3.connect(str(live))
    assert "epoch" not in [r[1] for r in con.execute("PRAGMA table_info(reports)")]
    con.close()
    # the current server refuses to start on the older schema instead of failing on the first SELECT
    with pytest.raises(SchemaError) as ei:
        init_engine(settings)
    assert "convoy-server migrate" in str(ei.value)
    plan = migrate_database(settings, dry_run=True)["plan"]
    assert "reports" in plan["rebuild"] and any(c["column"] == "binding_epoch" for c in plan["add_columns"])
    res = migrate_database(settings)
    assert (
        res["ok"] is True
        and "reports" in res["applied"]["rebuild"]
        and Path(res["pre_migration_copy"]).exists()
    )
    init_engine(settings)
    with session_scope() as db:
        assert user_version(db.get_bind()) == SCHEMA_VERSION
        rows = list(db.query(Report).filter(Report.device_id == a.device_id))
        assert rows and all(r.epoch == 1 for r in rows)  # retained, defaulted
        with write_txn(db):  # the new identity (device, epoch, seq) accepts a rebound device's seq 1
            db.add(Report(device_id=a.device_id, epoch=2, seq=rows[0].seq, kind="heartbeat", body={}))
    # the running app works on the migrated database
    assert login(TestClient(app)).get(f"/api/v1/devices/{a.device_id}").json()["id"] == a.device_id
    # a database stamped by a newer server is refused
    reset_engine()
    con = sqlite3.connect(str(live))
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    con.commit()
    con.close()
    with pytest.raises(SchemaError):
        init_engine(settings)
    con = sqlite3.connect(str(live))
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    con.commit()
    con.close()


# ---------------------------------------------------------------- R66 / R64 / R73
def test_r66_cli_exit_codes_and_maintenance_commands(app, admin, settings, tmp_path, capsys):
    from convoy_server.cli import main
    from convoy_server.services.scheduler import Worker

    s = seed(admin)
    assert main(["restore", str(tmp_path / "nope.db"), "--yes"]) == 1
    assert '"ok": false' in capsys.readouterr().out
    assert main(["verify-artifacts"]) == 0
    arts = admin.get("/api/v1/runtime-artifacts").json()
    art = next(x for x in arts if x["id"] == s["artifact_id"])
    assert art["storage"] == "server"
    blob = settings.artifacts_dir / "runtime" / art["archive_sha256"]
    assert blob.exists()
    blob.rename(blob.with_name("moved"))
    assert main(["verify-artifacts"]) == 1
    assert art["id"] in capsys.readouterr().out
    blob.with_name("moved").rename(blob)
    blob.write_bytes(blob.read_bytes() + b"x")
    assert main(["verify-artifacts"]) == 1  # digest mismatch
    assert main(["worker-health"]) == 1  # no lease yet
    with session_scope() as db:
        assert Worker("w").acquire(db)
    assert main(["worker-health"]) == 0
    from convoy_server.models import SchedulerLease

    with session_scope() as db:
        with write_txn(db):
            db.get(SchedulerLease, "scheduler").expires_at = utcnow() - timedelta(seconds=5)
    assert main(["worker-health"]) == 1
    assert main(["migrate", "--dry-run"]) == 1  # the service holds the lock
    reset_engine()
    assert main(["migrate", "--dry-run"]) == 0


# ---------------------------------------------------------------- R71
def test_r71_receipt_must_prove_the_recipe_identity(app, admin):
    from convoy_server.services.catalog import CUDA_TARGET, DEFAULT_CMAKE

    rec = admin.post(
        "/api/v1/recipes",
        json={
            "name": "pinned",
            "commit": "5266f24da75dc449bd56cbed7addb9c8e4a6a73e",
            "tag": "v0.4.0",
            "backend": "cuda",
        },
        headers=WEB,
    )
    assert rec.status_code == 201, rec.text
    files = [{"path": "bin/llama-server", "sha256": "ab" * 32, "size": 10}]

    def receipt(**prov):
        p = {
            "commit": "5266f24da75dc449bd56cbed7addb9c8e4a6a73e",
            "cmake_flags": list(DEFAULT_CMAKE),
            "cuda_version": "12.6.68",
            "l4t_release": "# R36 (release), REVISION: 5.2, GCID: 1, BOARD: generic, EABI: aarch64",
        }
        p.update(prov)
        return {"archive_sha256": "cd" * 32, "archive_size": 100, "files": files, "provenance": p}

    def register(r, sha=None):
        if sha:
            r = {**r, "archive_sha256": sha}
        return admin.post(
            "/api/v1/runtime-artifacts",
            json={"recipe_id": rec.json()["id"], "receipt": r, "scope": "fleet", "storage": "device"},
            headers=WEB,
        )

    assert register(receipt(commit="0" * 40)).status_code == 409
    assert register(receipt(cuda_version="13.0.1")).status_code == 409
    assert register(receipt(l4t_release="# R39 (release), REVISION: 2.1")).status_code == 409
    assert register(receipt(cmake_flags=DEFAULT_CMAKE[:-1])).status_code == 409
    r = receipt()
    del r["provenance"]["cuda_version"]
    assert register(r).status_code == 409
    # the typed cmake spelling is the same cache entry as the recipe's untyped identity string
    typed = [
        f.replace("-DCMAKE_CUDA_ARCHITECTURES=87", "-DCMAKE_CUDA_ARCHITECTURES:STRING=87")
        for f in DEFAULT_CMAKE
    ]
    ok = register(receipt(cmake_flags=typed))
    assert ok.status_code == 201, ok.text
    assert ok.json()["provenance"]["l4t_release"].startswith("# R36") and CUDA_TARGET["l4t"] == "36.5.2"


# ---------------------------------------------------------------- follow-ups at 1707022
def test_r63fu_migrate_refuses_a_future_schema_before_touching_it(app, admin, settings):
    from convoy_server.migrations import SCHEMA_VERSION, migrate_database

    seed(admin)
    live = settings.data_dir / "convoy.db"
    reset_engine()
    con = sqlite3.connect(str(live))
    con.execute("PRAGMA user_version=99")
    con.commit()
    before = (
        con.execute("SELECT count(*) FROM devices").fetchone()[0],
        con.execute("SELECT count(*) FROM releases").fetchone()[0],
    )
    con.close()
    for dry in (True, False):
        res = migrate_database(settings, dry_run=dry)
        assert res["ok"] is False and "newer" in res["error"], res
    con = sqlite3.connect(str(live))
    assert con.execute("PRAGMA user_version").fetchone()[0] == 99
    assert (
        con.execute("SELECT count(*) FROM devices").fetchone()[0],
        con.execute("SELECT count(*) FROM releases").fetchone()[0],
    ) == before
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    con.commit()
    con.close()
    assert not list(settings.data_dir.glob("convoy.pre-migrate-*.db"))


def test_r43fu_restore_persists_every_required_target_and_is_retryable(app, admin):
    from helpers import exec_sha, grant
    from test_review_round4 import _op_of, _rollout
    from test_rollouts_and_scheduler import _device_deploys, _tick

    s = seed(admin)
    devs = [enrolled_agent(app, admin, f"d{i}") for i in range(2)]
    for d in devs:
        from helpers import full_deploy

        full_deploy(d, admin, s["release_id"], s["plan_id"])
    ids = [d.device_id for d in devs]
    ro = _rollout(admin, s["candidate_plan_id"], ids, ids)  # both canaries -> completes without expansion
    for d in devs:
        _device_deploys(admin, d, _op_of(admin, ro, d.device_id), s["candidate_plan_id"])
    _tick()
    assert admin.get(f"/api/v1/rollouts/{ro['id']}").json()["status"] == "completed"
    # device 2 is busy with an unrelated unsettled operation: its restore cannot be issued
    busy = admin.post(
        f"/api/v1/devices/{ids[1]}/eval", json={"plan_id": s["candidate_plan_id"]}, headers=WEB
    ).json()
    res = admin.post(f"/api/v1/rollouts/{ro['id']}/restore", headers=WEB).json()
    assert (
        res["status"] == "restoring"
        and res["results"][ids[0]]["operation_id"]
        and "unsettled" in res["results"][ids[1]]["error"]
    )
    r = admin.get(f"/api/v1/rollouts/{ro['id']}").json()
    assert (
        r["device_states"][ids[1]]["restore"]["status"] == "error"
        and "unsettled" in r["device_states"][ids[1]]["restore"]["reason"]
    )
    # device 1 acknowledges its recover operation; the rollout is NOT restored because device 2 still runs the release
    rec = admin.get(f"/api/v1/operations/{res['results'][ids[0]]['operation_id']}").json()
    heartbeat(
        devs[0],
        active=s["candidate_release_id"],
        recovery=s["release_id"],
        stage="active",
        generation=rec["generation"] - 1,
    )
    g = grant(devs[0], rec, active=s["candidate_release_id"], recovery=s["release_id"]).json()
    ok = devs[0].client.post(
        f"/api/agent/v1/operations/{rec['id']}/outcome",
        json={
            "status": "succeeded",
            "grant_id": g["grant_id"],
            "grant_consumed_seq": devs[0].seq,
            "result": {
                "active_release_id": s["release_id"],
                "release_digest": rec["payload"]["release_digest"],
                "generation": rec["generation"],
            },
            "evidence": {"health": "ok", "runtime": {"binary_sha256": exec_sha(rec), "build_info": "sim"}},
        },  # fmt: skip
    )
    assert ok.status_code == 200, ok.text
    heartbeat(devs[0], active=s["release_id"], stage="active", generation=rec["generation"])
    _tick()
    r = admin.get(f"/api/v1/rollouts/{ro['id']}").json()
    assert r["status"] == "restore_failed" and ids[1] in r["message"]
    assert (
        r["device_states"][ids[0]]["restore"]["status"] == "succeeded"
        and r["device_states"][ids[1]]["restore"]["status"] == "error"
    )
    # retry once device 2 is free: only device 2 is re-issued; the rollout restores after it acknowledges
    assert admin.post(f"/api/v1/operations/{busy['id']}/cancel", headers=WEB).status_code == 200
    res = admin.post(f"/api/v1/rollouts/{ro['id']}/restore", headers=WEB).json()
    assert (
        res["status"] == "restoring"
        and res["results"][ids[0]]["skipped"]
        and res["results"][ids[1]]["operation_id"]
    )
    rec2 = admin.get(f"/api/v1/operations/{res['results'][ids[1]]['operation_id']}").json()
    heartbeat(
        devs[1],
        active=s["candidate_release_id"],
        recovery=s["release_id"],
        stage="active",
        generation=rec2["generation"] - 1,
    )
    g2 = grant(devs[1], rec2, active=s["candidate_release_id"], recovery=s["release_id"]).json()
    # a FAILED recover outcome is visible and keeps the rollout restore_failed (not restored)
    fail = devs[1].client.post(
        f"/api/agent/v1/operations/{rec2['id']}/outcome",
        json={
            "status": "failed",
            "grant_id": g2["grant_id"],
            "grant_consumed_seq": devs[1].seq,
            "failure": {"code": "RECOVER_FAILED", "message": "boom"},
        },
    )
    assert fail.status_code == 200, fail.text
    _tick()
    r = admin.get(f"/api/v1/rollouts/{ro['id']}").json()
    assert (
        r["status"] == "restore_failed"
        and r["device_states"][ids[1]]["restore"]["status"] == "failed"
        and "boom" in r["device_states"][ids[1]]["restore"]["reason"]
    )
    # deploy operations of this rollout still get no grant; a third restore attempt is possible
    heartbeat(
        devs[1],
        active=s["candidate_release_id"],
        recovery=s["release_id"],
        stage="active",
        generation=rec2["generation"],
    )
    res = admin.post(f"/api/v1/rollouts/{ro['id']}/restore", headers=WEB).json()
    assert res["status"] == "restoring" and res["results"][ids[1]]["operation_id"]


def test_r53fu_stalled_worker_performs_no_maintenance(app, admin, settings, tmp_path, monkeypatch):
    from convoy_server.models import SchedulerLease
    from convoy_server.services.backup import take_backup
    from convoy_server.services.evidence import apply_retention
    from convoy_server.services.scheduler import FenceLost, Worker
    from convoy_server.services.worker import run_tick

    seed(admin)
    w1, w2 = Worker("w1"), Worker("w2")

    def stall_after_schedule_tick(db, worker):
        # w1 finishes its schedule tick, then stalls past its lease while w2 takes over
        with write_txn(db):
            db.get(SchedulerLease, "scheduler").expires_at = utcnow() - timedelta(seconds=1)
        assert w2.acquire(db)
        return []

    import convoy_server.services.worker as worker_mod

    monkeypatch.setattr(worker_mod, "tick_schedules", stall_after_schedule_tick)
    out = run_tick(w1)
    assert out["fence_lost"] is True and out["leader"] is False  # no maintenance follows a lost lease
    with session_scope() as db:
        with pytest.raises(FenceLost):
            apply_retention(db, w1)
        with pytest.raises(FenceLost):
            take_backup(db, str(tmp_path / "stale.db"), worker=w1)
    assert not (tmp_path / "stale.db").exists()
    assert admin.get("/api/v1/admin/backups").json() == []
    with session_scope() as db:
        assert take_backup(db, str(tmp_path / "fresh.db"), worker=w2)["size"] > 0  # the live leader may


# ---------------------------------------------------------------- restore-gap reconciliation (server side)
def _usage_batch(a, records, loss_ranges=None):
    return a.client.post(
        "/api/agent/v1/spool", json={"lane": "usage", "records": records, "loss_ranges": loss_ranges or []}
    ).json()


def _rec(seq, n):
    import time

    return {"seq": seq, "kind": "usage", "body": {"ts": time.time(), "inference_requests": n}}


def test_restore_gap_declared_loss_resumes_exactly_once(app, admin, settings, tmp_path):
    """Server restored to an older snapshot: its cursor is behind the device's durable ACK frontier; the
    records in between were ACKed by the old server and deleted on the device. The server tells the device
    where its history ends (`next_expected_seq`); the device declares exactly that span as unrecoverable
    loss and re-sends what it still holds; ingestion resumes once, visibly, idempotently."""
    from conftest import FakeAgent, enrollment_token
    from convoy_server.services.backup import restore_backup, take_backup
    from convoy_server.services.identity import recover_admin

    seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    assert _usage_batch(a, [_rec(1, 10), _rec(2, 10), _rec(3, 10)])["committed_seq"] == 3
    with session_scope() as db:
        b = take_backup(db, str(tmp_path / "B.db"))
    assert _usage_batch(a, [_rec(4, 100), _rec(5, 100)])["committed_seq"] == 5  # ACKed, then deleted locally
    reset_engine()
    assert restore_backup(b["path"], confirm=True)["ok"] is True
    with session_scope() as db:
        recover_admin(db, "admin@example.com", "post-restore-password")
    rec = login(TestClient(app), "admin@example.com", "post-restore-password")
    d2 = FakeAgent(app, "same")
    assert d2.enroll(enrollment_token(rec, rebind_device_id=a.device_id)).status_code == 200
    assert d2.device_id == a.device_id
    assert rec.post("/api/v1/admin/quarantine/lift", headers=WEB).status_code == 200
    lanes_before = {c["lane"]: c for c in rec.get("/api/v1/usage").json()["coverage"]["lanes"]}
    assert lanes_before["usage"]["committed_seq"] == 3
    # a retained new record is not contiguous with the restored cursor: deferred, cursor untouched
    r = _usage_batch(d2, [_rec(6, 1)])
    assert r == {"committed_seq": 3, "next_expected_seq": 4, "accepted": 0, "rejected": [], "deferred": [6]}
    lanes = {c["lane"]: c for c in rec.get("/api/v1/usage").json()["coverage"]["lanes"]}
    assert lanes["usage"]["updated_at"] == lanes_before["usage"]["updated_at"]  # no progress, no refresh
    assert rec.get("/api/v1/usage").json()["totals"]["inference_requests"] == 30
    # the device declares exactly [next_expected_seq, its old frontier] as unrecoverable and resumes
    loss = [{"from_seq": 4, "to_seq": 5, "reason": "pre_restore_history_unrecoverable"}]
    r = _usage_batch(d2, [_rec(6, 1)], loss)
    assert r["committed_seq"] == 6 and r["accepted"] == 1 and r["next_expected_seq"] == 7
    u = rec.get("/api/v1/usage").json()
    assert u["totals"]["inference_requests"] == 31  # 4..5 are gone for good, never re-counted or invented
    mine = [x for x in u["coverage"]["loss_ranges"] if x["device_id"] == a.device_id]
    assert [(x["from_seq"], x["to_seq"], x["reason"]) for x in mine] == [
        (4, 5, "pre_restore_history_unrecoverable")
    ]
    # a reconnect replay of the same batch changes nothing and adds no loss rows; new records continue
    r = _usage_batch(d2, [_rec(6, 1)], loss)
    assert r["committed_seq"] == 6 and r["accepted"] == 0
    r = _usage_batch(d2, [_rec(7, 2)], loss)
    assert r["committed_seq"] == 7 and r["accepted"] == 1
    u = rec.get("/api/v1/usage").json()
    assert u["totals"]["inference_requests"] == 33
    assert len([x for x in u["coverage"]["loss_ranges"] if x["device_id"] == a.device_id]) == 1
    # a loss declaration wider than the real gap can never swallow retained records: 8..9 exist locally
    r = _usage_batch(
        d2, [_rec(10, 5)], [{"from_seq": 8, "to_seq": 9, "reason": "pre_restore_history_unrecoverable"}]
    )
    assert r["committed_seq"] == 10  # the device is authoritative for what it no longer holds...
    r = _usage_batch(d2, [_rec(8, 5), _rec(9, 5)])
    assert (
        r["accepted"] == 0 and r["committed_seq"] == 10
    )  # ...and those sequences can never be counted later


def test_r53fu_backup_takeover_barrier_never_overwrites_a_published_backup(
    app, admin, settings, tmp_path, monkeypatch
):
    """The stalled worker passes the entry fence, snapshots, then loses the lease before publication: the
    existing backup file keeps its bytes and digest, nothing is registered, nothing is pruned."""
    import hashlib

    from convoy_server.models import SchedulerLease
    from convoy_server.services import backup as bk
    from convoy_server.services.scheduler import FenceLost, Worker

    seed(admin)
    target = tmp_path / "convoy-sentinel.db"
    w1, w2 = Worker("w1"), Worker("w2")
    with session_scope() as db:
        assert w1.acquire(db)
        first = bk.take_backup(db, str(target), worker=w1)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    assert first["sha256"] == digest
    # more data, so a second snapshot would differ; then w1 stalls exactly between snapshot and publication
    enrolled_agent(app, admin)
    real_sha = bk.hashlib.sha256

    def lose_lease_while_hashing(*a, **k):
        # after the snapshot, before the fenced publication transaction: w1 stalls past its lease
        with session_scope() as db2:
            with write_txn(db2):
                db2.get(SchedulerLease, "scheduler").expires_at = utcnow() - timedelta(seconds=1)
            assert w2.acquire(db2)
        monkeypatch.setattr(bk.hashlib, "sha256", real_sha)
        return real_sha(*a, **k)

    monkeypatch.setattr(bk.hashlib, "sha256", lose_lease_while_hashing)
    with session_scope() as db:
        with pytest.raises(FenceLost):
            bk.take_backup(db, str(target), worker=w1)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest  # sentinel untouched
    assert not list(tmp_path.glob(".convoy-sentinel.db.staging-*"))  # own staging cleaned up
    assert [b["sha256"] for b in admin.get("/api/v1/admin/backups").json()] == [digest]
    with session_scope() as db:
        second = bk.take_backup(db, str(target), worker=w2)  # the live leader publishes a new one
    assert second["sha256"] != digest and hashlib.sha256(target.read_bytes()).hexdigest() == second["sha256"]


def test_usage_reports_server_derived_eval_results_received(app, admin):
    from helpers import eval_evidence, full_deploy

    s = seed(admin)
    a = enrolled_agent(app, admin)
    op = full_deploy(a, admin, s["release_id"], s["plan_id"])  # ingests one eval result
    heartbeat(a, active=s["release_id"], stage="active", generation=op["generation"])
    eop = admin.post(
        f"/api/v1/devices/{a.device_id}/eval", json={"plan_id": s["plan_id"]}, headers=WEB
    ).json()
    heartbeat(a, active=s["release_id"], stage="active", generation=eop["generation"] - 1)
    eval_evidence(a, eop, "failed")  # all verdicts count
    u = admin.get("/api/v1/usage").json()
    assert u["totals"]["eval_results_received"] == 2
    mine = next(d for d in u["devices"] if d["device_id"] == a.device_id)
    assert mine["metrics"]["eval_results_received"] == 2 and "eval_runs" not in u["totals"]
    assert "server-derived" in u["note"] and "eval_results_received" in u["derived"]
    day = utcnow().strftime("%Y-%m-%d")
    assert admin.get(f"/api/v1/usage?from={day}&to={day}").json()["totals"]["eval_results_received"] == 2
    assert (
        admin.get("/api/v1/usage?from=2020-01-01&to=2020-01-02")
        .json()["totals"]
        .get("eval_results_received", 0)
        == 0
    )


def test_overview_counts_only_current_state_and_unpaused_schedules(app, admin):
    """Overview follow-ups from the recovered installation: (1) a PAUSED schedule is not an upcoming
    occurrence (the scheduler's own predicate: enabled AND not paused); (2) an abandonment counts as
    unsettled only while the device still holds it; once reconciled and released it keeps its honest
    `abandoned_unconfirmed` status but leaves the CURRENT unsettled count and the drill-down."""
    s = seed(admin)
    a = enrolled_agent(app, admin)
    heartbeat(a)
    sch = admin.post(
        "/api/v1/schedules",
        json={
            "name": "nightly",
            "kind": "health",
            "cron": "0 3 * * *",
            "timezone": "UTC",
            "target": {"device_ids": [a.device_id]},
        },
        headers=WEB,
    ).json()
    assert [x["id"] for x in admin.get("/api/v1/overview").json()["schedules_next"]] == [sch["id"]]
    assert admin.post(f"/api/v1/schedules/{sch['id']}/pause", headers=WEB).status_code == 200
    assert admin.get("/api/v1/overview").json()["schedules_next"] == []
    assert admin.post(f"/api/v1/schedules/{sch['id']}/resume", headers=WEB).status_code == 200
    assert [x["id"] for x in admin.get("/api/v1/overview").json()["schedules_next"]] == [sch["id"]]

    op = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    heartbeat(a)
    ov = admin.get("/api/v1/overview").json()["operations"]
    assert ov == {"unsettled": 1, "uncertain": 0}
    assert (
        admin.post(
            f"/api/v1/operations/{op['id']}/abandon", json={"reason": "stuck"}, headers=WEB
        ).status_code
        == 200
    )
    ov = admin.get("/api/v1/overview").json()["operations"]
    assert ov == {"unsettled": 1, "uncertain": 1}  # still held by the device: still unsettled
    drill = admin.get("/api/v1/operations", params={"unsettled": "true"}).json()
    assert [o["id"] for o in drill] == [op["id"]]
    rep = heartbeat(a, generation=1)
    heartbeat(a, generation=1, kind="reconcile", challenge=rep["reconcile_challenge"])
    got = admin.get(f"/api/v1/operations/{op['id']}").json()
    assert got["status"] == "abandoned_unconfirmed" and got["outcome"]["reconciled_at"]  # history preserved
    assert admin.get(f"/api/v1/devices/{a.device_id}").json()["active_operation_id"] is None
    assert admin.get("/api/v1/overview").json()["operations"] == {"unsettled": 0, "uncertain": 0}
    assert admin.get("/api/v1/operations", params={"unsettled": "true"}).json() == []
    # the plain status filter still lists it: the drill-down is a current-state view, not a rewrite
    assert [
        o["id"] for o in admin.get("/api/v1/operations", params={"status": "abandoned_unconfirmed"}).json()
    ] == [op["id"]]
