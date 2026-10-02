"""Bounded physics-state recording and post-run MuJoCo reconstruction.

The recorder copies state at control ticks, without rendering or disk I/O. Its
overhead belongs to the measured physics loop. Images are reconstructed later;
they are observer views, not camera observations supplied to a policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import re
import time
from pathlib import Path

from convoy_contracts.execution import canonical_digest, canonical_json, validate_identity
from convoy_contracts.registered import validate_manifest

KIND = "registered-mujoco-state-v1"
MAX_SAMPLES = 501
MAX_VALUES = 2048
MAX_BYTES = 16 * 1024**2
LOCAL_QUOTA = 256 * 1024**2
FIELDS = ("qpos", "qvel", "act", "ctrl", "mocap_pos", "mocap_quat")
SOURCES = {"initial", "policy", "held-policy", "fallback"}


class TraceRecorder:
    def __init__(self, model):
        self.widths = {name: int(getattr(model, size)) * multiplier for name, size, multiplier in (
            ("qpos", "nq", 1), ("qvel", "nv", 1), ("act", "na", 1), ("ctrl", "nu", 1),
            ("mocap_pos", "nmocap", 3), ("mocap_quat", "nmocap", 4))}
        self.samples = []
        self.error = "model-state-too-large" if sum(self.widths.values()) > MAX_VALUES else None

    def capture(self, data, action, source, command_id):
        if self.error:
            return
        if len(self.samples) >= MAX_SAMPLES:
            self.error = "recording-horizon-exceeded"
            self.samples.clear()
            return
        state = {name: getattr(data, name).reshape(-1).tolist() for name in FIELDS}
        if any(not math.isfinite(value) for values in state.values() for value in values):
            self.error = "nonfinite-recorded-state"
            self.samples.clear()
            return
        self.samples.append({"tick": len(self.samples), "simulation_time_s": float(data.time),
                             "captured_monotonic_ns": time.monotonic_ns(), "state": state,
                             "action": list(action) if action is not None else None,
                             "action_source": source, "policy_command_id": command_id})

    def snapshot(self):
        return {"widths": self.widths, "samples": self.samples, "error": self.error}


def export_trace(directory, manifest, identity, trace):
    """Called after physics has stopped; a recording failure never changes task outcome."""
    validate_identity(identity)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity["mission_id"]):
        raise ValueError("invalid mission file identity")
    if trace.get("error") or not trace.get("samples"):
        return {"state": "unavailable", "reason": trace.get("error") or "no-physics-samples", "kind": KIND}
    value = {"kind": KIND, "identity": identity, "manifest": manifest, **trace}
    validate_trace(value)
    payload = canonical_json(value)
    if len(payload) > MAX_BYTES:
        return {"state": "unavailable", "reason": "recording-byte-limit", "kind": KIND}
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = root / f"{identity['mission_id']}.state.json"
    digest = hashlib.sha256(payload).hexdigest()
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != payload:
            raise ValueError("mission recording is immutable")
    else:
        used = sum(path.stat().st_size for path in root.iterdir() if path.is_file())
        if used + len(payload) > LOCAL_QUOTA:
            return {"state": "unavailable", "reason": "local-recording-quota", "kind": KIND}
        temporary = destination.with_suffix(".tmp")
        try:
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)
    return {"state": "recorded-locally", "kind": KIND, "sha256": digest, "bytes": len(payload),
            "samples": len(trace["samples"]), "physics_ticks": len(trace["samples"]) - 1,
            "scope": "physics control-tick states; observer images require post-run reconstruction"}


def validate_trace(value):
    if not isinstance(value, dict) or value.get("kind") != KIND or value.get("error") is not None:
        raise ValueError("unsupported trajectory")
    validate_identity(value["identity"])
    manifest = value["manifest"]
    validate_manifest(manifest)
    if manifest.get("schema_version") != 3 or canonical_digest(manifest) != value["identity"]["release_digest"]:
        raise ValueError("trajectory release identity mismatch")
    widths, samples = value["widths"], value["samples"]
    if not isinstance(widths, dict) or set(widths) != set(FIELDS) or any(type(w) is not int or w < 0 for w in widths.values()) or sum(widths.values()) > MAX_VALUES:
        raise ValueError("invalid trajectory state widths")
    if not isinstance(samples, list) or not 1 <= len(samples) <= min(MAX_SAMPLES, manifest["execution"]["max_steps"] + 1):
        raise ValueError("invalid trajectory horizon")
    previous_ns, previous_sim = -1, -1
    period = 1 / manifest["interface"]["control_rate_hz"]
    from convoy_contracts.registered import validate_action

    for index, sample in enumerate(samples):
        if type(sample.get("tick")) is not int or sample["tick"] != index:
            raise ValueError("trajectory ticks are not contiguous")
        at, sim = sample["captured_monotonic_ns"], sample["simulation_time_s"]
        if type(at) is not int or at <= previous_ns or type(sim) not in (float, int) or not math.isfinite(sim) or sim < 0 or (index and not math.isclose(sim - previous_sim, period, abs_tol=1e-8)):
            raise ValueError("invalid trajectory clock")
        previous_ns, previous_sim = at, sim
        state = sample["state"]
        if not isinstance(state, dict) or set(state) != set(FIELDS):
            raise ValueError("incomplete model state")
        for field, width in widths.items():
            vector = state[field]
            if not isinstance(vector, list) or len(vector) != width or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
                raise ValueError("invalid model state vector")
        source, command = sample["action_source"], sample["policy_command_id"]
        if source not in SOURCES or (command is not None and (not isinstance(command, str) or not 1 <= len(command) <= 256)):
            raise ValueError("invalid action provenance")
        if index == 0:
            if source != "initial" or sample["action"] is not None or command is not None:
                raise ValueError("invalid initial frame")
        else:
            validate_action(sample["action"], manifest)
            if source == "initial" or (source == "fallback" and command is not None) or (source != "fallback" and command is None):
                raise ValueError("invalid applied action provenance")
    return value


def load_trace(path, expected_sha256):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("trajectory must be a bounded regular file")
    with path.open("rb") as stream:
        payload = stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("trajectory digest mismatch")
    return validate_trace(json.loads(payload))


def _render(connection, trace_path, expected_sha256, asset_path, asset_format, output):
    try:
        import mujoco
        import numpy as np

        from .bimanual_pill_task.recording import png_rgb8
        from .qualification.assets import mujoco_files, read_asset

        trace = load_trace(trace_path, expected_sha256)
        manifest = trace["manifest"]
        if mujoco.__version__ != manifest["environment"]["version"]:
            raise ValueError("renderer engine version differs from the release")
        xml, assets = mujoco_files(read_asset(Path(asset_path), manifest["environment"]["asset_sha256"]), asset_format)
        model = mujoco.MjModel.from_xml_string(xml, assets)
        data = mujoco.MjData(model)
        if TraceRecorder(model).widths != trace["widths"]:
            raise ValueError("model state does not match trajectory")
        root = Path(output)
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        camera = mujoco.MjvCamera()
        mujoco.mjv_defaultFreeCamera(model, camera)
        frames = []
        total = 0
        with mujoco.Renderer(model, height=320, width=320) as renderer:
            for sample in trace["samples"]:
                mujoco.mj_resetData(model, data)
                for field in FIELDS:
                    getattr(data, field)[:] = np.asarray(sample["state"][field]).reshape(getattr(data, field).shape)
                data.time = sample["simulation_time_s"]
                mujoco.mj_forward(model, data)  # Reconstruct this pose; never step or rerun a policy.
                renderer.update_scene(data, camera=camera)
                raw = png_rgb8(renderer.render(), level=3)
                total += len(raw)
                if len(raw) > 768 * 1024 or total > 64 * 1024**2:
                    raise ValueError("rendered replay exceeds its byte budget")
                name = f"{sample['tick']:04d}.png"
                (root / name).write_bytes(raw)
                frames.append({"index": sample["tick"], "file": name, "sha256": hashlib.sha256(raw).hexdigest(),
                               "simulation_time_s": sample["simulation_time_s"], "captured_monotonic_ns": sample["captured_monotonic_ns"],
                               "action": sample["action"], "action_source": sample["action_source"], "policy_command_id": sample["policy_command_id"]})
        index = {"kind": KIND, "trajectory_sha256": expected_sha256, "identity": trace["identity"],
                 "action_labels": manifest["interface"]["joint_names"], "frames": frames,
                 "source": "Observer reconstruction from recorded MuJoCo states; not policy camera observations",
                 "camera": {"kind": "free-observer", "lookat": camera.lookat.tolist(), "distance": camera.distance,
                            "azimuth": camera.azimuth, "elevation": camera.elevation},
                 "engine_version": mujoco.__version__, "image_width": 320, "image_height": 320}
        # Only this final marker describes a complete rendering; interrupted folders remain partial.
        (root / "index.json").write_bytes(canonical_json(index))
        connection.send({"frames": len(frames), "bytes": total, "trajectory_sha256": expected_sha256})
    except Exception as error:
        connection.send({"error": str(error)[:250]})
    finally:
        connection.close()


def render_trace(trace_path, expected_sha256, asset_path, asset_format, output, timeout=90):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_render, args=(child, trace_path, expected_sha256, asset_path, asset_format, output))
    try:
        process.start()
        child.close()
        if not parent.poll(timeout):
            raise TimeoutError("replay renderer exceeded its budget")
        result = parent.recv()
        process.join(timeout=2)
        if process.is_alive() or process.exitcode != 0:
            raise RuntimeError("replay renderer did not stop cleanly")
        if "error" in result:
            raise ValueError(result["error"])
        return result
    finally:
        if process.pid is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2)
        if process.pid is not None and process.is_alive():
            process.kill()
            process.join(timeout=2)
        parent.close()
        child.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--sha256", required=True, help="trajectory digest from the task result")
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--asset-format", choices=("mjcf", "bundle"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="new directory; existing results are never overwritten")
    args = parser.parse_args(argv)
    print(json.dumps(render_trace(args.trace, args.sha256, args.asset, args.asset_format, args.output)))


if __name__ == "__main__":
    main()
