"""Export episodes in the platform's offline replay format (an offline evaluation import).

``OfflineReplayRecorder`` writes one episode directory that
``scripts/import_offline_eval.py`` uploads (``docs/v1/offline-evaluations.md``)::

    <episode>/replay.json         steps, seed, outcome, metrics, action_labels, skill, planner_ms, sim_seconds
    <episode>/frames/NNNN.json    index, action, reward, success, policy_ms (frame 0: the image only)
    <episode>/frames/NNNN.jpg     the head camera before the first step / after step NNNN
    <episode>/summary.json        the full episode summary (kept locally, not uploaded)

``write_evaluation`` puts the evaluation's labels (``evaluation.json``) beside the
episode directories.

* One replay step is 0.5 s of simulated time, about one skill phase (approach,
  descend, close, lift, transfer, release...). Steps are capped at 299, so an
  episode has at most 300 frames: over the 150 s horizon the last step runs to
  the end.
* Frames are the head camera rendered at 512 x 512 and averaged down to
  256 x 256, as baseline JPEG (quality 80, ~6-10 KiB). A frame that differs
  from the last stored image by at most 8 levels in every pixel (an arm
  holding while a planner answers) carries no image: the player repeats the
  previous one.
* The action is 8 values, ``L x, L y, L z, L grip, R x, R y, R z, R grip``:
  each arm's mean commanded TCP velocity over the step divided by the 0.6 m/s
  skill speed limit, and its gripper command (-1 open 80 mm, +1 closed).
* ``reward`` is the fraction of pills in the bottle after the step and
  ``success`` whether all of them are. ``policy_ms`` is the measured wall time
  of the skill controller (skill update and IK of both arms) during the step.
* Episode times are simulated: ``sim_seconds`` is set and ``wall_seconds`` is
  left out, so Convoy shows simulated time. ``planner_ms`` is the median
  modeled latency of the episode's skill-planner calls.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np

from .recording import png_rgb8

RECORD_DT = 0.5
MAX_STEPS = 299
FRAME_SIZE = 256
RENDER_SCALE = 2
JPEG_QUALITY = 80
MAX_IMAGE_BYTES = 256 * 1024  # the import's per-image limit
REPEAT_LEVELS = 8  # a frame within this many levels of the last stored image repeats it
ACTION_LABELS = ("L x", "L y", "L z", "L grip", "R x", "R y", "R z", "R grip")
SUCCESS, TIMEOUT, FAILURE = "success", "timeout", "failure"


def outcome(summary: dict) -> str:
    """The platform outcome: all pills in (success), the horizon (timeout), anything else (failure)."""
    if summary.get("status") == "error":
        return FAILURE
    if summary.get("success"):
        return SUCCESS
    return TIMEOUT if summary.get("outcome") == "horizon" else FAILURE


def episode_metrics(summary: dict) -> dict:
    """At most 32 lower_snake_case metrics (numbers, booleans, short text) for the import."""
    skills = summary.get("skills", {})

    def count(*names: str) -> int:
        return int(sum(skills.get(name, 0) for name in names))

    return {
        "pills_total": summary["pills"],
        "pills_placed": summary["placed"],
        "fraction_placed": round(summary["fraction_placed"], 4),
        "time_to_all_placed_s": summary.get("time_to_all_placed_s"),
        "planner_calls": summary["planner_calls"],
        "planner_failures": summary["planner_failures"],
        "planner_p50_ms": summary.get("planner_latency_p50_ms"),
        "planner_p95_ms": summary.get("planner_latency_p95_ms"),
        "planner_wait_s": summary.get("planner_wait_s"),
        "skill_calls": summary.get("skill_attempts", 0),
        "picks_placed": count("placed"),
        "picks_dropped": count("missed", "lost"),
        "grasp_failures": count("grasp_failed", "pill_moved", "blocked", "timeout"),
        "pushes": count("pushed"),
        "skills_refused": count("no_clear_grasp", "no_clear_push", "unreachable", "policy_unavailable"),
        "protective_stops": summary.get("protective_stops", 0),
        "operator_pages": summary.get("operator_pages", 0),
        "arm_arm_contacts": summary.get("arm_arm_contacts", 0),
        "max_contact_force_n": summary.get("max_robot_env_contact_force_n"),
        "max_bottle_tilt_deg": summary.get("max_bottle_tilt_deg"),
        "pills_lost": summary.get("lost_off_table", 0),
        "stale_decisions_rejected": summary.get("rejected_decisions", 0),
        "network_outage_s": summary.get("network_outage_s", 0.0),
        "motor_policy_available": bool(summary.get("motor_policy_available", True)),
        "sim_compute_wall_s": summary.get("wall_duration_s"),
        "config": summary["config"],
        "slice": summary["slice"],
        "end_reason": str(summary.get("outcome", summary.get("error", "")))[:200],
    }


def write_evaluation(directory: Path, config, task: str, name: str | None = None) -> Path:
    """evaluation.json: the labels the import creates the offline evaluation with."""
    directory.mkdir(parents=True, exist_ok=True)
    labels = {"name": name or f"Pills to bottle · {config.label}", "task": task,
              "config_label": config.deployment or config.label, "policy_label": config.policy or config.motor.name}
    for key, value in labels.items():
        if not 0 < len(value) <= 120:
            raise ValueError(f"{key} must be 1-120 characters: {value!r}")
    path = directory / "evaluation.json"
    path.write_text(json.dumps(labels, indent=2, ensure_ascii=False) + "\n")
    return path


def _downsample(image: np.ndarray, factor: int) -> np.ndarray:
    if factor == 1:
        return np.ascontiguousarray(image, dtype=np.uint8)
    h, w, c = image.shape
    blocks = image[: h - h % factor, : w - w % factor].reshape(h // factor, factor, w // factor, factor, c)
    return np.round(blocks.mean(axis=(1, 3))).astype(np.uint8)


def encode_jpeg(image: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, "JPEG", quality=quality, optimize=True)  # baseline, 4:2:0
    return buffer.getvalue()


class OfflineReplayRecorder:
    """Records one episode in the offline replay format (see the module docstring)."""

    record_dt = RECORD_DT

    def __init__(self, output: Path, *, seed: int | None = None, camera: str = "head_camera",
                 size: int = FRAME_SIZE, render_scale: int = RENDER_SCALE, quality: int = JPEG_QUALITY,
                 max_steps: int = MAX_STEPS, image_format: str = "jpeg", frame_source=None,
                 preview_camera: str | None = None):
        """`frame_source(world) -> HxWx3 uint8` replaces the MuJoCo renderer (tests without OpenGL).
        `image_format` "png" needs no imaging library. `preview_camera` (e.g. "photo") also
        renders that view for preview.mp4/preview.gif beside the head camera (not uploaded)."""
        if image_format not in ("jpeg", "png"):
            raise ValueError("image_format must be jpeg or png")
        self.output, self.seed, self.camera = output, seed, camera
        self.size, self.scale, self.quality, self.max_steps = size, render_scale, quality, max_steps
        self.image_format, self.frame_source, self.preview_camera = image_format, frame_source, preview_camera
        self.renderer = None
        self.preview_renderer = None
        self.images: list[bytes | None] = []  # encoded frame per observation, None: repeats the last image
        self.last_stored: np.ndarray | None = None
        self.preview: list[bytes] = []
        self.captions: list[str] = []
        self.rows: list[dict] = []

    # --- recording ---------------------------------------------------------------
    def _open(self, episode) -> None:
        self.output.mkdir(parents=True, exist_ok=True)
        if (self.output / "replay.json").exists() or (self.output / "frames").exists():
            raise FileExistsError(f"{self.output} already holds a replay; recordings are never overwritten")
        if self.seed is None:
            self.seed = episode.spec.seed
        if self.frame_source is None:
            import mujoco

            self.renderer = mujoco.Renderer(episode.world.model, self.size * self.scale, self.size * self.scale)
        if self.preview_camera:
            import mujoco

            self.preview_renderer = mujoco.Renderer(episode.world.model, 288, 384)

    def _render(self, world) -> np.ndarray:
        if self.frame_source is not None:
            return np.asarray(self.frame_source(world), dtype=np.uint8)
        self.renderer.update_scene(world.data, camera=self.camera)
        return _downsample(self.renderer.render(), self.scale)

    def _encode(self, image: np.ndarray) -> bytes:
        data = encode_jpeg(image, self.quality) if self.image_format == "jpeg" else png_rgb8(image)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"a frame is {len(data)} bytes, over the {MAX_IMAGE_BYTES} byte import limit")
        return data

    def observe(self, episode, t: float) -> int:
        """Render one frame (frame 0 before the first step, then one after each step)."""
        if not self.images and self.renderer is None:
            self._open(episode)
        world = episode.world
        image = self._render(world)
        if max(image.shape[:2]) > self.size:
            raise ValueError(f"frames must be at most {self.size} px a side")
        repeat = (self.last_stored is not None and image.shape == self.last_stored.shape
                  and int(np.abs(image.astype(np.int16) - self.last_stored).max()) <= REPEAT_LEVELS)
        if repeat:
            self.images.append(None)
        else:
            self.images.append(self._encode(image))
            self.last_stored = image
        self.captions.append(f"t = {t:5.1f} s   {world.placed()}/{world.n_pills} in the bottle")
        if self.preview_renderer is not None:
            self.preview_renderer.update_scene(world.data, camera=self.preview_camera)
            self.preview.append(encode_jpeg(self.preview_renderer.render(), 85))
        return len(self.images) - 1

    def step(self, before: int, action: list[float], trace, extension: dict, after: int, reward: float,
             success: bool, terminated: bool, truncated: bool, t: float) -> None:
        both = [float(min(1.0, max(-1.0, v))) for v in extension["left"] + extension["right"]]
        self.rows.append({"index": after, "action": [round(v, 4) for v in both], "reward": round(float(reward), 4),
                          "success": bool(success), "policy_ms": round(float(trace.policy_ms), 2), "t": round(t, 3)})

    # --- writing -------------------------------------------------------------------
    def finish(self, episode, summary: dict) -> None:
        frames = self.output / "frames"
        frames.mkdir()
        suffix = ".jpg" if self.image_format == "jpeg" else ".png"
        first = {"index": 0, "action": None, "reward": None, "success": None, "policy_ms": None}
        stored = 0
        for row in [first, *self.rows]:
            index = row["index"]
            frame = {key: row[key] for key in ("index", "action", "reward", "success", "policy_ms")}
            image = self.images[index]
            if image is None:
                frame["image_png_base64"] = None  # the player shows the latest earlier image
            else:
                (frames / f"{index:04d}{suffix}").write_bytes(image)
                stored += len(image)
            (frames / f"{index:04d}.json").write_text(json.dumps(frame, separators=(",", ":")) + "\n")
        skill_planner = summary.get("skill_planner_p50_ms")
        manifest = {
            "steps": len(self.rows), "seed": int(self.seed), "outcome": outcome(summary),
            "metrics": episode_metrics(summary), "action_labels": list(ACTION_LABELS), "skill": "pick_and_drop",
            "planner_ms": skill_planner, "sim_seconds": summary["simulated_duration_s"],
        }
        (self.output / "replay.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        images = sum(image is not None for image in self.images)
        summary["replay"] = {"format": "offline", "steps": len(self.rows), "frames": len(self.images),
                             "images": images, "image_bytes": stored, "camera": self.camera,
                             "image": f"{self.image_format} {self.size}x{self.size}", "record_dt_s": self.record_dt}
        (self.output / "summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
        if self.renderer is not None:
            self.renderer.close()
        if self.preview_renderer is not None:
            self.preview_renderer.close()
            self._write_preview(summary)

    def _write_preview(self, summary: dict) -> None:
        """preview.mp4 and preview.gif: the wide view beside the head camera, with a caption (not uploaded)."""
        from PIL import Image, ImageDraw

        from .configs import CONFIGS

        label = CONFIGS[summary["config"]].label if summary["config"] in CONFIGS else summary["config"]
        canvas = []
        shown = None
        for index, wide in enumerate(self.preview):
            if self.images[index] is not None:
                shown = Image.open(io.BytesIO(self.images[index])).convert("RGB").resize((288, 288), Image.Resampling.LANCZOS)
            frame = Image.new("RGB", (672, 312), (18, 18, 20))
            frame.paste(Image.open(io.BytesIO(wide)).convert("RGB"), (0, 0))
            frame.paste(shown, (384, 0))
            draw = ImageDraw.Draw(frame)
            draw.text((8, 294), f"{label} · {summary['slice']} · seed {self.seed}", fill=(200, 200, 205))
            draw.text((384 + 8, 294), self.captions[index], fill=(235, 235, 240))
            canvas.append(frame)
        if not canvas:
            return
        try:
            import imageio.v3 as iio

            iio.imwrite(self.output / "preview.mp4", [np.asarray(f) for f in canvas], fps=10, codec="libx264",
                        macro_block_size=8, quality=6)
        except Exception as error:  # the preview is optional evidence
            (self.output / "preview-error.txt").write_text(f"{type(error).__name__}: {error}\n")
        small = [f.resize((448, 208), Image.Resampling.LANCZOS).convert("P", palette=Image.Palette.ADAPTIVE, colors=96)
                 for f in canvas[::2]]
        small[0].save(self.output / "preview.gif", save_all=True, append_images=small[1:], duration=200, loop=0,
                      optimize=True)
