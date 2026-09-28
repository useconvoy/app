"""Run a registered simulated robot through the Convoy management pipeline.

Enroll using ``convoy-agent enroll --simulate`` first. This process reuses that
device credential and binds a dedicated durable coordinator journal to one robot.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
from importlib.metadata import version
from pathlib import Path

import gymnasium as gym
import metaworld  # noqa: F401 -- registers environments
import numpy as np
from convoy_agent.agent import AgentConfig
from convoy_agent.coordinator import Coordinator, ExecutionJournal, StepResult
from convoy_agent.coordinator.transport import JsonHTTP, WorkerHTTP


class MetaWorldAdapter:
    """Physics adapter; the coordinator alone owns calls to reset and step."""

    def __init__(self):
        expected = {"metaworld": "3.1.1", "mujoco": "3.3.0"}
        actual = {name: version(name) for name in expected}
        if actual != expected:
            raise ValueError(f"simulator versions do not match the qualified profile: {actual}")
        self.env = None
        self.control_period_s = 0.0125

    def reset(self, seed: int) -> list[float]:
        self.env = gym.make("Meta-World/MT1", env_name="pick-place-v3", seed=seed)
        observation, _ = self.env.reset(seed=seed)
        self.control_period_s = float(self.env.unwrapped.dt)
        return observation.tolist()

    def step(self, action: list[float], command_id: str) -> StepResult:
        if self.env is None:
            raise RuntimeError("simulator was not reset")
        observation, reward, terminated, truncated, info = self.env.step(np.asarray(action, dtype=np.float32))
        return StepResult(observation.tolist(), float(reward), bool(info["success"]), bool(terminated), bool(truncated))

    def close(self) -> None:
        if self.env is not None:
            self.env.close()
            self.env = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="existing simulated device enrollment directory")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--worker-url", default="http://127.0.0.1:8091")
    parser.add_argument("--server", help="override the enrolled control-plane endpoint")
    parser.add_argument("--poll-seconds", type=float, default=0.1)
    parser.add_argument("--clock-uncertainty-seconds", type=float, default=0.25,
                        help="explicit maximum wall-clock uncertainty for this local simulator")
    parser.add_argument("--once", action="store_true", help="perform one management iteration")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    cfg = AgentConfig(args.data_dir)
    if not cfg.credential or not cfg.data.get("simulate"):
        parser.error("run convoy-agent enroll --simulate for this data directory first")
    probe_token = os.environ.get("CONVOY_WORKER_PROBE_TOKEN")
    if not probe_token:
        parser.error("CONVOY_WORKER_PROBE_TOKEN is required")
    control = JsonHTTP(args.server or cfg.data["server"], cfg.credential, ca_file=cfg.data.get("ca_file"))
    worker = WorkerHTTP(args.worker_url, probe_token)
    # One directory/lock for the enrolled device prevents accidentally assigning
    # two processes to its simulated executor under different robot identifiers.
    journal = ExecutionJournal(args.data_dir / "coordinator", args.robot_id, cfg.data["device_id"])
    try:
        coordinator = Coordinator(
            robot_id=args.robot_id, device_id=cfg.data["device_id"], journal=journal,
            control=control, worker=worker, adapter_factory=MetaWorldAdapter,
            poll_s=args.poll_seconds, clock_uncertainty_s=args.clock_uncertainty_seconds,
        )
        signal.signal(signal.SIGINT, lambda *_: coordinator.request_stop())
        signal.signal(signal.SIGTERM, lambda *_: coordinator.request_stop())
        coordinator.run(once=args.once)
    finally:
        journal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
