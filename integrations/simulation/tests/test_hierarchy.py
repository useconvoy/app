"""Authority fencing and scheduler independence, plus a real seeded physics reference."""

import json
import threading
import time
from dataclasses import replace

import numpy as np
import pytest

from convoy_sim.hierarchy.experiment import (
    ExperimentConfig,
    PlannerCall,
    PlanRequest,
    ProposalGate,
    run_episode,
)
from convoy_sim.hierarchy.planner import DeterministicPlanner
from convoy_sim.hierarchy.scene import SawyerReference, SawyerScene


def request():
    context = {"request_id": "request-1", "observation_seq": 10, "task_revision": 0,
               "instruction": "Pick and place at target A", "task_target": "A",
               "available_skills": ["pick_place", "hold"], "available_targets": ["A", "B"]}
    return PlanRequest(context, 1_000_000_000, 1_010_000_000, 2_000_000_000)


def test_proposal_fences_identity_revision_age_expiry_duplicate_and_cancel():
    source = request()
    decision = DeterministicPlanner().plan(source.context).decision
    gate = ProposalGate(.5)
    assert gate.admit(source, replace(decision, observation_seq=9), 1_100_000_000) == "identity_mismatch"
    assert gate.admit(source, decision, 1_600_000_000) == "stale_observation"
    assert gate.admit(source, decision, 2_000_000_000) == "expired"
    gate.revision = 1
    assert gate.admit(source, decision, 1_100_000_000) == "stale_task_revision"
    gate.revision = 0
    assert gate.admit(source, decision, 1_100_000_000) == "accepted"
    assert gate.admit(source, decision, 1_100_000_001) == "duplicate"
    gate.cancelled = True
    assert gate.admit(source, decision, 1_100_000_002) == "cancelled"


def test_late_planner_completion_cannot_publish_after_cancel():
    entered, release = threading.Event(), threading.Event()

    class Delayed:
        def plan(self, context):
            entered.set()
            assert release.wait(3)
            return DeterministicPlanner().plan(context)

    call = PlannerCall(Delayed(), request())
    assert entered.wait(3)
    call.cancel()
    release.set()
    assert call.done.wait(3)
    assert call.poll() is None
    assert call.late_discarded == 1


class CountingScene:
    """Cheap unit-test scene; tests using it make no MuJoCo/task-success claim."""

    control_period_s = .0125

    def __init__(self, seed, *, max_steps, frames):
        self.targets = {"A": [1.0, 1.0, 1.0], "B": [-1.0, -1.0, 1.0]}
        self.count = 0
        self.observation = [0.0] * 39

    def state(self):
        return {"observation": self.observation[:], "qpos": [float(self.count)], "qvel": [0.0],
                "simulation_time_s": self.count * self.control_period_s,
                "goal_position": self.targets["A"], "goal_target": "A"}

    def step(self, action, target):
        self.count += 1
        return {**self.state(), "reward": 0.0, "success": False, "terminated": False, "truncated": False}

    def close(self):
        pass


@pytest.mark.parametrize("mode", ["local", "blocking", "async"])
def test_actual_physics_advances_during_pending_planner_and_cancel_is_bounded(tmp_path, mode):
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    scenes = []

    def factory(*args, **kwargs):
        scene = SawyerScene(*args, **kwargs)
        scene.initial_time = scene.env.unwrapped.data.time
        scenes.append(scene)
        return scene

    class Blocked:
        def plan(self, context):
            entered.set()
            assert release.wait(5)
            return DeterministicPlanner().plan(context)

    results = []
    thread = threading.Thread(target=lambda: results.append(run_episode(
        ExperimentConfig(mode=mode, duration_s=5, revise_at_s=None), Blocked(), tmp_path / mode,
        stop_event=cancel, scene_factory=factory)))
    thread.start()
    try:
        assert entered.wait(4)
        started = time.monotonic()
        # Observe actual MuJoCo time advancing while the same planner call is blocked.
        while scenes[0].env.unwrapped.data.time - scenes[0].initial_time < .04 and time.monotonic() - started < 3:
            time.sleep(.005)
        assert scenes[0].env.unwrapped.data.time - scenes[0].initial_time >= .04
        assert not release.is_set()
        cancel.set()
        thread.join(3)
        assert not thread.is_alive()
        assert results[0]["status"] == "cancelled"
        assert results[0]["accepted_plans"] == 0
        assert results[0]["cancelled_inflight_requests"] == 1
        assert results[0]["physics_steps"] > 0
    finally:
        cancel.set()
        release.set()
        thread.join(5)


def test_seeded_reference_reaches_both_named_targets_in_real_mujoco():
    scene = SawyerScene(0, max_steps=500)
    policy = SawyerReference()
    try:
        initial = scene.state()
        for target in ("A", "B"):
            reached = False
            for _ in range(150):
                observation = scene.state()["observation"]
                outcome = scene.step(policy.action(observation, scene.targets[target]), target)
                distance = np.linalg.norm(np.asarray(outcome["observation"][4:7]) - scene.targets[target])
                if distance <= .07:
                    reached = True
                    break
            assert reached, target
            assert outcome["qpos"] != initial["qpos"]
            assert outcome["simulation_time_s"] > initial["simulation_time_s"]
            assert outcome["goal_position"] == scene.targets[target]
    finally:
        scene.close()


def test_custom_horizon_reaches_past_nested_500_step_benchmark_limit():
    scene = SawyerScene(0, max_steps=700)
    try:
        for _ in range(501):
            outcome = scene.step([0, 0, 0, 0], None)
        assert not outcome["terminated"]
        assert not outcome["truncated"]
        assert scene.env.unwrapped.curr_path_length == 501
    finally:
        scene.close()


def test_stale_task_reply_rejected_before_new_revision_commands(tmp_path):
    class DelayedFirst:
        def __init__(self):
            self.calls = 0

        def plan(self, context):
            self.calls += 1
            if self.calls == 1:
                time.sleep(.1)
            return DeterministicPlanner().plan(context)

    output = tmp_path / "revision"
    summary = run_episode(ExperimentConfig(duration_s=.35, revise_at_s=.04, refresh_interval_s=.2),
                          DelayedFirst(), output, scene_factory=CountingScene)
    events = [json.loads(line) for line in (output / "planner-events.jsonl").read_text().splitlines()]
    assert any(row.get("admission") == "stale_task_revision" for row in events)
    accepted = [row for row in events if row.get("admission") == "accepted"]
    assert accepted and all(row["decision"]["task_revision"] == 1 for row in accepted)
    assert summary["rejected_plans"] >= 1
    assert summary["max_planner_inflight"] == 1


def test_async_continues_approved_skill_while_blocking_watchdog_holds(tmp_path):
    class DelayedRefresh:
        def __init__(self):
            self.calls = 0

        def plan(self, context):
            self.calls += 1
            if self.calls > 1:
                time.sleep(.25)
            return DeterministicPlanner().plan(context)

    summaries, traces = {}, {}
    for mode in ("blocking", "async"):
        output = tmp_path / mode
        summaries[mode] = run_episode(
            ExperimentConfig(mode=mode, duration_s=.45, revise_at_s=None, refresh_interval_s=.02,
                             command_validity_s=.025), DelayedRefresh(), output, scene_factory=CountingScene)
        traces[mode] = [json.loads(line) for line in (output / "steps.jsonl").read_text().splitlines()]
    assert any(row.get("action_source") == "idle_hold" and row["hierarchy"]["planner_state"] == "pending"
               for row in traces["blocking"])
    assert any(row.get("action_source") == "reference" and row["hierarchy"]["planner_state"] == "pending"
               for row in traces["async"])
    assert summaries["blocking"]["hold_ticks"] > summaries["async"]["hold_ticks"]


def test_evidence_provenance_and_output_cannot_be_overwritten(tmp_path):
    output = tmp_path / "output"
    summary = run_episode(ExperimentConfig(mode="local", duration_s=.08, revise_at_s=None),
                          DeterministicPlanner(), output, scene_factory=CountingScene)
    assert summary["measurement_source"].startswith("actual local monotonic")
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["policy"]["learned"] is False
    assert metadata["clock"].endswith("no cross-host timestamp subtraction")
    with pytest.raises(FileExistsError):
        run_episode(ExperimentConfig(duration_s=.08, revise_at_s=None), DeterministicPlanner(), output)


def test_planner_deadline_expiry_keeps_physics_running_and_does_not_backlog(tmp_path):
    class TooSlow:
        def plan(self, context):
            time.sleep(.12)
            return DeterministicPlanner().plan(context)

    output = tmp_path / "expired"
    result = run_episode(ExperimentConfig(mode="async", duration_s=.3, revise_at_s=None,
                                         planner_timeout_s=.03, refresh_interval_s=.05),
                         TooSlow(), output, scene_factory=CountingScene)
    events = [json.loads(line) for line in (output / "planner-events.jsonl").read_text().splitlines()]
    assert result["physics_steps"] > 0
    assert result["accepted_plans"] == 0
    assert result["planner_timeouts"] >= 1
    assert result["max_planner_inflight"] == 1
    assert any(row.get("admission") == "expired" for row in events)
    # Even after timeout, a replacement cannot start before the same call finishes.
    requests = [row["elapsed_s"] for row in events if row["type"] == "planner_requested"]
    assert len(requests) <= 3
    assert all(b - a >= .12 for a, b in zip(requests, requests[1:], strict=False))
