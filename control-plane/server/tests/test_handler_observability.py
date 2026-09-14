"""Server-only observability for the backup acceptance: allowlisted completion records around the real
report/spool service calls (logged only after the handler returned, i.e. after commit), with device id,
safe sequence/lane fields, applied/accepted/committed cursor, UTC start and the handler's monotonic
elapsed time; labelled as handler/service duration. Failures carry status and error class only. Plus a
proof that a real independent report commits while a WAL backup snapshot is held, and that the backup
markers (snapshot acquired/released, success) are emitted with the same attempt id."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import login
from convoy_server import config, db
from convoy_server.app import create_app
from convoy_server.db import session_scope
from convoy_server.services import backup as bk
from convoy_server.services.scheduler import Worker
from fastapi.testclient import TestClient
from helpers import enrolled_agent, heartbeat, spool

SECRET_MARKERS = ("cvd_", "nonce", "boot_id", "live_nonce", "Bearer", "password")


def _records(caplog, handler: str):
    return [
        r.handler_record
        for r in caplog.records
        if r.name == "convoy.handler" and getattr(r, "handler_record", {}).get("handler") == handler
    ]


@pytest.fixture()
def wal_stack(settings, monkeypatch):
    monkeypatch.setattr(db, "sqlite_wal_is_safe", lambda version=None: True)
    s = replace(settings, sqlite_wal=True)
    config.set_settings(s)
    db.reset_engine()
    app = create_app(s, start_scheduler=False)
    with TestClient(app) as c:
        admin = login(c)
        yield app, s, admin
    db.reset_engine()
    config.set_settings(settings)


def test_report_and_spool_completion_records_are_allowlisted_and_after_commit(app, admin, caplog):
    caplog.set_level(logging.INFO, logger="convoy.handler")
    a = enrolled_agent(app, admin, name="obs-1")
    res = heartbeat(a)
    assert res.get("live_nonce")
    recs = _records(caplog, "report")
    assert recs, caplog.text
    r = recs[-1]
    assert r["device_id"] == a.device_id and r["seq"] == a.seq and r["outcome"] == "committed"
    assert (
        isinstance(r["applied"], bool)
        and r["handler_elapsed_ms"] >= 0
        and r["started_utc"].endswith("+00:00")
    )
    assert set(r) <= {
        "handler",
        "device_id",
        "seq",
        "applied",
        "outcome",
        "started_utc",
        "handler_elapsed_ms",
    }
    # the record reflects the committed state: the device row already carries this seq
    from convoy_server.models import Device

    with session_scope() as s:
        assert s.get(Device, a.device_id).last_report_seq == a.seq
    # spool
    assert (
        spool(
            a, "telemetry", "log", {"ts": time.time(), "level": "info", "source": "t", "message": "hi"}
        ).status_code
        == 200
    )
    sp = _records(caplog, "spool")[-1]
    assert sp["device_id"] == a.device_id and sp["lane"] == "telemetry" and sp["records"] == 1
    assert (
        sp["accepted"] == 1
        and sp["committed_seq"] == 1
        and sp["deferred"] == 0
        and sp["outcome"] == "committed"
    )
    # failure path: status + error class only, never the body
    bad = a.client.post(
        "/api/agent/v1/spool",
        json={"lane": "nope", "records": [{"seq": 9, "kind": "log", "body": {"secret": "cvd_leak"}}]},
    )
    assert bad.status_code == 422
    fail = _records(caplog, "spool")[-1]
    assert fail["outcome"] == "failed" and fail["status"] == 422 and fail["error_class"] == "HTTPException"
    assert "cvd_leak" not in caplog.text
    # redaction: nothing secret-shaped from the requests reaches the handler log
    handler_text = "\n".join(r.getMessage() for r in caplog.records if r.name == "convoy.handler")
    for marker in SECRET_MARKERS:
        assert marker not in handler_text, marker
    assert a.secret not in handler_text and "obs-1" not in handler_text


@pytest.mark.timeout(120)
def test_a_real_report_commits_while_a_wal_backup_snapshot_is_held_and_markers_share_the_attempt(
    wal_stack, caplog
):
    app, s, admin = wal_stack
    caplog.set_level(logging.INFO)
    live = Path(db.get_engine().url.database)
    a = enrolled_agent(app, admin, name="obs-wal")
    heartbeat(a)
    w = Worker("w")
    with session_scope() as sess:
        assert w.acquire(sess)
    paused = threading.Event()
    resume = threading.Event()
    first = {"v": True}

    def pause_once(status, remaining, total):
        if first["v"]:
            first["v"] = False
            paused.set()
            resume.wait(30)

    out: dict = {}

    def run_backup():
        with session_scope() as sess:
            out["r"] = bk.take_backup(
                sess, str(s.backup_dir / "held.db") if s.backup_dir else str(live.parent / "backups" / "held.db"),
                worker=w, budget_s=60.0, snapshot_s=30.0, _step_hook=pause_once,
            )  # fmt: skip

    th = threading.Thread(target=run_backup, daemon=True)
    th.start()
    assert paused.wait(10)
    try:
        # the snapshot is pinned NOW; an independent device report must commit promptly
        t0 = time.monotonic()
        res = heartbeat(a)
        took = time.monotonic() - t0
        assert res.get("live_nonce") and took < 2.0, took
        rec = _records(caplog, "report")[-1]
        assert rec["device_id"] == a.device_id and rec["outcome"] == "committed" and rec["applied"] is True
        assert rec["handler_elapsed_ms"] < 2000
    finally:
        resume.set()
        th.join(30)
    r = out["r"]
    attempt = r["attempt"]
    text = caplog.text
    assert f"backup {attempt}: snapshot acquired" in text and "source_mode=wal" in text
    assert f"backup {attempt}: snapshot released" in text
    assert f"backup {attempt}: success id={r['id']}" in text
    assert f"backup {attempt}: post-copy PASSIVE checkpoint" in text
    # released before hash: the release marker precedes the success marker, and the held time is the
    # copy-phase hold only
    assert text.index("snapshot released") < text.index("success id=")
    assert r["source_lock_held_s"] is not None
    # the report that committed during the pin is not in the backup (snapshot), but is in the live db
    chk = sqlite3.connect(f"file:{r['path']}?mode=ro", uri=True)
    try:
        assert chk.execute("SELECT count(*) FROM reports").fetchone()[0] == 1
    finally:
        chk.close()
    with session_scope() as sess:
        from convoy_server.models import Report

        assert sess.query(Report).count() == 2
