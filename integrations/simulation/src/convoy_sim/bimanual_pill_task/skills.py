"""Scripted skills: IK-tracked pick of one pill and a drop into the open bottle.

This is the motor layer the planner calls. It reads simulator ground truth
(pill and bottle poses), i.e. privileged state, like the MetaWorld scripted
reference in this package; it is not a perception or learned policy.

``PickAndDrop`` phases: approach (Bezier transit to 5 cm above the pill) ->
settle -> descend -> close (force-limited squeeze, ~3 N) -> lift (slip check)
-> transfer (needs the bottle zone; waits at height otherwise) -> settle ->
release over the mouth -> retreat out of the zone.

Grasps are chosen by ``GraspProbe``: candidate finger yaws (the capsule's
cross axis +/- 23 deg), wrist leans (0, +/-0.3 rad) and finger orientations are
tested with MuJoCo's own collision detection on a private copy of the model,
with a 3 mm safety margin around the gripper, against neighbouring pills, the
bottle and the cap. If none is clear the skill reports ``no_clear_grasp``.
Transits between poses are checked the same way against the bottle and cap
(``transit``) and lifted until they are clear.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import mujoco
import numpy as np

from . import physics as P
from .control import (
    ArmKinematics,
    ServoCommand,
    grasp_rotation,
    min_jerk,
    move_duration,
    segment_point_distance,
    wrap,
)
from .robot import ENV
from .world import World

V_MAX = 0.6  # peak TCP speed on transfers [m/s]
DESCEND_S = 0.45
TRANSIT_Z = P.MAT_TOP_M + 0.17  # Bezier control height; paths peak ~4 cm above the bottle
PRE_GRASP_DZ = 0.05
GRASP_CLEARANCE = 0.002  # lowest fingertip point above the mat: the pads reach 2 mm below the pill's equator
OPEN_FOR_GRASP = 0.020
TIGHT_OPENING = 0.016
SQUEEZE_TARGET = 0.005  # commanded opening on an 8 mm pill: ~3 N per finger at 1500 N/m
RELEASE_OPEN = 0.034
DROP_DZ = 0.028  # TCP above the bottle mouth plane
TILT_MAX = 0.55  # forward wrist lean at transfer height [rad]
REST_OPENING = 0.03
ZONE_M = 0.10  # bottle zone radius in the table plane
RETREAT_M = 0.13  # distance from the bottle axis after a drop
SETTLE_TOL_M = 0.003  # TCP tracking error allowed before descending / releasing
SETTLE_TIMEOUT_S = 1.0
PROBE_MARGIN_M = 0.003
DESCENT_FORCE_N = 2.0  # contact force on the gripper that stops a descent (wrist force sensing)
PUSH_BRUSH_M = 0.003  # a push sweep may overlap a neighbouring pill by this much (it is nudged aside)
TIP_BELOW_TCP = 0.004  # fingertip end below the TCP along the approach axis
PAD_HALF_WIDTH = 0.007


def tilt_for(z: float) -> float:
    """Forward lean needed at height z so high poses stay inside the wrist range."""
    return TILT_MAX * min(1.0, max(0.0, (z - (P.MAT_TOP_M + 0.045)) / 0.08))


def grasp_height(pill_z: float, tilt: float) -> float:
    """TCP height that keeps the lowest fingertip corner GRASP_CLEARANCE above the mat."""
    lowest = TIP_BELOW_TCP * math.cos(tilt) + PAD_HALF_WIDTH * abs(math.sin(tilt))
    return max(pill_z - P.PILL_RADIUS_M + GRASP_CLEARANCE + lowest, pill_z + 0.002)


@dataclass
class BezierMotion:
    """Minimum-jerk Bezier TCP path with yaw, lean and gripper references.

    ``tilt0``/``tilt1`` of None mean "automatic" (``tilt_for(z)``); the lean
    blends from the start to the end value over the window [blend_a, blend_b]
    of the path parameter.
    """

    points: np.ndarray  # 4 x 3 control points
    yaw0: float
    yaw1: float
    t0: float
    duration: float
    grip0: float
    grip1: float
    grip_fraction: float = 0.4
    tilt0: float | None = None
    tilt1: float | None = None
    blend_a: float = 0.0
    blend_b: float = 1.0

    def sample(self, t: float) -> tuple[np.ndarray, float, float, float]:
        s = min_jerk((t - self.t0) / self.duration)
        u = 1 - s
        p = (u ** 3) * self.points[0] + 3 * u * u * s * self.points[1] + 3 * u * s * s * self.points[2] + s ** 3 * self.points[3]
        g = min(1.0, max(0.0, (t - self.t0) / (self.duration * self.grip_fraction)))
        auto = tilt_for(float(p[2]))
        a = auto if self.tilt0 is None else self.tilt0
        b = auto if self.tilt1 is None else self.tilt1
        w = min_jerk((s - self.blend_a) / max(self.blend_b - self.blend_a, 1e-6))
        return p, self.yaw0 + wrap(self.yaw1 - self.yaw0) * s, a + (b - a) * w, self.grip0 + (self.grip1 - self.grip0) * g


def bezier(start: np.ndarray, end: np.ndarray, t0: float, yaw0: float, yaw1: float, grip0: float, grip1: float,
           lift: float = TRANSIT_Z, minimum: float = 0.5, **tilt) -> BezierMotion:
    top = max(lift, start[2], end[2])
    points = np.array([start, [start[0], start[1], top], [end[0], end[1], top], end])
    probe = BezierMotion(points, 0, 0, 0.0, 1.0, 0, 0)
    samples = [probe.sample(k / 20)[0] for k in range(21)]
    length = sum(float(np.linalg.norm(samples[k + 1] - samples[k])) for k in range(20))
    return BezierMotion(points, yaw0, yaw1, t0, move_duration(length, V_MAX, minimum), grip0, grip1, **tilt)


def line(start: np.ndarray, end: np.ndarray, t0: float, duration: float, yaw: float, tilt: float, grip0: float,
         grip1: float, grip_fraction: float = 1.0, yaw1: float | None = None) -> BezierMotion:
    points = np.array([start, start + (end - start) / 3, start + 2 * (end - start) / 3, end])
    return BezierMotion(points, yaw, yaw if yaw1 is None else yaw1, t0, duration, grip0, grip1, grip_fraction,
                        tilt0=tilt, tilt1=tilt)


TRANSIT_RAISE = (0.0, 0.03, 0.06, 0.10, 0.15)  # extra control height tried until a transit is clear [m]
# Peak joint speeds a transit may ask for [rad/s]. A lean change moves the wrist
# centre (18.6 cm behind the fingertips) fast; above these the servos lag by
# centimetres and an arm can cut a corner into the bottle.
QD_MAX = np.array([2.5, 2.5, 3.0, 4.0, 4.0, 5.0])


def transit(probe: GraspProbe | None, start: np.ndarray, end: np.ndarray, t0: float, yaw0: float, yaw1: float,
            grip0: float, grip1: float, lift: float = TRANSIT_Z, minimum: float = 0.5, **tilt) -> BezierMotion:
    """A Bezier transit whose control height is raised until the gripper clears the
    bottle and cap along the whole path (the path ends are the caller's checked poses)."""
    motion = None
    for extra in TRANSIT_RAISE:
        motion = bezier(start, end, t0, yaw0, yaw1, grip0, grip1, lift=lift + extra, minimum=minimum, **tilt)
        if probe is None or probe.path_clear(motion):
            break
    if probe is not None:  # slow the move down until no joint exceeds QD_MAX
        path = np.array(probe.joint_path(motion, 24))
        rates = np.abs(np.diff(path, axis=0)).max(axis=0) * 24 / motion.duration
        motion.duration *= min(4.0, max(1.0, float(np.max(rates / QD_MAX))))
    return motion


class ArmController:
    """Turns TCP references into servo targets with closed-form IK each tick."""

    def __init__(self, world: World, side: str):
        self.world = world
        self.side = side
        self.sign = 1.0 if side == "left" else -1.0
        self.kin = ArmKinematics(world.model, side)
        self.servo = ServoCommand(world.model, side)
        self.q = np.zeros(6)
        self.pos = np.zeros(3)
        self.yaw = 0.0
        self.tilt = 0.0
        self.grip = REST_OPENING
        self.ik_failures = 0
        self.ik_jumps = 0

    @property
    def tcp(self) -> np.ndarray:
        return self.world.data.site_xpos[self.kin.site].copy()

    def tracking_error(self) -> float:
        return float(np.linalg.norm(self.tcp - self.pos))

    def links_xy(self) -> list[np.ndarray]:
        """Elbow, wrist and TCP projected on the table (for clearance checks)."""
        d, m = self.world.data, self.world.model
        return [d.xpos[m.body(f"{self.side}_forearm").id][:2].copy(), d.xpos[m.body(f"{self.side}_wrist").id][:2].copy(),
                d.site_xpos[self.kin.site][:2].copy()]

    def forward_yaw(self, pos: np.ndarray, yaw: float) -> float:
        """Pick yaw or yaw+pi so a positive lean tips away from the robot."""
        reach = pos[:2] - self.kin.shoulder_pos[:2]
        heading = math.atan2(reach[1], reach[0])
        toward = (math.sin(yaw), -math.cos(yaw))  # finger axis x up
        if toward[0] * math.cos(heading) + toward[1] * math.sin(heading) < 0:
            yaw += math.pi
        return wrap(yaw)

    def solve(self, pos: np.ndarray, yaw: float, tilt: float) -> np.ndarray | None:
        """Closed-form IK; of the two equivalent finger orientations (a parallel
        gripper is symmetric) take the one closest to the current joints, so a
        path never flips the wrist half a turn mid-motion."""
        best = None
        for candidate, lean in ((yaw, tilt), (yaw + math.pi, -tilt)):
            q = self.kin.analytic(pos, grasp_rotation(candidate, lean), self.q)
            if q is None:
                continue
            jump = float(np.max(np.abs(q - self.q)))
            if best is None or jump < best[0]:
                best = (jump, q)
        return None if best is None else best[1]

    def reset_to(self, pos: np.ndarray, yaw: float, opening: float) -> None:
        yaw = self.forward_yaw(pos, yaw)
        tilt = tilt_for(float(pos[2]))
        q = self.solve(pos, yaw, tilt)
        if q is None:
            raise ValueError(f"{self.side} rest pose is unreachable")
        self.q, self.pos, self.yaw, self.tilt, self.grip = q, pos.copy(), yaw, tilt, opening
        self.world.set_arm(self.side, q)
        self.world.set_gripper(self.side, opening)
        self.servo.hold(self.world.data, q)

    def command(self, pos: np.ndarray, yaw: float, tilt: float, grip: float, dt: float) -> bool:
        q = self.solve(pos, yaw, tilt)
        ok = q is not None
        if not ok:
            self.ik_failures += 1
            q, _ = self.kin.solve(self.q, pos, grasp_rotation(yaw, tilt), iterations=8)
        if float(np.max(np.abs(q - self.q))) > 0.25:  # > 25 rad/s at 100 Hz: a discontinuity
            self.ik_jumps += 1
        self.q, self.pos, self.yaw, self.tilt, self.grip = q, pos.copy(), yaw, tilt, grip
        self.servo.apply(self.world.data, q, dt)
        self.world.data.ctrl[self.servo.gripper] = float(np.clip(grip, 0.0, 0.08))
        return ok

    def follow(self, motion: BezierMotion, t: float, dt: float) -> None:
        pos, yaw, tilt, grip = motion.sample(t)
        self.command(pos, yaw, tilt, grip, dt)

    def hold(self, dt: float) -> None:
        self.servo.apply(self.world.data, self.q, dt)
        self.world.data.ctrl[self.servo.gripper] = self.grip

    def freeze(self) -> None:
        """Protective stop: command the joints where they are now."""
        self.q = self.world.arm_q(self.side)
        self.pos = self.tcp
        self.servo.hold(self.world.data, self.q)


@dataclass(frozen=True)
class Grasp:
    pos: np.ndarray
    yaw: float
    tilt: float
    q: np.ndarray
    clearance: float  # free space left beyond the required margin [m]; negative = blocked
    opening: float = OPEN_FOR_GRASP


class GraspProbe:
    """Collision-checks candidate grasps on a private copy of the model."""

    def __init__(self, world: World, arm: ArmController):
        self.world, self.arm = world, arm
        self.model = copy.copy(world.model)
        self.data = mujoco.MjData(self.model)
        m = self.model
        side = arm.side
        bodies = {m.body(f"{side}_{name}").id for name in ("flange", "finger_a", "finger_b")}
        self.gripper = {g for g in range(m.ngeom) if m.geom_bodyid[g] in bodies and m.geom_contype[g]}
        for g in self.gripper:
            m.geom_margin[g] = PROBE_MARGIN_M
        self.ignore = {m.geom("mat").id, m.geom("table").id, m.geom("floor").id}
        self.pills = frozenset(int(g) for g in world.pill_geoms)
        self.kin = ArmKinematics(self.model, side)
        self.finger_qadr = [m.jnt_qposadr[m.joint(f"{side}_finger_{k}").id] for k in ("a", "b")]

    def clearance(self, q: np.ndarray, opening: float, target_geom: int, skip: frozenset[int] = frozenset()) -> float:
        """Smallest gripper-obstacle distance below the probe margin (margin if none).

        Geoms in ``skip`` (and the target pill) are not obstacles for this query."""
        m, d = self.model, self.data
        d.qpos[:] = self.world.data.qpos
        d.qpos[self.kin.qadr] = q
        for adr in self.finger_qadr:
            d.qpos[adr] = opening / 2
        mujoco.mj_kinematics(m, d)
        mujoco.mj_collision(m, d)
        best = PROBE_MARGIN_M
        for k in range(d.ncon):
            c = d.contact[k]
            g1, g2 = c.geom1, c.geom2
            if g1 in self.gripper and g2 not in self.gripper:
                other = g2
            elif g2 in self.gripper and g1 not in self.gripper:
                other = g1
            else:
                continue
            if other in self.ignore or other == target_geom or other in skip or not (m.geom_contype[other] & ENV):
                continue
            best = min(best, float(c.dist))
        return best

    def ik(self, pos: np.ndarray, yaw: float, tilt: float, seed: np.ndarray | None = None) -> np.ndarray | None:
        """Closed-form IK as the controller solves it: of the two equivalent finger
        orientations (the gripper is symmetric), the one closest to `seed`."""
        seed = self.arm.q if seed is None else seed
        best = None
        for candidate, lean in ((yaw, tilt), (yaw + math.pi, -tilt)):
            q = self.kin.analytic(pos, grasp_rotation(candidate, lean), seed)
            if q is not None and (best is None or float(np.max(np.abs(q - seed))) < best[0]):
                best = (float(np.max(np.abs(q - seed))), q)
        return None if best is None else best[1]

    def joint_path(self, motion: BezierMotion, samples: int) -> list[np.ndarray]:
        """Joints the controller will command at `samples` + 1 evenly spaced times of a motion
        (closed-form IK, or its damped least-squares fallback from the previous joints)."""
        path = [self.arm.q.copy()]
        for k in range(1, samples + 1):
            pos, yaw, tilt, _ = motion.sample(motion.t0 + motion.duration * k / samples)
            q = self.ik(pos, yaw, tilt, path[-1])
            if q is None:
                q, _ = self.kin.solve(path[-1], pos, grasp_rotation(yaw, tilt), iterations=30)
            path.append(q)
        return path

    def path_clear(self, motion: BezierMotion, samples: int = 12) -> bool:
        """Whether the gripper keeps 1 mm from the bottle and cap along a transit.

        Pills are ignored: transits run above them, and a held pill moves with
        the fingers."""
        path = self.joint_path(motion, samples)
        for k in range(1, samples):
            grip = motion.sample(motion.t0 + motion.duration * k / samples)[3]
            if self.clearance(path[k], grip, -1, skip=self.pills) < 0.001:
                return False
        return True

    def choose(self, pill: int) -> Grasp | None:
        view = self.world.pill(pill)
        target_geom = int(self.world.pill_geoms[pill])
        base = view.axis_yaw + math.pi / 2
        forward = self.arm.forward_yaw(view.pos, base)
        options = []
        for offset in (0.0, 0.2, -0.2, 0.4, -0.4):
            for tilt in (0.0, 0.3, -0.3):
                for flip in (0.0, math.pi):
                    yaw = wrap(forward + offset + flip)
                    cost = abs(offset) + 0.6 * abs(tilt) + (0.05 if flip else 0.0)
                    options.append((cost, yaw, tilt))
        best = None
        # First pass: 20 mm opening and a 3 mm margin; second pass for tight
        # neighbours: 16 mm opening (4 mm each side of the capsule), 2 mm margin.
        for opening, required in ((OPEN_FOR_GRASP, PROBE_MARGIN_M), (TIGHT_OPENING, 0.002)):
            for _, yaw, tilt in sorted(options, key=lambda o: o[0]):
                pos = np.array([view.pos[0], view.pos[1], grasp_height(float(view.pos[2]), tilt)])
                q = self.arm.kin.analytic(pos, grasp_rotation(yaw, tilt), self.arm.q)
                # The pre-grasp pose above must be reachable too, or the approach
                # would end on an approximate (least-squares) pose.
                if q is None or self.ik(pos + [0, 0, PRE_GRASP_DZ], yaw, tilt) is None:
                    continue
                clear = self.clearance(q, opening, target_geom) - required
                grasp = Grasp(pos, yaw, tilt, q, clear, opening)
                if clear >= 0:
                    return grasp
                if best is None or clear > best.clearance:
                    best = grasp
        return best


class Workspace:
    """Shared-space rule for two arms: one arm at a time over the bottle zone.

    The zone is a 10 cm circle around the bottle in the table plane. An arm
    *occupies* it when the floor projection of its links (shoulder, elbow,
    wrist, fingertips), or the line from its shoulder to the pill it is
    working on, passes within 12 cm of the bottle: reaching a pill behind the
    bottle puts the forearm over it. An arm may enter (transfer a pill to the
    mouth, or pick inside the zone) only while the other arm does not occupy it.
    """

    def __init__(self, world: World, arms: dict[str, ArmController]):
        self.world, self.arms = world, arms
        self.owner: str | None = None
        self.targets: dict[str, int | None] = {side: None for side in arms}  # pill each arm works on

    def bottle_xy(self) -> np.ndarray:
        return self.world.bottle_pose()[0][:2]

    def in_zone(self, xy: np.ndarray, margin: float = 0.0) -> bool:
        return float(np.linalg.norm(np.asarray(xy)[:2] - self.bottle_xy())) < ZONE_M + margin

    def occupies(self, side: str, margin: float = 0.02) -> bool:
        arm = self.arms[side]
        points = [arm.kin.shoulder_pos[:2], *arm.links_xy()]
        segments = list(zip(points[:-1], points[1:], strict=True))
        if self.targets.get(side) is not None:
            segments.append((points[0], self.world.pill(self.targets[side]).pos[:2]))
        bottle = self.bottle_xy()
        return any(segment_point_distance(bottle, a, b) < ZONE_M + margin for a, b in segments)

    def try_enter(self, side: str) -> bool:
        if self.owner == side:
            return True
        if self.owner is not None or self.occupies("right" if side == "left" else "left"):
            return False
        self.owner = side
        return True

    def leave(self, side: str) -> None:
        if self.owner == side:
            self.owner = None


@dataclass
class SkillResult:
    status: str  # placed | missed | lost | grasp_failed | no_clear_grasp | pill_moved | unreachable | blocked | ...
    pill: int
    side: str
    started_s: float
    finished_s: float
    detail: str = ""
    skill: str = "pick_and_drop"


@dataclass
class PickAndDrop:
    """Pick pill `pill` with `arm` and drop it into the bottle."""

    world: World
    arm: ArmController
    probe: GraspProbe
    pill: int
    space: Workspace
    t0: float
    phase: str = "approach"
    motion: BezierMotion | None = None
    result: SkillResult | None = None
    grasp: Grasp | None = None
    pending: tuple[str, str] = ("aborted", "")
    settle_next: str = ""
    settle_until: float = 0.0
    zone_deadline: float = 0.0
    phase_log: list[tuple[str, float]] = field(default_factory=list)

    skill_id = "pick_and_drop"

    def __post_init__(self) -> None:
        view = self.world.pill(self.pill)
        if not view.on_mat or view.tilt > 0.5:
            self._finish("unreachable", self.t0, "pill is not lying flat on the table")
            return
        grasp = self.probe.choose(self.pill)
        if grasp is None:
            self._finish("unreachable", self.t0, "no grasp pose inside the arm's workspace")
            return
        if grasp.clearance < 0:
            self._finish("no_clear_grasp", self.t0, "every candidate grasp touches a neighbour, the bottle or the cap")
            return
        self.grasp = grasp
        if self.space.in_zone(view.pos, 0.03):
            self.phase, self.zone_deadline = "wait_zone_pick", self.t0 + 6.0
            return
        self._approach(self.t0)

    def _approach(self, t: float) -> None:
        g, arm = self.grasp, self.arm
        self._start("approach", transit(self.probe, arm.pos, g.pos + [0, 0, PRE_GRASP_DZ], t, arm.yaw,
                                        arm.yaw + wrap(g.yaw - arm.yaw), arm.grip, g.opening, tilt0=None, tilt1=g.tilt,
                                        blend_a=0.55, blend_b=1.0))

    def _start(self, phase: str, motion: BezierMotion) -> None:
        self.phase, self.motion = phase, motion
        self.phase_log.append((phase, round(motion.t0, 3)))

    def _finish(self, status: str, t: float, detail: str = "") -> None:
        self.space.leave(self.arm.side)
        self.phase = "done"
        self.result = SkillResult(status, self.pill, self.arm.side, self.t0, t, detail)

    @property
    def done(self) -> bool:
        return self.result is not None

    def update(self, t: float, dt: float) -> None:
        arm = self.arm
        if self.done:
            arm.hold(dt)
            return
        if self.phase == "wait_zone_pick":
            if self.space.try_enter(arm.side):
                self._approach(t)
            elif t >= self.zone_deadline:
                self._finish("blocked", t, "bottle zone stayed busy for 6 s")
                return
            else:
                arm.hold(dt)
                return
        if self.phase == "wait_zone":
            if self.space.try_enter(arm.side):
                self._begin_transfer(t)
            else:
                arm.hold(dt)
                return
        if self.phase == "settle":
            arm.hold(dt)
            if arm.tracking_error() < SETTLE_TOL_M or t >= self.settle_until:
                self._after_settle(t, arm.tracking_error())
            return
        arm.follow(self.motion, t, dt)
        if self.phase == "descend":
            force = self.world.gripper_contact_force(arm.side)
            if force > DESCENT_FORCE_N or arm.tracking_error() > 0.006:
                self._abort(t, "blocked", f"descent obstructed ({force:.1f} N on the gripper, "
                                          f"{arm.tracking_error() * 1000:.0f} mm tracking error)", rise=0.03)
                return
        if t >= self.motion.t0 + self.motion.duration:
            self._advance(t)

    def _settle(self, t: float, next_phase: str) -> None:
        self.phase, self.settle_next, self.settle_until = "settle", next_phase, t + SETTLE_TIMEOUT_S

    def stop(self, t: float, detail: str) -> None:
        """Protective stop (called after the arm was frozen): back off upward and report."""
        self._abort(t, "protective_stop", detail, rise=0.03)

    def _abort(self, t: float, status: str, detail: str, rise: float = PRE_GRASP_DZ, grip: float | None = None) -> None:
        arm = self.arm
        self.pending = (status, detail)
        self._start("abort", line(arm.pos, arm.pos + [0, 0, rise], t, 0.35, arm.yaw, arm.tilt, arm.grip,
                                  arm.grip if grip is None else grip))

    def _advance(self, t: float) -> None:
        arm, world = self.arm, self.world
        if self.phase == "approach":
            self._settle(t, "descend")
        elif self.phase == "descend":
            self._start("close", line(arm.pos, arm.pos, t, 0.4, arm.yaw, arm.tilt, self.grasp.opening, SQUEEZE_TARGET, 0.6))
        elif self.phase == "close":
            opening = world.gripper_opening(arm.side)
            if opening < 2 * P.PILL_RADIUS_M - 0.0025:
                self._abort(t, "grasp_failed", f"fingers closed to {opening * 1000:.1f} mm", grip=OPEN_FOR_GRASP)
                return
            self._start("lift", line(arm.pos, arm.pos + [0, 0, PRE_GRASP_DZ], t, 0.35, arm.yaw, arm.tilt,
                                     SQUEEZE_TARGET, SQUEEZE_TARGET))
        elif self.phase == "lift":
            view = world.pill(self.pill)
            if view.pos[2] < P.MAT_TOP_M + 0.025 or np.linalg.norm(view.pos - arm.tcp) > 0.02:
                self._abort(t, "grasp_failed", "pill slipped during lift", rise=0.0, grip=OPEN_FOR_GRASP)
                return
            if self.space.try_enter(arm.side):
                self._begin_transfer(t)
            elif self.space.occupies(arm.side):
                # Waiting here would keep the zone blocked: hold the pill out of the way.
                hold = rest_position(arm.side) + [0.0, 0.0, 0.03]
                yaw = arm.forward_yaw(hold, arm.yaw)
                self._start("clear_zone", transit(self.probe, arm.pos, hold, t, arm.yaw, arm.yaw + wrap(yaw - arm.yaw),
                                                  SQUEEZE_TARGET, SQUEEZE_TARGET, tilt0=arm.tilt, tilt1=None,
                                                  blend_a=0.0, blend_b=0.5))
            else:
                self.phase = "wait_zone"
        elif self.phase == "clear_zone":
            self.phase = "wait_zone"
        elif self.phase == "transfer":
            self._settle(t, "release")
        elif self.phase == "release":
            bottle = self.space.bottle_xy()
            away = np.array([arm.kin.shoulder_pos[0] + 0.25, arm.kin.shoulder_pos[1] + arm.sign * 0.02]) - bottle
            away = bottle + RETREAT_M * away / np.linalg.norm(away)
            target = np.array([away[0], away[1], arm.pos[2] + 0.02])
            yaw = arm.forward_yaw(target, arm.yaw)
            self._start("retreat", transit(self.probe, arm.pos, target, t, arm.yaw, arm.yaw + wrap(yaw - arm.yaw),
                                           RELEASE_OPEN, REST_OPENING, lift=arm.pos[2] + 0.02, minimum=0.35))
        elif self.phase == "retreat":
            view = world.pill(self.pill)
            status = "placed" if view.in_bottle else "lost" if view.lost else "missed"
            self._finish(status, t)
        elif self.phase == "abort":
            self._finish(self.pending[0], t, self.pending[1])

    def _after_settle(self, t: float, error: float) -> None:
        arm, world = self.arm, self.world
        if self.settle_next == "descend":
            view = world.pill(self.pill)
            moved = float(np.linalg.norm(view.pos[:2] - self.grasp.pos[:2]))
            if view.in_bottle or not view.on_mat or moved > 0.003:
                grasp = self.probe.choose(self.pill) if view.on_mat and moved < 0.02 else None
                if grasp is None or grasp.clearance < 0:
                    self._abort(t, "pill_moved", f"pill moved {moved * 1000:.0f} mm before descent", rise=0.03)
                    return
                self.grasp = grasp
            if error > 3 * SETTLE_TOL_M:
                self._abort(t, "blocked", f"tracking error {error * 1000:.0f} mm above the pill", rise=0.03)
                return
            g = self.grasp
            self._start("descend", line(arm.pos, g.pos, t, DESCEND_S, arm.yaw, g.tilt, g.opening, g.opening,
                                        yaw1=arm.yaw + wrap(g.yaw - arm.yaw)))
        elif self.settle_next == "release":
            if error > 3 * SETTLE_TOL_M:
                self.pending = ("blocked", f"tracking error {error * 1000:.0f} mm over the bottle")
            self._start("release", line(arm.pos, arm.pos, t, 0.45, arm.yaw, arm.tilt, SQUEEZE_TARGET, RELEASE_OPEN, 0.3))

    def _begin_transfer(self, t: float) -> None:
        arm = self.arm
        bottle_pos, _ = self.world.bottle_pose()
        target = np.array([bottle_pos[0], bottle_pos[1], bottle_pos[2] + P.BOTTLE_HEIGHT_M + DROP_DZ])
        heading = math.atan2(target[1] - arm.kin.shoulder_pos[1], target[0] - arm.kin.shoulder_pos[0])
        yaw = arm.forward_yaw(target, heading + math.pi / 2)
        self._start("transfer", transit(self.probe, arm.pos, target, t, arm.yaw, arm.yaw + wrap(yaw - arm.yaw),
                                        SQUEEZE_TARGET, SQUEEZE_TARGET, tilt0=arm.tilt, tilt1=None,
                                        blend_a=0.0, blend_b=0.45))


@dataclass
class PushApart:
    """Singulate a pill: slide it with the closed fingertips away from what blocks its grasp.

    Directions tried: along the capsule axis (both ways) and 0.5 rad either side,
    each with the wrist upright or leaning 0.3 rad to either side of the push
    line (next to the bottle only a lean keeps the 84 mm gripper housing off it).
    The fingertip block (18 x 16 mm) starts 3 mm behind the pill and pushes it
    22 mm (40 mm when the obstacle is the bottle, so it ends clear of the wall).
    The start pose, where the block comes straight down, must be 2 mm clear of
    everything. Along the sweep (two intermediate poses and the end) the block
    must stay 2 mm clear of the bottle and cap but may brush a neighbouring pill
    by up to 3 mm, nudging it aside: the block is twice as wide as a pill, so a
    side-by-side pair can only be separated that way. Brushing is penalised.
    """

    world: World
    arm: ArmController
    probe: GraspProbe
    pill: int
    space: Workspace
    t0: float
    phase: str = "approach"
    motion: BezierMotion | None = None
    result: SkillResult | None = None
    path: tuple[np.ndarray, np.ndarray, float, float] | None = None  # start, end, yaw, lean
    outcome: tuple[str, str] = ("pushed", "")
    zone_deadline: float = 0.0

    skill_id = "push_apart"

    def __post_init__(self) -> None:
        view = self.world.pill(self.pill)
        if not view.on_mat:
            self._finish("unreachable", self.t0, "pill is not on the table")
            return
        self.path = self._plan(view)
        if self.path is None:
            self._finish("no_clear_push", self.t0, "no collision-free push direction")
            return
        if self.space.in_zone(view.pos, 0.05) and not self.space.try_enter(self.arm.side):
            self.phase, self.zone_deadline = "wait_zone", self.t0 + 6.0
            return
        self._approach(self.t0)

    def _approach(self, t: float) -> None:
        start, _, yaw, tilt = self.path
        arm = self.arm
        self.phase, self.motion = "approach", transit(self.probe, arm.pos, start + [0, 0, PRE_GRASP_DZ], t,
                                                     arm.yaw, arm.yaw + wrap(yaw - arm.yaw), arm.grip, 0.0,
                                                     tilt0=None, tilt1=tilt, blend_a=0.55, blend_b=1.0)

    def _plan(self, view) -> tuple[np.ndarray, np.ndarray, float, float] | None:
        world = self.world
        axis = np.array([math.cos(view.axis_yaw), math.sin(view.axis_yaw)])
        bottle = self.space.bottle_xy()
        near_bottle = np.linalg.norm(view.pos[:2] - bottle) < P.BOTTLE_OUTER_RADIUS_M + 0.03
        others = [p.pos[:2] for p in world.pills() if p.index != self.pill and p.on_mat]
        nearest = bottle if near_bottle or not others else min(others, key=lambda q: np.linalg.norm(q - view.pos[:2]))
        distance = 0.04 if near_bottle else 0.022
        target_geom = int(world.pill_geoms[self.pill])
        pills = frozenset(int(g) for g in world.pill_geoms) - {target_geom}
        best = None
        for base in (axis, -axis):
            for turn in (0.0, 0.5, -0.5):
                c, s = math.cos(turn), math.sin(turn)
                d = np.array([c * base[0] - s * base[1], s * base[0] + c * base[1]])
                extent = P.PILL_HALF_LENGTH_M * abs(float(np.dot(d, axis))) + P.PILL_RADIUS_M
                gain = float(np.linalg.norm(view.pos[:2] + d * distance - nearest) - np.linalg.norm(view.pos[:2] - nearest))
                yaw = math.atan2(d[1], d[0])
                for tilt in (0.0, 0.3, -0.3):
                    start = np.array([*(view.pos[:2] - d * (extent + 0.012)), grasp_height(float(view.pos[2]), tilt)])
                    end = start + np.array([*(d * (distance + 0.003)), 0.0])
                    if self.probe.ik(start + [0, 0, PRE_GRASP_DZ], yaw, tilt) is None:
                        continue  # the approach would end on an unreachable pose
                    clear, brush = math.inf, math.inf
                    for k, pose in enumerate((start, start + (end - start) / 3, start + 2 * (end - start) / 3, end)):
                        q = self.probe.ik(pose, yaw, tilt)
                        if q is None:
                            clear = -1.0
                            break
                        if k == 0:
                            clear = min(clear, self.probe.clearance(q, 0.0, target_geom) - 0.002)
                        else:
                            clear = min(clear, self.probe.clearance(q, 0.0, target_geom, skip=pills) - 0.002)
                            brush = min(brush, self.probe.clearance(q, 0.0, target_geom))
                    if clear < 0 or brush < -PUSH_BRUSH_M:
                        continue
                    score = gain - 0.01 * abs(turn) - 0.005 * abs(tilt) - 2.0 * max(0.0, -brush)
                    if best is None or score > best[0]:
                        best = (score, start, end, yaw, tilt)
        return None if best is None else best[1:]

    def _finish(self, status: str, t: float, detail: str = "") -> None:
        self.space.leave(self.arm.side)
        self.phase = "done"
        self.result = SkillResult(status, self.pill, self.arm.side, self.t0, t, detail, self.skill_id)

    def stop(self, t: float, detail: str) -> None:
        """Protective stop (called after the arm was frozen): lift off and report."""
        arm = self.arm
        self.outcome = ("protective_stop", detail)
        self.phase, self.motion = "lift", line(arm.pos, arm.pos + [0, 0, PRE_GRASP_DZ], t, 0.35, arm.yaw, arm.tilt, 0.0,
                                               REST_OPENING)

    @property
    def done(self) -> bool:
        return self.result is not None

    def update(self, t: float, dt: float) -> None:
        arm = self.arm
        if self.done:
            arm.hold(dt)
            return
        if self.phase == "wait_zone":  # the pill is near the bottle: wait for the zone, up to 6 s
            if self.space.try_enter(arm.side):
                self._approach(t)
            elif t >= self.zone_deadline:
                self._finish("blocked", t, "bottle zone stayed busy for 6 s")
                return
            else:
                arm.hold(dt)
                return
        arm.follow(self.motion, t, dt)
        if self.phase == "descend" and self.world.gripper_contact_force(arm.side) > DESCENT_FORCE_N:
            self.outcome = ("blocked", "descent obstructed")
            self.phase, self.motion = "lift", line(arm.pos, arm.pos + [0, 0, PRE_GRASP_DZ], t, 0.35, arm.yaw, arm.tilt,
                                                   0.0, REST_OPENING)
            return
        if t < self.motion.t0 + self.motion.duration:
            return
        start, end, _, tilt = self.path
        if self.phase == "approach":
            self.phase, self.motion = "descend", line(arm.pos, start, t, DESCEND_S, arm.yaw, tilt, 0.0, 0.0)
        elif self.phase == "descend":
            duration = move_duration(float(np.linalg.norm(end - start)), 0.08, 0.4)
            self.phase, self.motion = "push", line(arm.pos, end, t, duration, arm.yaw, tilt, 0.0, 0.0)
        elif self.phase == "push":
            self.phase, self.motion = "lift", line(arm.pos, arm.pos + [0, 0, PRE_GRASP_DZ], t, 0.35, arm.yaw, tilt, 0.0,
                                                   REST_OPENING)
        elif self.phase == "lift":
            self._finish(self.outcome[0], t, self.outcome[1])


@dataclass
class MoveTo:
    """Bring an arm to a pose (e.g. back to rest when it has nothing to do)."""

    arm: ArmController
    target: np.ndarray
    t0: float
    probe: GraspProbe | None = None
    motion: BezierMotion | None = None

    def __post_init__(self) -> None:
        yaw = self.arm.forward_yaw(self.target, self.arm.yaw)
        self.motion = transit(self.probe, self.arm.pos, self.target, self.t0, self.arm.yaw,
                              self.arm.yaw + wrap(yaw - self.arm.yaw), self.arm.grip, REST_OPENING,
                              lift=max(self.arm.pos[2], self.target[2]) + 0.01, minimum=0.4, tilt0=self.arm.tilt,
                              tilt1=None, blend_a=0.0, blend_b=0.5)

    def done(self, t: float) -> bool:
        return t >= self.motion.t0 + self.motion.duration

    def update(self, t: float, dt: float) -> None:
        self.arm.follow(self.motion, t, dt)


def rest_position(side: str) -> np.ndarray:
    s = 1.0 if side == "left" else -1.0
    return np.array([0.33, s * 0.30, P.MAT_TOP_M + 0.12])
