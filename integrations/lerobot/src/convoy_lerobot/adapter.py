"""Upstream LeRobot camera/state/action semantics, in the simulator process only."""

from __future__ import annotations

import base64
import io

from convoy_agent.coordinator import StepResult
from convoy_contracts.execution import VISUAL_INSTRUCTION, VISUAL_PROFILE, validate_observation

from .artifact import verify_versions


class VisualMetaWorldAdapter:
    control_period_s = 0.0125

    def __init__(self):
        verify_versions(("lerobot", "metaworld", "mujoco", "gymnasium", "numpy", "pillow"))
        self.env = None

    def _observation(self, value):
        from PIL import Image

        output = io.BytesIO()
        Image.fromarray(value["pixels"]).save(output, format="PNG")
        result = {"image_png_base64": base64.b64encode(output.getvalue()).decode("ascii"),
                  "state": value["agent_pos"].tolist(), "instruction": self.env.task_description}
        return validate_observation(result, VISUAL_PROFILE)

    def reset(self, seed: int) -> dict:
        from lerobot.envs.metaworld import MetaworldEnv

        self.env = MetaworldEnv(task="pick-place-v3", obs_type="pixels_agent_pos", camera_name="corner2")
        if self.env.task_description != VISUAL_INSTRUCTION:
            raise ValueError("upstream task language changed")
        observation, _ = self.env.reset(seed=seed)
        self.control_period_s = float(self.env._env.dt)
        if self.control_period_s != 0.0125:
            raise ValueError("upstream controller period changed")
        return self._observation(observation)

    def step(self, action: list[float], command_id: str) -> StepResult:
        import numpy as np

        if self.env is None:
            raise RuntimeError("simulator was not reset")
        observation, reward, terminated, truncated, info = self.env.step(np.asarray(action, dtype=np.float32))
        # Upstream captures the terminal observation, then internally resets
        # on success. Stop here; no inference follows that reset in this mission.
        return StepResult(self._observation(observation), float(reward), bool(info["is_success"]),
                          bool(terminated), bool(truncated))

    def close(self):
        if self.env is not None:
            self.env.close()
            self.env = None
