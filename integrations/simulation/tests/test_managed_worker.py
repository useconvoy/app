"""Actual child startup failures are fenced; retries require a new deployment."""
import json

import pytest

pytest.importorskip("convoy_worker", reason="requires the managed extra")

from convoy_contracts.execution import canonical_digest  # noqa: E402

from convoy_sim.managed_worker import ManagedWorker  # noqa: E402


def test_failed_worker_does_not_spin_across_restart_and_new_deployment_can_retry(tmp_path, monkeypatch):
    secret = "test-only-action-secret-at-least-32-bytes"
    for name in ("CONVOY_ACTION_VERIFICATION_KEYS_FILE", "CONVOY_EXECUTION_SIGNING_KEYS_FILE", "CONVOY_PLANNER_EXECUTION_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVOY_EXECUTION_SECRET", secret)
    manifest = {
        "schema_version": 3, "profile": "registered-joint-policy-v1",
        "policy": {"runtime": "convoy-joint-target-reference-v1", "artifact_sha256": canonical_digest({"target_joint_positions": [0.25]})},
        "environment": {"engine": "mujoco", "version": "3.3.0", "robot_profile_sha256": "a" * 64, "asset_sha256": "b" * 64},
        "interface": {"joint_names": ["shoulder"], "command_interface": "joint-position", "action_bounds": [[-1, 1]], "control_rate_hz": 50},
        "task": {"instruction": "Reach target", "target_joint_positions": [0.25], "position_tolerance": 0.01, "velocity_tolerance": 0.02},
        "execution": {"max_steps": 200, "decision_timeout_ms": 1000, "mission_timeout_s": 60},
    }
    deployment = {"id": "dep_one", "generation": 1, "release": {"digest": canonical_digest(manifest), "manifest": manifest}}
    directory = tmp_path / "worker"
    with ManagedWorker(directory, startup_timeout_s=5) as owner:
        # Inject a real child initialization failure, not a mocked process result.
        owner.environment["CONVOY_EXECUTION_SECRET"] = "invalid"
        with pytest.raises(RuntimeError, match="exited before readiness"):
            owner.prepare(deployment, manifest)
        failed_process = json.loads((directory / "process.json").read_text())
        assert failed_process["state"] == "stopped"
        with pytest.raises(RuntimeError, match="new deployment"):
            owner.prepare(deployment, manifest)
        assert json.loads((directory / "process.json").read_text()) == failed_process
    with ManagedWorker(directory, startup_timeout_s=5) as restarted:
        with pytest.raises(RuntimeError, match="new deployment"):
            restarted.prepare(deployment, manifest)
        worker = restarted.prepare({**deployment, "id": "dep_two", "generation": 2}, manifest)
        probe = worker.probe(canonical_digest(manifest), manifest["profile"])
        assert probe["ready"] and probe["artifact_sha256"] == manifest["policy"]["artifact_sha256"]
        assert restarted.prepare({**deployment, "id": "dep_two", "generation": 2}, manifest) is worker
    assert json.loads((directory / "process.json").read_text())["state"] == "stopped"
