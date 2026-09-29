"""Colocated physics, camera and policy in three bounded, independently timed processes."""

import argparse
import hashlib
import importlib.metadata
import json
import math
import multiprocessing as mp
import os
import platform
import time
from pathlib import Path

from .metrics import assess, distribution
from .scheduling import Chunk, Playback
from .simulation import camera_worker, create_environment, render, restore, snapshot


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def receive(connection, process, timeout):
    if not connection.poll(timeout):
        raise TimeoutError(f"response absent after {timeout}s (child alive={process.is_alive()})")
    message = connection.recv()
    if "error" in message:
        raise RuntimeError(message["error"])
    return message


def stop(process):
    process.join(3)
    if process.is_alive():
        process.terminate()
        process.join(3)
    if process.is_alive():
        process.kill()
        process.join(3)


def scene_options(args):
    return {name: getattr(args, name) for name in ("camera_size", "shadow_size", "render_samples")}


def calibrate(seed, args):
    import numpy as np
    env, _, observation = create_environment(seed, **scene_options(args))
    physics, rendering = [], []
    try:
        render(env)
        for _ in range(80):
            start = time.monotonic()
            observation, *_ = env.step(np.zeros(4, dtype=np.float32))
            physics.append(time.monotonic() - start)
        for _ in range(10):
            start = time.monotonic()
            pixels = render(env)
            rendering.append(time.monotonic() - start)
            if pixels.shape != (args.camera_size, args.camera_size, 3):
                raise ValueError(f"unexpected camera shape {pixels.shape}")
        from OpenGL import GL
        renderer = {name: (GL.glGetString(token) or b"unknown").decode()
                    for name, token in (("vendor", GL.GL_VENDOR), ("renderer", GL.GL_RENDERER),
                                        ("version", GL.GL_VERSION))}
        return {"physics_step_s": distribution(physics), "camera_render_s": distribution(rendering),
                "control_period_s": float(env.dt), "camera_shape": list(pixels.shape), "opengl": renderer}
    finally:
        env.close()


def verify_camera(seed, args):
    """Verify scene/proprioception and allow only bounded 8-bit raster rounding."""
    import numpy as np
    env, expert, observation = create_environment(seed, **scene_options(args))
    replica, _, _ = create_environment(seed, **scene_options(args))
    comparisons = []
    try:
        initial = hashlib.sha256(np.asarray(observation, dtype="<f8").tobytes()).hexdigest()
        for tick in range(12):
            observation, *_ = env.step(np.clip(expert.get_action(observation), -1, 1))
            state = snapshot(env, observation, tick)
            restore(replica, state)
            for field in ("qpos", "qvel", "xpos", "xmat", "geom_xpos", "geom_xmat", "site_xpos", "cam_xpos"):
                if not np.allclose(getattr(env.data, field), getattr(replica.data, field), atol=1e-12, rtol=0):
                    raise ValueError(f"camera replica {field} differs at step {tick}")
            if not np.allclose(replica._get_curr_obs_combined_no_goal()[:4], state["state"], atol=1e-12, rtol=0):
                raise ValueError("camera/proprioception do not describe the same state")
            expected = render(env)
            actual = render(replica)
            difference = np.abs(expected.astype(np.int16) - actual.astype(np.int16))
            changed_fraction = float(np.count_nonzero(difference) / difference.size)
            if difference.max() > 1 or changed_fraction > .001:
                raise ValueError(f"rendered state replica differs at step {tick}")
            comparisons.append({"pixel_sha256": hashlib.sha256(actual.tobytes()).hexdigest(),
                                "max_channel_difference": int(difference.max()),
                                "changed_channel_fraction": changed_fraction})
        return {"verified_frames": len(comparisons), "initial_state_sha256": initial,
                "tolerance": "kinematics/proprioception 1e-12 absolute; <=1/255 color difference in <=0.1% of channels",
                "comparisons": comparisons}
    finally:
        env.close()
        replica.close()


def episode(args, seed, output, context, policy_connection=None, policy_process=None, requests=None):
    import numpy as np
    output.mkdir()
    env, expert, observation = create_environment(seed, **scene_options(args))
    dt = float(env.dt)
    initial_hash = hashlib.sha256(np.asarray(observation, dtype="<f8").tobytes()).hexdigest()
    playback = Playback(args.max_age)
    events, lags = [], []
    camera = camera_connection = None
    inflight = False
    requested = completed = rejected = dropped = 0
    success = False
    first_success_step = None
    status = "horizon"
    started = None
    steps = 0
    result = {}
    setup_started = time.monotonic()
    camera_profile = None
    try:
        if policy_connection or args.record:
            camera_connection, child = context.Pipe()
            camera = context.Process(target=camera_worker, args=(child, requests, seed, str(output), args.record,
                                                                scene_options(args)))
            camera.start()
            child.close()
            camera_profile = receive(camera_connection, camera, 90)
        started = time.monotonic()
        state_observed_at = started
        next_camera = started
        response = policy_connection or camera_connection
        responder = policy_process or camera
        for tick in range(args.steps):
            if time.monotonic() - started >= args.max_wall:
                status = "wall_budget_exhausted"
                break
            target = started + tick * dt
            if args.mode == "realtime":
                time.sleep(max(0, target - time.monotonic()))
            # Camera errors travel separately; pixels never traverse this process.
            if policy_connection and camera_connection.poll():
                receive(camera_connection, camera, 0)
            if inflight and response.poll():
                message = receive(response, responder, 0)
                received = time.monotonic()
                inflight = False
                completed += 1
                if policy_connection:
                    dropped += int(message["dropped"])
                    chunk = Chunk(message["sequence"], message["observed_at"], dt,
                                  tuple(tuple(a) for a in message.pop("actions")))
                    accepted = not message["dropped"] and playback.accept(chunk, received)
                    rejected += int(not message["dropped"] and not accepted)
                    message.update(actions_count=len(chunk.actions), accepted=accepted)
                events.append({"type": "result", **message, "received_at": received})
            if camera and not inflight and (policy_connection or time.monotonic() >= next_camera):
                message = snapshot(env, observation, requested, state_observed_at)
                camera_connection.send(message)
                requested += 1
                inflight = True
                next_camera = message["observed_at"] + .1
                if args.mode == "lockstep" and policy_connection:
                    message = receive(policy_connection, policy_process, 90)
                    inflight = False
                    completed += 1
                    offline_action = message.pop("actions")[0]
                    events.append({"type": "result", **message, "received_at": time.monotonic(),
                                   "actions_count": args.chunk_size, "accepted": True})
            now = time.monotonic()
            dispatch_lag = max(0, now - target) if args.mode == "realtime" else 0
            if args.mode == "realtime" and dispatch_lag > args.max_lag:
                status = "simulator_overrun"
                break
            if args.policy == "scripted":
                action = np.clip(expert.get_action(observation), -1, 1)
                evidence = {"source": "privileged_state_scripted"}
            elif args.mode == "lockstep":
                action, evidence = offline_action, {"source": "offline_first_action"}
            else:
                action, evidence = playback.action(now)
            begin = time.monotonic()
            observation, reward, terminated, truncated, info = env.step(np.asarray(action, dtype=np.float32))
            ended = time.monotonic()
            state_observed_at = ended
            completion_lag = max(0, ended - (target + dt)) if args.mode == "realtime" else 0
            lag = max(dispatch_lag, completion_lag)
            lags.append(lag)
            steps += 1
            if info.get("success", False):
                success = True
                if first_success_step is None:
                    first_success_step = steps
            events.append({"type": "step", "step": steps, "wall_s": ended - started,
                           "sim_s": steps * dt, "lag_s": lag, "dispatch_lag_s": dispatch_lag,
                           "completion_lag_s": completion_lag, "physics_s": ended - begin,
                           "action": list(map(float, action)), "reward": float(reward),
                           "success": bool(info.get("success", False)), **evidence})
            if lag > args.max_lag and args.mode == "realtime":
                status = "simulator_overrun"
                break
            if (success and not args.sustain) or terminated or truncated:
                status = "success" if success else "terminated" if terminated else "truncated"
                break
        wall = time.monotonic() - started
        timing = assess(events, dt, args.max_age, dt * args.chunk_size, args.policy == "smolvla",
                        args.max_fallback, args.min_results)
        if args.mode != "realtime":
            timing.update(status="offline_reference", reasons=["physics_waits_for_inference"],
                          startup_fallback_ticks=None, steady_fallback_fraction=None,
                          estimated_continuous_buffer_span_s=None, estimated_actions_for_continuity=None)
            for key in ("tick_dispatch_lag_s", "tick_completion_lag_s", "remaining_chunk_budget_s"):
                timing["stages"][key] = distribution([])
        elif status in {"simulator_overrun", "wall_budget_exhausted"}:
            timing["status"] = "failed"
            timing["reasons"].append(status)
        result = {"seed": seed, "status": status, "success": success, "first_success_step": first_success_step,
                  "steps": steps, "initial_state_sha256": initial_hash, "started_at": started,
                  "camera_setup_s": started - setup_started,
                  "camera_profile": camera_profile,
                  "wall_s": wall, "simulated_s": steps * dt, "real_time_factor": steps * dt / wall,
                  "physics_lag_s": distribution(lags), "policy_requests": requested if policy_connection else 0,
                  "policy_results": completed if policy_connection else 0, "stale_results": rejected,
                  "dropped_results": dropped, "inflight_at_end": inflight, "timing": timing,
                  "timing_pass": timing["status"] == "passed_observed_contract"}
        return result
    except BaseException as error:
        result = {"seed": seed, "status": "error", "success": success, "steps": steps,
                  "error": f"{type(error).__name__}: {error}", "timing_pass": False}
        raise
    finally:
        # Save failure evidence too; completed results outside the control window
        # are drained solely for cleanup, never counted as successful delivery.
        (output / "events.jsonl").write_text("".join(json.dumps(e, allow_nan=False) + "\n" for e in events))
        write_json(output / "result.json", result)
        env.close()
        if camera:
            try:
                if result.get("status") != "error":
                    if inflight:
                        receive(policy_connection or camera_connection, policy_process or camera, 90)
                    camera_connection.send(None)
                    receive(camera_connection, camera, 30)
            finally:
                stop(camera)
                camera_connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets", default="/assets")
    parser.add_argument("--mode", choices=("calibrate", "verify-camera", "lockstep", "realtime"), default="calibrate")
    parser.add_argument("--policy", choices=("scripted", "smolvla"), default="scripted")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--precision", choices=("float32", "float16", "bfloat16"), default="float32")
    parser.add_argument("--camera-size", type=int, choices=(256, 480), default=480)
    parser.add_argument("--vision-size", type=int, choices=(256, 512), default=512)
    parser.add_argument("--denoise-steps", type=int, choices=(1, 5, 10), default=10)
    parser.add_argument("--shadow-size", type=int, choices=(0, 512, 1024, 2048), default=0)
    parser.add_argument("--render-samples", type=int, choices=(-1, 0, 2, 4, 8), default=-1)
    parser.add_argument("--seeds", default="0,1,2")
    parser.add_argument("--steps", type=int, default=500)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--max-age", type=float, default=.625)
    parser.add_argument("--max-lag", type=float, default=.25)
    parser.add_argument("--max-wall", type=float, default=90)
    parser.add_argument("--max-fallback", type=float, default=.05)
    parser.add_argument("--min-results", type=int, default=10)
    parser.add_argument("--policy-delay-ms", type=float, default=0)
    parser.add_argument("--drop-every", type=int, default=0)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--sustain", action="store_true", help="continue after first task success to the bounded horizon")
    args = parser.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    if (not 1 <= args.steps <= 500 or not 1 <= args.chunk_size <= 50 or not 1 <= len(seeds) <= 20
            or len(set(seeds)) != len(seeds) or any(s < 0 or s >= 2**32 for s in seeds)
            or any(not math.isfinite(v) or v <= 0 for v in (args.max_age, args.max_lag, args.max_wall))
            or not math.isfinite(args.policy_delay_ms) or not 0 <= args.policy_delay_ms <= 5000
            or not 0 <= args.max_fallback <= 1 or not 1 <= args.min_results <= 1000 or args.drop_every < 0):
        parser.error("invalid bounded experiment configuration")
    if args.mode != "realtime" and (args.policy_delay_ms or args.drop_every):
        parser.error("fault injection requires realtime mode")
    if args.policy != "smolvla" and (args.policy_delay_ms or args.drop_every):
        parser.error("delivery fault injection requires the learned policy process")
    args.output.mkdir(parents=True, exist_ok=False)
    config = vars(args).copy()
    config["output"] = str(args.output)
    manifest = {"schema_version": 2, "profile": "jetson-timing-experimental-v2", "config": config,
                "platform": platform.platform(), "python": platform.python_version(),
                "container_image": os.environ.get("CONVOY_EXPERIMENT_IMAGE"),
                "render_backend": os.environ.get("MUJOCO_GL"),
                "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in Path(__file__).parent.glob("*.py")},
                "packages": {name: importlib.metadata.version(name) for name in
                             ("torch", "torchvision", "lerobot", "mujoco", "metaworld", "numpy", "transformers")},
                "semantics": "80Hz physics; separate snapshot camera and policy processes; timestamped chunks; no RTC blending",
                "scope": "local simulation/policy timing only; no physical actuation or cloud-planner claim"}
    write_json(args.output / "manifest.json", manifest)
    summary = {"status": "running", "episodes": [], "requested_episodes": len(seeds)}
    write_json(args.output / "summary.json", summary)
    process = connection = requests = None
    context = mp.get_context("spawn")
    try:
        if args.mode in {"calibrate", "verify-camera"}:
            fn = calibrate if args.mode == "calibrate" else verify_camera
            summary.update(status="completed", calibration=fn(seeds[0], args))
        else:
            if args.policy == "smolvla":
                from .policy import worker
                connection, child = context.Pipe()
                request_child, requests = context.Pipe(duplex=False)
                process = context.Process(target=worker, args=(child, request_child, args.assets, args.device,
                    args.chunk_size, args.policy_delay_ms / 1000, args.drop_every, args.precision,
                    args.camera_size, args.vision_size, args.denoise_steps))
                model_started = time.monotonic()
                process.start()
                child.close()
                request_child.close()
                manifest["policy_runtime"] = receive(connection, process, 180)
                manifest["policy_runtime"]["startup_and_warmup_s"] = time.monotonic() - model_started
                write_json(args.output / "manifest.json", manifest)
            for seed in seeds:
                if requests:
                    requests.send({"reset": True})
                    receive(connection, process, 15)
                summary["episodes"].append(episode(args, seed, args.output / f"seed-{seed}", context,
                                                     connection, process, requests))
                write_json(args.output / "summary.json", summary)
            summary["status"] = "completed"
    except BaseException as error:
        summary.update(status="error", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        if process:
            if process.is_alive():
                try:
                    requests.send(None)
                except (BrokenPipeError, OSError):
                    pass
            stop(process)
            connection.close()
            requests.close()
        summary["task_success_rate"] = (sum(e["success"] for e in summary["episodes"]) / len(seeds)
                                        if args.mode in {"realtime", "lockstep"} else None)
        write_json(args.output / "summary.json", summary)
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
