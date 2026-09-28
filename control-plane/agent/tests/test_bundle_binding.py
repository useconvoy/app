"""Local bundle staging cannot bypass execution authority or durable recovery."""

import json
import sqlite3
import threading

import pytest
from convoy_agent.coordinator import Coordinator, ExecutionJournal
from convoy_agent.coordinator.binding import BindingObservation, PreparedBinding
from convoy_contracts.execution import canonical_digest
from convoy_contracts.pairing import PAIRED_PROFILE
from test_paired_coordinator import paired  # noqa: F401 - shared protocol-faithful fixture


class Owner:
    def __init__(self, worker, planner):
        self.worker, self.planner = worker, planner
        self.binding_id = "bundle-A"
        self.calls = 0
        self.during_prepare = lambda: None
        self.failure = None

    def prepare(self, deployment, manifest):
        self.calls += 1
        self.during_prepare()
        if self.failure:
            raise self.failure
        return PreparedBinding(
            binding_id=self.binding_id, worker=self.worker, planner=self.planner,
            observation=BindingObservation(
                release_digest=deployment["release"]["digest"], profile=manifest["profile"],
                action_artifact_sha256=manifest["action_manifest"]["policy"]["artifact_sha256"],
                planner_artifact_sha256=manifest["planner"]["artifact_sha256"],
            ),
        )


@pytest.fixture
def managed(paired):  # noqa: F811 - pytest injects the imported shared fixture
    old, journal, control, worker, planner, adapter = paired
    control.desired["mission"] = None
    control.deployment_reports.clear()
    probes = []
    action_probe, planner_probe = worker.probe, planner.probe

    def action(digest, profile):
        probes.append("action")
        return {**action_probe(digest, profile), "ready": True}

    def planning(digest, profile):
        probes.append("planner")
        return planner_probe(digest, profile)

    worker.probe, planner.probe = action, planning
    owner = Owner(worker, planner)
    engine = Coordinator(robot_id="robot", device_id="device", journal=journal, control=control,
                         worker=None, planner=None, bundle_owner=owner, adapter_factory=adapter,
                         profile=PAIRED_PROFILE, poll_s=0.005)
    yield engine, owner, journal, control, worker, planner, adapter, probes
    for thread in (engine._inference_thread, engine._cleanup_thread):
        if thread:
            thread.join(timeout=2)


def request(control):
    mission = control.request_mission()
    deployment = control.desired["deployment"]
    mission.update(deployment_id=deployment["id"], generation=deployment["generation"],
                   release_digest=deployment["release"]["digest"], identity=None, execution_started=False)
    return mission


def test_observation_is_durable_before_ready_and_claim_waits_for_next_tick(managed):
    engine, owner, journal, control, worker, planner, adapter, probes = managed
    assert owner.calls == 0 and journal.observed_binding() is None
    mission = request(control)
    post = control.post

    def check_ready(path, body):
        if "/deployments/" in path and body["state"] == "ready":
            with sqlite3.connect(journal.db.execute("PRAGMA database_list").fetchone()[2]) as reader:
                saved = json.loads(reader.execute("SELECT value FROM meta WHERE key='observed_bundle'").fetchone()[0])
            assert saved["binding_id"] == owner.binding_id
            assert saved["planner_readiness"] == planner.identity
            assert saved["observation"]["release_digest"] == mission["release_digest"]
            assert set(body) == {"generation", "state", "release_digest"}
            assert engine.worker is worker and engine.planner is planner
            assert journal.get(mission["id"]) is None
        return post(path, body)

    control.post = check_ready
    engine.tick()
    assert owner.calls == 1 and probes == ["action", "planner"]
    assert adapter.steps == planner.calls == 0 and journal.get(mission["id"]) is None
    engine.tick()
    assert owner.calls == 2 and probes == ["action", "planner"] * 2
    assert adapter.steps == planner.calls == 1
    assert journal.get(mission["id"])["state"] == "completed"


@pytest.mark.parametrize("field", ["_inference_thread", "_cleanup_thread"])
@pytest.mark.parametrize("queued", [False, True])
def test_stalled_thread_blocks_preparation_even_without_a_mission(managed, field, queued):
    engine, owner, journal, control, *_ = managed
    if queued:
        request(control)
    release = threading.Event()
    thread = threading.Thread(target=lambda: release.wait(2))
    setattr(engine, field, thread)
    thread.start()
    try:
        engine.tick()
        assert owner.calls == 0 and not control.deployment_reports
        assert journal.observed_binding() is None
    finally:
        release.set()
        thread.join(timeout=2)


@pytest.mark.parametrize("change", [
    {"state": "starting"}, {"state": "running"}, {"state": "unknown"},
    {"identity": {"incarnation": "someone-else"}}, {"execution_started": True},
    {"state": "cancel_requested", "identity": {"incarnation": "someone-else"}},
    {"deployment_id": "another-deployment"},
])
def test_admitted_or_unresolved_mission_never_prepares_or_acknowledges(managed, change):
    engine, owner, journal, control, *_ = managed
    request(control).update(change)
    engine.tick()
    assert owner.calls == 0 and not control.deployment_reports and not control.reports
    assert journal.get("mission") is None and journal.observed_binding() is None


@pytest.mark.parametrize("change", ["generation", "digest", "robot", "device", "profile", "cancel", "admitted"])
def test_changes_during_preparation_cannot_bind_report_ready_or_claim(managed, change):
    engine, owner, journal, control, worker, planner, adapter, _ = managed
    request(control)

    def supersede():
        if change == "generation":
            control.desired["deployment"].update(id="next", generation=2)
        elif change == "digest":
            release = control.desired["deployment"]["release"]
            release["manifest"]["planning"]["timeout_ms"] += 1
            release["digest"] = canonical_digest(release["manifest"])
        elif change in {"robot", "device", "profile"}:
            key = {"robot": "id", "device": "device_id", "profile": "profile"}[change]
            control.desired["robot"][key] = "different"
        elif change == "cancel":
            control.desired["mission"]["state"] = "cancel_requested"
        else:
            control.desired["mission"].update(identity={"incarnation": "another-owner"}, execution_started=True)

    owner.during_prepare = supersede
    if change in {"robot", "device", "profile"}:
        with pytest.raises(ValueError):
            engine.tick()
    else:
        engine.tick()
    assert owner.calls == 1 and not control.deployment_reports and not control.reports
    assert engine.worker is None and engine.planner is None and engine._ready is None
    assert journal.observed_binding() is None and journal.get("mission") is None
    assert adapter.created == planner.calls == 0


def test_ready_a_then_failed_b_cannot_fall_back_and_changed_binding_reprobes(managed):
    engine, owner, journal, control, worker, planner, adapter, probes = managed
    engine.tick()
    first = journal.observed_binding()
    owner.binding_id = "bundle-A-restarted"
    planner.identity = {**planner.identity, "planner_incarnation": "restarted"}
    request(control)
    engine.tick()
    assert probes == ["action", "planner"] * 2
    assert len(control.deployment_reports) == 2 and adapter.steps == planner.calls == 0
    assert journal.observed_binding()["binding_id"] == "bundle-A-restarted"
    assert engine._planner_readiness["planner_incarnation"] == "restarted"

    control.desired["mission"] = None
    deployment = control.desired["deployment"]
    deployment.update(id="deployment-B", generation=2)
    deployment["release"]["manifest"]["planning"]["timeout_ms"] += 1
    deployment["release"]["digest"] = canonical_digest(deployment["release"]["manifest"])
    request(control)
    owner.failure = OSError("candidate B unavailable")
    with pytest.raises(OSError):
        engine.tick()
    assert engine._ready is None and engine._planner_readiness is None
    assert control.deployment_reports[-1]["state"] == "blocked"
    assert adapter.steps == planner.calls == 0 and probes == ["action", "planner"] * 2
    assert journal.observed_binding()["observation"] == first["observation"]  # historical evidence only
    with pytest.raises(OSError):
        engine.tick()
    assert adapter.steps == planner.calls == 0


def test_same_binding_id_cannot_hide_a_planner_restart(managed):
    engine, owner, journal, control, worker, planner, adapter, _ = managed
    engine.tick()
    saved = journal.observed_binding()
    planner.identity = {**planner.identity, "runtime_generation": 2}
    request(control)
    with pytest.raises(ValueError, match="cannot change an observed"):
        engine.tick()
    assert journal.observed_binding() == saved
    assert engine._ready is engine._planner_readiness is None and adapter.steps == 0


def test_pending_report_must_flush_before_owner_preparation(managed):
    engine, owner, journal, control, *_ = managed
    mission = request(control)
    journal.prepare(mission["id"], None)
    journal.finish(mission["id"], {"state": "cancelled", "identity": None, "detail": "cancelled", "summary": {}})
    control.fail_terminal = True
    with pytest.raises(OSError):
        engine.tick()
    assert owner.calls == 0 and not control.deployment_reports


def test_restart_keeps_observation_but_never_replays_an_accepted_plan(managed, tmp_path):
    engine, owner, journal, control, worker, planner, adapter, _ = managed
    request(control)
    engine.tick()
    engine.tick()
    previous = journal.observed_binding()
    row = journal.db.execute("SELECT request_json,result_json FROM plans WHERE mission_id='mission'").fetchone()
    plan, result = (json.loads(value) for value in row)
    mission = {**control.desired["mission"], "id": "interrupted", "state": "running", "execution_started": True}
    identity = {**plan["identity"], "mission_id": mission["id"]}
    mission["identity"] = identity
    journal.prepare(mission["id"], identity)
    for payload in (plan, result):
        payload.update(identity=identity, request_id="interrupted-plan", observation_id="interrupted:task-admission")
    journal.request_plan(plan)
    journal.record_plan(plan, result, accepted=True)
    journal.mark_running(mission["id"])
    control.desired["mission"] = mission
    journal.close()
    restarted = ExecutionJournal(tmp_path, "robot", "device")
    try:
        before_calls = owner.calls
        recovered = Coordinator(robot_id="robot", device_id="device", journal=restarted, control=control,
                                worker=None, planner=None, bundle_owner=owner, adapter_factory=adapter,
                                profile=PAIRED_PROFILE)
        assert restarted.observed_binding() == previous and recovered._ready is None
        assert owner.calls == before_calls
        recovered.tick()
        recovered.tick()
        assert owner.calls == before_calls and adapter.steps == planner.calls == 1
        assert restarted.get("interrupted")["state"] == "unknown"
        assert restarted.db.execute("SELECT state FROM plans WHERE mission_id='interrupted'").fetchone()[0] == "accepted"
    finally:
        restarted.close()


def test_local_terminal_ack_cannot_overrule_remote_admission(managed):
    engine, owner, journal, control, *_ = managed
    mission = request(control)
    journal.prepare(mission["id"], None)
    journal.finish(mission["id"], {"state": "cancelled", "identity": None, "detail": "cancelled", "summary": {}})
    journal.acknowledge(mission["id"])
    mission.update(state="running", identity={"incarnation": "remote-owner"}, execution_started=True)
    engine.tick()
    assert owner.calls == 0 and not control.deployment_reports and not control.reports
