"""Render completed hierarchy traces and export the existing offline replay format.

Rendering runs after timing capture, on a host with the same simulator versions.
These reconstructed observer images were not observations supplied to the policy.
All control ticks remain in the replay; only camera images are downsampled.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import shutil
import struct
import time
import zlib
from importlib.metadata import version
from pathlib import Path

import numpy as np

from .scene import SawyerScene

MAX_STEPS = 2048
MAX_IMAGES = 512
MAX_BODY = 16 * 1024**2
MAX_IMAGE = 256 * 1024
POLICY_LABEL = "Scripted Sawyer reference · privileged state · not learned"
EXPERIMENT = "sawyer-system1-system2-v1"
HIERARCHY_FIELDS = {"planner_state", "task_revision", "active_skill", "target",
                    "planner_latency_ms", "observation_age_ms", "physics_lag_ms"}
SCALARS = ("task_success", "physics_steps", "wall_duration_s", "simulated_duration_s",
           "simulation_wall_lag_s", "dropped_scheduler_slots", "hold_ticks", "fallback_ticks",
           "final_target_distance_m", "planner_requests", "accepted_plans", "rejected_plans",
           "planner_timeouts", "cancelled_inflight_requests")
DISTRIBUTIONS = ("physics_dispatch_lag_ms", "physics_completion_lag_ms", "observation_to_action_ms",
                 "controller_gap_ms", "planner_latency_ms")
OUTCOMES = {"task_success": "success", "duration_completed": "timeout", "error": "failure",
            "cancelled": "failure"}


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def _png(pixels: np.ndarray) -> bytes:
    """Encode a bounded RGB image without introducing a video/image dependency."""
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("Renderer must produce an 8-bit RGB image")
    height, width, _ = pixels.shape
    if not 1 <= width <= 320 or not 1 <= height <= 320:
        raise ValueError("Replay images must be at most 320 pixels on each side")
    rows = b"".join(b"\0" + row.tobytes() for row in pixels)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    encoded = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")
    if len(encoded) > MAX_IMAGE:
        raise ValueError("Rendered image exceeds the replay image limit")
    return encoded


def _restore(scene: SawyerScene, state: dict) -> None:
    import mujoco

    env = scene.env.unwrapped
    for key in ("qpos", "qvel", "mocap_pos", "mocap_quat", "ctrl"):
        if key not in state:
            continue
        values = np.asarray(state[key], dtype=np.float64)
        destination = getattr(env.data, key)
        if values.shape != destination.shape or not np.isfinite(values).all():
            raise ValueError(f"Recorded {key} does not match the simulator model")
        destination[:] = values
    goal = np.asarray(state["goal_position"], dtype=np.float64)
    if goal.shape != (3,) or not np.isfinite(goal).all():
        raise ValueError("Recorded goal position is invalid")
    env._target_pos = goal.copy()
    env.model.site("goal").pos = goal
    scene.target = state["goal_target"]
    env.data.time = state["simulation_time_s"]
    mujoco.mj_forward(env.model, env.data)


def _image_indices(rows: list[dict], interval_s: float) -> set[int]:
    indices, last_time = [0], rows[0]["elapsed_s"]
    for index, row in enumerate(rows[1:], 1):
        if row["elapsed_s"] - last_time >= interval_s:
            indices.append(index)
            last_time = row["elapsed_s"]
    if indices[-1] != len(rows) - 1:
        indices.append(len(rows) - 1)
    if len(indices) > MAX_IMAGES:
        indices = [indices[index] for index in np.linspace(0, len(indices) - 1, MAX_IMAGES, dtype=int)]
    return set(indices)


def _metrics(summary: dict, metadata: dict) -> dict:
    metrics = {name: summary[name] for name in SCALARS}
    for name in DISTRIBUTIONS:
        for percentile in ("p95", "max"):
            metrics[f"{name.removesuffix('_ms')}_{percentile}_ms"] = summary[name][percentile]
    provenance = metadata.get("provenance", {})
    metrics.update(
        trial_status=summary["status"], measurement_source="reported_hierarchy_experiment",
        edge_host=provenance.get("edge_host", "not reported"),
        planner_host=provenance.get("planner_host", "not reported"),
        policy_kind="scripted_privileged_state_reference", mode=summary["mode"],
        fault_profile=provenance.get("fault_profile", provenance.get("fault_source", "not reported")),
        evidence_scope="unsigned imported scheduling experiment; reconstructed observer images",
    )
    if len(metrics) > 32 or len(json.dumps(metrics, separators=(",", ":")).encode()) > 4096:
        raise ValueError("Exported metrics exceed the existing offline-import bounds")
    for value in metrics.values():
        if isinstance(value, str) and (len(value) > 200 or any(ord(char) < 32 for char in value)):
            raise ValueError("Reported provenance exceeds the metric text bounds")
    return metrics


def _episode(run: Path, output: Path, interval_s: float) -> dict:
    import mujoco

    metadata, summary = _read_json(run / "metadata.json"), _read_json(run / "summary.json")
    if metadata.get("experiment") != EXPERIMENT:
        raise ValueError(f"{run}: unsupported hierarchy experiment")
    packages = {name: version(name) for name in ("metaworld", "mujoco", "numpy")}
    if packages != metadata["packages"]:
        raise ValueError("Post-run reconstruction requires the recorded simulator package versions")
    rows = [json.loads(line) for line in (run / "steps.jsonl").read_text().splitlines() if line]
    steps = len(rows) - 1
    if not 1 <= steps <= MAX_STEPS or summary["physics_steps"] != steps:
        raise ValueError(f"{run}: replay needs 1..{MAX_STEPS} complete recorded control ticks")
    if rows[0]["type"] != "reset" or any(row["sequence"] != index for index, row in enumerate(rows)):
        raise ValueError("Recorded control ticks must be contiguous from reset 0")
    if summary["status"] not in OUTCOMES:
        raise ValueError("Experiment status has no truthful offline-outcome mapping")
    selected = _image_indices(rows, interval_s)
    frames = output / "frames"
    frames.mkdir(parents=True, exist_ok=False)
    scene = SawyerScene(summary["seed"], max_steps=steps + 1, frames=False)
    renderer = None
    body_frames, image_bytes = [], 0
    started = time.monotonic()
    try:
        renderer = mujoco.Renderer(scene.env.unwrapped.model, width=320, height=240)
        for index, row in enumerate(rows):
            frame = {"index": index, "action": row["action"] if index else None,
                     "reward": row.get("reward") if index else None,
                     "success": row.get("success") if index else None, "policy_ms": None,
                     "hierarchy": dict(row["hierarchy"])}
            if set(frame["hierarchy"]) - HIERARCHY_FIELDS:
                raise ValueError("Recorded hierarchy metadata has unsupported fields")
            encoded = None
            if index in selected:
                _restore(scene, row)
                renderer.update_scene(scene.env.unwrapped.data, camera="corner3")
                encoded = _png(renderer.render())
                (frames / f"{index:06d}.png").write_bytes(encoded)
                image_bytes += len(encoded)
            _write_json(frames / f"{index:06d}.json", frame)
            body_frames.append({**frame, "image_png_base64": base64.b64encode(encoded).decode() if encoded else None})
    finally:
        if renderer is not None:
            renderer.close()
        scene.close()
    replay = {"steps": steps, "seed": summary["seed"], "outcome": OUTCOMES[summary["status"]],
              "metrics": _metrics(summary, metadata), "action_labels": ["dx", "dy", "dz", "grip"],
              "skill": "pick_place", "wall_seconds": summary["wall_duration_s"],
              "sim_seconds": summary["simulated_duration_s"]}
    body = {key: value for key, value in replay.items() if key != "steps"} | {"frames": body_frames}
    body_bytes = len(json.dumps(body, separators=(",", ":"), allow_nan=False).encode())
    if body_bytes > MAX_BODY:
        raise ValueError("Episode exceeds 16 MiB; export with a larger image interval (all ticks are retained)")
    _write_json(output / "replay.json", replay)
    # Full distributions and source events remain unabridged as local evidence.
    for filename in ("summary.json", "metadata.json", "steps.jsonl", "planner-events.jsonl"):
        shutil.copyfile(run / filename, output / filename)
    rendering = {"source": "post_run_state_reconstruction", "measurement_source": "reported_hierarchy_experiment",
                 "observer_images_only": True, "packages": packages, "steps": steps,
                 "images": len(selected), "image_dimensions": [320, 240], "image_interval_s": interval_s,
                 "image_bytes": image_bytes, "upload_body_bytes": body_bytes,
                 "render_wall_seconds": time.monotonic() - started,
                 "source_sha256": {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                                   for name in ("summary.json", "metadata.json", "steps.jsonl", "planner-events.jsonl")}}
    _write_json(output / "rendering.json", rendering)
    return {"episode": output.name, "outcome": replay["outcome"], **rendering}


def export_runs(source: Path, output: Path, *, name: str = "System 1 + System 2 · measured scheduling",
                config_label: str = "Local, blocking remote and asynchronous remote",
                image_interval_s: float = 0.25) -> dict:
    """Export a completed episode or every episode under a completed matrix root."""
    if not math.isfinite(image_interval_s) or not .05 <= image_interval_s <= 10:
        raise ValueError("Image interval must be finite and between 0.05 and 10 seconds")
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or source in output.parents:
        raise ValueError("Replay export must be outside the original timing evidence")
    runs = [source] if (source / "summary.json").is_file() else sorted(path.parent for path in source.rglob("summary.json"))
    if not runs:
        raise ValueError("No completed hierarchy episodes were found")
    labels = {"name": name, "task": "Sawyer puck into target region with goal revisions",
              "config_label": config_label, "policy_label": POLICY_LABEL}
    if any(not 1 <= len(value) <= 120 or any(ord(char) < 32 for char in value) for value in labels.values()):
        raise ValueError("Offline evaluation labels must be printable text of 1..120 characters")
    output.mkdir(parents=True, exist_ok=False)
    reports = []
    for index, run in enumerate(runs):
        label = run.name if run == source else str(run.relative_to(source))
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-")[:80] or "episode"
        reports.append(_episode(run, output / f"{index:03d}-{slug}", image_interval_s))
    _write_json(output / "evaluation.json", labels)
    report = {"source": "post_run_state_reconstruction", "episodes": reports,
              "scope": "unsigned offline replay; timing copied from completed experiment; reference policy is not learned"}
    _write_json(output / "export.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="completed episode or matrix evidence directory")
    parser.add_argument("--output", type=Path, required=True, help="new export directory outside the original evidence")
    parser.add_argument("--name", default="System 1 + System 2 · measured scheduling")
    parser.add_argument("--config-label", default="Local, blocking remote and asynchronous remote")
    parser.add_argument("--image-interval-s", type=float, default=.25)
    args = parser.parse_args(argv)
    report = export_runs(args.input, args.output, name=args.name, config_label=args.config_label,
                         image_interval_s=args.image_interval_s)
    print(json.dumps({"episodes": len(report["episodes"]),
                      "steps": sum(row["steps"] for row in report["episodes"]),
                      "images": sum(row["images"] for row in report["episodes"]),
                      "source": report["source"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
