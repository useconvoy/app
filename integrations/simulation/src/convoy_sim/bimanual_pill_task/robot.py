"""Procedural MJCF for a generic wheeled bimanual station robot (SI units).

Layout, in the world frame (x forward toward the table, y left, z up, floor at 0):

* a wheeled base (0.52 x 0.46 m, ~27 kg with battery) with a small enclosure for
  the edge computer, fixed to the floor for a station task;
* a vertical 80 x 80 mm aluminium-extrusion column;
* a white rounded torso shell with a T-shaped shoulder crossbar at 1.06 m and
  three blue logo dots on the chest;
* a pan/tilt neck and a white box head (top at 1.30 m) with a dark face display,
  two green eye lights and the head camera;
* two compact 6-DOF arms (yaw, pitch, elbow, forearm roll, wrist pitch, wrist
  roll; 0.30 m upper arm, 0.28 m forearm, 0.77 m shoulder-to-fingertip) mounted
  either side of the torso at shoulder height, each with a white printed
  two-finger parallel gripper (0-80 mm stroke, 20 N) and a wrist camera.

Masses and inertias are explicit (solid cylinders/boxes of the stated mass), so
the totals are auditable: about 50 kg overall and 5.6 kg per moving arm. Joint
servos are torque limited; gravity compensation is routed through the actuators
(``actuatorgravcomp``) so it counts against those limits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import physics as P

ENV, BODY, LEFT, RIGHT = 1, 2, 4, 8
ARM_JOINTS = ("shoulder_yaw", "shoulder_pitch", "elbow", "forearm_roll", "wrist_pitch", "wrist_roll")
SIDES = ("left", "right")


def f(*values: float) -> str:
    return " ".join(f"{v:.6g}" for v in values)


def cylinder_inertia(mass: float, radius: float, length: float, axis: str) -> tuple[float, float, float]:
    axial = mass * radius * radius / 2
    transverse = mass * (3 * radius * radius + length * length) / 12
    return {"x": (axial, transverse, transverse), "y": (transverse, axial, transverse),
            "z": (transverse, transverse, axial)}[axis]


def box_inertia(mass: float, sx: float, sy: float, sz: float) -> tuple[float, float, float]:
    """Full side lengths."""
    return (mass * (sy * sy + sz * sz) / 12, mass * (sx * sx + sz * sz) / 12, mass * (sx * sx + sy * sy) / 12)


def inertial(pos: tuple[float, float, float], mass: float, diag: tuple[float, float, float]) -> str:
    return f'<inertial pos="{f(*pos)}" mass="{mass:.6g}" diaginertia="{f(*diag)}"/>'


@dataclass(frozen=True)
class ArmSpec:
    """One 6-DOF arm. Positions are for the left arm; the right arm is mirrored."""

    shoulder: tuple[float, float, float] = (0.08, 0.17, 1.13)  # shoulder-yaw axis on the torso side
    shoulder_offset: float = 0.07  # yaw axis to the pitch joint, outward
    upper_arm: float = 0.30
    forearm_roll_at: float = 0.11  # forearm roll joint, from the elbow
    forearm: float = 0.30  # elbow to wrist centre
    wrist_to_flange: float = 0.06
    tcp_from_flange: float = 0.126  # 4 mm above the fingertip end
    # Humanoid convention: zero = arm straight forward; positive pitch lowers the
    # arm; elbow flexion is negative (forearm raised), like a human elbow.
    lower: tuple[float, ...] = (-1.5, -1.0, -2.6, -3.0, -2.0, -3.0)
    upper: tuple[float, ...] = (2.2, 2.0, 0.2, 3.0, 2.4, 3.0)
    # Rated joint torque [N m]: harmonic-drive class for a 2.5 kg per-arm payload.
    # The worst static load (arm horizontal, 2.5 kg at the TCP) is ~36 N m at the
    # shoulder pitch and ~17 N m at the elbow.
    torque: tuple[float, ...] = (30.0, 60.0, 30.0, 12.0, 12.0, 6.0)
    kp: tuple[float, ...] = (300.0, 400.0, 300.0, 60.0, 60.0, 30.0)
    kv: tuple[float, ...] = (25.0, 35.0, 25.0, 4.0, 4.0, 2.0)
    # Reflected rotor inertia through ~100:1 harmonic drives [kg m^2].
    armature: tuple[float, ...] = (0.08, 0.10, 0.06, 0.015, 0.015, 0.008)
    damping: tuple[float, ...] = (0.5, 0.5, 0.4, 0.1, 0.1, 0.05)
    frictionloss: tuple[float, ...] = (0.4, 0.5, 0.3, 0.1, 0.1, 0.05)
    # Link masses [kg]: shoulder, upper arm (incl. elbow motor), forearm (incl.
    # roll motor), forearm distal (incl. wrist-pitch motor), wrist, gripper+camera.
    masses: tuple[float, ...] = (1.4, 1.9, 0.7, 0.6, 0.4, 0.5)


@dataclass(frozen=True)
class GripperSpec:
    stroke: float = 0.080  # total opening between the pads [m]
    max_force: float = 20.0  # per-finger squeeze at saturation [N]
    kp: float = 1500.0  # opening servo stiffness [N/m]
    kv: float = 30.0  # [N s/m]
    finger_mass: float = 0.025
    finger_armature: float = 0.10  # reflected servo inertia at the finger [kg]
    finger_damping: float = 2.0
    finger_frictionloss: float = 0.2


@dataclass(frozen=True)
class RobotSpec:
    arm: ArmSpec = field(default_factory=ArmSpec)
    gripper: GripperSpec = field(default_factory=GripperSpec)
    head_pan: float = 0.0
    head_tilt: float = 0.35  # the head looks down at the table [rad]
    head_camera_pitch: float = 0.55  # camera pitched down inside the head [rad]
    head_camera_fovy: float = 64.0
    base_mass: float = 27.0
    column_mass: float = 1.2
    torso_mass: float = 8.0  # shell, crossbar, shoulder-yaw motors, power electronics
    computer_mass: float = 0.6
    neck_mass: float = 0.3
    head_mass: float = 1.5


MATERIALS = """
    <material name="shell_white" rgba="0.92 0.92 0.91 1" specular="0.35" shininess="0.45"/>
    <material name="printed_white" rgba="0.95 0.95 0.93 1" specular="0.12" shininess="0.2"/>
    <material name="joint_grey" rgba="0.68 0.69 0.71 1" specular="0.4" shininess="0.5"/>
    <material name="dark_grey" rgba="0.17 0.18 0.19 1" specular="0.2" shininess="0.3"/>
    <material name="aluminium" rgba="0.74 0.76 0.79 1" specular="0.7" shininess="0.7"/>
    <material name="groove" rgba="0.30 0.31 0.33 1" specular="0.2"/>
    <material name="rubber" rgba="0.06 0.06 0.06 1" specular="0.08" shininess="0.1"/>
    <material name="face_glass" rgba="0.025 0.03 0.035 1" specular="0.9" shininess="0.95"/>
    <material name="eye_green" rgba="0.25 1 0.45 1" emission="1"/>
    <material name="logo_blue" rgba="0.12 0.45 0.95 1" emission="0.35" specular="0.5"/>
    <material name="pad" rgba="0.24 0.25 0.26 1" specular="0.1"/>
    <material name="camera_black" rgba="0.08 0.08 0.09 1" specular="0.3"/>
    <material name="lens" rgba="0.05 0.08 0.15 1" specular="1" shininess="1"/>
    <material name="led_green" rgba="0.2 1 0.3 1" emission="1"/>
"""


def _defaults() -> str:
    friction_shell = P.SHELL_FRICTION.mjcf()
    return f"""
    <default class="robot">
      <joint limited="true"/>
      <default class="visual">
        <geom contype="0" conaffinity="0" group="1" mass="0"/>
      </default>
      <default class="body_col">
        <geom contype="{BODY}" conaffinity="{ENV | LEFT | RIGHT}" group="2" mass="0" friction="{friction_shell}"/>
      </default>
      <default class="left_col">
        <geom contype="{LEFT}" conaffinity="{ENV | BODY | RIGHT}" group="2" mass="0" friction="{friction_shell}"/>
      </default>
      <default class="right_col">
        <geom contype="{RIGHT}" conaffinity="{ENV | BODY | LEFT}" group="2" mass="0" friction="{friction_shell}"/>
      </default>
    </default>"""


def _rounded_box(cls: str, material: str, center: tuple[float, float, float],
                 half: tuple[float, float, float], radius: float) -> list[str]:
    """A box with vertical edges rounded: two boxes and four cylinders."""
    cx, cy, cz = center
    hx, hy, hz = half
    out = [
        f'<geom class="{cls}" type="box" material="{material}" pos="{f(cx, cy, cz)}" size="{f(hx - radius, hy, hz)}"/>',
        f'<geom class="visual" type="box" material="{material}" pos="{f(cx, cy, cz)}" size="{f(hx, hy - radius, hz)}"/>',
    ]
    for sx in (-1, 1):
        for sy in (-1, 1):
            out.append(f'<geom class="visual" type="cylinder" material="{material}" '
                       f'pos="{f(cx + sx * (hx - radius), cy + sy * (hy - radius), cz)}" size="{f(radius, hz)}"/>')
    return out


def _base(spec: RobotSpec) -> list[str]:
    base = spec.base_mass + spec.computer_mass
    g = [inertial((-0.025, 0.0, 0.16), base, box_inertia(base, 0.52, 0.46, 0.20))]
    g += _rounded_box("body_col", "shell_white", (-0.02, 0.0, 0.17), (0.26, 0.23, 0.10), 0.06)
    # Dark bumper band, top plate and the drive wheels / casters.
    g += _rounded_box("visual", "dark_grey", (-0.02, 0.0, 0.085), (0.265, 0.235, 0.018), 0.062)
    g.append(f'<geom class="visual" type="box" material="joint_grey" pos="{f(-0.02, 0, 0.2715)}" size="0.2 0.17 0.002"/>')
    for sy in (-1, 1):
        g.append(f'<geom class="visual" type="cylinder" material="rubber" pos="{f(0.0, sy * 0.215, 0.075)}" '
                 f'size="0.075 0.02" euler="1.5708 0 0"/>')
        g.append(f'<geom class="visual" type="cylinder" material="joint_grey" pos="{f(0.0, sy * 0.237, 0.075)}" '
                 f'size="0.03 0.003" euler="1.5708 0 0"/>')
        for sx in (-1, 1):
            g.append(f'<geom class="visual" type="sphere" material="rubber" pos="{f(-0.02 + sx * 0.19, sy * 0.15, 0.03)}" size="0.03"/>')
    # Edge-computer enclosure (Jetson Orin Nano class) with vents and a status LED.
    m = spec.computer_mass
    del m  # counted in the base inertial above
    g.append(f'<geom class="body_col" type="box" material="dark_grey" pos="{f(-0.17, 0.0, 0.296)}" size="0.065 0.055 0.0225"/>')
    for i in range(5):
        g.append(f'<geom class="visual" type="box" material="groove" pos="{f(-0.17 + 0.066, -0.03 + i * 0.015, 0.296)}" '
                 'size="0.0008 0.004 0.014"/>')
    g.append(f'<geom class="visual" type="sphere" material="led_green" pos="{f(-0.104, 0.042, 0.31)}" size="0.0025"/>')
    return g


def _column(spec: RobotSpec) -> list[str]:
    # 80 x 80 mm extrusion from the base top (0.27 m) to inside the torso (0.86 m).
    z0, z1 = 0.27, 0.86
    cz, hz = (z0 + z1) / 2, (z1 - z0) / 2
    g = [f'<geom class="body_col" type="box" material="aluminium" pos="{f(-0.03, 0, cz)}" size="{f(0.04, 0.04, hz)}"/>']
    # T-slot grooves: two per face.
    for offset in (-0.02, 0.02):
        for sx in (-1, 1):
            g.append(f'<geom class="visual" type="box" material="groove" pos="{f(-0.03 + sx * 0.0405, offset, cz)}" '
                     f'size="{f(0.001, 0.003, hz - 0.004)}"/>')
            g.append(f'<geom class="visual" type="box" material="groove" pos="{f(-0.03 + offset, sx * 0.0405, cz)}" '
                     f'size="{f(0.003, 0.001, hz - 0.004)}"/>')
    # Mounting plate where the column meets the base.
    g.append(f'<geom class="visual" type="box" material="joint_grey" pos="{f(-0.03, 0, 0.278)}" size="0.07 0.07 0.006"/>')
    return g


def _torso(spec: RobotSpec) -> list[str]:
    cx, cz = 0.0, 1.0
    a, b, c = 0.11, 0.145, 0.165
    shoulder_z = spec.arm.shoulder[2]
    g = [f'<geom class="body_col" type="ellipsoid" material="shell_white" pos="{f(cx, 0, cz)}" size="{f(a, b, c)}"/>']
    # Waist collar around the column and the T-shaped shoulder crossbar.
    g.append(f'<geom class="visual" type="cylinder" material="joint_grey" pos="{f(-0.01, 0, 0.82)}" size="0.07 0.02"/>')
    g.append(f'<geom class="body_col" type="capsule" material="shell_white" '
             f'fromto="{f(0.05, -0.165, shoulder_z, 0.05, 0.165, shoulder_z)}" size="0.045"/>')
    # Three blue logo dots on the chest, on the ellipsoid surface.
    for y in (-0.028, 0.0, 0.028):
        z = cz + 0.02
        x = cx + a * (1 - (y / b) ** 2 - ((z - cz) / c) ** 2) ** 0.5
        g.append(f'<geom class="visual" type="cylinder" material="logo_blue" pos="{f(x - 0.0005, y, z)}" '
                 'size="0.0085 0.0018" euler="0 1.5708 0"/>')
    # Neck collar.
    g.append(f'<geom class="visual" type="cylinder" material="joint_grey" pos="{f(0.0, 0, 1.165)}" size="0.04 0.01"/>')
    return g


def _head(spec: RobotSpec) -> str:
    hx, hy, hz = 0.06, 0.085, 0.0575
    cx, cz = 0.01, hz
    front = cx + hx
    head_geoms = _rounded_box("body_col", "shell_white", (cx, 0.0, cz), (hx, hy, hz), 0.02)
    head_geoms.append(f'<geom class="visual" type="box" material="face_glass" pos="{f(front + 0.0015, 0, cz - 0.006)}" '
                      'size="0.0015 0.068 0.038"/>')
    for y in (-0.03, 0.03):
        head_geoms.append(f'<geom class="visual" type="cylinder" material="eye_green" pos="{f(front + 0.0032, y, cz + 0.002)}" '
                          'size="0.011 0.0006" euler="0 1.5708 0"/>')
    # Head camera module above the display (an RGB-D bar).
    head_geoms.append(f'<geom class="visual" type="box" material="camera_black" pos="{f(front + 0.002, 0, cz + 0.045)}" '
                      'size="0.004 0.045 0.007"/>')
    head_geoms.append(f'<geom class="visual" type="cylinder" material="lens" pos="{f(front + 0.0062, 0, cz + 0.045)}" '
                      'size="0.0055 0.0005" euler="0 1.5708 0"/>')
    pitch = spec.head_camera_pitch
    # MuJoCo cameras look along their -z with +y up: look along head +x, pitched
    # down by `pitch`; image right is the robot's right (-y).
    up = (math.sin(pitch), 0.0, math.cos(pitch))
    camera = (f'<camera name="head_camera" pos="{f(front + 0.007, 0, cz + 0.045)}" fovy="{spec.head_camera_fovy:g}" '
              f'xyaxes="{f(0.0, -1.0, 0.0, *up)}"/>')
    head_inertia = box_inertia(spec.head_mass, 2 * hx, 2 * hy, 2 * hz)
    neck_inertia = cylinder_inertia(spec.neck_mass, 0.03, 0.04, "z")
    return f"""
      <body name="neck" pos="0 0 1.16" gravcomp="1">
        <joint name="head_pan" type="hinge" axis="0 0 1" range="-1.2 1.2" armature="0.01" damping="0.5"
               actuatorgravcomp="true" actuatorfrcrange="-4 4"/>
        {inertial((0, 0, 0.02), spec.neck_mass, neck_inertia)}
        <geom class="visual" type="cylinder" material="joint_grey" pos="0 0 0.012" size="0.028 0.013"/>
        <body name="head" pos="0 0 0.025" gravcomp="1">
          <joint name="head_tilt" type="hinge" axis="0 1 0" range="-0.4 1.0" armature="0.01" damping="0.5"
                 actuatorgravcomp="true" actuatorfrcrange="-6 6"/>
          {inertial((cx, 0, cz), spec.head_mass, head_inertia)}
          <geom class="visual" type="cylinder" material="joint_grey" pos="0 0 0" size="0.02 0.03" euler="1.5708 0 0"/>
          {"".join(head_geoms)}
          {camera}
        </body>
      </body>"""


def _gripper(side: str, spec: RobotSpec) -> str:
    """Gripper in the wrist-roll (flange) frame: approach along +x, fingers along y."""
    gs = spec.gripper
    col = f"{side}_col"
    p = f"{side}_"
    pad = P.PAD_FRICTION.mjcf()
    printed = P.PRINTED_FRICTION.mjcf()
    fingers = []
    for name, sign in (("finger_a", 1), ("finger_b", -1)):
        fingers.append(f"""
            <body name="{p}{name}" pos="0 0 0">
              <joint name="{p}{name}" type="slide" axis="0 {sign} 0" range="0 {gs.stroke / 2:g}"
                     armature="{gs.finger_armature:g}" damping="{gs.finger_damping:g}" frictionloss="{gs.finger_frictionloss:g}"/>
              {inertial((0.092, sign * 0.0075, 0), gs.finger_mass, box_inertia(gs.finger_mass, 0.078, 0.009, 0.016))}
              <geom class="{col}" type="box" material="printed_white" pos="{f(0.091, sign * 0.0055, 0)}"
                    size="0.039 0.0035 0.008" friction="{printed}"/>
              <geom class="visual" type="box" material="printed_white" pos="{f(0.064, sign * 0.0105, 0)}" size="0.012 0.0045 0.0095"/>
              <geom name="{p}{name}_pad" class="{col}" type="box" material="pad" pos="{f(0.118, sign * 0.001, 0)}"
                    size="0.012 0.001 0.007" friction="{pad}" condim="{P.PAD_CONDIM}"/>
            </body>""")
    return f"""
            <geom class="{col}" type="box" material="printed_white" pos="0.026 0 0" size="0.026 0.042 0.024" friction="{printed}"/>
            <geom class="visual" type="cylinder" material="joint_grey" pos="0.002 0 0" size="0.03 0.004" euler="0 1.5708 0"/>
            <geom class="visual" type="box" material="joint_grey" pos="0.0555 0 0" size="0.0035 0.046 0.011"/>
            <geom class="visual" type="box" material="camera_black" pos="0.03 0 0.036" size="0.021 0.021 0.012"/>
            <geom class="visual" type="cylinder" material="lens" pos="0.0515 0 0.036" size="0.006 0.0006" euler="0 1.5708 0"/>
            <camera name="{p}wrist_camera" pos="0.052 0 0.036" fovy="58" xyaxes="0 -1 0 0.26 0 0.966"/>
            <site name="{p}tcp" pos="{spec.arm.tcp_from_flange:g} 0 0" size="0.003" rgba="1 0 0 0.6" group="4"/>
            {"".join(fingers)}"""


def _arm(side: str, spec: RobotSpec) -> str:
    a = spec.arm
    s = 1 if side == "left" else -1
    p = f"{side}_"
    col = f"{side}_col"
    m = a.masses

    def joint(i: int, axis: tuple[float, float, float]) -> str:
        return (f'<joint name="{p}{ARM_JOINTS[i]}" type="hinge" axis="{f(*axis)}" range="{f(a.lower[i], a.upper[i])}" '
                f'armature="{a.armature[i]:g}" damping="{a.damping[i]:g}" frictionloss="{a.frictionloss[i]:g}" '
                f'actuatorgravcomp="true" actuatorfrcrange="{f(-a.torque[i], a.torque[i])}"/>')

    sx, sy, sz = a.shoulder
    ua, fr, fa = a.upper_arm, a.forearm_roll_at, a.forearm
    distal = fa - fr
    return f"""
      <body name="{p}shoulder" pos="{f(sx, s * sy, sz)}" gravcomp="1">
        {joint(0, (0, 0, s))}
        {inertial((0, s * 0.03, 0), m[0], cylinder_inertia(m[0], 0.046, 0.10, "z"))}
        <geom class="{col}" type="cylinder" material="shell_white" size="0.046 0.05"/>
        <geom class="visual" type="cylinder" material="joint_grey" pos="0 0 0.05" size="0.047 0.006"/>
        <geom class="visual" type="cylinder" material="joint_grey" pos="0 0 -0.05" size="0.047 0.006"/>
        <geom class="{col}" type="cylinder" material="shell_white" pos="{f(0, s * a.shoulder_offset, 0)}" size="0.044 0.03"
              euler="1.5708 0 0"/>
        <body name="{p}upper_arm" pos="{f(0, s * a.shoulder_offset, 0)}" gravcomp="1">
          {joint(1, (0, 1, 0))}
          {inertial((ua * 0.55, 0, 0), m[1], cylinder_inertia(m[1], 0.034, ua, "x"))}
          <geom class="visual" type="cylinder" material="joint_grey" pos="{f(0, s * 0.031, 0)}" size="0.045 0.004" euler="1.5708 0 0"/>
          <geom class="{col}" type="capsule" material="shell_white" fromto="{f(0.04, 0, 0, ua - 0.035, 0, 0)}" size="0.033"/>
          <geom class="{col}" type="cylinder" material="joint_grey" pos="{f(ua, 0, 0)}" size="0.039 0.033" euler="1.5708 0 0"/>
          <body name="{p}forearm" pos="{f(ua, 0, 0)}" gravcomp="1">
            {joint(2, (0, 1, 0))}
            {inertial((fr * 0.5, 0, 0), m[2], cylinder_inertia(m[2], 0.029, fr, "x"))}
            <geom class="{col}" type="capsule" material="shell_white" fromto="{f(0.035, 0, 0, fr - 0.012, 0, 0)}" size="0.029"/>
            <geom class="visual" type="cylinder" material="joint_grey" pos="{f(fr - 0.004, 0, 0)}" size="0.031 0.008" euler="0 1.5708 0"/>
            <body name="{p}forearm_distal" pos="{f(fr, 0, 0)}" gravcomp="1">
              {joint(3, (s, 0, 0))}
              {inertial((distal * 0.5, 0, 0), m[3], cylinder_inertia(m[3], 0.026, distal, "x"))}
              <geom class="{col}" type="capsule" material="shell_white" fromto="{f(0.012, 0, 0, distal - 0.03, 0, 0)}" size="0.026"/>
              <geom class="{col}" type="cylinder" material="joint_grey" pos="{f(distal, 0, 0)}" size="0.029 0.027" euler="1.5708 0 0"/>
              <body name="{p}wrist" pos="{f(distal, 0, 0)}" gravcomp="1">
                {joint(4, (0, 1, 0))}
                {inertial((0.03, 0, 0), m[4], cylinder_inertia(m[4], 0.025, 0.06, "x"))}
                <geom class="{col}" type="cylinder" material="shell_white" fromto="{f(0.012, 0, 0, a.wrist_to_flange - 0.004, 0, 0)}" size="0.024"/>
                <body name="{p}flange" pos="{f(a.wrist_to_flange, 0, 0)}" gravcomp="1">
                  {joint(5, (s, 0, 0))}
                  {inertial((0.035, 0, 0.004), m[5], box_inertia(m[5], 0.07, 0.084, 0.06))}
                  {_gripper(side, spec)}
                </body>
              </body>
            </body>
          </body>
        </body>
      </body>"""


def robot_bodies(spec: RobotSpec) -> str:
    static = _base(spec) + _column(spec) + _torso(spec)
    column_inertia = box_inertia(spec.column_mass, 0.08, 0.08, 0.59)
    torso_inertia = box_inertia(spec.torso_mass, 0.22, 0.30, 0.33)
    return f"""
    <body name="robot" childclass="robot">
      {"".join(static)}
      <body name="column_mass" pos="-0.03 0 0.565">{inertial((0, 0, 0), spec.column_mass, column_inertia)}</body>
      <body name="torso_mass" pos="0 0 0.98">{inertial((0, 0, 0), spec.torso_mass, torso_inertia)}</body>
      {_head(spec)}
      {_arm("left", spec)}
      {_arm("right", spec)}
    </body>"""


def robot_actuators(spec: RobotSpec) -> str:
    a = spec.arm
    g = spec.gripper
    lines = []
    for side in SIDES:
        for i, name in enumerate(ARM_JOINTS):
            lo, hi = a.lower[i] - 0.5, a.upper[i] + 0.5
            lines.append(f'<position name="{side}_{name}" joint="{side}_{name}" kp="{a.kp[i]:g}" kv="{a.kv[i]:g}" '
                         f'ctrlrange="{f(lo, hi)}"/>')
        lines.append(f'<general name="{side}_gripper" tendon="{side}_grip" gaintype="fixed" gainprm="{g.kp:g}" '
                     f'biastype="affine" biasprm="0 {-g.kp:g} {-g.kv:g}" ctrlrange="0 {g.stroke:g}" '
                     f'forcerange="{f(-g.max_force, g.max_force)}"/>')
    lines.append('<position name="head_pan" joint="head_pan" kp="20" kv="2" ctrlrange="-1.2 1.2"/>')
    lines.append('<position name="head_tilt" joint="head_tilt" kp="30" kv="3" ctrlrange="-0.4 1.0"/>')
    return "\n    ".join(lines)


def robot_tendons() -> str:
    return "\n    ".join(
        f'<fixed name="{side}_grip"><joint joint="{side}_finger_a" coef="1"/><joint joint="{side}_finger_b" coef="1"/></fixed>'
        for side in SIDES)


def robot_equality() -> str:
    # Rack-and-pinion coupling: both fingers move together (one servo).
    return "\n    ".join(
        f'<joint joint1="{side}_finger_a" joint2="{side}_finger_b" polycoef="0 1 0 0 0" solref="0.005 1"/>'
        for side in SIDES)


def robot_contact_excludes() -> str:
    # The gripper housing and the fingers share the flange body pair only through
    # joints, so exclude the touching neighbours explicitly.
    pairs = []
    for side in SIDES:
        pairs.append(f'<exclude body1="{side}_flange" body2="{side}_finger_a"/>')
        pairs.append(f'<exclude body1="{side}_flange" body2="{side}_finger_b"/>')
        pairs.append(f'<exclude body1="{side}_finger_a" body2="{side}_finger_b"/>')
        # The robot root is welded to the world, so MuJoCo's parent-child filter
        # does not apply to it: exclude the shoulder mounts explicitly.
        pairs.append(f'<exclude body1="robot" body2="{side}_shoulder"/>')
        pairs.append(f'<exclude body1="robot" body2="{side}_upper_arm"/>')
    pairs.append('<exclude body1="robot" body2="neck"/>')
    pairs.append('<exclude body1="robot" body2="head"/>')
    return "\n    ".join(pairs)
