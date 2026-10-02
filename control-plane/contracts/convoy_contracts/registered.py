"""Explicit SI joint interfaces for versioned, registered robot simulations."""
from .execution import _digest, _integer, _keys, _number, _text, _vector

REGISTERED_PROFILE = "registered-joint-policy-v1"


def validate_manifest(value):
    _keys(value, {"schema_version", "profile", "policy", "environment", "interface", "task", "execution"}, "registered manifest")
    _integer(value["schema_version"], "schema_version", 3, 3)
    if value["profile"] != REGISTERED_PROFILE:
        raise ValueError("unsupported registered execution profile")
    _keys(value["policy"], {"runtime", "artifact_sha256"}, "policy")
    _text(value["policy"]["runtime"], "policy runtime")
    _digest(value["policy"]["artifact_sha256"], "policy artifact")
    env = value["environment"]
    _keys(env, {"engine", "version", "robot_profile_sha256", "asset_sha256"}, "environment")
    if env["engine"] != "mujoco":
        raise ValueError("registered execution currently requires MuJoCo")
    _text(env["version"], "engine version")
    _digest(env["robot_profile_sha256"], "robot profile")
    _digest(env["asset_sha256"], "simulation asset")
    interface = value["interface"]
    _keys(interface, {"joint_names", "command_interface", "action_bounds", "control_rate_hz"}, "interface")
    names = interface["joint_names"]
    if not isinstance(names, list) or not 1 <= len(names) <= 128:
        raise ValueError("name between 1 and 128 actuated joints")
    for name in names:
        _text(name, "joint name", 120)
    if len(set(names)) != len(names):
        raise ValueError("joint names must be unique")
    if interface["command_interface"] != "joint-position":
        raise ValueError("this execution adapter requires joint-position commands in SI units")
    _number(interface["control_rate_hz"], "control rate", 0.1, 1000)
    bounds = interface["action_bounds"]
    if not isinstance(bounds, list) or len(bounds) != len(names):
        raise ValueError("each joint needs an action bound")
    for pair in bounds:
        _vector(pair, 2, "joint bound", 1e6)
        if pair[0] >= pair[1]:
            raise ValueError("joint bounds must be ordered")
    task = value["task"]
    _keys(task, {"instruction", "target_joint_positions", "position_tolerance", "velocity_tolerance"}, "task")
    _text(task["instruction"], "instruction", 1000)
    validate_action(task["target_joint_positions"], value)
    _number(task["position_tolerance"], "position tolerance", 1e-6, 1)
    _number(task["velocity_tolerance"], "velocity tolerance", 1e-6, 1)
    execution = value["execution"]
    fields = {"max_steps", "decision_timeout_ms", "mission_timeout_s"}
    _keys(execution, fields | ({"timing"} if isinstance(execution, dict) and "timing" in execution else set()), "execution")
    if "timing" in execution:
        timing = execution["timing"]
        _keys(timing, {"mode", "max_observation_age_ms", "max_physics_lag_ms", "fallback"}, "timing")
        if timing["mode"] != "realtime" or timing["fallback"] != "hold-position":
            raise ValueError("unsupported timing mode or fallback")
        _integer(timing["max_observation_age_ms"], "observation age", 1, 30000)
        _integer(timing["max_physics_lag_ms"], "physics lag", 1, 1000)
        if interface["control_rate_hz"] < 10:
            raise ValueError("real-time simulation requires at least 10 control steps per second")
    _integer(execution["max_steps"], "max_steps", 1, 500)
    _integer(execution["decision_timeout_ms"], "decision timeout", 1, 30000)
    _integer(execution["mission_timeout_s"], "mission timeout", 1, 3600)
    return value


def validate_action(action, manifest):
    interface = manifest["interface"]
    _vector(action, len(interface["joint_names"]), "joint action", 1e6)
    for position, bounds in zip(action, interface["action_bounds"], strict=True):
        if not bounds[0] <= position <= bounds[1]:
            raise ValueError("joint action exceeds the declared limits")
    return action


def validate_observation(value, manifest):
    _keys(value, {"positions", "velocities", "simulation_time_s"}, "joint observation")
    count = len(manifest["interface"]["joint_names"])
    _vector(value["positions"], count, "joint positions", 1e6)
    _vector(value["velocities"], count, "joint velocities", 1e6)
    _number(value["simulation_time_s"], "simulation time", 0, 86400)
    return value


def validate_robot_binding(manifest, profile_digest, spec, engine):
    validate_manifest(manifest)
    model = next((m for m in spec["simulations"] if m["engine"] == engine), None)
    if (model is None or manifest["environment"] != {
        "engine": engine, "version": model["engine_version"], "robot_profile_sha256": profile_digest,
        "asset_sha256": model["asset"]["sha256"],
    }):
        raise ValueError("release does not match the registered robot profile and simulation asset")
    joints = [j for j in spec["joints"] if j["kind"] != "fixed"]
    if (model["controller"] != "position" or manifest["interface"] != {
        "joint_names": [j["name"] for j in joints], "command_interface": spec["command_interface"],
        "action_bounds": [[j.get("lower"), j.get("upper")] for j in joints],
        "control_rate_hz": spec["control_rate_hz"],
    }):
        raise ValueError("release joint interface does not match the physical profile")
    return model
