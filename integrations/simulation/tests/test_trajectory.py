"""Actual physics states reconstruct visible poses after inference has finished."""
import hashlib
import json
import time
from copy import deepcopy

import pytest

pytest.importorskip("convoy_agent", reason="requires managed execution")

from convoy_contracts.execution import canonical_digest  # noqa: E402
from test_robot_qualification import fixture  # noqa: E402

from convoy_sim.realtime import RealtimeJointAdapter  # noqa: E402
from convoy_sim.registered import JointAdapter  # noqa: E402
from convoy_sim.trajectory import export_trace, load_trace, render_trace  # noqa: E402


def setup(tmp_path, realtime=False):
    asset, _, model = fixture(tmp_path)
    manifest = {"schema_version": 3, "profile": "registered-joint-policy-v1",
        "policy": {"runtime": "convoy-joint-target-reference-v1", "artifact_sha256": "a" * 64},
        "environment": {"engine": "mujoco", "version": "3.3.0", "robot_profile_sha256": "b" * 64, "asset_sha256": model["asset"]["sha256"]},
        "interface": {"joint_names": ["shoulder"], "command_interface": "joint-position", "action_bounds": [[-1, 1]], "control_rate_hz": 50},
        "task": {"instruction": "Reach the target", "target_joint_positions": [.9], "position_tolerance": .001, "velocity_tolerance": .001},
        "execution": {"max_steps": 100, "decision_timeout_ms": 1000, "mission_timeout_s": 30}}
    if realtime:
        manifest["execution"]["timing"] = {"mode": "realtime", "max_observation_age_ms": 150, "max_physics_lag_ms": 100, "fallback": "hold-position"}
    identity = {"robot_id": "robot", "device_id": "device", "mission_id": "mission", "boot_id": "boot", "incarnation": "incarnation",
                "release_digest": canonical_digest(manifest), "authority_epoch": 1}
    return asset, manifest, identity


def test_lockstep_records_real_states_and_reconstructs_frames_without_reexecuting(tmp_path):
    asset, manifest, identity = setup(tmp_path)
    runner = JointAdapter(manifest, asset.read_text(), {}, recording_directory=tmp_path / "traces")
    runner.reset(0)
    try:
        for index in range(20):
            runner.step([.5], f"command-{index}")
    finally:
        runner.close()
    evidence = runner.export_recording(identity)
    assert evidence["state"] == "recorded-locally" and evidence["physics_ticks"] == 20
    trace_path = tmp_path / "traces/mission.state.json"
    original = trace_path.read_bytes()
    assert evidence["sha256"] == hashlib.sha256(trace_path.read_bytes()).hexdigest()
    assert runner.export_recording(identity) == evidence  # immutable retry
    trace = load_trace(trace_path, evidence["sha256"])
    assert trace["samples"][0]["state"]["qpos"] == [0]
    assert trace["samples"][-1]["state"]["qpos"][0] > .1
    assert trace["samples"][-1]["simulation_time_s"] == pytest.approx(.4)
    output = tmp_path / "rendered"
    rendered = render_trace(trace_path, evidence["sha256"], asset, "mjcf", output)
    assert rendered["frames"] == 21
    index = json.loads((output / "index.json").read_text())
    assert index["identity"] == identity
    assert index["action_labels"] == ["shoulder"]
    assert index["frames"][0]["sha256"] != index["frames"][-1]["sha256"]
    assert (output / "0000.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "not policy camera observations" in index["source"]
    assert trace_path.read_bytes() == original
    with pytest.raises(ValueError):
        render_trace(trace_path, evidence["sha256"], asset, "mjcf", output)
    altered_asset = tmp_path / "other.xml"
    altered_asset.write_text(asset.read_text().replace('mass="1"', 'mass="2"'))
    with pytest.raises(ValueError, match="digest"):
        render_trace(trace_path, evidence["sha256"], altered_asset, "mjcf", tmp_path / "wrong-model")
    assert not (tmp_path / "wrong-model").exists()


def test_realtime_trace_includes_motion_and_fallback_between_policy_results(tmp_path):
    asset, manifest, identity = setup(tmp_path, realtime=True)
    runner = RealtimeJointAdapter(manifest, asset.read_text(), {}, recording_directory=tmp_path / "traces")
    try:
        runner.reset(0)
        runner.capture()
        result = runner.step_timed([.5], "one-policy-command", time.monotonic_ns() + 1_000_000_000)
        assert result.applied
        time.sleep(.3)
    finally:
        runner.close()
    summary = runner.execution_summary()
    evidence = runner.export_recording(identity)
    assert evidence["physics_ticks"] == summary["physics_control_steps"] > 10
    trace = load_trace(tmp_path / "traces/mission.state.json", evidence["sha256"])
    policy = [sample for sample in trace["samples"] if sample["action_source"] == "policy"]
    held = [sample for sample in trace["samples"] if sample["action_source"] == "held-policy"]
    fallback = [sample for sample in trace["samples"] if sample["action_source"] == "fallback"]
    assert len(policy) == summary["timing"]["applied_actions"] == 1
    assert len(fallback) == summary["timing"]["fallback_ticks"]
    assert held and fallback
    assert all(sample["policy_command_id"] == "one-policy-command" for sample in policy + held)
    assert all(sample["policy_command_id"] is None for sample in fallback)
    assert trace["samples"][-1]["state"]["qpos"][0] > trace["samples"][0]["state"]["qpos"][0]
    assert trace["samples"][-1]["simulation_time_s"] == pytest.approx(summary["simulated_duration_s"])


def test_trajectory_rejects_wrong_identity_and_discontinuity_and_bounds_local_storage(tmp_path, monkeypatch):
    from convoy_sim import trajectory

    asset, manifest, identity = setup(tmp_path)
    runner = JointAdapter(manifest, asset.read_text(), {}, recording_directory=tmp_path / "traces")
    runner.reset(0)
    runner.step([.5], "command")
    runner.close()
    evidence = runner.export_recording(identity)
    trace_path = tmp_path / "traces/mission.state.json"
    with pytest.raises(ValueError, match="digest"):
        load_trace(trace_path, "0" * 64)
    with pytest.raises(ValueError, match="identity"):
        runner.export_recording({**identity, "release_digest": "0" * 64})
    broken = deepcopy(runner.recorder.snapshot())
    broken["samples"][1]["simulation_time_s"] = 8
    with pytest.raises(ValueError, match="clock"):
        export_trace(tmp_path / "bad", manifest, identity, broken)
    broken = deepcopy(runner.recorder.snapshot())
    broken["samples"][1]["state"]["qpos"] = []
    with pytest.raises(ValueError, match="vector"):
        export_trace(tmp_path / "bad", manifest, identity, broken)
    monkeypatch.setattr(trajectory, "LOCAL_QUOTA", 1)
    assert export_trace(tmp_path / "full", manifest, identity, runner.recorder.snapshot())["reason"] == "local-recording-quota"
    assert not list((tmp_path / "full").glob("*.json"))
    assert runner.export_recording(identity) == evidence
