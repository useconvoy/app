"""Measured checks behind the physics note: jitter, tunnelling, bottle fill, grasp slip."""

from __future__ import annotations

import math

import mujoco
import numpy as np

from . import physics as P
from .robot import SIDES
from .scene import Layout, PillPose, pill_quat, sample_layout
from .skills import GraspProbe, PickAndDrop, Workspace, rest_position
from .world import World


def _set_pill(world: World, i: int, pos, quat=None) -> None:
    adr = world.model.jnt_qposadr[world.model.joint(f"pill_{i:02d}").id]
    world.data.qpos[adr:adr + 3] = pos
    if quat is not None:
        world.data.qpos[adr + 3:adr + 7] = quat
    world.data.qvel[world.model.jnt_dofadr[world.model.joint(f"pill_{i:02d}").id]:][:6] = 0


def _hold_arms(world: World) -> dict:
    from .skills import ArmController

    arms = {}
    for side in SIDES:
        arm = ArmController(world, side)
        arm.reset_to(rest_position(side), math.pi / 2, 0.03)
        arms[side] = arm
    world.set_head(world.robot.head_pan, world.robot.head_tilt)
    mujoco.mj_forward(world.model, world.data)
    return arms


def _run(world: World, arms: dict, seconds: float, track=None) -> None:
    ticks = int(round(seconds / 0.01))
    per_tick = int(round(0.01 / P.TIMESTEP_S))
    for _ in range(ticks):
        for arm in arms.values():
            arm.hold(0.01)
        world.step(per_tick)
        if track:
            track()


def settle_check(seed: int = 0) -> dict:
    world = World(sample_layout(np.random.default_rng(seed), 24, (0.49, 0.0), (0.10, 0.22)))
    arms = _hold_arms(world)
    _run(world, arms, 1.0)
    before = np.array([world.pill(i).pos for i in range(world.n_pills)])
    worst = [0.0]
    _run(world, arms, 2.0, lambda: worst.__setitem__(0, max(worst[0], world.penetration())))
    after = np.array([world.pill(i).pos for i in range(world.n_pills)])
    return {"pills": world.n_pills, "max_speed_m_s": float(world.pill_speeds().max()),
            "max_drift_over_2s_m": float(np.linalg.norm(after - before, axis=1).max()),
            "max_penetration_m": worst[0]}


def drop_check(height: float = 0.30) -> dict:
    pills = tuple(PillPose(0.42 + 0.03 * (k % 4), -0.15 + 0.1 * (k // 4), 0.3 * k, 0.0008) for k in range(12))
    world = World(Layout(pills))
    arms = _hold_arms(world)
    rng = np.random.default_rng(2)
    for i in range(world.n_pills):
        q = rng.normal(size=4)
        _set_pill(world, i, [pills[i].x, pills[i].y, P.MAT_TOP_M + height], q / np.linalg.norm(q))
    mujoco.mj_forward(world.model, world.data)
    lowest = [math.inf]
    deepest = [0.0]

    def track():
        lowest[0] = min(lowest[0], min(world.pill(i).pos[2] for i in range(world.n_pills)))
        deepest[0] = max(deepest[0], world.penetration())

    _run(world, arms, 1.5, track)
    impact = math.sqrt(2 * P.GRAVITY * height)
    final = min(world.pill(i).pos[2] for i in range(world.n_pills))
    return {"drop_height_m": height, "impact_speed_m_s": round(impact, 3),
            "travel_per_step_mm": round(impact * P.TIMESTEP_S * 1000, 2),
            "lowest_pill_centre_above_mat_m": float(lowest[0] - P.MAT_TOP_M),
            "centre_dipped_below_surface": bool(lowest[0] < P.MAT_TOP_M),
            "tunnelled": bool(final < P.MAT_TOP_M + P.PILL_RADIUS_M - 0.001 or lowest[0] < P.TABLE_HEIGHT_M - 0.03),
            "max_penetration_m": deepest[0], "max_speed_after_1_5s_m_s": float(world.pill_speeds().max())}


def bottle_fill_check(count: int = 30) -> dict:
    pills = tuple(PillPose(0.55 + 0.02 * (k % 5), -0.2 + 0.03 * (k // 5), 0.0, 0.0008) for k in range(count))
    world = World(Layout(pills))
    arms = _hold_arms(world)
    bottle, _ = world.bottle_pose()
    mouth = bottle[2] + P.BOTTLE_HEIGHT_M
    rng = np.random.default_rng(3)
    for i in range(count):
        offset = rng.uniform(-0.006, 0.006, size=2)
        yaw = rng.uniform(-math.pi, math.pi)
        quat = pill_quat(yaw)
        _set_pill(world, i, [bottle[0] + offset[0], bottle[1] + offset[1], mouth + 0.04], quat)
        mujoco.mj_forward(world.model, world.data)
        _run(world, arms, 0.25)
    _run(world, arms, 2.0)
    views = world.pills()
    local_z = [float(v.pos[2] - bottle[2]) for v in views]
    return {"pills": count, "inside": int(sum(v.in_bottle for v in views)),
            "below_bottle_floor": int(sum(z < 0.002 for z in local_z)),
            "max_speed_after_2s_m_s": float(world.pill_speeds().max()), "max_pile_height_m": max(local_z)}


def grasp_hold_check(hold_s: float = 2.0) -> dict:
    pills = (PillPose(0.48, 0.12, 0.4, 0.001),)
    world = World(Layout(pills))
    arms = _hold_arms(world)
    space = Workspace(world, arms)
    probe = GraspProbe(world, arms["left"])
    _run(world, arms, 0.3)
    skill = PickAndDrop(world, arms["left"], probe, 0, space, 0.0)
    t = 0.0
    per_tick = int(round(0.01 / P.TIMESTEP_S))
    while skill.phase not in ("transfer", "wait_zone", "done") and t < 10:
        skill.update(t, 0.01)
        arms["right"].hold(0.01)
        world.step(per_tick)
        t += 0.01
    lifted = skill.phase in ("transfer", "wait_zone")
    tcp = arms["left"].tcp
    rel0 = world.pill(0).pos - tcp
    force = float(abs(world.data.actuator_force[world.grip_actuator["left"]]))
    _run(world, {"right": arms["right"], "left": arms["left"]}, hold_s)
    rel1 = world.pill(0).pos - arms["left"].tcp
    return {"lifted": bool(lifted), "grip_force_n": round(force, 3), "opening_mm": round(world.gripper_opening("left") * 1000, 2),
            "slip_over_hold_mm": round(float(np.linalg.norm(rel1 - rel0)) * 1000, 4),
            "pill_mass_kg": 0.001, "hold_s": hold_s}


def check_physics() -> dict:
    return {"timestep_s": P.TIMESTEP_S, "solver": P.SOLVER, "tolerance": P.SOLVER_TOLERANCE,
            "noslip_iterations": P.NOSLIP_ITERATIONS,
            "contact_solref": P.CONTACT_SOLREF, "contact_solimp": P.CONTACT_SOLIMP,
            "settle": settle_check(), "drop_0_15m": drop_check(0.15), "drop_0_3m": drop_check(0.30),
            "bottle_fill_30": bottle_fill_check(30),
            "grasp_hold": grasp_hold_check()}
