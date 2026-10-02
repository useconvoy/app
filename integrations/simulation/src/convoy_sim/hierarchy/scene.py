"""Seeded Sawyer pick/place physics and its explicitly privileged-state expert.

Named goals change the benchmark scene. This is a bounded scheduling experiment,
not a new learned policy or a result under the unchanged MetaWorld benchmark.
Only the physics thread accesses the environment or its renderer.
"""

from __future__ import annotations

import metaworld
import numpy as np

from convoy_sim.policies import ScriptedPickPlace, validate_action


class SawyerScene:
    control_period_s = 0.0125

    def __init__(self, seed: int, *, max_steps: int = 500, frames: bool = False):
        # Gym.make consumes max_episode_steps for an OUTER TimeLimit; the
        # MetaWorld factory would otherwise still install an inner 500-step one.
        self.env = metaworld.make_mt_envs(
            "pick-place-v3", seed=seed, max_episode_steps=max_steps,
            render_mode="rgb_array" if frames else None, camera_name="corner3",
        )
        # The experiment's custom wall-time horizon is explicit in its metadata.
        self.env.unwrapped.max_path_length = max_steps
        self.control_period_s = float(self.env.unwrapped.dt)
        if self.control_period_s != 0.0125:
            self.env.close()
            raise ValueError("MetaWorld control period changed")
        self.observation, _ = self.env.reset(seed=seed)
        original = self.env.unwrapped._target_pos.copy()
        self.targets = {name: [x, float(original[1]), float(original[2])]
                        for name, x in (("A", -0.08), ("B", 0.08))}
        self.target = None
        self._set_target("A")
        self.observation = self.env.unwrapped._get_obs()

    def _set_target(self, target: str) -> None:
        if self.target == target:
            return
        goal = np.asarray(self.targets[target], dtype=np.float64)
        env = self.env.unwrapped
        env._target_pos = goal.copy()
        env.model.site("goal").pos = goal
        env.maxPlacingDist = (float(np.linalg.norm(
            np.array([env.obj_init_pos[0], env.obj_init_pos[1], env.heightTarget]) - goal))
            + env.heightTarget)
        env.maxPushDist = float(np.linalg.norm(env.obj_init_pos[:2] - goal[:2]))
        self.target = target

    def state(self) -> dict:
        return {"observation": np.asarray(self.observation, dtype=float).tolist(),
                "qpos": self.env.unwrapped.data.qpos.tolist(),
                "qvel": self.env.unwrapped.data.qvel.tolist(),
                "mocap_pos": self.env.unwrapped.data.mocap_pos.tolist(),
                "mocap_quat": self.env.unwrapped.data.mocap_quat.tolist(),
                "ctrl": self.env.unwrapped.data.ctrl.tolist(),
                "simulation_time_s": float(self.env.unwrapped.data.time),
                "goal_position": self.env.unwrapped._target_pos.tolist(),
                "goal_target": self.target}

    def step(self, action: list[float], target: str | None) -> dict:
        if target is not None:
            self._set_target(target)
        self.observation, reward, terminated, truncated, info = self.env.step(validate_action(action))
        return {**self.state(), "reward": float(reward), "success": bool(info["success"]),
                "grasp_success": bool(info["grasp_success"]), "obj_to_target_m": float(info["obj_to_target"]),
                "terminated": bool(terminated), "truncated": bool(truncated)}

    def render(self):
        return self.env.render()

    def close(self) -> None:
        self.env.close()


class SawyerReference:
    """The upstream scripted expert, with an explicitly selected named goal."""

    def __init__(self):
        self.policy = ScriptedPickPlace()

    def action(self, observation: list[float], target: list[float]) -> list[float]:
        values = np.asarray(observation, dtype=np.float64).copy()
        if values.shape != (39,) or not np.isfinite(values).all():
            raise ValueError("expected the finite MetaWorld 39-D privileged observation")
        values[-3:] = target
        return self.policy.get_action(values).tolist()
