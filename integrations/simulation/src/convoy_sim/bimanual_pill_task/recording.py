"""Record episodes in the platform's replay format (the coordinator journal).

The control plane replays an episode from a SQLite journal with the coordinator's
schema (``control-plane/agent/convoy_agent/coordinator/journal.py``): one
``missions`` row with the identity and terminal report, and one ``commands`` row
per applied action holding ``request_json`` (the observation the action was
chosen from), ``result_json`` (the action and policy time) and
``observation_json`` (the next observation, reward and success). The server's
reader (``convoy_server/services/replay.py``) checks identity, consecutive
sequences, observation continuity, 4-value actions in [-1, 1] and PNG frames of
at most 768 KiB; the same rows can be posted to the recording upload endpoints.

This task's additions are additive keys the reader ignores: ``planner_duration_ms``
(latency of planner calls that completed during the step: modeled, or the measured
round trip for a planner on a connected device) and
``bimanual`` (both arms' commands, active skills, joint targets, planner calls)
on each result row.

The 4-value ``action`` is the arm moving more during the step: its commanded TCP
velocity normalised by the 0.6 m/s skill speed limit (x, y, z) and the gripper
command mapped to [-1 open (80 mm), +1 closed].

Frames are kept as full-quality RGB8 PNGs while the episode runs; at the end the
largest colour depth (8 down to 4 bits per channel) whose journal fits the byte
budget (default 28 MiB) is written. Each frame is stored twice (as the next
step's request observation), as the platform's continuity check requires.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
import uuid
import zlib
from pathlib import Path

import numpy as np

from .planning import INSTRUCTION

PROFILE = "bimanual-pill-task-v1"
ROBOT_ID = "sim-bimanual-station-01"
DEVICE_ID = "sim-edge-computer-01"
MAX_PNG_BYTES = 768 * 1024  # replay.MAX_IMAGE_BYTES
DEFAULT_BUDGET_BYTES = 28 * 1024 * 1024
NAMESPACE = uuid.UUID("6f1d2c43-6a2b-4c8e-9d0e-5b7a1c2f3e4d")


def encode(value) -> str:
    """The coordinator journal's canonical JSON."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def png_rgb8(image: np.ndarray, level: int = 9) -> bytes:
    """Non-interlaced RGB8 PNG (IHDR/IDAT/IEND only) with per-row adaptive filters.

    Filtering uses the raw neighbouring bytes, so all five filters are computed
    for the whole image at once and each row keeps the one with the smallest sum
    of absolute residuals (the libpng heuristic).
    """
    img = np.ascontiguousarray(image, dtype=np.uint8)
    h, w, _ = img.shape
    x = img.reshape(h, w * 3).astype(np.int16)
    a = np.zeros_like(x)
    a[:, 3:] = x[:, :-3]
    b = np.zeros_like(x)
    b[1:] = x[:-1]
    c = np.zeros_like(x)
    c[1:, 3:] = x[:-1, :-3]
    p = a + b - c
    pa, pb, pc = np.abs(p - a), np.abs(p - b), np.abs(p - c)
    paeth = np.where((pa <= pb) & (pa <= pc), a, np.where(pb <= pc, b, c))
    candidates = np.stack([x, x - a, x - b, x - ((a + b) >> 1), x - paeth]) & 0xFF
    signed = np.where(candidates > 127, 256 - candidates, candidates)
    best = np.argmin(signed.sum(axis=2), axis=0)
    rows = candidates[best, np.arange(h)].astype(np.uint8)
    raw = np.concatenate([best.astype(np.uint8)[:, None], rows], axis=1).tobytes()

    def chunk(kind: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")

    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b"")


def decode_png(data: bytes) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def posterize(image: np.ndarray, bits: int) -> np.ndarray:
    if bits >= 8:
        return image
    shift = 8 - bits
    return ((image >> shift) << shift) + (1 << (shift - 1))


def episode_key(config: str, slice_id: str, seed: int) -> str:
    return hashlib.sha256(f"{config}|{slice_id}|{seed}".encode()).hexdigest()[:12]


class JournalRecorder:
    """Renders the head camera at each replay step and writes the journal at the end."""

    def __init__(self, output: Path, *, camera: str = "head_camera", size: int = 480,
                 budget_bytes: int = DEFAULT_BUDGET_BYTES, release_manifest: dict, gif_every: int = 2,
                 gif_size: int = 320, frame_source=None, preview_camera: str | None = None):
        """`frame_source(world) -> HxWx3 uint8` replaces the MuJoCo renderer (tests without OpenGL).

        `preview_camera` (e.g. "photo") adds a third-person view beside the head
        camera in preview.gif only; the journal always holds the head camera.
        """
        self.output = output
        self.frame_source = frame_source
        self.preview_camera = preview_camera
        self.preview_renderer = None
        self.side_frames: dict[int, bytes] = {}
        self.camera, self.size, self.budget = camera, size, budget_bytes
        self.manifest = release_manifest
        self.release_digest = hashlib.sha256(encode(release_manifest).encode()).hexdigest()
        self.renderer = None
        self.frames: list[bytes] = []  # full-quality PNGs
        self.states: list[list[float]] = []
        self.captions: list[str] = []
        self.rows: list[dict] = []
        self.gif_every, self.gif_size = gif_every, gif_size
        self.first_plan: dict | None = None

    def _open(self, episode) -> None:
        import mujoco

        spec = episode.spec
        key = episode_key(spec.config.id, spec.slice.id, spec.seed)
        self.mission_id, self.episode_id = f"mis_{key}", f"epi_{key}"
        self.identity = {
            "robot_id": ROBOT_ID, "device_id": DEVICE_ID, "mission_id": self.mission_id,
            "release_digest": self.release_digest, "boot_id": "offline-simulation",
            "incarnation": str(uuid.uuid5(NAMESPACE, key)), "authority_epoch": 1,
        }
        self.output.mkdir(parents=True, exist_ok=True)
        if (self.output / "journal.sqlite3").exists():
            raise FileExistsError(f"{self.output / 'journal.sqlite3'} exists; recordings are never overwritten")
        if self.frame_source is None:
            self.renderer = mujoco.Renderer(episode.world.model, self.size, self.size)
            self.frame_source = self._render
        else:
            self.renderer = False
        if self.preview_camera:
            self.preview_renderer = mujoco.Renderer(episode.world.model, self.gif_size, self.gif_size * 4 // 3)

    def _render(self, world) -> np.ndarray:
        self.renderer.update_scene(world.data, camera=self.camera)
        return self.renderer.render()

    def observe(self, episode, t: float) -> int:
        """Render and keep one frame; returns its index (the episode passes it back to step)."""
        if self.renderer is None:
            self._open(episode)
        world = episode.world
        self.frames.append(png_rgb8(self.frame_source(world)))
        if self.preview_renderer is not None and (len(self.frames) - 1) % self.gif_every == 0:
            self.preview_renderer.update_scene(world.data, camera=self.preview_camera)
            self.side_frames[len(self.frames) - 1] = png_rgb8(self.preview_renderer.render())
        state = []
        for side in ("left", "right"):
            state += [round(float(v), 5) for v in world.arm_q(side)] + [round(world.gripper_opening(side), 5)]
        self.states.append(state)
        self.captions.append(f"t={t:5.1f}s  {world.placed()}/{world.n_pills} in bottle")
        return len(self.frames) - 1

    def step(self, before: int, action: list[float], trace, extension: dict, after: int, reward: float,
             success: bool, terminated: bool, truncated: bool, t: float) -> None:
        planner_ms = sum(c["latency_ms"] for c in trace.planner_calls) if trace.planner_calls else None
        if self.first_plan is None:
            for call in trace.planner_calls:
                if call["role"] == "skill" and call["status"] == "ok":
                    self.first_plan = call
                    break
        self.rows.append({
            "before": before, "after": after, "t": t,
            "action": [float(min(1.0, max(-1.0, v))) for v in action],
            "policy_duration_ms": round(trace.policy_ms, 3),
            "planner_duration_ms": None if planner_ms is None else round(planner_ms, 1),
            "bimanual": {**extension, "sim_time_s": round(t, 3), "planner_calls": trace.planner_calls,
                         "skill_events": trace.skill_events},
            "reward": round(float(reward), 6), "success": bool(success),
            "terminated": bool(terminated), "truncated": bool(truncated),
        })

    # --- writing ---------------------------------------------------------------
    def _encoded(self, bits: int) -> list[str]:
        frames = self.frames if bits == 8 else [png_rgb8(posterize(decode_png(f), bits)) for f in self.frames]
        if max(len(f) for f in frames) > MAX_PNG_BYTES:
            raise ValueError("a frame exceeds the replay image limit")
        return [base64.b64encode(f).decode("ascii") for f in frames]

    def _observation(self, images: list[str], index: int) -> dict:
        return {"image_png_base64": images[index], "state": self.states[index], "instruction": INSTRUCTION}

    def _command_rows(self, images: list[str]) -> list[tuple]:
        out = []
        for seq, row in enumerate(self.rows):
            request_id = str(uuid.uuid5(NAMESPACE, f"{self.mission_id}:{seq}"))
            deadline = int(round(row["t"] * 1e9)) + 1
            observation_id = f"{self.mission_id}:{seq}"
            request = {"identity": self.identity, "request_id": request_id, "observation_id": observation_id,
                       "sequence": seq, "observation": self._observation(images, row["before"]),
                       "deadline_monotonic_ns": deadline, "budget_ms": 200.0}
            result = {"identity": self.identity, "request_id": request_id, "observation_id": observation_id,
                      "sequence": seq, "deadline_monotonic_ns": deadline, "action": row["action"],
                      "policy_duration_ms": row["policy_duration_ms"], "planner_duration_ms": row["planner_duration_ms"],
                      "bimanual": row["bimanual"]}
            outcome = {"observation": self._observation(images, row["after"]), "reward": row["reward"],
                       "success": row["success"], "terminated": row["terminated"], "truncated": row["truncated"]}
            out.append((self.mission_id, seq, request_id, encode(request), encode(result), "applied", encode(outcome)))
        return out

    def finish(self, episode, summary: dict) -> None:
        bits, rows = 8, None
        for bits in (8, 7, 6, 5, 4):
            rows = self._command_rows(self._encoded(bits))
            if sum(len(r[3]) + len(r[4]) + len(r[6]) for r in rows) <= self.budget:
                break
        state = "failed" if summary["outcome"].startswith("planner_declined") else "completed"
        cfg = episode.spec.config
        plan = self.first_plan or {}
        reward_sum = sum(r["reward"] for r in self.rows)
        platform_summary = {
            "execution_mode": ("simulated_time_measured_planner_latency" if cfg.skill_planner.source == "device"
                               else "simulated_time_modeled_latency"),
            "evidence_scope": "simulated_physics_with_privileged_state",
            "profile": PROFILE,
            "seed": episode.spec.seed,
            "steps": len(self.rows),
            "ever_success": any(r["success"] for r in self.rows),
            "final_success": bool(summary["success"]),
            "reward_sum": round(reward_sum, 6),
            "simulated_duration_s": summary["simulated_duration_s"],
            "wall_duration_s": summary["wall_duration_s"],
            "policy_runtime": cfg.motor.name,
            "planner_runtime": cfg.skill_planner.name,
            "planner_accepted": bool(plan),
            "planner_result": {
                "decision": plan.get("decision") or {"kind": "decline", "reason": "no_planner_decision"},
                "planner_duration_ms": plan.get("latency_ms"),
                "placement": cfg.skill_planner.placement,
                "model": cfg.skill_planner.model,
            },
            "task": {k: summary[k] for k in ("config", "slice", "outcome", "pills", "placed", "fraction_placed",
                                             "success", "time_to_all_placed_s", "planner_calls",
                                             "planner_latency_p50_ms", "planner_latency_p95_ms", "operator_pages")},
        }
        report = {"identity": self.identity, "state": state, "detail": summary["outcome"][:1000],
                  "summary": platform_summary}
        db = sqlite3.connect(self.output / "journal.sqlite3")
        db.executescript("""
            PRAGMA journal_mode=DELETE;
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE missions (id TEXT PRIMARY KEY, identity_json TEXT, state TEXT NOT NULL,
                report_json TEXT, reported INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE commands (mission_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                request_id TEXT NOT NULL UNIQUE, request_json TEXT NOT NULL, result_json TEXT NOT NULL,
                state TEXT NOT NULL, observation_json TEXT, PRIMARY KEY(mission_id, sequence));
            CREATE TABLE bundle_bindings (binding_id TEXT PRIMARY KEY, observation_json TEXT NOT NULL);
            CREATE TABLE plans (mission_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                request_json TEXT NOT NULL, result_json TEXT, state TEXT NOT NULL);
        """)
        with db:
            db.execute("INSERT INTO meta VALUES ('binding', ?)", (encode({"robot_id": ROBOT_ID, "device_id": DEVICE_ID}),))
            db.execute("INSERT INTO meta VALUES ('epoch', '1')")
            db.execute("INSERT INTO missions VALUES (?, ?, ?, ?, 1)",
                       (self.mission_id, encode(self.identity), state, encode(report)))
            db.executemany("INSERT INTO commands VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            if plan:
                request = {"identity": self.identity, "request_id": str(uuid.uuid5(NAMESPACE, self.mission_id + ":plan")),
                           "observation_id": f"{self.mission_id}:first-skill-call",
                           "budget_ms": cfg.skill_planner.timeout_s * 1000}
                db.execute("INSERT INTO plans VALUES (?, ?, ?, ?, 'accepted')",
                           (self.mission_id, request["request_id"], encode(request), encode(plan)))
        db.close()
        if self.renderer:
            self.renderer.close()
        if self.preview_renderer is not None:
            self.preview_renderer.close()
        record = {"id": self.episode_id, "mission_id": self.mission_id, "release_digest": self.release_digest,
                  "identity": self.identity, "state": state, "summary": platform_summary}
        (self.output / "episode.json").write_text(json.dumps(record, indent=2) + "\n")
        (self.output / "release.json").write_text(json.dumps(self.manifest, indent=2) + "\n")
        summary["recording"] = {"journal": "journal.sqlite3", "bytes": (self.output / "journal.sqlite3").stat().st_size,
                                "steps": len(self.rows), "png_bits_per_channel": bits, "camera": self.camera,
                                "episode_id": self.episode_id, "mission_id": self.mission_id}
        self._write_preview()

    def _write_preview(self) -> None:
        try:
            from PIL import Image, ImageDraw
        except ImportError:  # previews need the `video` extra; the journal does not
            return

        frames, stills = [], []
        for index in range(0, len(self.frames), self.gif_every):
            image = Image.fromarray(decode_png(self.frames[index])).resize((self.gif_size, self.gif_size),
                                                                           Image.Resampling.LANCZOS)
            if index in self.side_frames:
                side = Image.fromarray(decode_png(self.side_frames[index]))
                both = Image.new("RGB", (side.width + image.width, self.gif_size))
                both.paste(side, (0, 0))
                both.paste(image, (side.width, 0))
                image = both
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, self.gif_size - 22, image.width, self.gif_size), fill=(15, 15, 18))
            draw.text((8, self.gif_size - 18), self.captions[index], fill=(235, 235, 235))
            frames.append(image.convert("P", palette=Image.Palette.ADAPTIVE, colors=128))
            stills.append(index)
        if frames:
            frames[0].save(self.output / "preview.gif", save_all=True, append_images=frames[1:], duration=100, loop=0,
                           optimize=True)
            for name, index in (("frame_first.png", 0), ("frame_mid.png", len(self.frames) // 2),
                                ("frame_last.png", len(self.frames) - 1)):
                (self.output / name).write_bytes(self.frames[index])
