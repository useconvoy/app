"""MetaWorld scene without importing the model stack into the physics process.

Setup follows LeRobot's MetaWorld wrapper: MT1 seed 42, first task, randomized
resets, corner2 pose and double-axis image flip. Physics never calls render.
"""

import time


def create_environment(seed, camera_size=480, shadow_size=0, render_samples=-1):
    import metaworld
    from metaworld.policies import SawyerPickPlaceV3Policy

    suite = metaworld.MT1("pick-place-v3", seed=42)
    env = suite.train_classes["pick-place-v3"](render_mode="rgb_array", camera_name="corner2",
                                               width=camera_size, height=camera_size)
    if shadow_size:
        env.model.vis.quality.shadowsize = shadow_size
    if render_samples >= 0:
        env.model.vis.quality.offsamples = render_samples
    env.set_task(suite.train_tasks[0])
    env.model.cam_pos[2] = [0.75, 0.075, 0.7]
    env.reset()
    env._freeze_rand_vec = False
    env.seeded_rand_vec = True
    env.seed(seed)
    observation, _ = env.reset(seed=seed)
    if float(env.dt) != .0125:
        env.close()
        raise ValueError("MetaWorld action cadence changed; requalify the experiment")
    return env, SawyerPickPlaceV3Policy(), observation.copy()


def snapshot(env, observation, sequence, observed_at=None):
    import mujoco
    import numpy as np

    if observed_at is None:
        observed_at = time.monotonic()
    # mj_step can leave rendering transforms at the last pre-integration state.
    # Canonicalize kinematics before capturing pixels + proprioception together.
    # This small CPU operation is included in the control-loop timing.
    mujoco.mj_forward(env.model, env.data)
    proprioception = env._get_curr_obs_combined_no_goal()[:4].copy()
    spec = mujoco.mjtState.mjSTATE_INTEGRATION
    state = np.empty(mujoco.mj_stateSize(env.model, spec))
    mujoco.mj_getState(env.model, env.data, state, spec)
    return {"sequence": sequence, "observed_at": observed_at,
            "integration_state": state, "body_pos": env.model.body_pos.copy(),
            "site_pos": env.model.site_pos.copy(), "state": proprioception.tolist(),
            "sim_time": float(env.data.time)}


def restore(env, message):
    import mujoco

    env.model.body_pos[:] = message["body_pos"]
    env.model.site_pos[:] = message["site_pos"]
    mujoco.mj_setState(env.model, env.data, message["integration_state"],
                       mujoco.mjtState.mjSTATE_INTEGRATION)
    mujoco.mj_forward(env.model, env.data)


def render(env):
    import numpy as np

    return np.flip(env.render(), (0, 1)).copy()


def camera_worker(connection, policy_connection, seed, output, record, scene_options):
    """One snapshot in flight. Pixels go directly to inference, never via physics.

    Captured timestamps describe the physics state, not the later render finish.
    Frames are bounded in RAM and written only after the timed episode.
    """
    import hashlib
    import json
    from pathlib import Path

    env = None
    frames = []
    next_frame = 0
    try:
        env, _, _ = create_environment(seed, **scene_options)
        render(env)  # Warm EGL before the control clock starts.
        connection.send({"ready": True, "camera_size": env.width,
                         "shadow_size": int(env.model.vis.quality.shadowsize),
                         "render_samples": int(env.model.vis.quality.offsamples)})
        while True:
            message = connection.recv()
            if message is None:
                break
            started = time.monotonic()
            restore(env, message)
            pixels = render(env)
            finished = time.monotonic()
            metadata = {k: message[k] for k in ("sequence", "observed_at", "state", "sim_time")}
            metadata.update(render_started_at=started, render_finished_at=finished,
                            render_s=finished - started)
            if record and message["observed_at"] >= next_frame and len(frames) < 120:
                frames.append((message["observed_at"], message["sim_time"], pixels.copy()))
                next_frame = message["observed_at"] + .1
            if message.get("verify"):
                connection.send({**metadata, "pixel_sha256": hashlib.sha256(pixels.tobytes()).hexdigest()})
            elif policy_connection:
                policy_connection.send({**metadata, "pixels": pixels})
            else:
                connection.send(metadata)
        if frames:
            from PIL import Image
            root = Path(output)
            (root / "frames").mkdir(parents=True)
            for i, (_, _, frame) in enumerate(frames):
                Image.fromarray(frame).save(root / "frames" / f"{i:04d}.png")
            (root / "frames.json").write_text(json.dumps([
                {"captured_at": t, "sim_s": sim, "file": f"frames/{i:04d}.png"}
                for i, (t, sim, _) in enumerate(frames)], indent=2) + "\n")
        connection.send({"closed": True, "recorded_frames": len(frames)})
    except (EOFError, BrokenPipeError):
        pass
    except Exception as error:
        connection.send({"error": f"camera: {type(error).__name__}: {error}"})
    finally:
        if env:
            env.close()
        connection.close()
        if policy_connection:
            policy_connection.close()
