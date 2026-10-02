"""Task scene: dark table and mat, scattered capsule pills, open bottle and cap.

``Layout`` is the sampled, seed-determined arrangement; ``build_mjcf`` turns a
layout plus the robot into one MJCF document. The bottle's collision geometry is
a ring of thin boxes (MuJoCo collides convex shapes only, and a convex hull of a
bottle would be solid); a smooth lathe mesh is drawn on top for rendering.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import physics as P
from .robot import (
    BODY,
    ENV,
    LEFT,
    MATERIALS,
    RIGHT,
    RobotSpec,
    f,
    robot_actuators,
    robot_bodies,
    robot_contact_excludes,
    robot_equality,
    robot_tendons,
)
from .robot import _defaults as robot_defaults

ALL = ENV | BODY | LEFT | RIGHT
MAT_CENTER = (0.47, 0.0)
MAT_HALF = (0.19, 0.34)
# The open bottle stands front-centre between the two arms; its cap lies beside it.
BOTTLE_XY = (0.37, 0.0)
CAP_XY = (0.352, -0.082)


@dataclass(frozen=True)
class Lighting:
    name: str = "nominal"
    key: float = 0.75  # diffuse intensity of the shadow-casting key light
    fill: float = 0.35
    ambient: float = 0.22
    warm: float = 0.0  # 0 neutral, >0 warmer (lower blue)
    key_from: tuple[float, float, float] = (0.9, 0.9, 2.4)


LIGHTING = {
    "nominal": Lighting(),
    "dim": Lighting("dim", key=0.28, fill=0.10, ambient=0.08, warm=0.15),
    "side_glare": Lighting("side_glare", key=1.15, fill=0.05, ambient=0.10, key_from=(0.45, 1.6, 1.15)),
}


@dataclass(frozen=True)
class PillPose:
    x: float
    y: float
    yaw: float
    mass: float


@dataclass(frozen=True)
class Layout:
    pills: tuple[PillPose, ...]
    bottle_xy: tuple[float, float] = BOTTLE_XY
    cap_xy: tuple[float, float] = CAP_XY
    lighting: Lighting = field(default_factory=Lighting)


def quat_from_matrix(r: np.ndarray) -> np.ndarray:
    q = np.zeros(4)
    import mujoco

    mujoco.mju_mat2Quat(q, np.asarray(r, dtype=float).reshape(9))
    return q


def rot_z(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1.0, 0], [-s, 0, c]])


def pill_quat(yaw: float) -> np.ndarray:
    """Capsule axis (local z) horizontal, pointing along `yaw`."""
    return quat_from_matrix(rot_z(yaw) @ rot_y(math.pi / 2))


def _segment(p: np.ndarray, yaw: float) -> tuple[np.ndarray, np.ndarray]:
    d = np.array([math.cos(yaw), math.sin(yaw)]) * P.PILL_HALF_LENGTH_M
    return p - d, p + d


def segment_distance(a0, a1, b0, b1) -> float:
    """Closest distance between two 2-D segments."""
    def point_seg(p, s0, s1):
        v = s1 - s0
        t = np.clip(np.dot(p - s0, v) / max(np.dot(v, v), 1e-12), 0.0, 1.0)
        return float(np.linalg.norm(p - (s0 + t * v)))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1, d2 = cross(a0, a1, b0), cross(a0, a1, b1)
    d3, d4 = cross(b0, b1, a0), cross(b0, b1, a1)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return 0.0
    return min(point_seg(a0, b0, b1), point_seg(a1, b0, b1), point_seg(b0, a0, a1), point_seg(b1, a0, a1))


def sample_layout(rng: np.random.Generator, count: int, center: tuple[float, float], half: tuple[float, float],
                  near_bottle: int = 0, lighting: Lighting | None = None,
                  bottle_xy: tuple[float, float] = BOTTLE_XY, cap_xy: tuple[float, float] = CAP_XY,
                  min_gap: float = 0.002) -> Layout:
    """Rejection-sample non-overlapping flat pills on the mat, clear of bottle and cap."""
    bottle = np.array(bottle_xy)
    cap = np.array(cap_xy)
    mat_lo = np.array(MAT_CENTER) - np.array(MAT_HALF) + 0.03
    mat_hi = np.array(MAT_CENTER) + np.array(MAT_HALF) - 0.03
    placed: list[tuple[np.ndarray, float]] = []
    poses: list[PillPose] = []

    def clear(p: np.ndarray, yaw: float, bottle_gap: float) -> bool:
        if np.any(p < mat_lo) or np.any(p > mat_hi):
            return False
        s0, s1 = _segment(p, yaw)
        for end in (s0, s1):
            if np.linalg.norm(end - bottle) < P.BOTTLE_OUTER_RADIUS_M + P.PILL_RADIUS_M + bottle_gap:
                return False
            if np.linalg.norm(end - cap) < P.CAP_RADIUS_M + P.PILL_RADIUS_M + 0.012:
                return False
        for q, qyaw in placed:
            if np.linalg.norm(p - q) > 0.03:
                continue
            if segment_distance(s0, s1, *_segment(q, qyaw)) < 2 * P.PILL_RADIUS_M + min_gap:
                return False
        return True

    def add(p: np.ndarray, yaw: float) -> None:
        placed.append((p, yaw))
        lo, hi = P.PILL_MASS_RANGE_KG
        poses.append(PillPose(float(p[0]), float(p[1]), float(yaw), float(rng.uniform(lo, hi))))

    attempts = 0
    while len(poses) < near_bottle and attempts < 20000:
        attempts += 1
        angle = rng.uniform(-math.pi, math.pi)
        radius = P.BOTTLE_OUTER_RADIUS_M + P.PILL_RADIUS_M + rng.uniform(0.004, 0.016)
        p = bottle + radius * np.array([math.cos(angle), math.sin(angle)])
        yaw = angle + math.pi / 2 + rng.normal(0, 0.35)  # roughly tangent to the bottle
        if clear(p, yaw, bottle_gap=0.003):
            add(p, yaw)
    lo = np.array(center) - np.array(half)
    hi = np.array(center) + np.array(half)
    while len(poses) < count and attempts < 200000:
        attempts += 1
        p = rng.uniform(lo, hi)
        yaw = rng.uniform(-math.pi, math.pi)
        if clear(p, yaw, bottle_gap=0.02):
            add(p, yaw)
    if len(poses) < count:
        raise ValueError(f"could not place {count} pills in the requested region")
    return Layout(tuple(poses), tuple(bottle_xy), tuple(cap_xy), lighting or Lighting())


def _lathe(profile: list[tuple[float, float]], segments: int = 48) -> tuple[list[float], list[int]]:
    """Revolve an (r, z) polyline about z. Points with r == 0 become poles."""
    verts: list[float] = []
    rings: list[list[int] | int] = []
    for r, z in profile:
        if r <= 1e-9:
            rings.append(len(verts) // 3)
            verts += [0.0, 0.0, z]
            continue
        ring = []
        for j in range(segments):
            a = 2 * math.pi * j / segments
            ring.append(len(verts) // 3)
            verts += [r * math.cos(a), r * math.sin(a), z]
        rings.append(ring)
    faces: list[int] = []
    for i in range(len(rings) - 1):
        a, b = rings[i], rings[i + 1]
        for j in range(segments):
            k = (j + 1) % segments
            # Winding (a_j, a_k, b_k), (a_j, b_k, b_j) gives normals along
            # tangent x profile-direction, i.e. outward on the outer wall.
            if isinstance(a, int):
                faces += [a, b[k], b[j]]
            elif isinstance(b, int):
                faces += [a[j], a[k], b]
            else:
                faces += [a[j], a[k], b[k], a[j], b[k], b[j]]
    return verts, faces


def bottle_profile() -> list[tuple[float, float]]:
    ro, h = P.BOTTLE_OUTER_RADIUS_M, P.BOTTLE_HEIGHT_M
    rn, rm = P.BOTTLE_NECK_OUTER_RADIUS_M, P.BOTTLE_MOUTH_RADIUS_M
    z0, z1 = P.BOTTLE_SHOULDER_Z_M
    ri = ro - P.BOTTLE_WALL_M
    return [
        (0.0, 0.0), (ro - 0.003, 0.0), (ro, 0.003), (ro, z0), (rn, z1), (rn, z1 + 0.003),
        (rn + 0.0012, z1 + 0.0035), (rn + 0.0012, z1 + 0.0055), (rn, z1 + 0.006), (rn, h),
        (rm, h), (rm, z1), (ri, z0 - 0.0005), (ri, 0.002), (0.0, 0.002),
    ]


def _bottle_body(layout: Layout) -> str:
    x, y = layout.bottle_xy
    ro, h, w = P.BOTTLE_OUTER_RADIUS_M, P.BOTTLE_HEIGHT_M, P.BOTTLE_WALL_M
    z0, z1 = P.BOTTLE_SHOULDER_Z_M
    rn, rm = P.BOTTLE_NECK_OUTER_RADIUS_M, P.BOTTLE_MOUTH_RADIUS_M
    fr = P.HDPE_FRICTION.mjcf()
    n = 24
    geoms = [f'<geom name="bottle_floor" class="env_col" type="cylinder" size="{f(ro - 0.0005, 0.001)}" pos="0 0 0.001" '
             f'friction="{fr}" mass="0"/>']
    for i in range(n):
        a = 2 * math.pi * i / n
        width = 2 * math.pi * ro / n * 1.15
        # Body wall.
        q = quat_from_matrix(rot_z(a))
        r_mid = ro - w / 2
        geoms.append(f'<geom class="env_col" type="box" size="{f(w / 2, width / 2, (z0 - 0.002) / 2)}" '
                     f'pos="{f(r_mid * math.cos(a), r_mid * math.sin(a), 0.002 + (z0 - 0.002) / 2)}" quat="{f(*q)}" '
                     f'friction="{fr}" mass="0"/>')
        # Conical shoulder.
        dr, dz = rn - ro, z1 - z0
        length = math.hypot(dr, dz)
        tilt = math.atan2(dr, dz)
        rq = rot_z(a) @ rot_y(tilt)
        r_c = (ro + rn) / 2 - w / 2
        geoms.append(f'<geom class="env_col" type="box" size="{f(w / 2, 2 * math.pi * r_c / n * 0.6, length / 2 + 0.001)}" '
                     f'pos="{f(r_c * math.cos(a), r_c * math.sin(a), (z0 + z1) / 2)}" quat="{f(*quat_from_matrix(rq))}" '
                     f'friction="{fr}" mass="0"/>')
        # Neck.
        rn_mid = (rn + rm) / 2
        geoms.append(f'<geom class="env_col" type="box" size="{f((rn - rm) / 2, 2 * math.pi * rn / n * 0.6, (h - z1) / 2)}" '
                     f'pos="{f(rn_mid * math.cos(a), rn_mid * math.sin(a), (z1 + h) / 2)}" quat="{f(*q)}" '
                     f'friction="{fr}" mass="0"/>')
    m = P.BOTTLE_MASS_KG
    izz = m * ro * ro
    ixx = m * (ro * ro / 2 + h * h / 12)
    return f"""
    <body name="bottle" pos="{f(x, y, P.MAT_TOP_M)}">
      <freejoint name="bottle"/>
      <inertial pos="0 0 0.045" mass="{m:g}" diaginertia="{f(ixx, ixx, izz)}"/>
      <geom class="env_visual" type="mesh" mesh="bottle_shell" material="hdpe"/>
      <geom class="env_visual" type="cylinder" size="{f(ro + 0.0002, 0.028)}" pos="0 0 0.045" material="label"/>
      <site name="bottle_mouth" pos="0 0 {h:g}" size="0.004" rgba="0 1 0 0.5" group="4"/>
      {"".join(geoms)}
    </body>"""


def _cap_body(layout: Layout) -> str:
    x, y = layout.cap_xy
    r, h = P.CAP_RADIUS_M, P.CAP_HEIGHT_M
    m = P.CAP_MASS_KG
    izz = m * r * r / 2
    ixx = m * (3 * r * r + h * h) / 12
    return f"""
    <body name="cap" pos="{f(x, y, P.MAT_TOP_M + h / 2)}">
      <freejoint name="cap"/>
      <inertial pos="0 0 0" mass="{m:g}" diaginertia="{f(ixx, ixx, izz)}"/>
      <geom class="env_col" type="cylinder" size="{f(r, h / 2)}" material="cap_red" friction="{P.HDPE_FRICTION.mjcf()}" mass="0"/>
      <geom class="env_visual" type="cylinder" size="{f(r + 0.0006, h / 2 - 0.003)}" material="cap_ridge"/>
    </body>"""


def _pill_bodies(layout: Layout) -> str:
    out = []
    z = P.MAT_TOP_M + P.PILL_RADIUS_M + 0.0002
    for i, pill in enumerate(layout.pills):
        q = pill_quat(pill.yaw)
        out.append(f"""
    <body name="pill_{i:02d}" pos="{f(pill.x, pill.y, z)}" quat="{f(*q)}">
      <freejoint name="pill_{i:02d}"/>
      <geom name="pill_{i:02d}" class="pill" mass="{pill.mass:.6g}"/>
    </body>""")
    return "".join(out)


def _look_at(eye: tuple[float, float, float], target: tuple[float, float, float]) -> str:
    e, t = np.array(eye), np.array(target)
    fwd = (t - e) / np.linalg.norm(t - e)
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    return f'pos="{f(*eye)}" xyaxes="{f(*right, *up)}"'


def _lights(lighting: Lighting) -> str:
    k, fl = lighting.key, lighting.fill
    warm = lighting.warm
    key = f"{k:.3g} {k * (1 - warm * 0.3):.3g} {k * (1 - warm):.3g}"
    fill = f"{fl * 0.95:.3g} {fl:.3g} {fl * 1.05:.3g}"
    kx, ky, kz = lighting.key_from
    target = np.array([0.45, 0.0, 0.75])
    d = target - np.array([kx, ky, kz])
    d /= np.linalg.norm(d)
    return f"""
    <light name="key" pos="{f(kx, ky, kz)}" dir="{f(*d)}" diffuse="{key}" specular="0.25 0.25 0.25" castshadow="true"
           cutoff="55" exponent="2"/>
    <light name="fill" pos="0.3 -1.3 2.0" dir="0.1 0.55 -0.83" diffuse="{fill}" specular="0.05 0.05 0.05" castshadow="false"/>
    <light name="ceiling" pos="0.6 0 2.8" dir="0 0 -1" directional="true" diffuse="{fl * 0.6:.3g} {fl * 0.6:.3g} {fl * 0.6:.3g}"
           specular="0 0 0" castshadow="false"/>"""


def build_mjcf(layout: Layout, robot: RobotSpec | None = None) -> str:
    robot = robot or RobotSpec()
    verts, faces = _lathe(bottle_profile())
    th = P.TABLE_HEIGHT_M
    mx, my = MAT_CENTER
    hx, hy = MAT_HALF
    amb = layout.lighting.ambient
    solref = f(*P.CONTACT_SOLREF)
    solimp = f(*P.CONTACT_SOLIMP)
    return f"""<mujoco model="bimanual_pill_task">
  <compiler angle="radian" autolimits="true" inertiafromgeom="auto"/>
  <option timestep="{P.TIMESTEP_S:g}" integrator="{P.INTEGRATOR}" cone="{P.CONE}" impratio="{P.IMPRATIO:g}"
          solver="{P.SOLVER}" iterations="{P.SOLVER_ITERATIONS}" tolerance="{P.SOLVER_TOLERANCE:g}"
          noslip_iterations="{P.NOSLIP_ITERATIONS}" gravity="0 0 {-P.GRAVITY:g}">
    <flag multiccd="enable"/>
  </option>
  <size memory="64M"/>
  <visual>
    <global offwidth="1280" offheight="960" fovy="45"/>
    <quality shadowsize="4096" offsamples="4"/>
    <headlight ambient="{amb:.3g} {amb:.3g} {amb:.3g}" diffuse="0.18 0.18 0.18" specular="0.05 0.05 0.05"/>
    <map znear="0.01" zfar="30" shadowclip="2.5"/>
  </visual>
  <default>
    <geom solref="{solref}" solimp="{solimp}" condim="{P.DEFAULT_CONDIM}"/>
    <default class="env_col">
      <geom contype="{ENV}" conaffinity="{ALL}" group="3"/>
    </default>
    <default class="env_visual">
      <geom contype="0" conaffinity="0" group="1" mass="0"/>
    </default>
    <default class="pill">
      <geom type="capsule" size="{P.PILL_RADIUS_M:g} {P.PILL_HALF_LENGTH_M:g}" contype="{ENV}" conaffinity="{ALL}"
            condim="{P.PILL_CONDIM}" friction="{P.PILL_FRICTION.mjcf()}" material="pill" group="2"/>
    </default>
    {robot_defaults()}
  </default>
  <asset>
    {MATERIALS}
    <material name="floor" rgba="0.52 0.53 0.55 1" specular="0.1"/>
    <material name="wall" rgba="0.80 0.80 0.79 1" specular="0"/>
    <material name="table" rgba="0.13 0.13 0.14 1" specular="0.25" shininess="0.3"/>
    <material name="table_leg" rgba="0.25 0.26 0.27 1" specular="0.4"/>
    <material name="mat" rgba="0.055 0.057 0.062 1" specular="0.05" shininess="0.05"/>
    <material name="pill" rgba="0.97 0.97 0.95 1" specular="0.35" shininess="0.6"/>
    <material name="hdpe" rgba="0.96 0.96 0.94 1" specular="0.25" shininess="0.35"/>
    <material name="label" rgba="0.93 0.93 0.91 1" specular="0.1"/>
    <material name="cap_red" rgba="0.82 0.07 0.07 1" specular="0.35" shininess="0.5"/>
    <material name="cap_ridge" rgba="0.70 0.05 0.05 1" specular="0.2"/>
    <mesh name="bottle_shell" vertex="{" ".join(f"{v:.6g}" for v in verts)}" face="{" ".join(map(str, faces))}"/>
  </asset>
  <worldbody>
    {_lights(layout.lighting)}
    <geom name="floor" type="plane" size="4 4 0.1" material="floor" contype="{ENV}" conaffinity="{ALL}"
          friction="{P.SHELL_FRICTION.mjcf()}"/>
    <geom name="wall" type="box" pos="2.3 0 1.4" size="0.02 3 1.4" material="wall" contype="0" conaffinity="0"/>
    <geom name="back_wall" type="box" pos="-1.6 0 1.4" size="0.02 3 1.4" material="wall" contype="0" conaffinity="0"/>
    <geom name="side_wall" type="box" pos="0.35 -2.4 1.4" size="2 0.02 1.4" material="wall" contype="0" conaffinity="0"/>
    <geom name="table" type="box" pos="{f(0.70, 0, th - 0.015)}" size="0.45 0.6 0.015" material="table"
          contype="{ENV}" conaffinity="{ALL}" friction="{P.TABLE_FRICTION.mjcf()}"/>
    {"".join(f'<geom type="box" pos="{f(0.70 + sx * 0.41, sy * 0.56, (th - 0.03) / 2)}" size="{f(0.02, 0.02, (th - 0.03) / 2)}" material="table_leg" contype="0" conaffinity="0"/>' for sx in (-1, 1) for sy in (-1, 1))}
    <geom name="mat_visual" type="box" pos="{f(mx, my, th + P.MAT_THICKNESS_M / 2)}" size="{f(hx, hy, P.MAT_THICKNESS_M / 2)}"
          material="mat" contype="0" conaffinity="0"/>
    <!-- Collision box of the 3 mm mat extends 25 mm into the table top, so a fast
         impact that penetrates deeper than the mat still resolves upward instead
         of lodging between two overlapping boxes. The top surface is unchanged. -->
    <geom name="mat" type="box" pos="{f(mx, my, P.MAT_TOP_M - 0.014)}" size="{f(hx, hy, 0.014)}" group="3"
          contype="{ENV}" conaffinity="{ALL}" friction="{P.MAT_FRICTION.mjcf()}"/>
    <camera name="photo" fovy="40" {_look_at((1.55, 0.62, 1.42), (0.28, 0.0, 0.93))}/>
    <camera name="overview" fovy="50" {_look_at((1.25, -0.95, 1.65), (0.35, 0.0, 0.85))}/>
    <camera name="top" fovy="45" {_look_at((0.47, 0.0, 1.75), (0.47, 0.0001, 0.75))}/>
    {robot_bodies(robot)}
    {_bottle_body(layout)}
    {_cap_body(layout)}
    {_pill_bodies(layout)}
  </worldbody>
  <tendon>
    {robot_tendons()}
  </tendon>
  <equality>
    {robot_equality()}
  </equality>
  <contact>
    {robot_contact_excludes()}
  </contact>
  <actuator>
    {robot_actuators(robot)}
  </actuator>
</mujoco>"""
