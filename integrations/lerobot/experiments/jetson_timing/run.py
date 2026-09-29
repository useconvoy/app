"""Colocated physics/policy qualification. Run only against simulated hardware.

The parent owns MuJoCo, the child owns Torch, and only one inference can be in
flight. Absolute tick deadlines are never reset after a late tick. Rendering and
IPC overhead remain included in the measured physics lag, not hidden by pauses.
"""

import argparse
import hashlib
import importlib.metadata
import json
import multiprocessing as mp
import os
import platform
import resource
import time
from pathlib import Path

from .scheduling import Chunk, Playback


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def distribution(values):
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    import math
    return {"count": len(values), "p50": ordered[math.ceil(len(values) * .5) - 1],
            "p95": ordered[math.ceil(len(values) * .95) - 1], "max": ordered[-1]}


def create_environment(seed):
    from lerobot.envs.metaworld import MetaworldEnv

    wrapper = MetaworldEnv(task="pick-place-v3", obs_type="pixels_agent_pos", camera_name="corner2")
    wrapper.reset(seed=seed)
    raw = wrapper._env
    if float(raw.dt) != .0125:
        wrapper.close()
        raise ValueError("MetaWorld action cadence changed; requalify the experiment")
    return wrapper, raw, raw._get_obs().copy()


def receive(connection, process, timeout):
    if not connection.poll(timeout):
        raise TimeoutError(f"policy response absent after {timeout}s (child alive={process.is_alive()})")
    message = connection.recv()
    if "error" in message:
        raise RuntimeError(message["error"])
    return message


def calibrate(seed):
    import numpy as np
    wrapper, env, observation = create_environment(seed)
    physics, rendering = [], []
    try:
        for _ in range(80):
            start = time.monotonic()
            observation, *_ = env.step(np.zeros(4, dtype=np.float32))
            physics.append(time.monotonic() - start)
        for _ in range(10):
            start = time.monotonic()
            pixels = wrapper.render()
            rendering.append(time.monotonic() - start)
            if pixels.shape != (480, 480, 3):
                raise ValueError(f"unexpected camera shape {pixels.shape}")
        from OpenGL import GL
        renderer = {name: (GL.glGetString(token) or b"unknown").decode()
                    for name, token in (("vendor", GL.GL_VENDOR), ("renderer", GL.GL_RENDERER),
                                        ("version", GL.GL_VERSION))}
        return {"physics_step_s": distribution(physics), "camera_render_s": distribution(rendering),
                "control_period_s": float(env.dt), "camera_shape": list(pixels.shape), "opengl": renderer}
    finally:
        wrapper.close()


def episode(args, seed, output, connection=None, process=None):
    import numpy as np

    wrapper, env, observation = create_environment(seed)
    dt = float(env.dt)
    initial_state_sha256 = hashlib.sha256(np.asarray(observation, dtype="<f8").tobytes()).hexdigest()
    playback = Playback(args.max_age)
    frames, events = [], []
    inference_times, render_times, physics_times, ages, lags = [], [], [], [], []
    requested = completed = rejected = dropped = fallback = 0
    inflight = False
    success = False
    status = "horizon"
    started = time.monotonic()
    next_frame = started
    steps = 0
    try:
        for tick in range(args.steps):
            if time.monotonic() - started >= args.max_wall:
                status = "wall_budget_exhausted"
                break
            target = started + tick * dt
            if args.mode == "realtime":
                time.sleep(max(0, target - time.monotonic()))
            if connection and inflight and (args.mode == "lockstep" or connection.poll()):
                message = receive(connection, process, 60 if args.mode == "lockstep" else 0)
                inflight = False
                completed += 1
                inference_times.append(message["inference_s"])
                dropped += int(message["dropped"])
                chunk = Chunk(message["sequence"], message["observed_at"], dt,
                              tuple(tuple(a) for a in message["actions"]))
                if args.mode == "lockstep":
                    # Offline correctness reference consumes the first prediction.
                    # It deliberately does not qualify timestamped chunk playback.
                    offline_action = chunk.actions[0]
                elif not message["dropped"]:
                    accepted = playback.accept(chunk, time.monotonic())
                    rejected += int(not accepted)
                events.append({"type": "result", "sequence": chunk.sequence,
                               "received_at_s": time.monotonic() - started,
                               "observation_age_s": time.monotonic() - chunk.observed_at,
                               "inference_s": message["inference_s"], "dropped": message["dropped"]})

            if connection and not inflight:
                captured = time.monotonic()
                pixels = wrapper.render().copy()
                render_times.append(time.monotonic() - captured)
                if args.record and captured >= next_frame and len(frames) < 120:
                    frames.append((captured - started, pixels.copy()))
                    next_frame = captured + .1
                connection.send({"sequence": requested, "observed_at": captured,
                                 "pixels": pixels, "state": observation[:4].tolist()})
                requested += 1
                inflight = True
                if args.mode == "lockstep":
                    message = receive(connection, process, 60)
                    completed += 1
                    inference_times.append(message["inference_s"])
                    inflight = False
                    if message["dropped"]:
                        raise RuntimeError("drop injection is only supported in realtime mode")
                    offline_action = message["actions"][0]

            if args.policy == "scripted" and args.record and time.monotonic() >= next_frame and len(frames) < 120:
                captured = time.monotonic()
                frames.append((captured - started, wrapper.render().copy()))
                render_times.append(time.monotonic() - captured)
                next_frame = captured + .1

            now = time.monotonic()
            lag = max(0, now - target) if args.mode == "realtime" else 0
            if args.mode == "realtime" and lag > args.max_lag:
                lags.append(lag)
                status = "simulator_overrun"
                break
            if args.policy == "scripted":
                action = np.clip(wrapper.expert_policy.get_action(observation), -1, 1)
                evidence = {"source": "privileged_state_scripted"}
            elif args.mode == "lockstep":
                action, evidence = offline_action, {"source": "offline_first_action"}
            else:
                action, evidence = playback.action(now)
                fallback += int(evidence.get("fallback", False))
                if "observation_age_s" in evidence:
                    ages.append(evidence["observation_age_s"])
            begin_step = time.monotonic()
            observation, reward, terminated, truncated, info = env.step(np.asarray(action, dtype=np.float32))
            physics_times.append(time.monotonic() - begin_step)
            # Include the step's execution, not only its dispatch lateness.
            lag = max(lag, time.monotonic() - (target + dt)) if args.mode == "realtime" else 0
            lags.append(lag)
            steps += 1
            success = bool(info.get("success", False))
            events.append({"type": "step", "step": steps, "wall_s": time.monotonic() - started,
                           "sim_s": steps * dt, "lag_s": lag, "action": list(map(float, action)),
                           "reward": float(reward), "success": success, **evidence})
            if success or terminated or truncated:
                status = "success" if success else "terminated" if terminated else "truncated"
                break
        wall = time.monotonic() - started
        if args.record and len(frames) < 120:
            # Final-state capture occurs after the timed loop, with no further step.
            frames.append((wall, wrapper.render().copy()))
        result = {
            "seed": seed, "status": status, "success": success, "steps": steps,
            "initial_state_sha256": initial_state_sha256,
            "wall_s": wall, "simulated_s": steps * dt, "real_time_factor": steps * dt / wall,
            "physics_lag_s": distribution(lags), "physics_step_s": distribution(physics_times),
            "camera_render_s": distribution(render_times), "inference_s": distribution(inference_times),
            "observation_age_at_action_s": distribution(ages),
            "late_ticks_over_one_period": sum(v > dt for v in lags),
            "policy_requests": requested, "policy_results": completed,
            "stale_results": rejected, "dropped_results": dropped,
            "fallback_ticks": fallback, "fallback_fraction": fallback / steps if steps else None,
            "timing_pass": args.mode == "realtime" and status in {"success", "horizon", "terminated", "truncated"}
                           and bool(lags) and distribution(lags)["p95"] <= dt,
            "parent_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        output.mkdir()
        (output / "events.jsonl").write_text("".join(json.dumps(e, allow_nan=False) + "\n" for e in events))
        if frames:
            from PIL import Image
            (output / "frames").mkdir()
            for i, (_, frame) in enumerate(frames):
                Image.fromarray(frame).save(output / "frames" / f"{i:04d}.png")
            write_json(output / "frames.json", [{"wall_s": t, "file": f"frames/{i:04d}.png"}
                                                 for i, (t, _) in enumerate(frames)])
        write_json(output / "result.json", result)
        return result
    finally:
        wrapper.close()
        # Drain an outstanding result before a new episode reset. No cross-episode
        # chunks survive; a timeout aborts the run and the owned process is killed.
        if inflight and connection:
            receive(connection, process, 60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets", default="/assets")
    parser.add_argument("--mode", choices=("calibrate", "lockstep", "realtime"), default="calibrate")
    parser.add_argument("--policy", choices=("scripted", "smolvla"), default="scripted")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--seeds", default="0,1,2")
    parser.add_argument("--steps", type=int, default=500)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--max-age", type=float, default=.625)
    parser.add_argument("--max-lag", type=float, default=.25)
    parser.add_argument("--max-wall", type=float, default=90)
    parser.add_argument("--policy-delay-ms", type=float, default=0)
    parser.add_argument("--drop-every", type=int, default=0)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    import math
    seeds = [int(s) for s in args.seeds.split(",")]
    if (not 1 <= args.steps <= 500 or not 1 <= args.chunk_size <= 50 or not 1 <= len(seeds) <= 20
            or len(set(seeds)) != len(seeds) or any(s < 0 or s >= 2**32 for s in seeds)
            or any(not math.isfinite(v) or v <= 0 for v in (args.max_age, args.max_lag, args.max_wall))
            or not math.isfinite(args.policy_delay_ms) or not 0 <= args.policy_delay_ms <= 5000
            or args.drop_every < 0):
        parser.error("invalid bounded experiment configuration")
    if args.mode != "realtime" and (args.policy_delay_ms or args.drop_every):
        parser.error("fault injection requires realtime mode")
    args.output.mkdir(parents=True, exist_ok=False)
    config = vars(args).copy()
    config["output"] = str(args.output)
    manifest = {"schema_version": 1, "profile": "jetson-timing-experimental-v1", "config": config,
                "platform": platform.platform(), "python": platform.python_version(),
                "container_image": os.environ.get("CONVOY_EXPERIMENT_IMAGE"),
                "render_backend": os.environ.get("MUJOCO_GL"),
                "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in Path(__file__).parent.glob("*.py")},
                "packages": {name: importlib.metadata.version(name) for name in
                             ("torch", "torchvision", "lerobot", "mujoco", "metaworld", "numpy", "transformers")},
                "semantics": "80Hz MetaWorld control; float32; timestamped chunks; expire elapsed prefix; no RTC blending",
                "scope": "simulated physics and local policy; no physical actuation or cloud-planner claim"}
    write_json(args.output / "manifest.json", manifest)
    summary = {"status": "running", "episodes": [], "requested_episodes": len(seeds)}
    write_json(args.output / "summary.json", summary)
    process = connection = None
    try:
        if args.mode == "calibrate":
            summary.update(status="completed", calibration=calibrate(seeds[0]))
        else:
            if args.policy == "smolvla":
                from .policy import worker
                context = mp.get_context("spawn")
                connection, child = context.Pipe()
                process = context.Process(target=worker, args=(child, args.assets, args.device, args.chunk_size,
                                                               args.policy_delay_ms / 1000, args.drop_every))
                process.start()
                child.close()
                manifest["policy_runtime"] = receive(connection, process, 180)
                write_json(args.output / "manifest.json", manifest)
            for seed in seeds:
                if connection:
                    connection.send({"reset": True})
                    receive(connection, process, 15)
                summary["episodes"].append(episode(args, seed, args.output / f"seed-{seed}", connection, process))
                write_json(args.output / "summary.json", summary)
            summary["status"] = "completed"
    except BaseException as error:
        summary.update(status="error", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        if process:
            if process.is_alive():
                try:
                    connection.send(None)
                except (BrokenPipeError, OSError):
                    pass
                process.join(3)
            if process.is_alive():
                process.terminate()
                process.join(3)
            if process.is_alive():
                process.kill()
                process.join(3)
            connection.close()
        summary["task_success_rate"] = (sum(e["success"] for e in summary["episodes"]) / len(seeds)
                                        if args.mode != "calibrate" else None)
        write_json(args.output / "summary.json", summary)
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
