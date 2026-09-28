"""Checks for execution authority, durable recovery, and real cancellation races."""

import copy
import json
import sqlite3
import threading
import time

import pytest
from convoy_agent.coordinator import Coordinator, ExecutionJournal, StepResult
from convoy_agent.coordinator.engine import DecisionExpired, ExecutionUnknown
from convoy_agent.coordinator.transport import RemoteError
from convoy_contracts.execution import PROFILE, canonical_digest


def manifest():
    return {"schema_version": 1, "profile": PROFILE,
            "policy": {"runtime": "scripted-test", "artifact_sha256": "a" * 64},
            "environment": {"name": "pick-place-v3", "metaworld": "3.1.1", "mujoco": "3.3.0"},
            "execution": {"max_steps": 2, "decision_timeout_ms": 2000, "mission_timeout_s": 30}}


class Control:
    def __init__(self):
        release = {"id": "release", "manifest": manifest(), "digest": canonical_digest(manifest())}
        self.desired = {"robot": {"id": "robot", "device_id": "device", "profile": PROFILE},
                        "deployment": {"id": "deployment", "generation": 1, "release": release, "state": "ready"},
                        "mission": None}
        self.reports = []
        self.deployment_reports = []
        self.fail_terminal = False

    def request_mission(self):
        self.desired["mission"] = {"id": "mission", "deployment_id": "deployment", "generation": 1,
                                   "release_digest": canonical_digest(manifest()), "state": "requested",
                                   "seed": 0, "expires_at": time.time() + 30}
        return self.desired["mission"]

    def get(self, path):
        return copy.deepcopy(self.desired)

    def post(self, path, body):
        if path.endswith("/claim"):
            identity = {**body, "device_id": "device", "robot_id": "robot", "mission_id": "mission",
                        "release_digest": canonical_digest(manifest())}
            self.desired["mission"]["state"] = "starting"
            return {"identity": identity, "grant": "test-grant"}
        if "/missions/" in path:
            if body["state"] != "running" and self.fail_terminal:
                raise OSError("offline")
            self.reports.append(copy.deepcopy(body))
            self.desired["mission"]["state"] = body["state"]
            return {"mission": copy.deepcopy(self.desired["mission"])}
        elif "/deployments/" in path:
            self.deployment_reports.append(copy.deepcopy(body))
        return {}


class Worker:
    def probe(self, digest, profile):
        return {"release_digest": digest, "profile": profile}

    def decide(self, request, grant):
        return {**{k: request[k] for k in ("identity", "request_id", "observation_id", "sequence",
                                          "deadline_monotonic_ns")},
                "action": [0.0] * 4, "policy_duration_ms": 0.1}


class Adapter:
    control_period_s = 0.0125

    def __init__(self, journal):
        self.journal = journal
        self.steps = 0
        self.closed = False

    def reset(self, seed):
        return [0.0] * 39

    def step(self, action, command_id):
        row = self.journal.db.execute("SELECT state FROM commands WHERE request_id=?", (command_id,)).fetchone()
        assert row[0] == "intended"  # committed intent exists before any physical effect
        self.steps += 1
        return StepResult([0.0] * 39, 1.0, True)

    def close(self):
        self.closed = True


@pytest.fixture
def setup(tmp_path):
    journal = ExecutionJournal(tmp_path, "robot", "device")
    control, worker = Control(), Worker()
    adapter = Adapter(journal)
    engine = Coordinator(robot_id="robot", device_id="device", journal=journal, control=control,
                         worker=worker, adapter_factory=lambda: adapter, poll_s=0.01)
    yield engine, journal, control, worker, adapter
    journal.close()


def test_ready_is_idle_then_mission_executes_once_and_terminal_retry_never_replays(setup):
    engine, journal, control, worker, adapter = setup
    engine.tick()
    assert adapter.steps == 0
    control.request_mission()
    control.fail_terminal = True
    with pytest.raises(OSError):
        engine.tick()
    assert adapter.steps == 2 and adapter.closed
    assert journal.get("mission")["state"] == "completed"
    assert len(journal.pending()) == 1
    control.fail_terminal = False
    engine.tick()
    engine.tick()
    assert not journal.pending() and adapter.steps == 2
    assert control.reports[-1]["summary"]["steps"] == 2


def test_retained_completed_mission_does_not_block_next_deployment(setup):
    engine, journal, control, worker, adapter = setup
    engine.tick()
    control.request_mission()
    engine.tick()
    control.desired["deployment"]["generation"] = 2
    control.desired["deployment"]["id"] = "next-deployment"
    engine.tick()
    assert control.deployment_reports[-1]["generation"] == 2
    assert control.deployment_reports[-1]["state"] == "ready"
    assert adapter.steps == 2


def test_cancellation_between_desired_and_claim_acks_without_authority_or_actions(setup):
    engine, journal, control, worker, adapter = setup
    engine.tick()
    control.request_mission()
    original = control.post

    def cancel_before_claim(path, body):
        if path.endswith("/claim"):
            control.desired["mission"].update({"state": "cancel_requested", "identity": None})
            raise RemoteError(409)
        return original(path, body)

    control.post = cancel_before_claim
    engine.tick()
    assert adapter.steps == 0
    assert control.reports[-1]["state"] == "cancelled"
    assert control.reports[-1]["identity"] is None
    assert not journal.pending()


def test_recovered_unclaimed_cancellation_allows_a_later_deployment(setup):
    engine, journal, control, worker, adapter = setup
    mission = control.request_mission()
    identity = journal.identity(mission, "device", "robot")
    journal.prepare("mission", identity)
    journal.recover()
    control.desired["mission"].update({"state": "cancel_requested", "identity": None})
    original = control.post

    def check_admission(path, body):
        if "/missions/" in path and body.get("identity") is not None:
            raise RemoteError(409)
        return original(path, body)

    control.post = check_admission
    engine.tick()
    assert control.reports[-1]["state"] == "cancelled"
    control.desired["deployment"].update({"generation": 2, "id": "next-deployment"})
    engine.tick()
    assert control.deployment_reports[-1]["generation"] == 2
    assert adapter.steps == 0


def test_claim_conflict_with_failed_reconciliation_still_persists_unknown(setup):
    engine, journal, control, worker, adapter = setup
    engine.tick()
    control.request_mission()
    original_get = control.get

    def unavailable(path):
        raise OSError("offline")

    def conflict(path, body):
        if path.endswith("/claim"):
            control.get = unavailable
            raise RemoteError(409)
        raise OSError("offline")

    control.post = conflict
    with pytest.raises(OSError):
        engine.tick()
    control.get = original_get
    row = journal.get("mission")
    assert row["state"] == "unknown" and row["report_json"] is not None
    assert adapter.steps == 0


def test_cancel_in_running_ack_prevents_even_adapter_initialization(setup):
    engine, journal, control, worker, adapter = setup
    engine.tick()
    control.request_mission()
    original = control.post

    def race_running(path, body):
        if body.get("state") == "running":
            control.desired["mission"]["state"] = "cancel_requested"
            return {"mission": copy.deepcopy(control.desired["mission"])}
        return original(path, body)

    control.post = race_running
    engine.adapter_factory = lambda: pytest.fail("must not initialize after cancellation acknowledgement")
    engine.tick()
    assert control.reports[-1]["state"] == "cancelled"
    assert adapter.steps == 0


def request_for(engine, journal, control):
    mission = control.request_mission()
    identity = journal.identity(mission, "device", "robot")
    journal.prepare("mission", identity)
    request = {"identity": identity, "request_id": "request", "observation_id": "obs", "sequence": 0,
               "deadline_monotonic_ns": time.monotonic_ns() + 1_000_000_000,
               "budget_ms": 1000, "observation": [0.0] * 39}
    engine._outstanding = request
    return request


@pytest.mark.parametrize("field", ["robot_id", "device_id", "mission_id", "boot_id", "incarnation",
                                  "release_digest", "authority_epoch"])
def test_entire_authority_identity_is_checked_before_execution(setup, field):
    engine, journal, control, worker, adapter = setup
    request = request_for(engine, journal, control)
    result = copy.deepcopy(worker.decide(request, "grant"))
    result["identity"][field] = 999 if field == "authority_epoch" else ("b" * 64 if field == "release_digest" else "other")
    with pytest.raises(ValueError):
        engine._apply(adapter, request, result)
    assert adapter.steps == 0 and journal.command_count("mission") == 0


def test_expiry_after_journal_commit_blocks_action_and_duplicate_is_not_applied(setup, monkeypatch):
    engine, journal, control, worker, adapter = setup
    request = request_for(engine, journal, control)
    result = worker.decide(request, "grant")
    original = journal.intend

    def slow_commit(req, value):
        original(req, value)
        monkeypatch.setattr("convoy_agent.coordinator.engine.time.monotonic_ns",
                            lambda: request["deadline_monotonic_ns"] + 1)

    monkeypatch.setattr(journal, "intend", slow_commit)
    with pytest.raises(DecisionExpired):
        engine._apply(adapter, request, result)
    with pytest.raises(ValueError):
        engine._apply(adapter, request, result)
    assert adapter.steps == 0
    assert journal.db.execute("SELECT state FROM commands").fetchone()[0] == "not_applied"


def test_cancel_during_inference_returns_promptly_and_late_result_cannot_apply(setup):
    engine, journal, control, worker, adapter = setup
    entered, release = threading.Event(), threading.Event()
    normal_decide = worker.decide

    def blocked(request, grant):
        entered.set()
        release.wait(2)
        return normal_decide(request, grant)

    worker.decide = blocked
    engine.tick()
    control.request_mission()

    def cancel_when_inflight():
        assert entered.wait(2)
        control.desired["mission"]["state"] = "cancel_requested"

    cancellation = threading.Thread(target=cancel_when_inflight)
    cancellation.start()
    try:
        started = time.monotonic()
        engine.tick()
        assert time.monotonic() - started < 0.75
        assert control.reports[-1]["state"] == "cancelled"
        assert adapter.steps == 0
    finally:
        release.set()
        cancellation.join()
        engine._inference_thread.join(timeout=1)
    assert adapter.steps == 0


def test_restart_fences_incarnation_and_recovers_unknown_without_replay(tmp_path):
    first = ExecutionJournal(tmp_path, "robot", "device")
    control = Control()
    mission = control.request_mission()
    identity = first.identity(mission, "device", "robot")
    first.prepare("mission", identity)
    first.mark_running("mission")
    first.close()
    second = ExecutionJournal(tmp_path, "robot", "device")
    try:
        assert second.epoch > identity["authority_epoch"]
        assert second.incarnation != identity["incarnation"]
        engine = Coordinator(robot_id="robot", device_id="device", journal=second, control=control,
                             worker=Worker(), adapter_factory=lambda: pytest.fail("must not construct adapter"))
        engine.tick()
        assert control.reports[-1]["state"] == "unknown"
        assert control.reports[-1]["identity"] == identity
        assert json.loads(second.get("mission")["report_json"])["summary"]["recovered_after_restart"]
    finally:
        second.close()


def test_single_executor_lock_and_durable_binding(tmp_path):
    first = ExecutionJournal(tmp_path, "robot", "device")
    try:
        with pytest.raises(RuntimeError, match="another coordinator"):
            ExecutionJournal(tmp_path, "robot", "device")
    finally:
        first.close()
    with pytest.raises(ValueError, match="another robot"):
        ExecutionJournal(tmp_path, "other", "device")


def test_failed_database_initialization_releases_executor_lock(tmp_path, monkeypatch):
    connect = sqlite3.connect

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk unavailable")

    monkeypatch.setattr("convoy_agent.coordinator.journal.sqlite3.connect", fail)
    with pytest.raises(sqlite3.OperationalError) as retained_exception:
        ExecutionJournal(tmp_path, "robot", "device")
    monkeypatch.setattr("convoy_agent.coordinator.journal.sqlite3.connect", connect)
    retry = ExecutionJournal(tmp_path, "robot", "device")
    retry.close()
    assert retained_exception.value  # traceback still references the failed instance


def test_adapter_failure_after_intent_is_unknown_not_retried(setup):
    engine, journal, control, worker, adapter = setup
    request = request_for(engine, journal, control)

    def ambiguous_step(action, command_id):
        adapter.steps += 1
        raise OSError("lost controller response")

    adapter.step = ambiguous_step
    with pytest.raises(ExecutionUnknown):
        engine._apply(adapter, request, worker.decide(request, "grant"))
    with pytest.raises(ValueError):
        engine._apply(adapter, request, worker.decide(request, "grant"))
    assert adapter.steps == 1
    assert journal.db.execute("SELECT state FROM commands").fetchone()[0] == "intended"
