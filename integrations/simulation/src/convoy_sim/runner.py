"""Lockstep MuJoCo experiments with traceable observations, actions and outcomes.

Physics waits for each policy result. Wall time is recorded but this runner does
not qualify real-time control, network latency tolerance, or physical hardware.
"""

import hashlib
import json
import platform
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path

import gymnasium as gym
import metaworld  # noqa: F401 -- registers the Gymnasium environment
import numpy as np

from .policies import POLICIES, Policy, validate_action

ENVIRONMENT = "pick-place-v3"
PACKAGES = ("convoy-sim", "metaworld", "mujoco", "gymnasium", "numpy", "scipy")


@dataclass(frozen=True)
class RunConfig:
    episodes: int = 3
    seed: int = 0
    steps: int = 500
    policy: str = "scripted"
    video: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.episodes <= 100:
            raise ValueError("episodes must be between 1 and 100")
        if not 1 <= self.steps <= 500:
            raise ValueError("steps must be between 1 and the benchmark horizon of 500")
        if not 0 <= self.seed <= 2**32 - self.episodes:
            raise ValueError("seed range must fit in an unsigned 32-bit integer")
        if self.policy not in POLICIES:
            raise ValueError(f"unknown policy: {self.policy}")


def _write_json(path: Path, value: dict) -> None:
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    pending.replace(path)


def _source_revision() -> dict:
    try:
        root = Path(__file__).parent
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL, text=True, timeout=5
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=root, stderr=subprocess.DEVNULL, text=True, timeout=5
        ).strip()
        return {"commit": sha, "dirty": bool(dirty)}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def _observation(value: np.ndarray) -> list[float]:
    if value.shape != (39,) or not np.isfinite(value).all():
        raise ValueError("expected a finite 39-element MetaWorld observation")
    return value.tolist()


def run_episode(config: RunConfig, seed: int, folder: Path, policy: Policy) -> dict:
    """Run one policy in real physics. The caller owns policy loading/checkpoints."""
    started = time.perf_counter()
    env = None
    writer = None
    result = {
        "seed": seed,
        "status": "running",
        "steps": 0,
        "ever_success": False,
        "final_success": False,
        "first_success_step": None,
        "reward_sum": 0.0,
        "simulated_duration_s": 0.0,
        "terminated": False,
        "truncated": False,
    }
    policy_times = []
    trace_path = folder / f"episode-{seed}.jsonl"
    with trace_path.open("x") as trace:
        def emit(event: dict) -> None:
            trace.write(json.dumps(event, allow_nan=False) + "\n")

        try:
            env = gym.make(
                "Meta-World/MT1", env_name=ENVIRONMENT, seed=seed,
                render_mode="rgb_array" if config.video else None, camera_name="corner3",
            )
            observation, _ = env.reset(seed=seed)
            initial = _observation(observation)
            initial_hash = hashlib.sha256(json.dumps(initial, allow_nan=False).encode()).hexdigest()
            dt = float(env.unwrapped.dt)
            result.update({"control_period_s": dt, "initial_observation_sha256": initial_hash})
            emit({"type": "reset", "seed": seed, "observation_id": 0, "observation": initial})
            if config.video:
                import imageio.v2 as imageio

                writer = imageio.get_writer(folder / f"episode-{seed}.mp4", fps=1 / (dt * 4))
                writer.append_data(env.render())

            for step in range(config.steps):
                before = time.perf_counter()
                action = validate_action(policy.get_action(observation.copy()))
                policy_s = time.perf_counter() - before
                policy_times.append(policy_s)
                observation, reward, terminated, truncated, info = env.step(action)
                current = _observation(observation)
                success = bool(info["success"])
                result["steps"] = step + 1
                result["reward_sum"] += float(reward)
                result["final_success"] = success
                result["ever_success"] |= success
                if success and result["first_success_step"] is None:
                    result["first_success_step"] = step + 1
                result["terminated"] = bool(terminated)
                result["truncated"] = bool(truncated)
                result["simulated_duration_s"] = (step + 1) * dt
                emit({
                    "type": "step", "step": step + 1, "source_observation_id": step,
                    "action": action.tolist(), "policy_duration_s": policy_s,
                    "observation_id": step + 1, "observation": current,
                    "reward": float(reward), "success": success,
                    "terminated": bool(terminated), "truncated": bool(truncated),
                })
                if writer is not None and (step + 1) % 4 == 0:
                    writer.append_data(env.render())
                if terminated or truncated:
                    break
            result["status"] = "completed"
        except Exception as error:
            result.update({"status": "error", "error": f"{type(error).__name__}: {error}"[:1000]})
            emit({"type": "error", "message": result["error"], "after_steps": result["steps"]})
        finally:
            cleanup_errors = []
            for resource in (writer, env):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as error:
                        cleanup_errors.append(f"{type(error).__name__}: {error}"[:1000])
            if cleanup_errors:
                result.update({"status": "error", "cleanup_errors": cleanup_errors})
                emit({"type": "cleanup_error", "messages": cleanup_errors, "after_steps": result["steps"]})
    result["wall_duration_s"] = time.perf_counter() - started
    result["policy_duration_p95_s"] = float(np.percentile(policy_times, 95)) if policy_times else None
    result["trace"] = trace_path.name
    return result


def run(config: RunConfig, output: Path, *, policy_factory: Callable[[], Policy] | None = None) -> dict:
    """Write a new run directory; existing evidence is never overwritten."""
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "environment": ENVIRONMENT,
        "execution_mode": "lockstep_offline",
        "evidence_scope": "simulated_physics_with_privileged_state",
        "policy_kind": "custom" if policy_factory is not None else "scripted_baseline",
        "policy_transform": "clip_to_action_bounds" if config.policy == "scripted" and policy_factory is None else "none",
        "observation_contract": "metaworld-v3-state39-goal-observable",
        "action_contract": "metaworld-v3-normalized-xyz-gripper4",
        "video_camera": "corner3" if config.video else None,
        "config": asdict(config),
        "packages": {name: version(name) for name in PACKAGES},
        "python": platform.python_version(),
        "platform": platform.platform(),
        "source": _source_revision(),
    }
    _write_json(output / "manifest.json", manifest)
    summary = {"schema_version": 1, "status": "running", "episodes": []}
    _write_json(output / "summary.json", summary)
    factory = policy_factory or POLICIES[config.policy]
    try:
        for seed in range(config.seed, config.seed + config.episodes):
            episode = run_episode(config, seed, output, factory())
            summary["episodes"].append(episode)
            _write_json(output / "summary.json", summary)
        summary["status"] = "error" if any(e["status"] != "completed" for e in summary["episodes"]) else "completed"
    except (Exception, KeyboardInterrupt) as error:
        summary.update({"status": "error", "error": f"{type(error).__name__}: {error}"[:1000]})
        raise
    finally:
        # Failed/incomplete episodes count against the requested denominator.
        summary["final_success_rate"] = sum(
            e["status"] == "completed" and e["final_success"] for e in summary["episodes"]
        ) / config.episodes
        _write_json(output / "summary.json", summary)
    return summary
