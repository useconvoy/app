from __future__ import annotations

import sqlite3

from convoy_agent.journal import LANE_QUOTA_BYTES, Journal


def test_journal_pragmas_and_seq(tmp_path):
    j = Journal(tmp_path / "j.db")
    assert j.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert j.conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert j.next_seq() == 1 and j.next_seq() == 2
    j.close()
    j2 = Journal(tmp_path / "j.db")
    assert j2.next_seq() == 3  # persistent device-wide sequence


def test_operation_transitions_survive_reopen(tmp_path):
    j = Journal(tmp_path / "j.db")
    j.begin_operation(
        {"id": "op_1", "type": "deploy", "payload": {"target_release_id": "rel_b"}, "generation": 3}
    )
    j.set_stage("op_1", "WaitingGrant")
    j.set_stage(
        "op_1",
        "Cutover",
        grant={"grant_id": "g1"},
        grant_consumed_seq=9,
        started_monotonic=1.5,
        detail={"intent": {"target": "rel_b", "recovery": "rel_a"}},
    )
    j.close()
    j = Journal(tmp_path / "j.db")
    op = j.current_operation()
    assert (
        op["stage"] == "Cutover"
        and op["grant"]["grant_id"] == "g1"
        and op["grant_consumed_seq"] == 9
        and op["detail"]["intent"]["recovery"] == "rel_a"
    )
    j.finish_operation(
        "op_1",
        "Succeeded",
        {"status": "succeeded"},
        {"active_release_id": "rel_b", "recovery_release_id": "rel_a"},
    )
    assert j.current_operation() is None and j.get("active_release_id") == "rel_b"
    assert [o["id"] for o in j.unacked_terminal()] == ["op_1"]
    j.mark_acked("op_1")
    assert j.unacked_terminal() == []


def test_lane_cursor_never_moves_backwards_and_delete_only_after_ack(tmp_path):
    j = Journal(tmp_path / "j.db")
    for i in range(5):
        j.append("usage", "usage", {"i": i})
    recs, _ = j.pending("usage")
    assert [r["seq"] for r in recs] == [1, 2, 3, 4, 5]
    j.commit_lane("usage", 3, [])
    assert [r["seq"] for r in j.pending("usage")[0]] == [4, 5]
    j.commit_lane("usage", 1, [])  # stale ack: ignored
    assert [r["seq"] for r in j.pending("usage")[0]] == [4, 5]
    assert j.lane_status()["usage"]["committed_seq"] == 3


def test_quota_eviction_records_loss_and_critical_refuses(tmp_path, monkeypatch):
    monkeypatch.setitem(LANE_QUOTA_BYTES, "telemetry", 400)
    monkeypatch.setitem(LANE_QUOTA_BYTES, "critical", 120)
    j = Journal(tmp_path / "j.db")
    for i in range(20):
        j.append("telemetry", "telemetry", {"i": i, "pad": "x" * 20})
    recs, losses = j.pending("telemetry")
    assert losses and losses[0]["reason"] == "quota_evicted" and losses[0]["from_seq"] == 1
    assert recs[0]["seq"] > losses[-1]["to_seq"]
    assert j.append("critical", "failure", {"code": "A", "pad": "y" * 60}) == 1
    assert (
        j.append("critical", "failure", {"code": "B", "pad": "y" * 60}) is None
    )  # full: refused, loss recorded
    _, closs = j.pending("critical")
    assert closs and closs[0]["reason"] == "critical_lane_full"


def test_journal_is_a_real_sqlite_file(tmp_path):
    Journal(tmp_path / "j.db").close()
    c = sqlite3.connect(tmp_path / "j.db")
    assert {r[0] for r in c.execute("select name from sqlite_master where type='table'")} >= {
        "kv",
        "operation",
        "pins",
        "lane",
        "lane_cursor",
        "loss",
    }
