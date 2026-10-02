"""Compiled scene plus task-level state: pill poses, bottle containment, safety."""

from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

from . import physics as P
from .robot import ARM_JOINTS, SIDES, RobotSpec
from .scene import Layout, build_mjcf

BOTTLE_INNER_RADIUS_M = P.BOTTLE_OUTER_RADIUS_M - P.BOTTLE_WALL_M


@dataclass(frozen=True)
class PillView:
    index: int
    pos: np.ndarray
    axis_yaw: float  # direction of the capsule's long axis in the table plane
    tilt: float  # angle of the long axis from horizontal [rad]
    in_bottle: bool
    on_mat: bool
    lost: bool  # off the table or on the floor


class World:
    """One episode's MuJoCo model/data and the task's ground-truth measurements."""

    def __init__(self, layout: Layout, robot: RobotSpec | None = None):
        self.layout = layout
        self.robot = robot or RobotSpec()
        self.xml = build_mjcf(layout, self.robot)
        self.model = mujoco.MjModel.from_xml_string(self.xml)
        self.data = mujoco.MjData(self.model)
        m = self.model
        self.n_pills = len(layout.pills)
        self.pill_bodies = np.array([m.body(f"pill_{i:02d}").id for i in range(self.n_pills)], dtype=int)
        self.pill_geoms = np.array([m.geom(f"pill_{i:02d}").id for i in range(self.n_pills)], dtype=int)
        self.bottle = m.body("bottle").id
        self.cap = m.body("cap").id
        self.pad_geoms = {side: (m.geom(f"{side}_finger_a_pad").id, m.geom(f"{side}_finger_b_pad").id) for side in SIDES}
        self.grip_actuator = {side: m.actuator(f"{side}_gripper").id for side in SIDES}
        self.grip_tendon = {side: m.tendon(f"{side}_grip").id for side in SIDES}
        self.arm_bodies = {side: {b for b in range(m.nbody) if m.body(b).name.startswith(f"{side}_")} for side in SIDES}
        self.robot_bodies = {b for b in range(m.nbody) if m.body_rootid[b] == m.body("robot").id}
        self.env_bodies = {0, self.bottle, self.cap, *self.pill_bodies.tolist()}
        self.time_offset = 0.0
        self.max_robot_contact_force = 0.0  # non-pill contacts between robot and environment [N]
        self.max_arm_arm_contact_force = 0.0  # left arm touching right arm [N]
        self.arm_arm_contacts = 0  # separate arm-arm contact events
        # Peak forces during the last step() call, for the protective stop.
        self.arm_env_force = {side: 0.0 for side in SIDES}  # bottle/cap contact on each arm [N]
        self.arm_static_force = {side: 0.0 for side in SIDES}  # table/mat contact on each arm [N]
        self.arm_arm_force = 0.0
        self.unstable = False  # MuJoCo reported a diverging step (and reset the state)
        self._arms_touching = False
        self._force = np.zeros(6)
        # Per-body flags for the vectorised contact scan after every physics step.
        self._is_robot = np.zeros(m.nbody, dtype=bool)
        self._is_robot[list(self.robot_bodies)] = True
        self._is_pill = np.zeros(m.nbody, dtype=bool)
        self._is_pill[self.pill_bodies] = True
        self._arm_side = np.zeros(m.nbody, dtype=np.int8)  # 1 left arm, 2 right arm
        self._arm_side[list(self.arm_bodies["left"])] = 1
        self._arm_side[list(self.arm_bodies["right"])] = 2
        self._gripper_body = {}
        for side in SIDES:
            flags = np.zeros(m.nbody, dtype=bool)
            flags[[m.body(f"{side}_{name}").id for name in ("flange", "finger_a", "finger_b")]] = True
            self._gripper_body[side] = flags

    # --- configuration -----------------------------------------------------
    def set_arm(self, side: str, q: np.ndarray) -> None:
        for name, value in zip(ARM_JOINTS, q, strict=True):
            jid = self.model.joint(f"{side}_{name}").id
            self.data.qpos[self.model.jnt_qposadr[jid]] = value
            self.data.ctrl[self.model.actuator(f"{side}_{name}").id] = value

    def set_gripper(self, side: str, opening: float) -> None:
        for name in ("finger_a", "finger_b"):
            jid = self.model.joint(f"{side}_{name}").id
            self.data.qpos[self.model.jnt_qposadr[jid]] = opening / 2
        self.data.ctrl[self.grip_actuator[side]] = opening

    def set_head(self, pan: float, tilt: float) -> None:
        for name, value in (("head_pan", pan), ("head_tilt", tilt)):
            jid = self.model.joint(name).id
            self.data.qpos[self.model.jnt_qposadr[jid]] = value
            self.data.ctrl[self.model.actuator(name).id] = value

    def step(self, n: int = 1) -> None:
        self.arm_env_force = {side: 0.0 for side in SIDES}
        self.arm_static_force = {side: 0.0 for side in SIDES}
        self.arm_arm_force = 0.0
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
            if self.data.warning[mujoco.mjtWarning.mjWARN_BADQACC].number > 0:
                self.unstable = True
                return
            self._record_contact_forces()

    @property
    def time(self) -> float:
        return float(self.data.time)

    # --- measurements --------------------------------------------------------
    def gripper_opening(self, side: str) -> float:
        return float(self.data.ten_length[self.grip_tendon[side]])

    def arm_q(self, side: str) -> np.ndarray:
        return np.array([self.data.qpos[self.model.jnt_qposadr[self.model.joint(f"{side}_{n}").id]] for n in ARM_JOINTS])

    def bottle_pose(self) -> tuple[np.ndarray, np.ndarray]:
        return self.data.xpos[self.bottle].copy(), self.data.xmat[self.bottle].reshape(3, 3).copy()

    def bottle_tilt(self) -> float:
        return float(math.acos(max(-1.0, min(1.0, self.data.xmat[self.bottle][8]))))

    def pill(self, i: int) -> PillView:
        b = self.pill_bodies[i]
        pos = self.data.xpos[b].copy()
        axis = self.data.xmat[b].reshape(3, 3)[:, 2]
        bottle_pos, bottle_rot = self.bottle_pose()
        local = bottle_rot.T @ (pos - bottle_pos)
        inside = (math.hypot(local[0], local[1]) < BOTTLE_INNER_RADIUS_M and 0.0 < local[2] < P.BOTTLE_HEIGHT_M)
        on_table = abs(pos[0] - 0.70) < 0.45 and abs(pos[1]) < 0.60 and pos[2] > P.TABLE_HEIGHT_M - 0.002
        on_mat = (on_table and not inside and pos[2] < P.MAT_TOP_M + 0.02)
        return PillView(i, pos, math.atan2(axis[1], axis[0]), math.asin(min(1.0, abs(axis[2]))), bool(inside),
                        bool(on_mat), bool(not on_table and not inside))

    def pills(self) -> list[PillView]:
        return [self.pill(i) for i in range(self.n_pills)]

    def placed(self) -> int:
        return int(sum(bool(p.in_bottle) for p in self.pills()))

    def pill_speeds(self) -> np.ndarray:
        out = np.zeros(self.n_pills)
        vel = np.zeros(6)
        for k, b in enumerate(self.pill_bodies):
            mujoco.mj_objectVelocity(self.model, self.data, mujoco.mjtObj.mjOBJ_BODY, int(b), vel, 0)
            out[k] = np.linalg.norm(vel[3:])
        return out

    def pads_touch(self, side: str, pill: int) -> tuple[bool, bool]:
        a, b = self.pad_geoms[side]
        g = self.pill_geoms[pill]
        hit_a = hit_b = False
        for k in range(self.data.ncon):
            c = self.data.contact[k]
            pair = {c.geom1, c.geom2}
            if g in pair:
                hit_a |= a in pair
                hit_b |= b in pair
        return hit_a, hit_b

    def gripper_contact_force(self, side: str) -> float:
        """Largest normal force on the gripper housing or fingers from anything but the
        table and mat [N]: what a wrist force sensor flags during a descent."""
        m, d = self.model, self.data
        if d.ncon == 0:
            return 0.0
        gripper = self._gripper_body[side]
        b1, b2 = m.geom_bodyid[d.contact.geom1], m.geom_bodyid[d.contact.geom2]
        hits = (gripper[b1] & (b2 != 0)) | (gripper[b2] & (b1 != 0))
        peak = 0.0
        for k in np.flatnonzero(hits):
            mujoco.mj_contactForce(m, d, int(k), self._force)
            peak = max(peak, float(abs(self._force[0])))
        return peak

    def penetration(self) -> float:
        """Deepest current contact penetration [m] (positive = overlap)."""
        depth = 0.0
        for k in range(self.data.ncon):
            depth = max(depth, -float(self.data.contact[k].dist))
        return depth

    def _record_contact_forces(self) -> None:
        """Peak robot-environment (non-pill) and arm-arm contact forces, after every physics step."""
        m, d = self.model, self.data
        if d.ncon == 0:
            self._arms_touching = False
            return
        b1 = m.geom_bodyid[d.contact.geom1]
        b2 = m.geom_bodyid[d.contact.geom2]
        s1, s2 = self._arm_side[b1], self._arm_side[b2]
        arm_arm = (s1 > 0) & (s2 > 0) & (s1 != s2)
        robot_env = (self._is_robot[b1] != self._is_robot[b2]) & ~self._is_pill[b1] & ~self._is_pill[b2]
        for k in np.flatnonzero(arm_arm):
            mujoco.mj_contactForce(m, d, int(k), self._force)
            self.arm_arm_force = max(self.arm_arm_force, float(abs(self._force[0])))
            self.max_arm_arm_contact_force = max(self.max_arm_arm_contact_force, self.arm_arm_force)
        for k in np.flatnonzero(robot_env):
            mujoco.mj_contactForce(m, d, int(k), self._force)
            force = float(abs(self._force[0]))
            self.max_robot_contact_force = max(self.max_robot_contact_force, force)
            side = int(s1[k] or s2[k])  # 0 when the robot side is the torso or head
            if side:
                key = SIDES[side - 1]
                table = b1[k] == 0 or b2[k] == 0  # table, mat or floor
                forces = self.arm_static_force if table else self.arm_env_force
                forces[key] = max(forces[key], force)
        touching = bool(arm_arm.any())
        if touching and not self._arms_touching:
            self.arm_arm_contacts += 1
        self._arms_touching = touching
