"""Still images of the robot and the scene from the photo, overview and head cameras."""

from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np

from .configs import SliceSpec
from .episode import stable_hash
from .scene import LIGHTING, sample_layout
from .skills import REST_OPENING, ArmController, rest_position
from .world import World

VIEWS = {"photo": (720, 960), "overview": (720, 960), "head_camera": (480, 480), "top": (480, 480)}


def render_stills(output: Path, sl: SliceSpec, seed: int) -> list[Path]:
    from PIL import Image

    output.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(stable_hash("layout", sl.id, seed))
    world = World(sample_layout(rng, sl.pills, sl.center, sl.half, sl.near_bottle, LIGHTING[sl.lighting]))
    for side in ("left", "right"):
        ArmController(world, side).reset_to(rest_position(side), math.pi / 2, REST_OPENING)
    world.set_head(world.robot.head_pan, world.robot.head_tilt)
    mujoco.mj_forward(world.model, world.data)
    for _ in range(300):
        world.step()
    paths = []
    for camera, (h, w) in VIEWS.items():
        renderer = mujoco.Renderer(world.model, h, w)
        renderer.update_scene(world.data, camera=camera)
        path = output / f"{camera}.png"
        Image.fromarray(renderer.render()).save(path)
        renderer.close()
        paths.append(path)
    return paths
