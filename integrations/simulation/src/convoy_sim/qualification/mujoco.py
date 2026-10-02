"""Actual MuJoCo model/controller/camera checks, independent of policy/task success."""
from __future__ import annotations

import platform
import time
from pathlib import Path

from .assets import mujoco_files, read_asset


def qualify(spec: dict, model_spec: dict, path: Path) -> dict:
    import mujoco
    import numpy as np

    started = time.perf_counter()
    payload = read_asset(path, model_spec["asset"]["sha256"])
    if mujoco.__version__ != model_spec["engine_version"]:
        raise ValueError(f"engine version mismatch: expected {model_spec['engine_version']}, installed {mujoco.__version__}")
    xml, assets = mujoco_files(payload, model_spec["asset"]["format"])
    model = mujoco.MjModel.from_xml_string(xml, assets)
    if not np.isfinite(model.opt.timestep) or not 0 < model.opt.timestep <= 0.1:
        raise ValueError("physics timestep must be finite and at most 100 ms")
    period = 1 / spec["control_rate_hz"]
    substeps = round(period / model.opt.timestep)
    if not 1 <= substeps <= 100 or not np.isclose(substeps * model.opt.timestep, period, rtol=1e-5):
        raise ValueError("controller cadence must be an integer number of 1–100 physics steps")
    expected_controller = {"joint-position": "position", "joint-velocity": "velocity", "joint-torque": "torque"}
    controller = expected_controller.get(spec["command_interface"])
    if controller is None or model_spec["controller"] != controller:
        raise ValueError("select a position, velocity or torque joint controller matching the profile")
    joints = [joint for joint in spec["joints"] if joint["kind"] != "fixed"]
    if not joints or model.nu != len(joints):
        raise ValueError("profile must name exactly the actuated robot joints")
    qpos, actuators = [], []
    for joint in joints:
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint["name"])
        expected = mujoco.mjtJoint.mjJNT_SLIDE if joint["kind"] == "prismatic" else mujoco.mjtJoint.mjJNT_HINGE
        if jid < 0 or model.jnt_type[jid] != expected:
            raise ValueError(f"joint missing or wrong type: {joint['name']}")
        if joint["kind"] == "continuous" and model.jnt_limited[jid]:
            raise ValueError(f"continuous joint is limited in the model: {joint['name']}")
        if joint.get("lower") is not None and (not model.jnt_limited[jid] or not np.allclose(
                model.jnt_range[jid], [joint["lower"], joint["upper"]], rtol=1e-5, atol=1e-6)):
            raise ValueError(f"joint limits differ from the profile: {joint['name']}")
        matched = np.flatnonzero((model.actuator_trnid[:, 0] == jid)
                                & (model.actuator_trntype == mujoco.mjtTrn.mjTRN_JOINT))
        if len(matched) != 1:
            raise ValueError(f"joint requires exactly one direct actuator: {joint['name']}")
        aid = int(matched[0])
        if not np.allclose(model.actuator_gear[aid], [1, 0, 0, 0, 0, 0]):
            raise ValueError("non-unit actuator gearing requires an explicit controller adapter")
        gain, bias = model.actuator_gainprm[aid], model.actuator_biasprm[aid]
        if model.actuator_dyntype[aid] != mujoco.mjtDyn.mjDYN_NONE or model.actuator_gaintype[aid] != mujoco.mjtGain.mjGAIN_FIXED:
            raise ValueError("dynamic or custom actuator gains require an explicit controller adapter")
        if controller != "torque" and model.actuator_biastype[aid] != mujoco.mjtBias.mjBIAS_AFFINE:
            raise ValueError("servo actuator requires affine position/velocity feedback")
        if controller == "position" and (gain[0] <= 0 or not np.isclose(bias[1], -gain[0]) or bias[2] > 0 or bias[0] != 0):
            raise ValueError("actuator does not implement position control")
        if controller == "velocity" and (gain[0] <= 0 or not np.isclose(bias[2], -gain[0]) or bias[1] != 0 or bias[0] != 0):
            raise ValueError("actuator does not implement velocity control")
        if controller == "torque" and (not np.isclose(gain[0], 1) or not np.allclose(bias, 0)):
            raise ValueError("actuator does not implement direct joint torque")
        qpos.append(int(model.jnt_qposadr[jid]))
        actuators.append(aid)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    initial = data.qpos[qpos].copy()
    target = initial + 0.01 if controller == "position" else np.full(len(joints), 0.01)
    for i, joint in enumerate(joints):
        if controller == "position" and joint.get("lower") is not None:
            target[i] = np.clip(target[i], joint["lower"], joint["upper"])
        aid = actuators[i]
        if model.actuator_ctrllimited[aid] and not model.actuator_ctrlrange[aid, 0] <= target[i] <= model.actuator_ctrlrange[aid, 1]:
            raise ValueError("calibration probe is outside actuator control bounds")
    data.ctrl[actuators] = target
    steps = min(1000, substeps * 20)
    for _ in range(steps):
        mujoco.mj_step(model, data)
        if not np.all(np.isfinite(data.qpos)) or not np.all(np.isfinite(data.qvel)) or np.any(data.warning.number):
            raise ValueError("physics produced nonfinite state or a simulator warning")
    cameras = []
    for sensor in spec["sensors"]:
        if sensor["kind"] == "camera":
            if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, sensor["name"]) < 0:
                raise ValueError(f"camera not found: {sensor['name']}")
            cameras.append(sensor["name"])
        elif sensor["kind"] != "joint-state":
            raise ValueError(f"sensor kind requires an explicit mapping: {sensor['kind']}")
    checks = ["asset-digest", "model-load", "joint-contract", "controller-contract", "physics-step"]
    if cameras:
        with mujoco.Renderer(model, height=128, width=128) as renderer:
            for camera in cameras:
                renderer.update_scene(data, camera=camera)
                pixels = renderer.render()
                if pixels.shape != (128, 128, 3) or not np.all(np.isfinite(pixels)):
                    raise ValueError("camera did not produce a valid RGB image")
        checks.append("camera-render")
    return {"engine_version": mujoco.__version__, "joint_names": [j["name"] for j in joints],
            "camera_names": cameras, "command_interface": spec["command_interface"], "checks": checks,
            "steps": steps, "sim_seconds": float(data.time), "wall_seconds": time.perf_counter() - started,
            "max_joint_displacement": float(np.max(np.abs(data.qpos[qpos] - initial))),
            "host": {"system": platform.system(), "architecture": platform.machine(), "python": platform.python_version()}}
