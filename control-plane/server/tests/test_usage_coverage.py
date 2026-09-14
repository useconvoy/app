"""Usage ingestion keyed on the `schema` discriminator: unversioned bodies never populate the measured
populations, and the explicit unobserved intervals schema-2 recovery records declare are stored durably at
ingestion and exposed in `/usage.coverage.unknown_intervals` (never inferred from daily sums)."""

from __future__ import annotations

import sqlite3
import time

import pytest
from helpers import enrolled_agent

DAY_START = 1767225600  # 2026-01-01T00:00:00Z


def _spool(a, records, loss_ranges=None):
    r = a.client.post(
        "/api/agent/v1/spool", json={"lane": "usage", "records": records, "loss_ranges": loss_ranges or []}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _usage(admin, **params):
    r = admin.get("/api/v1/usage", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_unversioned_records_contribute_only_counters_and_mixed_renames(app, admin):
    """An unversioned body carrying schema-2-only names (measured minutes, contacts, unknown coverage) must
    not land in the measured populations; its counters are still valid and are stored."""
    a = enrolled_agent(app, admin)
    ts = time.time()
    unversioned = {
        "ts": ts,
        "inference_requests": 1,
        "tokens_in": 5,
        "tokens_out": 7,
        "download_bytes": 11,
        "eval_runs": 2,
        "inference_minutes": 40.0,
        "contact_minutes": 30.0,
        "runtime_up_minutes": 3.0,
        "agent_up_minutes": 4.0,
        "contacts": 9,
        "reconnects": 8,
        "unknown_coverage_s": 99.0,
        "unknown_interval": {"from_ts": DAY_START, "to_ts": DAY_START + 99, "reason": "x"},
        "active_minutes": 2.0,
        "online_minutes": 6.0,
        "connected_minutes": 5.0,  # alias of online_minutes in the unversioned population: not summed
    }
    versioned = {"schema": 2, "ts": ts, "inference_minutes": 0.5, "contact_minutes": 1.0}
    out = _spool(
        a,
        [{"seq": 1, "kind": "usage", "body": unversioned}, {"seq": 2, "kind": "usage", "body": versioned}],
    )
    assert out["accepted"] == 2 and out["committed_seq"] == 2, out
    u = _usage(admin)
    t = u["totals"]
    assert t["inference_minutes"] == 0.5 and t["contact_minutes"] == 1.0
    for dropped in ("runtime_up_minutes", "agent_up_minutes", "contacts", "reconnects", "unknown_coverage_s"):
        assert dropped not in t, (dropped, t)
    assert t["inference_requests"] == 1 and t["tokens_in"] == 5 and t["tokens_out"] == 7
    assert t["download_bytes"] == 11 and t["eval_runs"] == 2
    assert t["mixed_active_minutes"] == 2.0 and t["mixed_online_minutes"] == 6.0
    for absent in ("active_minutes", "online_minutes", "connected_minutes"):
        assert absent not in t
    assert not [
        k for k in t if k.startswith("mixed_") and k not in ("mixed_active_minutes", "mixed_online_minutes")
    ]
    assert u["coverage"]["unknown_intervals"] == []  # an unversioned record never stores an interval
    assert not [x for x in u["coverage"]["loss_ranges"] if x["device_id"] == a.device_id]


def test_unknown_intervals_are_stored_at_ingestion_and_exposed_in_coverage(app, admin):
    a = enrolled_agent(app, admin)
    rec = {
        "schema": 2,
        "ts": DAY_START + 10,
        "inference_requests": 0,
        "unknown_coverage_s": 10,
        "unknown_interval": {
            "from_ts": DAY_START,
            "to_ts": DAY_START + 10,
            "reason": "restart_before_durable_checkpoint",
        },
        "previous_incarnation": "inc-old",
    }
    out = _spool(a, [{"seq": 1, "kind": "usage", "body": rec}])
    assert out["accepted"] == 1 and out["committed_seq"] == 1, out

    def check(u):
        cov = u["coverage"]
        assert cov["loss_ranges"] == [] and u["totals"]["unknown_coverage_s"] == 10.0
        assert len(cov["unknown_intervals"]) == 1, cov
        iv = cov["unknown_intervals"][0]
        assert iv["from_ts"] == "2026-01-01T00:00:00Z" and iv["to_ts"] == "2026-01-01T00:00:10Z"
        assert iv["seconds"] == 10.0 and iv["reason"] == "restart_before_durable_checkpoint"
        assert iv["previous_incarnation"] == "inc-old"
        assert iv["device_id"] == a.device_id and iv["name"] == "sim-1" and iv["simulated"] is True
        assert isinstance(iv["id"], int) and iv["received_at"].endswith("Z")
        return iv

    first = check(_usage(admin, **{"from": "2025-12-31", "to": "2026-01-02"}))
    # a later spool round trip (the agent trims its journal on the ACK) changes nothing server-side
    assert _spool(a, [])["committed_seq"] == 1
    assert check(_usage(admin, **{"from": "2025-12-31", "to": "2026-01-02"})) == first
    # the interval is selected by overlap with the requested UTC days, not by the record's day
    assert _usage(admin, **{"from": "2026-01-02", "to": "2026-01-03"})["coverage"]["unknown_intervals"] == []
    assert (
        len(_usage(admin, **{"from": "2026-01-01", "to": "2026-01-01"})["coverage"]["unknown_intervals"]) == 1
    )

    # an inverted interval disposes the record and stores nothing (no sum, no interval, no counters)
    bad = {
        **rec,
        "inference_requests": 5,
        "unknown_interval": {**rec["unknown_interval"], "from_ts": DAY_START + 10, "to_ts": DAY_START},
    }
    out = _spool(a, [{"seq": 2, "kind": "usage", "body": bad}])
    assert out["accepted"] == 0 and out["committed_seq"] == 2, out
    assert "interval" in out["rejected"][0]["reason"]
    u = _usage(admin, **{"from": "2025-12-31", "to": "2026-01-02"})
    assert len(u["coverage"]["unknown_intervals"]) == 1 and u["totals"]["unknown_coverage_s"] == 10.0
    assert "inference_requests" not in u["totals"] or u["totals"]["inference_requests"] == 0.0
    mine = [x for x in u["coverage"]["loss_ranges"] if x["device_id"] == a.device_id]
    assert len(mine) == 1 and mine[0]["reason"].startswith("rejected:usage unknown_interval")
    assert (mine[0]["from_seq"], mine[0]["to_seq"]) == (2, 2)
    # a span that disagrees with the daily sum, a non-finite bound and a non-object are refused likewise
    for i, wrong in enumerate(
        (
            {**rec, "unknown_coverage_s": 30},
            {**rec, "unknown_interval": {**rec["unknown_interval"], "to_ts": "soon"}},
            {**rec, "unknown_interval": [DAY_START, DAY_START + 10]},
        )
    ):
        out = _spool(a, [{"seq": 3 + i, "kind": "usage", "body": wrong}])
        assert out["accepted"] == 0 and "interval" in out["rejected"][0]["reason"], out
    # an unversioned record carrying an interval stores no interval; a schema-2 record with a daily sum
    # but no interval stores only the sum (nothing is invented from totals)
    unversioned = {"ts": DAY_START + 20, "inference_requests": 1, "unknown_coverage_s": 5, "unknown_interval": rec["unknown_interval"]}  # fmt: skip
    sum_only = {"schema": 2, "ts": DAY_START + 30, "unknown_coverage_s": 7.0}
    out = _spool(
        a, [{"seq": 6, "kind": "usage", "body": unversioned}, {"seq": 7, "kind": "usage", "body": sum_only}]
    )
    assert out["accepted"] == 2, out
    u = _usage(admin, **{"from": "2025-12-31", "to": "2026-01-02"})
    assert len(u["coverage"]["unknown_intervals"]) == 1 and u["totals"]["unknown_coverage_s"] == 17.0
    # a producer that omits the reason gets the explicit "unobserved" label, never a guessed one
    plain = {"schema": 2, "ts": DAY_START + 60, "unknown_coverage_s": 2.5, "unknown_interval": {"from_ts": DAY_START + 57.5, "to_ts": DAY_START + 60}}  # fmt: skip
    assert _spool(a, [{"seq": 8, "kind": "usage", "body": plain}])["accepted"] == 1
    ivs = _usage(admin, **{"from": "2026-01-01", "to": "2026-01-01"})["coverage"]["unknown_intervals"]
    # newest first (from_ts DESC): the bounded listing keeps the latest intervals when it truncates
    assert [(x["reason"], x["seconds"], x["previous_incarnation"]) for x in ivs] == [
        ("unobserved", 2.5, None),
        ("restart_before_durable_checkpoint", 10.0, "inc-old"),
    ]


def test_existing_v3_database_gains_the_unknown_interval_table_at_startup(app, admin, settings):
    """Adding the table is ADDITIVE under schema version 3: a v3 database without it is completed at
    startup by `ensure_schema`, and `convoy-server migrate` afterwards reports nothing to do."""
    from convoy_server import migrations
    from convoy_server.db import init_engine, make_engine, reset_engine

    a = enrolled_agent(app, admin)
    assert (
        _spool(a, [{"seq": 1, "kind": "usage", "body": {"ts": time.time(), "inference_requests": 1}}])[
            "committed_seq"
        ]
        == 1
    )
    reset_engine()
    live = settings.data_dir / "convoy.db"
    con = sqlite3.connect(str(live))
    con.execute("DROP TABLE usage_unknown_intervals")
    con.commit()
    assert con.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION == 3
    con.close()

    engine = make_engine(settings)
    plan = migrations.plan_migration(engine)
    assert plan["add_tables"] == ["usage_unknown_intervals"] and not plan["needs_rebuild"]
    assert not plan["needs_data_migration"] and not plan["empty"]
    out = migrations.ensure_schema(engine)  # startup completes the schema without a rebuild
    assert out["add_tables"] == ["usage_unknown_intervals"] and out["user_version"] == 3, out
    with engine.connect() as c:
        idx = [r[1] for r in c.execute(migrations.text("PRAGMA index_list(usage_unknown_intervals)"))]
        assert "ix_usage_unknown_intervals_device_id" in idx  # created with the table
        cols = [r[1] for r in c.execute(migrations.text("PRAGMA table_info(usage_unknown_intervals)"))]
        assert {"id", "device_id", "from_ts", "to_ts", "seconds", "reason", "previous_incarnation", "record_seq", "simulated", "received_at"} <= set(cols)  # fmt: skip
    engine.dispose()
    again = migrations.migrate_database(settings)
    assert again["ok"] and again["applied"] == {"unchanged": True, "user_version": 3}, again
    assert not list(settings.data_dir.glob("convoy.pre-migrate-*.db"))

    # the services come back on the completed database and ingest an interval into the new table
    init_engine(settings)
    rec = {"schema": 2, "ts": DAY_START + 10, "unknown_coverage_s": 10, "unknown_interval": {"from_ts": DAY_START, "to_ts": DAY_START + 10}}  # fmt: skip
    assert _spool(a, [{"seq": 2, "kind": "usage", "body": rec}])["accepted"] == 1
    ivs = _usage(admin, **{"from": "2026-01-01", "to": "2026-01-01"})["coverage"]["unknown_intervals"]
    assert len(ivs) == 1 and ivs[0]["seconds"] == 10.0 and ivs[0]["reason"] == "unobserved"


@pytest.mark.parametrize("schema", [None, 2])
def test_usage_totals_stay_exact_across_replay(app, admin, schema):
    """A replayed batch (lost ACK) never recounts either population or duplicates an interval."""
    a = enrolled_agent(app, admin)
    body = {"ts": DAY_START + 10, "inference_requests": 1}
    if schema == 2:
        body.update(
            schema=2,
            unknown_coverage_s=10,
            unknown_interval={"from_ts": DAY_START, "to_ts": DAY_START + 10},
        )
    recs = [{"seq": 1, "kind": "usage", "body": body}]
    assert _spool(a, recs)["accepted"] == 1
    assert _spool(a, recs)["accepted"] == 0
    u = _usage(admin, **{"from": "2026-01-01", "to": "2026-01-01"})
    assert u["totals"]["inference_requests"] == 1.0
    assert len(u["coverage"]["unknown_intervals"]) == (1 if schema == 2 else 0)


def _interval_record(i: int) -> dict:
    """A valid schema-2 usage record declaring its own 10 s unknown interval; distinct from_ts per i."""
    start = DAY_START + i * 100
    return {
        "schema": 2,
        "ts": start + 10,
        "unknown_coverage_s": 10,
        "unknown_interval": {"from_ts": start, "to_ts": start + 10, "reason": f"r{i}"},
    }


def _ingest_intervals(a, n: int, chunk: int = 100) -> None:
    from convoy_server.services import evidence as ev

    assert chunk <= ev.MAX_RECORDS
    seq = 1
    for i in range(0, n, chunk):
        recs = [
            {"seq": seq + k, "kind": "usage", "body": _interval_record(i + k)}
            for k in range(min(chunk, n - i))
        ]
        out = _spool(a, recs)
        assert out["accepted"] == len(recs) and out["committed_seq"] == seq + len(recs) - 1, out
        seq += len(recs)


def test_unknown_intervals_listing_is_bounded_to_the_newest_rows(app, admin, settings):
    """More overlapping intervals than the bound: `/usage` returns only the newest UNKNOWN_INTERVALS_LIMIT
    (from_ts DESC, id DESC) with the total counted, flags the truncation, and keeps every audit row."""
    from convoy_server.services import evidence as ev

    assert ev.UNKNOWN_INTERVALS_LIMIT == 200
    n = ev.UNKNOWN_INTERVALS_LIMIT + 5
    a = enrolled_agent(app, admin)
    _ingest_intervals(a, n)

    cov = _usage(admin, **{"from": "2026-01-01", "to": "2026-01-01"})["coverage"]
    ivs = cov["unknown_intervals"]
    assert len(ivs) == 200
    assert cov["unknown_intervals_total"] == 205
    assert cov["unknown_intervals_limit"] == 200
    assert cov["unknown_intervals_truncated"] is True
    reasons = [x["reason"] for x in ivs]
    assert reasons[0] == "r204"  # newest first
    assert reasons == [f"r{i}" for i in range(204, 4, -1)]  # strictly descending by from_ts
    assert set(reasons) == {f"r{i}" for i in range(5, 205)}  # the 5 oldest (r0..r4) are the ones omitted
    assert all(x["from_ts"] >= y["from_ts"] for x, y in zip(ivs, ivs[1:], strict=False))
    assert all(
        x["id"] > y["id"] for x, y in zip(ivs, ivs[1:], strict=False)
    )  # ids were assigned in from_ts order
    assert set(ivs[0]) == {"id", "device_id", "name", "simulated", "from_ts", "to_ts", "seconds", "reason", "previous_incarnation", "received_at"}  # fmt: skip
    # every row is still in the table: the bound is on the response, not on retention
    con = sqlite3.connect(f"file:{settings.data_dir / 'convoy.db'}?mode=ro", uri=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM usage_unknown_intervals").fetchone()[0] == 205
    finally:
        con.close()
    # a range that overlaps none of them counts none
    empty = _usage(admin, **{"from": "2026-01-03", "to": "2026-01-04"})["coverage"]
    assert empty["unknown_intervals"] == [] and empty["unknown_intervals_total"] == 0
    assert empty["unknown_intervals_truncated"] is False


def test_unknown_intervals_at_exactly_the_limit_are_not_truncated(app, admin, monkeypatch):
    from convoy_server.services import evidence as ev

    monkeypatch.setattr(ev, "UNKNOWN_INTERVALS_LIMIT", 3)
    a = enrolled_agent(app, admin)
    _ingest_intervals(a, 3)
    cov = _usage(admin, **{"from": "2026-01-01", "to": "2026-01-01"})["coverage"]
    assert [x["reason"] for x in cov["unknown_intervals"]] == ["r2", "r1", "r0"]
    assert cov["unknown_intervals_total"] == 3 and cov["unknown_intervals_limit"] == 3
    assert cov["unknown_intervals_truncated"] is False
    # one more row crosses the bound: the oldest drops out of the listing, the total still counts it
    assert _spool(a, [{"seq": 4, "kind": "usage", "body": _interval_record(3)}])["accepted"] == 1
    cov = _usage(admin, **{"from": "2026-01-01", "to": "2026-01-01"})["coverage"]
    assert [x["reason"] for x in cov["unknown_intervals"]] == ["r3", "r2", "r1"]
    assert cov["unknown_intervals_total"] == 4 and cov["unknown_intervals_truncated"] is True


def _device_logs(admin, device_id):
    r = admin.get(f"/api/v1/devices/{device_id}/logs")
    assert r.status_code == 200, r.text
    return [e for e in r.json() if e["source"] == "usage"]


def _log_rows(settings):
    con = sqlite3.connect(f"file:{settings.data_dir / 'convoy.db'}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT level, source, attrs FROM log_entries WHERE source='usage' ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def test_checkpoint_lapse_on_a_minute_record_is_logged_not_counted(app, admin, settings):
    """(a) An ordinary schema-2 record reporting a recovered checkpoint-persistence lapse is accepted;
    the lapse becomes one warning LogEntry in the same transaction and nothing else: no loss range, no
    unknown interval, no usage metric (a recovered storage lapse does not establish lost requests)."""
    a = enrolled_agent(app, admin)
    anchor = DAY_START + 30.5
    rec = {
        "schema": 2,
        "ts": DAY_START + 60,
        "inference_requests": 3,
        "unknown_coverage_s": 0,
        "checkpoint_failures": 2,
        "checkpoint_failures_since_wall": anchor,
    }
    out = _spool(a, [{"seq": 1, "kind": "usage", "body": rec}])
    assert out["accepted"] == 1 and out["committed_seq"] == 1 and out["rejected"] == [], out

    logs = _device_logs(admin, a.device_id)
    assert len(logs) == 1, logs
    e = logs[0]
    assert e["level"] == "warning" and e["ts"] == "2026-01-01T00:01:00Z"
    assert e["operation_id"] is None and e["trace_id"] is None
    assert e["message"] == (
        "usage checkpoint persistence lapsed on the device: 2 failed checkpoint writes since "
        "2026-01-01T00:00:30.500000Z (later recovered)"
    )
    assert e["attrs"] == {
        "lane": "usage",
        "record_seq": 1,
        "checkpoint_failures": 2,
        "since_wall": anchor,
        "scope": "current",
        "previous_incarnation": None,
        "schema": 2,
    }
    rows = _log_rows(settings)
    assert len(rows) == 1 and rows[0][0] == "warning" and '"checkpoint_failures": 2' in rows[0][2]

    u = _usage(admin, **{"from": "2026-01-01", "to": "2026-01-01"})
    assert u["coverage"]["unknown_intervals"] == [] and u["coverage"]["unknown_intervals_total"] == 0
    assert not [x for x in u["coverage"]["loss_ranges"] if x["device_id"] == a.device_id]
    assert u["totals"]["inference_requests"] == 3.0 and u["totals"]["unknown_coverage_s"] == 0.0
    assert not [k for k in u["totals"] if "checkpoint" in k]  # never a metric

    # a null anchor is allowed ("since unknown"); a zero count logs nothing; ill-formed values dispose
    # the record without storing anything from it
    more = [
        {"seq": 2, "kind": "usage", "body": {**rec, "ts": DAY_START + 120, "checkpoint_failures_since_wall": None}},
        {"seq": 3, "kind": "usage", "body": {**rec, "ts": DAY_START + 180, "checkpoint_failures": 0}},
    ]  # fmt: skip
    assert _spool(a, more)["accepted"] == 2
    logs = _device_logs(admin, a.device_id)
    assert len(logs) == 2 and logs[1]["attrs"]["since_wall"] is None and "since unknown" in logs[1]["message"]
    for i, bad in enumerate(
        (
            {**rec, "checkpoint_failures": -1},
            {**rec, "checkpoint_failures": 1.5},
            {**rec, "checkpoint_failures": True},
            {**rec, "checkpoint_failures": "2"},
            {**rec, "checkpoint_failures_since_wall": "soon"},
        )
    ):
        out = _spool(a, [{"seq": 4 + i, "kind": "usage", "body": {**bad, "inference_requests": 100}}])
        assert out["accepted"] == 0 and "checkpoint_failures" in out["rejected"][0]["reason"], out
    assert len(_device_logs(admin, a.device_id)) == 2
    assert _usage(admin, **{"from": "2026-01-01", "to": "2026-01-01"})["totals"]["inference_requests"] == 9.0


def test_previous_incarnation_lapse_and_unknown_interval_are_independent_facts(app, admin):
    """(b) A restart-recovery record carries both the previous incarnation's checkpoint lapse (logged)
    and a declared unknown interval (stored); neither is derived from the other."""
    a = enrolled_agent(app, admin)
    rec = {
        "schema": 2,
        "ts": DAY_START + 10,
        "unknown_coverage_s": 10,
        "unknown_interval": {"from_ts": DAY_START, "to_ts": DAY_START + 10, "reason": "restart"},
        "previous_incarnation": "inc-old",
        "previous_checkpoint_failures": 2,
        "previous_undurable_since_wall": DAY_START - 40,
    }
    assert _spool(a, [{"seq": 1, "kind": "usage", "body": rec}])["accepted"] == 1
    logs = _device_logs(admin, a.device_id)
    assert len(logs) == 1
    assert logs[0]["message"] == (
        "usage checkpoint persistence lapsed on the device: 2 failed checkpoint writes since "
        "2025-12-31T23:59:20Z reported by restart recovery of incarnation inc-old"
    )
    assert logs[0]["attrs"] == {
        "lane": "usage",
        "record_seq": 1,
        "checkpoint_failures": 2,
        "since_wall": DAY_START - 40,
        "scope": "previous_incarnation",
        "previous_incarnation": "inc-old",
        "schema": 2,
    }
    cov = _usage(admin, **{"from": "2026-01-01", "to": "2026-01-01"})["coverage"]
    assert cov["unknown_intervals_total"] == 1 and cov["unknown_intervals"][0]["seconds"] == 10.0
    assert cov["unknown_intervals"][0]["previous_incarnation"] == "inc-old"
    assert not [x for x in cov["loss_ranges"] if x["device_id"] == a.device_id]


def test_checkpoint_lapse_log_is_not_duplicated_by_replay(app, admin, settings):
    """(c) A lost-ACK replay of the same seq is skipped as already committed: no second LogEntry."""
    a = enrolled_agent(app, admin)
    rec = {"schema": 2, "ts": DAY_START + 60, "checkpoint_failures": 1, "checkpoint_failures_since_wall": DAY_START}  # fmt: skip
    recs = [{"seq": 1, "kind": "usage", "body": rec}]
    assert _spool(a, recs)["accepted"] == 1
    assert _spool(a, recs)["accepted"] == 0
    assert (
        _spool(a, recs + [{"seq": 2, "kind": "usage", "body": {**rec, "ts": DAY_START + 120}}])["accepted"]
        == 1
    )
    assert [e["attrs"]["record_seq"] for e in _device_logs(admin, a.device_id)] == [1, 2]
    assert len(_log_rows(settings)) == 2


def test_checkpoint_lapse_log_rolls_back_with_the_cursor(app, admin, settings, monkeypatch):
    """(d) The LogEntry is written in the ingest transaction: a commit failure rolls it back together
    with the cursor advance, so the device re-sends and nothing is stored twice or half-stored."""
    from sqlalchemy.orm import Session as SaSession

    a = enrolled_agent(app, admin)
    rec = {"schema": 2, "ts": DAY_START + 60, "inference_requests": 1, "checkpoint_failures": 1, "checkpoint_failures_since_wall": DAY_START}  # fmt: skip
    recs = [{"seq": 1, "kind": "usage", "body": rec}]

    def boom(self):
        raise RuntimeError("injected commit failure")

    with monkeypatch.context() as m:
        m.setattr(SaSession, "commit", boom)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            a.client.post("/api/agent/v1/spool", json={"lane": "usage", "records": recs, "loss_ranges": []})
    assert _log_rows(settings) == [] and _device_logs(admin, a.device_id) == []
    con = sqlite3.connect(f"file:{settings.data_dir / 'convoy.db'}?mode=ro", uri=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM usage_daily").fetchone()[0] == 0
        cur = con.execute("SELECT committed_seq FROM lane_cursors WHERE lane='usage'").fetchall()
        assert cur in ([], [(0,)]), cur
    finally:
        con.close()
    # the re-send after the failed ACK commits exactly once
    assert _spool(a, recs)["committed_seq"] == 1
    assert len(_log_rows(settings)) == 1 and len(_device_logs(admin, a.device_id)) == 1


def test_legacy_checkpoint_recovery_record_is_classified_like_legacy_ingress(app, admin):
    """The record the agent emits when recovering a PRE-REWORK checkpoint (built by the agent's own
    `legacy_checkpoint_recovery_record` from the reviewer's probe shape) lands with its integer deltas
    in the counters, its residual durations under the MIXED names, its unknown interval stored, and
    nothing in the measured populations; the same mixed names without legacy provenance are refused."""
    from convoy_agent.agent import legacy_checkpoint_recovery_record

    a = enrolled_agent(app, admin)
    now = time.time()
    old = {
        "boot_id": "boot-legacy",
        "wall": now - 40.0,
        "monotonic": 12345.0,
        "cum": {
            "requests": 12,
            "tokens_in": 120,
            "tokens_out": 24,
            "inference_s": 60.0,
            "runtime_up_s": 120.0,
            "agent_up_s": 180.0,
            "connected_s": 35.0,
        },
        "emitted": {"requests": 5, "tokens_in": 50, "tokens_out": 10},
        "requests": 12,
    }
    rec = legacy_checkpoint_recovery_record(old, now)
    r = a.client.post(
        "/api/agent/v1/spool", json={"lane": "usage", "records": [{"seq": 1, "kind": "usage", "body": rec}]}
    )
    assert r.status_code == 200 and r.json()["accepted"] == 1, r.text
    out = admin.get("/api/v1/usage").json()
    t = out["totals"]
    assert (t["inference_requests"], t["tokens_in"], t["tokens_out"]) == (7, 70, 14)
    assert t["mixed_active_minutes"] == 1.0 and t["mixed_online_minutes"] == 0.5833
    for measured in ("inference_minutes", "contact_minutes", "runtime_up_minutes", "agent_up_minutes"):
        assert measured not in t
    assert abs(t["unknown_coverage_s"] - 40.0) < 1.0
    iv = out["coverage"]["unknown_intervals"]
    assert (
        len(iv) == 1
        and iv[0]["reason"] == "restart_before_durable_checkpoint"
        and iv[0]["seconds"] == rec["unknown_coverage_s"]
    )
    # provenance is mandatory: mixed names under schema 2 without the legacy flag are refused, not stored
    bad = {**rec, "legacy_checkpoint": False}
    r = a.client.post(
        "/api/agent/v1/spool", json={"lane": "usage", "records": [{"seq": 2, "kind": "usage", "body": bad}]}
    )
    assert (
        r.status_code == 200
        and r.json()["accepted"] == 0
        and "provenance" in r.json()["rejected"][0]["reason"]
    )
    assert admin.get("/api/v1/usage").json()["totals"] == t
