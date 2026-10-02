"""Real physics processes keep advancing through delayed policy responses."""
import os
import signal
import time

import pytest

pytest.importorskip("convoy_agent", reason="requires managed execution")

from convoy_agent.coordinator.engine import TimingRejected  # noqa: E402
from test_robot_qualification import fixture  # noqa: E402

from convoy_sim.realtime import RealtimeJointAdapter  # noqa: E402


def adapter(tmp_path, age=100, lag=100):
    asset, _, _ = fixture(tmp_path)
    manifest = {
        "interface": {"joint_names": ["shoulder"], "action_bounds": [[-1, 1]], "control_rate_hz": 50},
        "task": {"target_joint_positions": [0.9], "position_tolerance": .001, "velocity_tolerance": .001},
        "execution": {"max_steps": 100, "timing": {"mode": "realtime", "max_observation_age_ms": age,
                                                   "max_physics_lag_ms": lag, "fallback": "hold-position"}},
    }
    return RealtimeJointAdapter(manifest, asset.read_text(), {})


def test_physics_advances_and_rejects_old_observation_before_action(tmp_path):
    runner = adapter(tmp_path)
    try:
        runner.reset(0)
        _, captured, terminal = runner.capture()
        assert terminal is None
        started = time.monotonic_ns()
        time.sleep(.18)  # Policy delay; the physics process must continue.
        runner.record_policy_wait(started, time.monotonic_ns(), captured, True)
        with pytest.raises(TimingRejected):
            runner.step_timed([.25], "late", time.monotonic_ns() + 1_000_000_000)
        runner.record_deadline_miss()
    finally:
        runner.close()
    result = runner.execution_summary()
    assert result["physics_control_steps"] >= 7
    assert result["simulated_duration_s"] >= .14
    assert result["timing"]["applied_actions"] == 0
    assert result["timing"]["rejected_actions"] == 1
    assert result["timing"]["status"] == "failed"
    assert "policy_deadline_missed" in result["timing"]["reasons"]
    assert not runner.process.is_alive()


def test_fresh_target_moves_then_expires_to_local_hold(tmp_path):
    runner = adapter(tmp_path, age=150)
    try:
        runner.reset(0)
        initial, _, _ = runner.capture()
        result = runner.step_timed([.25], "fresh", time.monotonic_ns() + 1_000_000_000)
        assert result.applied
        time.sleep(.3)
        later, _, _ = runner.capture()
        assert later["positions"][0] > initial["positions"][0]
    finally:
        runner.close()
    result = runner.execution_summary()["timing"]
    assert result["applied_actions"] == 1
    assert result["steady_fallback_ticks"] > 0
    assert result["observation_to_action_ms"]["max"] < 150
    assert result["status"] == "failed"


def test_sustained_fresh_actions_report_observed_timing_without_task_success(tmp_path):
    runner = adapter(tmp_path, age=200)
    runner.manifest["execution"]["max_steps"] = 210
    try:
        runner.reset(0)
        for command in range(220):
            _, captured, terminal = runner.capture()
            if terminal:
                break
            started = time.monotonic_ns()
            # Known reference target, deliberately different from the task target.
            runner.record_policy_wait(started, time.monotonic_ns(), captured, True)
            result = runner.step_timed([.25], str(command), captured + 200_000_000)
            if result.truncated or result.terminated:
                break
    finally:
        runner.close()
    result = runner.execution_summary()
    assert not result["final_success"]
    assert result["physics_control_steps"] == 210
    assert result["timing"]["applied_actions"] >= 10
    assert result["timing"]["status"] == "observed_deadlines_met"
    assert result["timing"]["reasons"] == []
    assert result["timing"]["physics_wall_s"] >= result["simulated_duration_s"]


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="requires process pause support")
@pytest.mark.parametrize("observe_before_close", [True, False])
def test_physics_overrun_is_a_failed_timing_measurement(tmp_path, observe_before_close):
    runner = adapter(tmp_path)
    try:
        runner.reset(0)
        runner.capture()
        os.kill(runner.process.pid, signal.SIGSTOP)
        try:
            time.sleep(.25)
        finally:
            os.kill(runner.process.pid, signal.SIGCONT)
        if observe_before_close:
            with pytest.raises(RuntimeError, match="could not maintain"):
                runner.capture()
    finally:
        runner.close()
    timing = runner.execution_summary()["timing"]
    assert timing["status"] == "failed"
    assert timing["physics_fault"] == "physics_dispatch_lag"
    assert timing["dispatch_lag_ms"]["max"] > 100
