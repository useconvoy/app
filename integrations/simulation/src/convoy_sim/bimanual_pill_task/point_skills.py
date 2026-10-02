"""A pick at a pointed table location: the motor skill of the cloud vision planner.

``PickAtPoint`` takes a point on the table (the back-projected pixel the planner pointed at) and an
optional finger yaw (across the long axis of the raised depth blob under that pixel, from the same
camera frame) instead of a pill id. It goes to that point and nowhere else: there is no snapping to
the nearest pill, so a point on bare mat closes the gripper on nothing.

What it decides on is what a robot measures: its own joints and gripper opening, and a wrist force
sensor during the descent. Like ``PickAndDrop`` it is scripted: closed-form IK, Bézier transits checked
against the bottle and cap (the station's fixtures), the bottle-zone rule and the same squeeze. The
grasp pose is checked against the bottle and cap only; neighbouring pills are not known to it, so a
finger that lands on one stops the descent (``blocked``).

Phases: approach (to 5 cm above the point) -> settle -> descend (fingertips 2 mm above the mat; more
than 2 N on the gripper stops it) -> close -> the gripper opening says whether it holds something
(``empty_grasp`` when the fingers close on nothing) -> lift (``dropped`` when the opening collapses) ->
transfer (needs the bottle zone) -> settle -> release over the mouth -> retreat out of the bottle zone.

Outcomes are measured on the simulator after the fact, for the metrics only: which pills were lifted
(``lifted``) and which ended in the bottle during the skill (``placed`` vs ``missed``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import physics as P
from .control import grasp_rotation, wrap
from .skills import (
    DESCEND_S,
    DESCENT_FORCE_N,
    DROP_DZ,
    OPEN_FOR_GRASP,
    PRE_GRASP_DZ,
    PROBE_MARGIN_M,
    RELEASE_OPEN,
    REST_OPENING,
    RETREAT_M,
    SETTLE_TIMEOUT_S,
    SETTLE_TOL_M,
    SQUEEZE_TARGET,
    ArmController,
    BezierMotion,
    Grasp,
    GraspProbe,
    SkillResult,
    Workspace,
    grasp_height,
    line,
    rest_position,
    transit,
)
from .world import World

EMPTY_OPENING_M = 2 * P.PILL_RADIUS_M - 0.0025  # fingers closed past this: nothing between them (as PickAndDrop)
FLAT_PILL_Z = P.MAT_TOP_M + P.PILL_RADIUS_M  # the grasp height assumes a pill lying flat on the mat


def choose_grasp(probe: GraspProbe, arm: ArmController, point, finger_yaw: float | None) -> Grasp | None:
    """The grasp at `point` (x, y): finger yaw from the image (else across the reach direction), +/-0.2 and
    +/-0.4 rad around it, wrist lean 0 or +/-0.3 rad. The first with a closed-form IK solution here and 5 cm
    above, clear of the bottle and cap by the probe margin; else the least blocked one (negative clearance),
    or None when no candidate has an IK solution."""
    x, y = float(point[0]), float(point[1])
    if finger_yaw is None:
        reach = np.array([x, y]) - arm.kin.shoulder_pos[:2]
        finger_yaw = math.atan2(reach[1], reach[0]) + math.pi / 2
    forward = arm.forward_yaw(np.array([x, y, FLAT_PILL_Z]), finger_yaw)
    options = sorted((abs(offset) + 0.6 * abs(tilt) + (0.05 if flip else 0.0), wrap(forward + offset + flip), tilt)
                     for offset in (0.0, 0.2, -0.2, 0.4, -0.4) for tilt in (0.0, 0.3, -0.3) for flip in (0.0, math.pi))
    best = None
    for _, yaw, tilt in options:
        pos = np.array([x, y, grasp_height(FLAT_PILL_Z, tilt)])
        q = arm.kin.analytic(pos, grasp_rotation(yaw, tilt), arm.q)
        if q is None or probe.ik(pos + [0, 0, PRE_GRASP_DZ], yaw, tilt) is None:
            continue
        clear = probe.clearance(q, OPEN_FOR_GRASP, -1, skip=probe.pills) - PROBE_MARGIN_M
        grasp = Grasp(pos, yaw, tilt, q, clear, OPEN_FOR_GRASP)
        if clear >= 0:
            return grasp
        if best is None or clear > best.clearance:
            best = grasp
    return best


@dataclass
class PickAtPoint:
    """Pick at table point `point` with `arm` and drop what it holds into the bottle."""

    world: World
    arm: ArmController
    probe: GraspProbe
    point: np.ndarray  # (x, y, z) in the world frame, from the pointed pixel
    finger_yaw: float | None
    space: Workspace
    t0: float
    pixel: tuple[int, int] = (0, 0)
    phase: str = "approach"
    motion: BezierMotion | None = None
    result: SkillResult | None = None
    grasp: Grasp | None = None
    pending: tuple[str, str] = ("aborted", "")
    settle_next: str = ""
    settle_until: float = 0.0
    zone_deadline: float = 0.0
    held: bool = False  # the gripper opening said something was between the fingers after the lift
    lifted: list[int] = field(default_factory=list)  # measured afterwards: pills in the gripper after the lift
    in_bottle_before: frozenset[int] = frozenset()
    phase_log: list[tuple[str, float]] = field(default_factory=list)

    skill_id = "pick_at_point"
    pill = None  # no pill id: the skill knows only the point

    def __post_init__(self) -> None:
        self.point = np.asarray(self.point, dtype=float)
        self.in_bottle_before = frozenset(p.index for p in self.world.pills() if p.in_bottle)
        grasp = choose_grasp(self.probe, self.arm, self.point, self.finger_yaw)
        if grasp is None:
            self._finish("unreachable", self.t0, "no grasp pose at the point inside the arm's workspace")
            return
        if grasp.clearance < 0:
            self._finish("no_clear_grasp", self.t0, "every grasp at the point touches the bottle or the cap")
            return
        self.grasp = grasp
        if self.space.in_zone(self.point, 0.03):
            self.phase, self.zone_deadline = "wait_zone_pick", self.t0 + 6.0
            return
        self._approach(self.t0)

    @property
    def target_xy(self) -> np.ndarray:
        """Where the arm is working: the point until it holds something, then the gripper (a held pill moves with
        it, as PickAndDrop's target pill does)."""
        return self.arm.tcp[:2] if self.held else self.point[:2]

    @property
    def done(self) -> bool:
        return self.result is not None

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
        self.result = SkillResult(status, -1, self.arm.side, self.t0, t, detail, self.skill_id)

    def update(self, t: float, dt: float) -> None:
        arm = self.arm
        if self.done:
            arm.hold(dt)
            return
        if self.phase == "wait_zone_pick":
            if self.space.try_enter(arm.side):
                self._approach(t)
            elif t >= self.zone_deadline:
                self._finish("zone_busy", t, "bottle zone stayed busy for 6 s")
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
                self._abort(t, "blocked", f"descent stopped by contact ({force:.1f} N on the gripper, "
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

    def _measure_lifted(self) -> list[int]:
        """Pills in the gripper now (simulator state, for the metrics only)."""
        tcp = self.arm.tcp
        return [p.index for p in self.world.pills()
                if p.pos[2] > P.MAT_TOP_M + 0.025 and float(np.linalg.norm(p.pos - tcp)) < 0.02]

    def _advance(self, t: float) -> None:
        arm, world = self.arm, self.world
        if self.phase == "approach":
            self._settle(t, "descend")
        elif self.phase == "descend":
            self._start("close", line(arm.pos, arm.pos, t, 0.4, arm.yaw, arm.tilt, self.grasp.opening, SQUEEZE_TARGET, 0.6))
        elif self.phase == "close":
            opening = world.gripper_opening(arm.side)
            if opening < EMPTY_OPENING_M:
                self._abort(t, "empty_grasp", f"fingers closed to {opening * 1000:.1f} mm: nothing between them",
                            grip=OPEN_FOR_GRASP)
                return
            self._start("lift", line(arm.pos, arm.pos + [0, 0, PRE_GRASP_DZ], t, 0.35, arm.yaw, arm.tilt,
                                     SQUEEZE_TARGET, SQUEEZE_TARGET))
        elif self.phase == "lift":
            self.lifted = self._measure_lifted()
            opening = world.gripper_opening(arm.side)
            if opening < EMPTY_OPENING_M:
                self._abort(t, "dropped", f"fingers closed to {opening * 1000:.1f} mm during the lift", rise=0.0,
                            grip=OPEN_FOR_GRASP)
                return
            self.held = True
            # The spot it picked at no longer says where the arm works: its own links do, as when PickAndDrop's
            # target pill moves with the gripper. (A fixed spot here deadlocked two arms waiting for the bottle zone.)
            self.space.targets[arm.side] = None
            if self.space.try_enter(arm.side):
                self._begin_transfer(t)
            elif self.space.occupies(arm.side):
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
        elif self.phase == "release":  # out of the bottle zone, as PickAndDrop retreats
            bottle = self.space.bottle_xy()
            away = np.array([arm.kin.shoulder_pos[0] + 0.25, arm.kin.shoulder_pos[1] + arm.sign * 0.02]) - bottle
            away = bottle + RETREAT_M * away / np.linalg.norm(away)
            target = np.array([away[0], away[1], arm.pos[2] + 0.02])
            yaw = arm.forward_yaw(target, arm.yaw)
            self._start("retreat", transit(self.probe, arm.pos, target, t, arm.yaw, arm.yaw + wrap(yaw - arm.yaw),
                                           RELEASE_OPEN, REST_OPENING, lift=arm.pos[2] + 0.02, minimum=0.35))
        elif self.phase == "retreat":
            pills = world.pills()
            entered = [p.index for p in pills if p.in_bottle and p.index not in self.in_bottle_before]
            lost = any(p.lost for p in pills if p.index in self.lifted)
            status = "placed" if entered else "lost" if lost else "missed"
            self._finish(status, t, f"pills now in the bottle: {len(entered)}" if entered else "")
        elif self.phase == "abort":
            self._finish(self.pending[0], t, self.pending[1])

    def _after_settle(self, t: float, error: float) -> None:
        arm = self.arm
        if self.settle_next == "descend":
            if error > 3 * SETTLE_TOL_M:
                self._abort(t, "blocked", f"tracking error {error * 1000:.0f} mm above the point", rise=0.03)
                return
            g = self.grasp
            self._start("descend", line(arm.pos, g.pos, t, DESCEND_S, arm.yaw, g.tilt, g.opening, g.opening,
                                        yaw1=arm.yaw + wrap(g.yaw - arm.yaw)))
        elif self.settle_next == "release":
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


def result_words(status: str, pixel: tuple[int, int] | None) -> str:
    """The free arm's last pick as the executive knows it (gripper, force sensing, its own motion), for the
    next request. Whether a released pill went into the bottle is not something the arm measures."""
    at = f" at ({pixel[0]}, {pixel[1]})" if pixel else ""
    return {
        "placed": "carried something to the bottle and released it over the mouth",
        "missed": "carried something to the bottle and released it over the mouth",
        "lost": "carried something to the bottle and released it over the mouth",
        "empty_grasp": f"the fingers closed on nothing{at}",
        "dropped": f"lost its grip while lifting{at}",
        "blocked": f"the fingers were stopped by contact while lowering{at} (a pill or the bottle under them)",
        "unreachable": f"could not reach{at}",
        "no_clear_grasp": f"could not grasp{at} without touching the bottle or the cap",
        "zone_busy": f"waited 6 s for the other arm to leave the bottle area before picking{at}, then gave up",
        "protective_stop": "was stopped by a contact (protective stop)",
    }.get(status, status.replace("_", " "))
