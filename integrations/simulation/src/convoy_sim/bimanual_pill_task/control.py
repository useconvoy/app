"""Arm kinematics, Cartesian trajectories and the joint servo command.

Inverse kinematics is damped least squares on MuJoCo's own site Jacobian,
computed on a private ``MjData`` so the physics state is never touched. The
servo command adds velocity feed-forward to the position target (a lead of
``kv/kp * qdot``), which turns MuJoCo's ``kp(q*-q) - kv qdot`` position servo into
``kp(q*-q) + kv(qdot*-qdot)``, i.e. a PD tracker without steady velocity lag.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

from .robot import ARM_JOINTS

DOWN = np.array([0.0, 0.0, -1.0])


def grasp_rotation(finger_yaw: float, tilt: float = 0.0) -> np.ndarray:
    """Gripper frame pointing down; fingers close along `finger_yaw`.

    Columns are the gripper's local axes in the world: x = approach, y = finger
    axis (horizontal at `finger_yaw`), z = x cross y. A positive `tilt` rotates
    the approach about the finger axis toward ``finger_axis x up``.
    """
    y = np.array([math.cos(finger_yaw), math.sin(finger_yaw), 0.0])
    toward = np.cross(y, [0.0, 0.0, 1.0])
    x = math.cos(tilt) * DOWN + math.sin(tilt) * toward
    return np.column_stack([x, y, np.cross(x, y)])


def wrap(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


def segment_point_distance(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    """Distance from point p to the segment ab."""
    v = b - a
    t = float(np.clip(np.dot(p - a, v) / max(float(np.dot(v, v)), 1e-12), 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * v)))


def rotation_error(target: np.ndarray, current: np.ndarray) -> np.ndarray:
    """World-frame rotation vector taking `current` to `target`."""
    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, (target @ current.T).reshape(9))
    vel = np.zeros(3)
    mujoco.mju_quat2Vel(vel, quat, 1.0)
    return vel


class ArmKinematics:
    """Forward/inverse kinematics for one arm's six joints and TCP site."""

    def __init__(self, model: mujoco.MjModel, side: str):
        self.model = model
        self.side = side
        self.data = mujoco.MjData(model)
        ids = [model.joint(f"{side}_{name}").id for name in ARM_JOINTS]
        self.qadr = np.array([model.jnt_qposadr[i] for i in ids])
        self.dadr = np.array([model.jnt_dofadr[i] for i in ids])
        self.lower = model.jnt_range[ids, 0].copy()
        self.upper = model.jnt_range[ids, 1].copy()
        self.site = model.site(f"{side}_tcp").id
        self.shoulder = model.body(f"{side}_shoulder").id
        self._jacp = np.zeros((3, model.nv))
        self._jacr = np.zeros((3, model.nv))
        # Geometry for the closed-form solution, read from the compiled model.
        body = model.body
        self.sign = 1.0 if model.jnt_axis[ids[0], 2] > 0 else -1.0
        self.shoulder_pos = model.body_pos[self.shoulder].copy()  # robot root is at the world origin
        self.offset = abs(float(model.body_pos[body(f"{side}_upper_arm").id][1]))
        self.l1 = float(model.body_pos[body(f"{side}_forearm").id][0])
        self.l2 = float(model.body_pos[body(f"{side}_forearm_distal").id][0] + model.body_pos[body(f"{side}_wrist").id][0])
        self.wrist_to_tcp = float(model.body_pos[body(f"{side}_flange").id][0] + model.site_pos[self.site][0])

    def analytic(self, pos: np.ndarray, rot: np.ndarray, previous: np.ndarray | None = None) -> np.ndarray | None:
        """Closed-form elbow-down solution, or None when out of reach/limits.

        Shoulder yaw places the wrist centre in the arm plane (offset from the
        yaw axis), shoulder pitch and elbow solve the planar two-link problem,
        and the wrist (roll-pitch-roll about x-y-x) takes the remaining rotation.
        """
        s, d = self.sign, self.offset
        wrist = np.asarray(pos) - self.wrist_to_tcp * rot[:, 0]
        rel = wrist[:2] - self.shoulder_pos[:2]
        rho = math.hypot(rel[0], rel[1])
        if rho <= d + 1e-6:
            return None
        alpha = math.atan2(rel[1], rel[0]) - math.asin(s * d / rho)
        ca, sa = math.cos(alpha), math.sin(alpha)
        pitch_joint = self.shoulder_pos[:2] + s * d * np.array([-sa, ca])
        u = float(np.dot(wrist[:2] - pitch_joint, [ca, sa]))
        depth = float(self.shoulder_pos[2] - wrist[2])
        cos3 = (u * u + depth * depth - self.l1 ** 2 - self.l2 ** 2) / (2 * self.l1 * self.l2)
        if abs(cos3) > 1:
            return None
        t3 = -math.acos(cos3)
        t2 = math.atan2(depth, u) - math.atan2(self.l2 * math.sin(t3), self.l1 + self.l2 * math.cos(t3))
        beta = t2 + t3
        cb, sb = math.cos(beta), math.sin(beta)
        r03 = np.array([[ca, -sa, 0], [sa, ca, 0], [0, 0, 1.0]]) @ np.array([[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]])
        m = r03.T @ rot
        best = None
        for branch in (1.0, -1.0):
            b = branch * math.acos(max(-1.0, min(1.0, m[0, 0])))
            if abs(math.sin(b)) < 1e-6:  # wrist singularity: put all roll on J6
                a, c = 0.0, math.atan2(m[2, 1], m[1, 1])
            else:
                sgn = 1.0 if math.sin(b) > 0 else -1.0
                a = math.atan2(sgn * m[1, 0], -sgn * m[2, 0])
                c = math.atan2(sgn * m[0, 1], sgn * m[0, 2])
            q = np.array([wrap(s * alpha), t2, t3, wrap(s * a), b, wrap(s * c)])
            if np.any(q < self.lower - 1e-9) or np.any(q > self.upper + 1e-9):
                continue
            ref = previous if previous is not None else np.zeros(6)
            cost = float(np.sum(np.abs(q[3:] - ref[3:])))
            if best is None or cost < best[0]:
                best = (cost, q)
        return None if best is None else best[1]

    def _set(self, q: np.ndarray) -> None:
        self.data.qpos[self.qadr] = q
        mujoco.mj_kinematics(self.model, self.data)
        mujoco.mj_comPos(self.model, self.data)

    def fk(self, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        self._set(q)
        return self.data.site_xpos[self.site].copy(), self.data.site_xmat[self.site].reshape(3, 3).copy()

    def shoulder_position(self) -> np.ndarray:
        mujoco.mj_kinematics(self.model, self.data)
        return self.data.xpos[self.shoulder].copy()

    def solve(self, q0: np.ndarray, pos: np.ndarray, rot: np.ndarray, iterations: int = 30,
              tolerance: float = 1e-4, rot_weight: float = 0.3) -> tuple[np.ndarray, float]:
        """Damped least squares from `q0`; returns (q, residual) with residual in m / weighted rad."""
        q = np.clip(np.asarray(q0, dtype=float).copy(), self.lower, self.upper)
        residual = math.inf
        for _ in range(iterations):
            self._set(q)
            p = self.data.site_xpos[self.site]
            r = self.data.site_xmat[self.site].reshape(3, 3)
            err = np.concatenate([pos - p, rot_weight * rotation_error(rot, r)])
            residual = float(np.linalg.norm(err))
            if residual < tolerance:
                break
            mujoco.mj_jacSite(self.model, self.data, self._jacp, self._jacr, self.site)
            jac = np.vstack([self._jacp[:, self.dadr], rot_weight * self._jacr[:, self.dadr]])
            damping = 1e-4 + 0.02 * residual
            step = jac.T @ np.linalg.solve(jac @ jac.T + damping * np.eye(6), err)
            norm = np.linalg.norm(step)
            if norm > 0.35:
                step *= 0.35 / norm
            q = np.clip(q + step, self.lower, self.upper)
        return q, residual


def min_jerk(s: float) -> float:
    s = min(max(s, 0.0), 1.0)
    return s * s * s * (10 - 15 * s + 6 * s * s)


@dataclass
class Segment:
    """Minimum-jerk straight-line TCP move with yaw interpolation."""

    start: np.ndarray
    end: np.ndarray
    yaw0: float
    yaw1: float
    t0: float
    duration: float

    def sample(self, t: float) -> tuple[np.ndarray, float, bool]:
        s = (t - self.t0) / self.duration if self.duration > 0 else 1.0
        k = min_jerk(s)
        yaw = self.yaw0 + wrap(self.yaw1 - self.yaw0) * k
        return self.start + (self.end - self.start) * k, yaw, s >= 1.0


def move_duration(distance: float, v_max: float, minimum: float = 0.25) -> float:
    """Min-jerk peak speed is 1.875 x average; size the move so the peak is v_max."""
    return max(minimum, 1.875 * distance / v_max)


class ServoCommand:
    """Joint targets with velocity feed-forward for one arm's position servos."""

    def __init__(self, model: mujoco.MjModel, side: str):
        self.ids = np.array([model.actuator(f"{side}_{name}").id for name in ARM_JOINTS])
        gains = model.actuator_gainprm[self.ids, 0]
        damping = -model.actuator_biasprm[self.ids, 2]
        self.lead = damping / gains
        self.lo = model.actuator_ctrlrange[self.ids, 0]
        self.hi = model.actuator_ctrlrange[self.ids, 1]
        self.gripper = model.actuator(f"{side}_gripper").id
        self.previous: np.ndarray | None = None

    def apply(self, data: mujoco.MjData, q_target: np.ndarray, dt: float) -> None:
        qdot = np.zeros(6) if self.previous is None else (q_target - self.previous) / dt
        self.previous = q_target.copy()
        data.ctrl[self.ids] = np.clip(q_target + self.lead * qdot, self.lo, self.hi)

    def hold(self, data: mujoco.MjData, q_target: np.ndarray) -> None:
        self.previous = q_target.copy()
        data.ctrl[self.ids] = np.clip(q_target, self.lo, self.hi)
