import json

import gymnasium as gym
import imageio.v2 as imageio
import numpy as np
import pytest

from convoy_sim import runner
from convoy_sim.policies import validate_action
from convoy_sim.runner import RunConfig, run


def test_real_pick_place_and_zero_baseline_record_different_outcomes(tmp_path):
    output = tmp_path / "scripted"
    scripted = run(RunConfig(episodes=3), output)
    zero = run(RunConfig(episodes=3, policy="zero"), tmp_path / "zero")
    assert scripted["status"] == zero["status"] == "completed"
    assert scripted["final_success_rate"] == 1
    assert zero["final_success_rate"] == 0
    assert all(e["steps"] == 500 and e["simulated_duration_s"] > 0 for e in scripted["episodes"])
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["execution_mode"] == "lockstep_offline"
    assert manifest["policy_kind"] == "scripted_baseline"
    events = [json.loads(line) for line in (output / "episode-0.jsonl").read_text().splitlines()]
    assert events[0]["type"] == "reset"
    assert len(events) == 501
    assert events[1]["source_observation_id"] == events[0]["observation_id"]
    assert events[-1]["observation_id"] == 500
    assert events[0]["observation"][4:7] != events[-1]["observation"][4:7]  # object moved in physics


def test_seeded_reset_and_actions_are_reproducible(tmp_path):
    run(RunConfig(episodes=1, steps=20, seed=7), tmp_path / "a")
    run(RunConfig(episodes=1, steps=20, seed=7), tmp_path / "b")
    traces = []
    for folder in ("a", "b"):
        events = [json.loads(line) for line in (tmp_path / folder / "episode-7.jsonl").read_text().splitlines()]
        traces.append([(e.get("action"), e["observation"]) for e in events])
    assert traces[0] == traces[1]


def test_invalid_policy_is_recorded_without_applying_an_action(tmp_path):
    class BrokenPolicy:
        def get_action(self, observation):
            return np.full(4, np.nan)

    summary = run(RunConfig(episodes=1), tmp_path / "broken", policy_factory=BrokenPolicy)
    assert summary["status"] == "error"
    assert summary["final_success_rate"] == 0
    assert summary["episodes"][0]["steps"] == 0
    events = [json.loads(line) for line in (tmp_path / "broken/episode-0.jsonl").read_text().splitlines()]
    assert [e["type"] for e in events] == ["reset", "error"]


@pytest.mark.parametrize("action", [[0, 0, 0], [0, 0, 0, float("inf")], [0, 0, 0, 1.01]])
def test_action_contract_rejects_invalid_values(action):
    with pytest.raises(ValueError):
        validate_action(action)


def test_existing_evidence_is_not_overwritten(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "summary.json"
    marker.write_text("original")
    with pytest.raises(FileExistsError):
        run(RunConfig(episodes=1), output)
    assert marker.read_text() == "original"


def test_video_finalization_failure_preserves_episode_and_closes_physics(tmp_path, monkeypatch):
    env = gym.make("Meta-World/MT1", env_name="pick-place-v3", seed=0)
    original_close = env.close
    closed = []

    def close():
        closed.append(True)
        original_close()

    class FailingWriter:
        def append_data(self, frame):
            pass

        def close(self):
            raise OSError("encoder failed to finalize")

    monkeypatch.setattr(env, "close", close)
    monkeypatch.setattr(env, "render", lambda: np.zeros((8, 8, 3), dtype=np.uint8))
    monkeypatch.setattr(runner.gym, "make", lambda *args, **kwargs: env)
    monkeypatch.setattr(imageio, "get_writer", lambda *args, **kwargs: FailingWriter())
    summary = run(RunConfig(episodes=1, steps=1, video=True), tmp_path / "video-error")
    assert closed == [True]
    assert summary["status"] == "error"
    assert summary["episodes"][0]["steps"] == 1
    assert summary["episodes"][0]["cleanup_errors"] == ["OSError: encoder failed to finalize"]
    events = (tmp_path / "video-error/episode-0.jsonl").read_text().splitlines()
    assert json.loads(events[-1])["type"] == "cleanup_error"
