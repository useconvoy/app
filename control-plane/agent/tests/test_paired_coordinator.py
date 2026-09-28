"""Planner admission must precede any policy session or adapter action."""

import base64
import json
import sqlite3
import struct
import threading
import time
import zlib

import pytest
from convoy_agent.coordinator import Coordinator, ExecutionJournal, StepResult
from convoy_contracts.execution import VISUAL_PROFILE, canonical_digest
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
)
from test_execution_coordinator import Control, Worker, manifest


def release():
    child = manifest()
    child["profile"] = VISUAL_PROFILE
    child["environment"]["metaworld"] = "3.0.0"
    return {"schema_version": 2, "profile": PAIRED_PROFILE, "action_manifest": child,
            "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": "b" * 64,
                        "protocol_sha256": PLANNER_PROTOCOL_SHA256},
            "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID},
            "catalog_sha256": CATALOG_SHA256, "planning": {"timeout_ms": 100},
            "placement": {"policy": "development-local-cpu", "planner": "development-jetson-lan"}}


def observation():
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 480, 480, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(480 * 1441))) + chunk(b"IEND", b""))
    return {"image_png_base64": base64.b64encode(png).decode(), "state": [0.0] * 4,
            "instruction": FIXED_TASK}


@pytest.fixture
def paired(tmp_path):
    bundle = release()
    journal = ExecutionJournal(tmp_path, "robot", "device")
    control, worker = Control(), Worker()
    control.desired["robot"]["profile"] = PAIRED_PROFILE
    control.desired["deployment"]["release"].update(manifest=bundle, digest=canonical_digest(bundle))
    post = control.post

    def claim(path, body):
        reply = post(path, body)
        if "/deployments/" in path:
            control.desired["deployment"]["state"] = body["state"]
        if path.endswith("/claim"):
            reply["identity"]["release_digest"] = canonical_digest(bundle)
            reply["planner_grant"] = "planner-only-grant"
        return reply

    control.post = claim
    worker.probe = lambda digest, profile: {"release_digest": digest, "profile": profile,
                                          **bundle["action_manifest"]["policy"]}
    worker.sessions = 0

    def start(identity, grant):
        with sqlite3.connect(tmp_path / "execution.sqlite3") as reader:
            assert reader.execute("SELECT state FROM plans").fetchone()[0] == "accepted"
        worker.sessions += 1
        return {"identity": identity, "next_sequence": 0}

    worker.start_session = start
    worker.end_session = lambda identity, grant: {"identity": identity, "closed": True}

    class Planner:
        mode = "skill"
        calls = 0
        closed = False
        identity = {"planner_artifact_sha256": "b" * 64, "planner_incarnation": "planner-boot",
                    "runtime_generation": 1}

        def probe(self, digest, profile):
            return {"ready": True, "release_digest": digest, "profile": profile,
                    "runtime": PLANNER_RUNTIME, **self.identity}

        def start_session(self, identity, grant):
            assert grant == "planner-only-grant"
            return {"identity": identity, "next_sequence": 0, **self.identity}

        def propose(self, request, grant):
            self.calls += 1
            with sqlite3.connect(tmp_path / "execution.sqlite3") as independent_reader:
                row = independent_reader.execute("SELECT request_json FROM plans").fetchone()
                assert json.loads(row[0]) == request
            decision = {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}}
            result = {**{key: value for key, value in request.items() if key != "budget_ms"},
                      **self.identity, "decision": decision, "planner_duration_ms": 1.0}
            if self.mode == "decline":
                result["decision"] = {"kind": "decline", "reason": "unsupported_task"}
            elif self.mode == "wrong_identity":
                result["planner_incarnation"] = "another-boot"
            elif self.mode == "wrong_request":
                result["request_id"] = "another-request"
            elif self.mode == "cancel":
                engine.cancel("cancel during planning")
            elif self.mode == "late":
                time.sleep(0.15)
            elif self.mode == "unavailable":
                raise OSError("planner unavailable")
            return result

        def end_session(self, identity, grant):
            self.closed = True

    class Adapter:
        control_period_s = 0.0125
        created = 0
        steps = 0

        def __init__(self):
            type(self).created += 1

        def reset(self, seed):
            return observation()

        def step(self, action, command_id):
            type(self).steps += 1
            return StepResult(observation(), 1.0, True)

        def close(self):
            pass

    planner = Planner()
    engine = Coordinator(robot_id="robot", device_id="device", journal=journal, control=control,
                         worker=worker, planner=planner, adapter_factory=Adapter, profile=PAIRED_PROFILE,
                         poll_s=0.005)
    engine.tick()
    mission = control.request_mission()
    mission["release_digest"] = canonical_digest(bundle)
    yield engine, journal, control, worker, planner, Adapter
    if engine._inference_thread:
        engine._inference_thread.join(timeout=1)
    if engine._cleanup_thread:
        engine._cleanup_thread.join(timeout=1)
    journal.close()


@pytest.mark.parametrize("mode,expected", [
    ("skill", "completed"), ("decline", "failed"), ("wrong_identity", "failed"),
    ("wrong_request", "failed"), ("late", "failed"), ("cancel", "cancelled"), ("unavailable", "failed"),
])
def test_only_current_accepted_plan_can_start_the_local_policy(paired, mode, expected):
    engine, journal, control, worker, planner, adapter = paired
    planner.mode = mode
    engine.tick()
    report = control.reports[-1]
    assert report["state"] == expected
    assert worker.sessions == adapter.created == adapter.steps == (1 if mode == "skill" else 0)
    assert report["summary"]["planner_accepted"] is (mode == "skill")
    assert planner.calls == 1 and planner.closed
    engine.tick()
    assert planner.calls == 1  # terminal polling never regenerates a decision


def test_accepted_plan_survives_planner_loss_but_crash_never_replays_it(paired):
    engine, journal, control, worker, planner, adapter = paired
    original = worker.start_session

    def after_plan(identity, grant):
        planner.propose = lambda *_: (_ for _ in ()).throw(OSError("now offline"))
        return original(identity, grant)

    worker.start_session = after_plan
    engine.tick()
    assert adapter.steps == 1 and control.reports[-1]["state"] == "completed"
    # A new prepared mission with a durable accepted proposal still requires
    # explicit execution reconciliation after process loss, not plan replay.
    identity = {**control.reports[-1]["identity"], "mission_id": "interrupted"}
    journal.prepare("interrupted", identity)
    row = journal.db.execute("SELECT request_json, result_json FROM plans WHERE mission_id='mission'").fetchone()
    request, result = (json.loads(item) for item in row)
    for value in (request, result):
        value.update(identity=identity, request_id="interrupted-plan", observation_id="interrupted:task-admission")
    journal.request_plan(request)
    journal.record_plan(request, result, accepted=True)
    journal.mark_running("interrupted")
    journal.recover()
    recovered = json.loads(journal.get("interrupted")["report_json"])
    assert recovered["state"] == "unknown" and planner.calls == 1
    assert journal.db.execute("SELECT state FROM plans WHERE mission_id='interrupted'").fetchone()[0] == "accepted"


def test_cancellation_does_not_wait_for_a_stalled_planner(paired):
    engine, journal, control, worker, planner, adapter = paired
    started, unblock = threading.Event(), threading.Event()

    def wait_forever(request, grant):
        started.set()
        unblock.wait(1)
        raise OSError("late planner completion")

    planner.propose = wait_forever
    canceller = threading.Thread(target=lambda: (started.wait(1), engine.cancel("operator cancellation")))
    canceller.start()
    try:
        engine.tick()
        assert control.reports[-1]["state"] == "cancelled"
        assert adapter.created == 0 and worker.sessions == 0
    finally:
        unblock.set()
        canceller.join(timeout=1)


def test_pending_unclaimed_mission_recovers_after_transient_readiness_failure(paired):
    engine, journal, control, worker, planner, adapter = paired
    engine._ready = None  # a fresh coordinator must re-probe the same deployment
    probe = planner.probe
    planner.probe = lambda *_: (_ for _ in ()).throw(OSError("temporary outage"))
    with pytest.raises(OSError):
        engine.tick()
    assert control.desired["deployment"]["state"] == "blocked"
    assert control.desired["mission"]["state"] == "requested"
    assert adapter.created == 0 and journal.get("mission") is None
    planner.probe = probe
    engine.tick()
    assert control.desired["deployment"]["state"] == "ready"
    assert control.desired["mission"]["state"] == "requested" and adapter.created == 0
    engine.tick()  # fresh desired state precedes admission
    assert control.reports[-1]["state"] == "completed" and adapter.steps == 1


@pytest.mark.parametrize("stalled_component", ["policy_start", "policy_close", "planner_close"])
def test_stalled_session_io_cannot_delay_durable_cancellation(paired, stalled_component):
    engine, journal, control, worker, planner, adapter = paired
    entered, unblock = threading.Event(), threading.Event()

    def stall(*_):
        entered.set()
        assert unblock.wait(2)
        raise OSError("late session response")

    if stalled_component == "policy_start":
        worker.start_session = stall
        canceller = threading.Thread(target=lambda: (entered.wait(1), engine.cancel("operator cancellation")))
        canceller.start()
    else:
        planner.mode = "cancel"
        endpoint = worker if stalled_component == "policy_close" else planner
        endpoint.end_session = stall
        canceller = None
    try:
        engine.tick()
        # The remote operation is still blocked when both the local and remote
        # terminal outcome become visible. No wall-time threshold is needed.
        assert entered.is_set() and not unblock.is_set()
        assert control.reports[-1]["state"] == journal.get("mission")["state"] == "cancelled"
        assert adapter.created == adapter.steps == 0
        engine.tick()
        assert planner.calls == 1  # retrying the management loop cannot replay it
    finally:
        unblock.set()
        if canceller:
            canceller.join(timeout=1)
