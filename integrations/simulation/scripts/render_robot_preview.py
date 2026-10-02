#!/usr/bin/env python3
"""Render the bimanual station robot for the website: a turntable loop and a still.

The robot is this package's own MJCF (``convoy_sim.bimanual_pill_task.robot``) in the idle pose an
episode starts from: both arms at rest in front of the torso, grippers part open, the head tilted
down to the work surface. The task's controller computes that pose in the full task scene (2 ms
steps, 4 noslip iterations); it is copied joint by joint into a studio scene that holds only the
robot, an endless floor and directional lights. The robot turns once about the centre of its base,
so the loop is seamless.

Shadows: the key light is jittered over a small cone and the passes averaged, which softens the
robot's own shadows. On the floor, that shadow and a contact shadow (the floor's visibility of a
dome of overhead light directions) are blurred over floor pixels only and laid on the exact stage
colour (``--surface-subtle`` in website/src/styles/tokens.css), so the background matches the page
wherever nothing shades it.

Writes, to website/public/sim/bimanual-station/ by default:

    turntable.webm   VP9, the loop
    turntable.mp4    H.264, the same loop for browsers without VP9
    poster.webp      the first frame: shown before playback and under reduced motion

Rendering needs OpenGL; on headless Linux use MUJOCO_GL=egl (or osmesa). From integrations/simulation:

    uv sync --frozen --extra video
    MUJOCO_GL=egl uv run --frozen --extra video python scripts/render_robot_preview.py
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from functools import cached_property
from pathlib import Path

import mujoco
import numpy as np
from scipy.ndimage import binary_dilation, gaussian_filter

from convoy_sim.bimanual_pill_task.robot import (
    MATERIALS,
    SIDES,
    RobotSpec,
    f,
    robot_actuators,
    robot_bodies,
    robot_contact_excludes,
    robot_equality,
    robot_tendons,
)
from convoy_sim.bimanual_pill_task.robot import _defaults as robot_defaults
from convoy_sim.bimanual_pill_task.scene import Layout
from convoy_sim.bimanual_pill_task.skills import REST_OPENING, ArmController, rest_position
from convoy_sim.bimanual_pill_task.world import World

OUTPUT = Path(__file__).resolve().parents[3] / "website/public/sim/bimanual-station"
STAGE = "#ece9e1"  # --surface-subtle in website/src/styles/tokens.css: keep the two equal
PIVOT = np.array([-0.02, 0.0, 0.0])  # centre of the wheeled base, on the floor [m]
LOOK_AT_Z = 0.64  # a little below mid-height of the 1.30 m robot [m]
FOVY = 22.0  # a long lens: little perspective distortion [deg]
ELEVATION = -14.0  # the camera looks down from 1.65 m, about eye height [deg]
DISTANCE = 4.25  # [m]
START_YAW = -38.0  # first frame: a three-quarter view [deg]
AMBIENT = 0.30
# Directional lights: (name, azimuth of the source [deg], elevation [deg], diffuse, specular). The
# camera looks along -x at the robot's front: azimuth 0 is behind the camera, -90 its left, 90 its right.
LIGHTS = (
    ("key", -45.0, 50.0, 0.45, 0.30),
    ("front", 0.0, 14.0, 0.20, 0.05),
    ("fill", 60.0, 20.0, 0.15, 0.0),
    ("top", 0.0, 90.0, 0.15, 0.0),
    ("rim", 180.0, 35.0, 0.15, 0.20),
)
KEY_CONE = 3.0  # the key light's directions spread this far [deg]: soft shadows on the robot
KEY_BLUR = 2.0  # Gaussian sigma of the key shadow on the floor [output pixels]
# Contact shadow: the floor's view of a dome of overhead directions, blocked by the wheeled base alone
# (geoms below BASE_TOP); the upper body's shadow is the key light's.
DOME_CONE = 55.0  # directions within this angle of vertical [deg]
DOME_PASSES = 48
DOME_BLUR = 2.0  # [output pixels]
DOME_STRENGTH = 0.6  # floor darkening where the whole dome is blocked
BASE_TOP = 0.33  # [m]


def rgb(hex_color: str) -> np.ndarray:
    value = hex_color.lstrip("#")
    return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4)], dtype=float) / 255.0


def idle_pose(settle_steps: int = 300) -> dict[str, float]:
    """Joint positions at rest, as an episode starts (the same reset as ``stills.render_stills``)."""
    world = World(Layout(pills=()))
    for side in SIDES:
        ArmController(world, side).reset_to(rest_position(side), math.pi / 2, REST_OPENING)
    world.set_head(world.robot.head_pan, world.robot.head_tilt)
    mujoco.mj_forward(world.model, world.data)
    world.step(settle_steps)
    if world.unstable:
        raise RuntimeError("the idle pose did not settle")
    m, d = world.model, world.data
    robot = m.body("robot").id
    return {m.joint(j).name: float(d.qpos[m.jnt_qposadr[j]]) for j in range(m.njnt)
            if m.body_rootid[m.jnt_bodyid[j]] == robot}


def studio_mjcf(floor: np.ndarray, width: int, height: int, spec: RobotSpec | None = None) -> str:
    """The robot alone on an endless floor; the lights' directions are set per render pass."""
    spec = spec or RobotSpec()
    lights = "".join(f'<light name="{name}" directional="true" diffuse="{f(diffuse, diffuse, diffuse)}" '
                     f'specular="{f(specular, specular, specular)}" castshadow="false"/>'
                     for name, _, _, diffuse, specular in LIGHTS)
    return f"""<mujoco model="bimanual_station_preview">
  <compiler angle="radian" autolimits="true"/>
  <statistic center="{f(PIVOT[0], PIVOT[1], LOOK_AT_Z)}" extent="2"/>
  <visual>
    <global offwidth="{width}" offheight="{height}" fovy="{FOVY:g}"/>
    <quality shadowsize="4096" offsamples="4"/>
    <headlight ambient="{f(AMBIENT, AMBIENT, AMBIENT)}" diffuse="0 0 0" specular="0 0 0"/>
    <map znear="0.5" zfar="60" shadowclip="0.75"/>
  </visual>
  <default>{robot_defaults()}</default>
  <asset>
    {MATERIALS}
    <material name="stage_floor" rgba="{f(*floor)} 1" specular="0" shininess="0" reflectance="0"/>
  </asset>
  <worldbody>
    {lights}
    <geom name="floor" type="plane" size="0 0 0.05" material="stage_floor" contype="0" conaffinity="0"/>
    {robot_bodies(spec)}
  </worldbody>
  <tendon>{robot_tendons()}</tendon>
  <equality>{robot_equality()}</equality>
  <contact>{robot_contact_excludes()}</contact>
  <actuator>{robot_actuators(spec)}</actuator>
</mujoco>"""


def light_direction(azimuth: float, elevation: float) -> np.ndarray:
    """The direction a light travels, for a source at (azimuth, elevation)."""
    a, e = math.radians(azimuth), math.radians(elevation)
    return -np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])


def spread(base: np.ndarray, cone_deg: float, count: int) -> list[np.ndarray]:
    """`count` directions within `cone_deg` of `base`, on a Vogel spiral: the same on every run."""
    u = np.cross(base, [0.0, 0.0, 1.0] if abs(base[2]) < 0.9 else [1.0, 0.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(base, u)
    golden = math.pi * (3 - math.sqrt(5))
    out = []
    for i in range(count):
        r = math.tan(math.radians(cone_deg)) * math.sqrt((i + 0.5) / count)
        d = base + r * (math.cos(i * golden) * u + math.sin(i * golden) * v)
        out.append(d / np.linalg.norm(d))
    return out


def pose_studio(model: mujoco.MjModel, data: mujoco.MjData, pose: dict[str, float]) -> None:
    for name, value in pose.items():
        data.qpos[model.jnt_qposadr[model.joint(name).id]] = value


def turn(model: mujoco.MjModel, data: mujoco.MjData, yaw_deg: float) -> None:
    """Turn the robot about the vertical axis through the centre of its base (it is fixed to the world)."""
    quat, rotation = np.zeros(4), np.zeros(9)
    mujoco.mju_axisAngle2Quat(quat, np.array([0.0, 0.0, 1.0]), math.radians(yaw_deg))
    mujoco.mju_quat2Mat(rotation, quat)
    robot = model.body("robot").id
    model.body_quat[robot] = quat
    model.body_pos[robot] = PIVOT - rotation.reshape(3, 3) @ PIVOT
    mujoco.mj_kinematics(model, data)


def separate_coplanar_faces(model: mujoco.MjModel, root: int, step: float = 5e-5) -> None:
    """The robot's rounded boxes are overlapping boxes and corner cylinders whose top and bottom faces
    coincide, and coinciding faces disagree on the shadow test pixel by pixel (speckle). Shorten all but
    one of each coinciding set by 0.05 mm steps: invisible (a pixel is 2.6 mm here), and resolved by the
    depth buffer because the near plane sits at 1 m (``znear`` is relative to the 2 m extent), so every
    face has one owner. Rendering only: the studio scene is never stepped."""
    axis = {mujoco.mjtGeom.mjGEOM_BOX: 2, mujoco.mjtGeom.mjGEOM_CYLINDER: 1}  # the half-height's index
    faces: dict[tuple, list[int]] = {}
    for g in range(model.ngeom):
        kind = int(model.geom_type[g])
        if kind in axis and model.body_rootid[model.geom_bodyid[g]] == root and np.allclose(model.geom_quat[g], [1, 0, 0, 0]):
            key = (int(model.geom_bodyid[g]), round(float(model.geom_pos[g][2]), 6), round(float(model.geom_size[g][axis[kind]]), 6))
            faces.setdefault(key, []).append(g)
    for members in faces.values():
        for k, g in enumerate(members[1:], start=1):
            model.geom_size[g][axis[int(model.geom_type[g])]] -= step * k


def downsample(image: np.ndarray, factor: int) -> np.ndarray:
    h, w = image.shape[:2]
    return image.reshape(h // factor, factor, w // factor, factor, *image.shape[2:]).mean(axis=(1, 3))


def ratio(shaded: np.ndarray, empty: np.ndarray) -> np.ndarray:
    """How much light reaches each floor pixel with the robot there, from 0 to 1 (1 where the floor is dark anyway)."""
    return np.where(empty > 2.0, np.clip(shaded / np.maximum(empty, 2.0), 0.0, 1.0), 1.0)


def soften(shade: np.ndarray, floor: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur over floor pixels only, so the robot's own pixels never bleed into the floor."""
    weight = floor.astype(float)
    return np.clip(gaussian_filter(shade * weight, sigma) / np.maximum(gaussian_filter(weight, sigma), 1e-6), 0.0, 1.0)


class Studio:
    """The robot posed in the studio scene, one camera, and two offscreen renderers."""

    def __init__(self, pose: dict[str, float], floor: np.ndarray, size: tuple[int, int], supersample: int, passes: int):
        width, height = size
        self.factor = supersample
        self.model = m = mujoco.MjModel.from_xml_string(studio_mjcf(floor, width * supersample, height * supersample))
        self.data = mujoco.MjData(m)
        pose_studio(m, self.data, pose)
        self.robot = m.body("robot").id
        separate_coplanar_faces(m, self.robot)
        self.floor = m.geom("floor").id
        self.lights = {name: m.light(name).id for name, *_ in LIGHTS}
        self.directions = {name: light_direction(azimuth, elevation) for name, azimuth, elevation, *_ in LIGHTS}
        self.key_passes = spread(self.directions["key"], KEY_CONE, passes)
        self.dome = spread(np.array([0.0, 0.0, -1.0]), DOME_CONE, DOME_PASSES)
        self.camera = mujoco.MjvCamera()
        self.camera.type = mujoco.mjtCamera.mjCAMERA_FREE
        self.camera.lookat[:] = [PIVOT[0], PIVOT[1], LOOK_AT_Z]
        self.camera.distance, self.camera.azimuth, self.camera.elevation = DISTANCE, 180.0, ELEVATION
        self.option = mujoco.MjvOption()
        self.renderer = mujoco.Renderer(m, height * supersample, width * supersample)
        # The contact shadow is blurred anyway: render it at the output size with a small shadow map.
        m.vis.quality.shadowsize = 1024
        self.dome_renderer = mujoco.Renderer(m, height, width)
        m.vis.quality.shadowsize = 4096
        self.turn(0.0)
        robot_geoms = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == self.robot]
        self.upper_body = [g for g in robot_geoms if self.data.geom_xpos[g][2] > BASE_TOP]

    # The floor without the robot, per pixel: dividing by it leaves only the robot's shadow, and cancels
    # whatever the floor does to itself (shadow-map acne, the edge of a light's shadow box).
    @cached_property
    def empty_key(self) -> np.ndarray:
        return downsample(self.key_light(robot=False), self.factor).mean(axis=2)

    @cached_property
    def empty_dome(self) -> list[np.ndarray]:
        return [self.dome_light(d, robot=False).astype(np.float32) for d in self.dome]

    def close(self) -> None:
        self.renderer.close()
        self.dome_renderer.close()

    def turn(self, yaw_deg: float) -> None:
        turn(self.model, self.data, yaw_deg)

    def _render(self, renderer: mujoco.Renderer, directions: dict[str, np.ndarray], robot: bool) -> np.ndarray:
        target = np.array([PIVOT[0], PIVOT[1], LOOK_AT_Z])
        for name, d in directions.items():
            self.data.light_xdir[self.lights[name]] = d
            self.data.light_xpos[self.lights[name]] = target - 6.0 * d  # the shadow map looks from here
        self.option.geomgroup[1] = self.option.geomgroup[2] = int(robot)  # the robot's geoms
        renderer.update_scene(self.data, camera=self.camera, scene_option=self.option)
        return renderer.render().astype(float)

    def key_light(self, robot: bool = True) -> np.ndarray:
        """All lights, the key light casting shadows: the mean over its jittered directions."""
        m = self.model
        m.light_castshadow[:] = 0
        m.light_castshadow[self.lights["key"]] = 1
        total = sum(self._render(self.renderer, {**self.directions, "key": d}, robot) for d in self.key_passes)
        return total / len(self.key_passes)

    def dome_light(self, direction: np.ndarray, robot: bool = True) -> np.ndarray:
        """The floor lit by one overhead direction alone (no ambient), the base casting shadows."""
        m = self.model
        diffuse, ambient, groups = m.light_diffuse.copy(), m.vis.headlight.ambient.copy(), m.geom_group.copy()
        m.light_diffuse[:] = 0.0
        m.light_diffuse[self.lights["top"]] = 1.0
        m.vis.headlight.ambient[:] = 0.0
        m.light_castshadow[:] = 0
        m.light_castshadow[self.lights["top"]] = 1
        m.geom_group[self.upper_body] = 5  # a group the renderer does not draw
        try:
            return self._render(self.dome_renderer, {**self.directions, "top": direction}, robot).mean(axis=2)
        finally:
            m.light_diffuse[:], m.vis.headlight.ambient[:], m.geom_group[:] = diffuse, ambient, groups

    def robot_mask(self) -> np.ndarray:
        """Pixels that show the robot, grown by one pixel to keep its anti-aliased edge."""
        self.option.geomgroup[1] = self.option.geomgroup[2] = 1
        self.renderer.enable_segmentation_rendering()
        try:
            self.renderer.update_scene(self.data, camera=self.camera, scene_option=self.option)
            ids, types = np.moveaxis(self.renderer.render(), 2, 0)
        finally:
            self.renderer.disable_segmentation_rendering()
        robot = (types == mujoco.mjtObj.mjOBJ_GEOM) & (ids >= 0) & (ids != self.floor)
        return binary_dilation(robot, iterations=1)

    def frame(self, stage: np.ndarray) -> np.ndarray:
        """The robot as rendered; the floor as the stage colour times its softened key and contact shadows."""
        colour = downsample(self.key_light(), self.factor)
        robot = downsample(self.robot_mask().astype(float), self.factor) > 0
        floor = ~robot
        key = soften(ratio(colour.mean(axis=2), self.empty_key), floor, KEY_BLUR)
        visible = sum(ratio(self.dome_light(d), empty) for d, empty in zip(self.dome, self.empty_dome, strict=True))
        contact = 1.0 - DOME_STRENGTH * (1.0 - soften(visible / len(self.dome), floor, DOME_BLUR))
        out = np.where(robot[..., None], colour, stage * (key * contact)[..., None])
        return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def floor_albedo(pose: dict[str, float], size: tuple[int, int], supersample: int) -> np.ndarray:
    """The floor albedo that renders as the stage colour (lighting is linear in albedo), so the
    robot's anti-aliased edge blends into the same colour the floor is replaced with."""
    probe = np.full(3, 0.5)
    studio = Studio(pose, probe, size, supersample, passes=4)
    try:
        lit = np.median(studio.key_light(robot=False).reshape(-1, 3), axis=0) / 255.0  # robust to acne
    finally:
        studio.close()
    albedo = rgb(STAGE) * probe / lit
    if np.any(albedo > 1.0):
        raise RuntimeError(f"the lights are too dim for the stage colour (albedo {albedo})")
    return albedo


def encode(frames: list[np.ndarray], path: Path, fps: int, codec: list[str]) -> None:
    import imageio_ffmpeg

    h, w, _ = frames[0].shape
    # BT.709 matrix and limited range, tagged, so a browser converts back to the rendered colours; the
    # accurate conversion keeps the flat stage within one level of its colour (the fast one is 3 off).
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-an",
               "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p",
               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
               *codec, str(path)]
    subprocess.run(command, input=b"".join(item.tobytes() for item in frames), check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--frames", type=int, default=72, help="frames per turn (default 72: 5 degrees each)")
    parser.add_argument("--fps", type=int, default=12, help="playback rate (default 12: one turn in 6 s)")
    parser.add_argument("--size", default="960x640", help="output WIDTHxHEIGHT (default 960x640)")
    parser.add_argument("--supersample", type=int, default=2, help="render at this multiple, then average down")
    parser.add_argument("--passes", type=int, default=8, help="jittered key-light passes per frame")
    parser.add_argument("--stills", type=int, default=0, help="write this many evenly spaced PNG stills instead")
    args = parser.parse_args(argv)
    from PIL import Image

    width, height = (int(v) for v in args.size.lower().split("x"))
    pose = idle_pose()
    studio = Studio(pose, floor_albedo(pose, (width, height), args.supersample), (width, height), args.supersample, args.passes)
    stage = rgb(STAGE) * 255.0
    count = args.stills or args.frames
    frames: list[np.ndarray] = []
    try:
        for k in range(count):
            studio.turn(START_YAW + 360.0 * k / count)
            frames.append(studio.frame(stage))
            print(f"\rframe {k + 1}/{count}", end="", file=sys.stderr, flush=True)
    finally:
        studio.close()
    print(file=sys.stderr)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.stills:
        for k, image in enumerate(frames):
            Image.fromarray(image).save(args.output / f"still-{k:02d}.png")
        return 0
    written = [args.output / name for name in ("poster.webp", "turntable.webm", "turntable.mp4")]
    Image.fromarray(frames[0]).save(written[0], quality=90, method=6)
    # Both at about 40 dB PSNR on the robot. The website names the codecs: vp9, and avc1.64001F (High, level 3.1).
    encode(frames, written[1], args.fps,
           ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "30", "-deadline", "best", "-row-mt", "1", "-g", str(args.frames)])
    encode(frames, written[2], args.fps,
           ["-c:v", "libx264", "-preset", "veryslow", "-crf", "22", "-tune", "animation", "-profile:v", "high", "-level:v", "3.1",
            "-g", str(args.frames), "-movflags", "+faststart"])
    for path in written:
        print(f"{path.name}\t{path.stat().st_size / 1024:.0f} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
