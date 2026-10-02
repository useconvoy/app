"""One pills_to_bottle episode: physics, both arms, planners, metrics, recording.

Time is simulated time. A planner call's latency elapses in simulation while the
requesting arm holds its pose, so planning delays and outages cost task time
exactly as they would on a robot; this is not wall-clock real-time control. The
latency is modeled for the stand-in planner and measured for a real model on a
connected device (``device_planner``), whose calls block the simulation in wall
time and then elapse their measured round trip in simulated time.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field

import mujoco
import numpy as np

from . import physics as P
from .configs import EpisodeSpec
from .control import segment_point_distance
from .device_planner import DevicePlannerEndpoint
from .planning import (
    ARM_SEPARATION_M,
    INSTRUCTION,
    PUSH_SKILL_ID,
    SKILL_ID,
    GreedyPillPlanner,
    LatencyModel,
    PlannerCall,
    PlannerEndpoint,
)
from .policies import LearnedSkill, make_policy
from .robot import SIDES
from .scene import LIGHTING, sample_layout
from .skills import (
    REST_OPENING,
    V_MAX,
    ZONE_M,
    ArmController,
    GraspProbe,
    MoveTo,
    PickAndDrop,
    PushApart,
    SkillResult,
    Workspace,
    rest_position,
)
from .world import World

CONTROL_DT = 0.01  # skill + IK update period (100 Hz); physics steps every physics.TIMESTEP_S
RECORD_DT = 0.2  # one replay step (5 Hz) unless the recorder sets `record_dt`
SETTLE_S = 0.6  # pills settle before the episode clock starts
STALL_PAGE_S = 8.0  # no progress for this long while work remains -> page an operator
ZONE_HOLD_S = 6.0  # a device decision waits at most this long for the bottle zone (as PickAndDrop does)
SUCCESS_SETTLE_S = 0.5
PROTECTIVE_STOP_N = 10.0  # contact with the bottle, cap or other arm that stops an arm (power and force limiting)
PROTECTIVE_STOP_TABLE_N = 60.0  # fingertips bumping the table/mat stop the arm only above this


def stable_hash(*parts: object) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "little")


@dataclass
class ArmSlot:
    controller: ArmController
    probe: GraspProbe | None = None
    skill: PickAndDrop | PushApart | LearnedSkill | None = None
    parking: MoveTo | None = None  # returning to rest while it has nothing to do
    call: PlannerCall | None = None
    next_request_s: float = 0.0
    failures_in_row: int = 0
    finished: bool = False  # planner said there is nothing left for this arm
    waiting: bool = False  # planner said wait; re-ask after the other arm's next result
    stop_until: float = 0.0  # no second protective stop before this time (the arm is backing off)
    held: tuple | None = None  # a device decision waiting for the bottle zone: (call, decision, pill, since, until)


@dataclass
class StepTrace:
    """What happened during one replay step (for the recorder)."""

    planner_calls: list[dict] = field(default_factory=list)
    skill_events: list[dict] = field(default_factory=list)
    policy_ms: float = 0.0


class Episode:
    def __init__(self, spec: EpisodeSpec, recorder=None, planner=None, planner_log=None):
        """`planner` is the device transport (``device_planner.ChatTransport``) a configuration whose skill
        planner runs on a connected device needs; without one such an episode refuses to start (there is
        no stand-in for it)."""
        self.spec = spec
        self.recorder = recorder
        cfg, sl = spec.config, spec.slice
        layout_rng = np.random.default_rng(stable_hash("layout", sl.id, spec.seed))
        layout = sample_layout(layout_rng, sl.pills, sl.center, sl.half, sl.near_bottle, LIGHTING[sl.lighting])
        self.world = World(layout)
        self.arms = {side: ArmSlot(ArmController(self.world, side)) for side in SIDES}
        for side, slot in self.arms.items():
            slot.controller.reset_to(rest_position(side), math.pi / 2, REST_OPENING)
            slot.probe = GraspProbe(self.world, slot.controller)
        self.world.set_head(self.world.robot.head_pan, self.world.robot.head_tilt)
        mujoco.mj_forward(self.world.model, self.world.data)
        self.space = Workspace(self.world, {side: slot.controller for side, slot in self.arms.items()})
        rng = np.random.default_rng(stable_hash("latency", cfg.id, sl.id, spec.seed))
        network = sl.network()
        self.device = cfg.skill_planner.source == "device"
        # Every decision is a real, measured model call (the device planner, the cloud vision planner).
        self.real_calls = cfg.skill_planner.source in ("device", "vision")
        self.skill_planner = self._make_skill_planner(planner, planner_log, network, rng)
        self.policy_rng = np.random.default_rng(stable_hash("policy", cfg.id, sl.id, spec.seed))
        self.task_planner = (PlannerEndpoint(cfg.task_planner, network, rng, TaskPlanStandIn(), "task")
                             if cfg.task_planner else None)
        self.attempts: dict[int, int] = {}
        self.pushes: dict[int, int] = {}
        self.last_status: dict[int, str] = {}
        self.history: list[dict] = []
        self.results: list[SkillResult] = []
        self.events: list[dict] = []
        self.gate_open = self.task_planner is None
        self.completed_skills = 0
        self.declined: str | None = None
        self.stopped: str | None = None  # the device planner stopped (offline, call budget...): see FailurePolicy
        self.failed_decisions = 0
        self.idle_since: float | None = 0.0
        self.pages = 0
        self.stall_s = 0.0
        self.trace = StepTrace()
        self.max_bottle_tilt = 0.0
        self.placed_at: float | None = None
        self.rejected_decisions = 0  # stale decisions caught by the separation re-check
        self.protective_stops = 0
        self.last_placed = 0

    def _make_skill_planner(self, planner, planner_log, network, rng):
        cfg, sl = self.spec.config, self.spec.slice
        if self.device:
            if planner is None:
                raise ValueError(f"{cfg.id} asks the model on a connected device for every decision: pass a device "
                                 "planner connection (there is no stand-in for this configuration)")
            return DevicePlannerEndpoint(
                cfg.skill_planner, planner, self.observation, sl.pills, network=network,
                on_record=lambda record: self.trace.planner_calls.append(record),
                label=f"{sl.id}/{self.spec.seed}", log=planner_log)
        if cfg.skill_planner.source != "stand_in":
            raise ValueError(f"{cfg.id} plans from camera images: run it with make_episode (a VisionEpisode); there is "
                             "no stand-in for it")
        return PlannerEndpoint(cfg.skill_planner, network, rng, GreedyPillPlanner(), "skill")

    # --- what a decision targets: a pill (here) or a pointed table location (vision_episode) ------------
    def _decision_target(self, decision: dict):
        return int(decision["parameters"]["pill"].split("_")[1])

    def _target_xy(self, target) -> np.ndarray:
        return self.world.pill(target).pos[:2]

    def _target_label(self, target) -> str:
        return f"pill_{target:02d}"

    def _skill_label(self, skill) -> str:
        return f"{skill.skill_id}:pill_{skill.pill:02d}:{skill.phase}"

    # --- planner I/O -------------------------------------------------------
    def observation(self, arm: str) -> dict:
        world = self.world
        held = {s.skill.pill: side for side, s in self.arms.items() if s.skill and not s.skill.done}
        pills = []
        for view in world.pills():
            state = ("in_bottle" if view.in_bottle else "held" if view.index in held else
                     "lost" if view.lost else "on_mat" if view.on_mat else "elsewhere")
            pills.append({"id": f"pill_{view.index:02d}", "xy": [round(float(view.pos[0]), 4), round(float(view.pos[1]), 4)],
                          "yaw": round(view.axis_yaw, 3), "state": state, "attempts": self.attempts.get(view.index, 0),
                          "pushes": self.pushes.get(view.index, 0), "last_status": self.last_status.get(view.index)})
        arms = {}
        for side, slot in self.arms.items():
            target = slot.skill.pill if slot.skill and not slot.skill.done else None
            arms[side] = {
                "tcp": [round(float(v), 4) for v in slot.controller.pos],
                "shoulder": [round(float(v), 4) for v in slot.controller.kin.shoulder_pos],
                "links_xy": [[round(float(v), 4) for v in p] for p in slot.controller.links_xy()],
                "phase": slot.skill.phase if slot.skill else None,
                "busy": target is not None,
                "target": None if target is None else f"pill_{target:02d}",
                "target_xy": None if target is None else [round(float(v), 4) for v in world.pill(target).pos[:2]],
            }
        bottle_pos, _ = world.bottle_pose()
        return {"instruction": INSTRUCTION, "pills": pills, "arms": arms,
                "bottle": {"xy": [round(float(bottle_pos[0]), 4), round(float(bottle_pos[1]), 4)],
                           "upright": world.bottle_tilt() < 0.3},
                "zone_owner": self.space.owner,
                "motor_policy": {"name": self.spec.config.motor.name, "available": self.spec.config.motor.available},
                "history": self.history[-6:]}

    def request(self, side: str, t: float) -> None:
        slot = self.arms[side]
        slot.call = self.skill_planner.submit(side, {"arm": side, "observation": self.observation(side)}, t)

    def _on_skill_call(self, call: PlannerCall, t: float) -> None:
        slot = self.arms[call.arm]
        slot.call = None
        if not self.real_calls:  # a real call's own records reach the trace as each call completes
            self.trace.planner_calls.append(call_record(call))
        if call.status != "ok":
            backoff = self.spec.config.retry_backoff_s
            slot.next_request_s = t + backoff[min(slot.failures_in_row, len(backoff) - 1)]
            slot.failures_in_row += 1
            self.events.append({"t": round(t, 3), "event": f"planner_{call.status}", "arm": call.arm})
            return
        slot.failures_in_row = 0
        decision = call.decision or {}
        kind = decision.get("kind")
        if kind == "skill":
            pill = self._decision_target(decision)  # a pill, or a pointed table location (vision_episode)
            # The decision was made on the observation sent with the request,
            # which can be seconds old (queued calls, cloud latency). Re-check
            # the two-arm separation rule against the current state.
            conflict = self._arm_conflict(call.arm, pill)
            if conflict == "bottle_zone_in_use" and self.real_calls:
                # A real decision costs a measured round trip of seconds, during which the other arm often
                # starts a transfer. Its only conflict being the bottle zone, it is held until the zone is
                # free (at most ZONE_HOLD_S), then checked again (device_planner.FailurePolicy).
                slot.held = (call, decision, pill, t, t + ZONE_HOLD_S)
                self.events.append({"t": round(t, 3), "event": "decision_held", "arm": call.arm,
                                    "pill": self._target_label(pill), "reason": conflict})
                return
            if conflict is not None:
                self._reject(call, pill, conflict, t)
                return
            self._start_skill(call, decision, pill, t)
        elif kind in ("wait", "failed"):
            # Ask again when the other arm finishes a skill (or after 10 s);
            # meanwhile clear the shared space by returning to rest. "failed": the
            # device planner gave no usable action within its calls (FailurePolicy).
            if kind == "failed":
                self.failed_decisions += 1
                self.events.append({"t": round(t, 3), "event": "planner_failed_decision", "arm": call.arm})
            slot.next_request_s = t + 10.0
            slot.waiting = True
            self._park(slot, t)
        elif kind == "stop":
            self.stopped = decision.get("reason", "stopped")
            self.events.append({"t": round(t, 3), "event": "planner_stopped", "reason": self.stopped})
        elif kind == "done":
            slot.finished = True
            self._park(slot, t)
        else:
            self.declined = decision.get("reason", "declined")
            self.events.append({"t": round(t, 3), "event": "planner_declined", "reason": self.declined})

    def _reject(self, call: PlannerCall, pill: int, conflict: str, t: float) -> None:
        """A delivered decision that the current state rules out: counted, and the arm asks again at once."""
        self.rejected_decisions += 1
        if self.real_calls:
            self.skill_planner.mark_stale(call, conflict)
        self.events.append({"t": round(t, 3), "event": "decision_rejected", "arm": call.arm,
                            "pill": self._target_label(pill), "reason": conflict})
        self.arms[call.arm].next_request_s = t  # ask again with a fresh observation

    def _release_held(self, side: str, t: float) -> None:
        """A held device decision starts once the bottle zone is free, or is rejected as stale."""
        slot = self.arms[side]
        call, decision, pill, since, until = slot.held
        conflict = self._arm_conflict(side, pill)
        if conflict == "bottle_zone_in_use" and t < until:
            return
        slot.held = None
        if conflict is not None:
            self._reject(call, pill, conflict if t < until else f"{conflict} (held {t - since:.1f} s)", t)
            return
        self.skill_planner.mark_held(call, round(t - since, 3))
        self._start_skill(call, decision, pill, t)

    def _start_skill(self, call: PlannerCall, decision: dict, pill: int, t: float) -> None:
        slot = self.arms[call.arm]
        motor = self.spec.config.motor
        if motor.runtime == "learned":
            skill = LearnedSkill(self.world, slot.controller, make_policy(motor.name, motor.reason),
                                 decision.get("skill_id", SKILL_ID), pill, t, motor.latency or LatencyModel(0.5, 1.0),
                                 self.policy_rng)
            if skill.done:  # e.g. no checkpoint for this embodiment
                self._record_result(skill.result, t)
                slot.next_request_s = t
                return
            self.attempts[pill] = self.attempts.get(pill, 0) + 1
            slot.parking, slot.skill = None, skill
            self.space.targets[call.arm] = pill
            self.trace.skill_events.append({"arm": call.arm, "skill": skill.skill_id, "pill": f"pill_{pill:02d}",
                                            "event": "start", "runtime": "learned"})
            return
        slot.parking = None
        self.space.targets[call.arm] = pill
        skill_id = decision.get("skill_id", SKILL_ID)
        if skill_id == PUSH_SKILL_ID:
            self.pushes[pill] = self.pushes.get(pill, 0) + 1
            slot.skill = PushApart(self.world, slot.controller, slot.probe, pill, self.space, t)
        else:
            self.attempts[pill] = self.attempts.get(pill, 0) + 1
            slot.skill = PickAndDrop(self.world, slot.controller, slot.probe, pill, self.space, t)
        self.trace.skill_events.append({"arm": call.arm, "skill": skill_id, "pill": f"pill_{pill:02d}", "event": "start"})

    def _arm_conflict(self, side: str, pill: int) -> str | None:
        """The planner's separation rules, evaluated on the current state."""
        other_side = "right" if side == "left" else "left"
        other = self.arms[other_side]
        xy = self._target_xy(pill)
        if self.space.owner == other_side:
            shoulder = self.arms[side].controller.kin.shoulder_pos[:2]
            if segment_point_distance(self.space.bottle_xy(), shoulder, xy) < ZONE_M + 0.02:
                return "bottle_zone_in_use"
        points = other.controller.links_xy()[1:]  # wrist and TCP
        if other.skill is not None and not other.skill.done:
            skill = other.skill
            points.append(self.world.pill(skill.pill).pos[:2] if skill.pill is not None else skill.target_xy)
        if min(float(np.linalg.norm(xy - p)) for p in points) >= ARM_SEPARATION_M:
            return None
        if other.skill is not None:
            return "other_arm_working_nearby"
        return "other_arm_moving_nearby" if other.parking is not None else "other_arm_idle_nearby"

    def _park(self, slot: ArmSlot, t: float) -> None:
        rest = rest_position(slot.controller.side)
        if np.linalg.norm(slot.controller.pos - rest) > 0.02:
            slot.parking = MoveTo(slot.controller, rest, t, slot.probe)

    def _record_result(self, result: SkillResult, t: float) -> None:
        self.results.append(result)
        for slot in self.arms.values():
            if slot.waiting:
                slot.waiting, slot.next_request_s = False, t
        if result.status == "placed":
            self.completed_skills += 1
        self.last_status[result.pill] = result.status
        self.history.append({"pill": f"pill_{result.pill:02d}", "arm": result.side, "status": result.status})
        self.trace.skill_events.append({"arm": result.side, "skill": result.skill, "pill": f"pill_{result.pill:02d}",
                                        "event": result.status, "detail": result.detail})
        if self.task_planner and result.status == "placed" and self.completed_skills % self.spec.config.verify_every == 0:
            self.task_planner.submit("task", {"purpose": "verify_progress", "observation": self.observation("left")}, t)

    # --- main loop -----------------------------------------------------------
    def run(self) -> dict:
        world, spec = self.world, self.spec
        wall0 = time.perf_counter()
        for _ in range(int(SETTLE_S / CONTROL_DT)):
            for slot in self.arms.values():
                slot.controller.hold(CONTROL_DT)
            world.step(int(round(CONTROL_DT / P.TIMESTEP_S)))
        t0 = world.time
        self.settle_report = {"max_pill_speed_after_settle": float(world.pill_speeds().max()),
                              "max_penetration_after_settle_m": world.penetration()}
        if self.task_planner:
            self.task_planner.submit("task", {"purpose": "decompose_task", "observation": self.observation("left")}, 0.0)
        recorder = self.recorder
        # A recorder sets its own step length and may cap the number of steps: the
        # last step before the cap then runs until the episode ends.
        record_dt = getattr(recorder, "record_dt", RECORD_DT)
        max_steps = getattr(recorder, "max_steps", None)
        steps_per_record = max(1, int(round(record_dt / CONTROL_DT)))
        physics_per_tick = int(round(CONTROL_DT / P.TIMESTEP_S))
        tick = 0
        recorded = 0
        last_record_t = 0.0
        outcome = "horizon"
        obs = recorder.observe(self, 0.0) if recorder else None
        cmd_before = {side: (slot.controller.pos.copy(), slot.controller.grip) for side, slot in self.arms.items()}
        terminated = False
        while True:
            t = world.time - t0
            # 1. Planner deliveries.
            for call in self.skill_planner.poll(t):
                self._on_skill_call(call, t)
            if self.task_planner:
                for call in self.task_planner.poll(t):
                    self.trace.planner_calls.append(call_record(call))
                    if not self.gate_open:
                        self.gate_open = True
                        self.events.append({"t": round(t, 3), "event": f"task_plan_{call.status}"})
                if not self.gate_open and t >= spec.config.task_planner_deadline_s:
                    self.gate_open = True
                    self.events.append({"t": round(t, 3), "event": "task_plan_deadline_passed"})
            # 2. Held device decisions start once the bottle zone is free; idle arms ask for their next skill.
            for side, slot in self.arms.items():
                if slot.held is not None:
                    self._release_held(side, t)
            for side, slot in self.arms.items():
                if (self.gate_open and not slot.finished and slot.call is None and slot.skill is None and slot.held is None
                        and t >= slot.next_request_s and self.declined is None and self.stopped is None):
                    self.request(side, t)
            # 3. Skills and servo commands.
            before = time.perf_counter()
            for side, slot in self.arms.items():
                if slot.skill is not None:
                    slot.skill.update(t, CONTROL_DT)
                    if slot.skill.done:
                        self._record_result(slot.skill.result, t)
                        slot.skill = None
                        self.space.targets[side] = None
                        slot.next_request_s = t
                elif slot.parking is not None:
                    slot.parking.update(t, CONTROL_DT)
                    if slot.parking.done(t):
                        slot.parking = None
                        for other in self.arms.values():  # it is out of the way now
                            if other is not slot and other.waiting:
                                other.waiting, other.next_request_s = False, t
                else:
                    slot.controller.hold(CONTROL_DT)
            self.trace.policy_ms += (time.perf_counter() - before) * 1000
            # 4. Physics.
            world.step(physics_per_tick)
            if world.unstable:  # MuJoCo reset the state: nothing after this is physics
                outcome = "simulation_unstable"
                self.events.append({"t": round(t, 3), "event": "simulation_unstable"})
                break
            tick += 1
            t = world.time - t0
            self.max_bottle_tilt = max(self.max_bottle_tilt, world.bottle_tilt())
            for side, slot in self.arms.items():
                force = max(world.arm_env_force[side], world.arm_arm_force)
                table = world.arm_static_force[side]
                if ((force > PROTECTIVE_STOP_N or table > PROTECTIVE_STOP_TABLE_N) and t >= slot.stop_until
                        and (slot.skill is not None or slot.parking is not None)):
                    self._protective_stop(side, max(force, table), t)
            self._track_stall(t)
            # 5. Termination.
            if tick % 10 == 0:
                placed = self.last_placed = world.placed()
                if placed == world.n_pills and self.placed_at is None:
                    self.placed_at = t
            if self.placed_at is not None and t >= self.placed_at + SUCCESS_SETTLE_S:
                outcome, terminated = "all_pills_in_bottle", True
            elif self.declined is not None and all(s.skill is None for s in self.arms.values()):
                outcome, terminated = f"planner_declined:{self.declined}", True
            elif self.stopped is not None and all(s.skill is None for s in self.arms.values()):
                outcome, terminated = f"planner_stopped:{self.stopped}", True
            elif all(s.finished for s in self.arms.values()) and all(s.skill is None for s in self.arms.values()):
                outcome, terminated = "planner_done", True
            truncated = not terminated and t >= spec.horizon_s - 1e-9
            # 6. Recording, one replay step every record_dt.
            due = tick % steps_per_record == 0 and (max_steps is None or recorded < max_steps - 1)
            if recorder and (due or terminated or truncated):
                after = recorder.observe(self, t)
                action, extension = self._action(cmd_before, t, max(t - last_record_t, CONTROL_DT))
                cmd_before = {side: (slot.controller.pos.copy(), slot.controller.grip) for side, slot in self.arms.items()}
                recorder.step(obs, action, self.trace, extension, after, world.placed() / world.n_pills,
                              world.placed() == world.n_pills, terminated, truncated, t)
                obs = after
                recorded += 1
                last_record_t = t
                self.trace = StepTrace()
            elif not recorder and tick % steps_per_record == 0:
                self.trace = StepTrace()
            if terminated or truncated:
                break
        # After a diverging step MuJoCo has reset the state; report the last count measured before it.
        placed = self.last_placed if world.unstable else world.placed()
        summary = self._summary(t, outcome, placed, time.perf_counter() - wall0)
        if recorder:
            recorder.finish(self, summary)
        return summary

    def _protective_stop(self, side: str, force: float, t: float) -> None:
        """Freeze the arm where it is, then let its skill back off (or re-plan the parking move)."""
        slot = self.arms[side]
        slot.stop_until = t + 0.5
        self.protective_stops += 1
        self.events.append({"t": round(t, 3), "event": "protective_stop", "arm": side, "force_n": round(force, 1)})
        slot.controller.freeze()
        if slot.skill is not None and not slot.skill.done:
            slot.skill.stop(t, f"protective stop at {force:.0f} N contact")
        elif slot.parking is not None:
            slot.parking = MoveTo(slot.controller, rest_position(side), t, slot.probe)

    def _track_stall(self, t: float) -> None:
        busy = any(s.skill is not None for s in self.arms.values())
        work_left = self.placed_at is None and self.declined is None and self.stopped is None
        if busy or not work_left:
            self.idle_since = None
            return
        if self.idle_since is None:
            self.idle_since = t
        elif t - self.idle_since >= STALL_PAGE_S:
            self.pages += 1
            self.stall_s += t - self.idle_since
            self.events.append({"t": round(t, 3), "event": "operator_page", "reason": "no skill running for 8 s"})
            self.idle_since = t

    def _action(self, before: dict, t: float, dt: float = RECORD_DT) -> tuple[list[float], dict]:
        """Each arm's mean commanded TCP velocity over the step (/ V_MAX) and gripper command
        (-1 open 80 mm, +1 closed); the 4-value action is the arm that moved more."""
        per_arm = {}
        speed = {}
        for side, slot in self.arms.items():
            p0, _ = before[side]
            v = (slot.controller.pos - p0) / dt
            grip = 1.0 - 2.0 * min(1.0, max(0.0, slot.controller.grip / 0.08))
            per_arm[side] = [float(np.clip(c / V_MAX, -1, 1)) for c in v] + [float(grip)]
            speed[side] = float(np.linalg.norm(v)) + (0.0 if slot.skill is None else 1e-3)
        primary = max(SIDES, key=lambda s: speed[s])
        skills = {side: (None if s.skill is None else self._skill_label(s.skill)) for side, s in self.arms.items()}
        extension = {"arm": primary, "left": [round(v, 4) for v in per_arm["left"]],
                     "right": [round(v, 4) for v in per_arm["right"]], "skills": skills,
                     "joint_targets": {side: [round(float(v), 5) for v in s.controller.q] for side, s in self.arms.items()},
                     "network_up": self.skill_planner.network.up(t)}
        return [round(v, 5) for v in per_arm[primary]], extension

    def _summary(self, t: float, outcome: str, placed: int, wall_s: float) -> dict:
        # Time arms spent waiting for skill decisions: from the request to its answer
        # (queueing included), or to the episode end for a call still out.
        waited = sum((t if c.status == "pending" else min(c.completes_s, t)) - c.submitted_s
                     for c in self.skill_planner.calls)
        outage = sum(max(0.0, min(b, t) - a) for a, b in self.spec.slice.outages if a < t)
        statuses: dict[str, int] = {}
        for r in self.results:
            statuses[r.status] = statuses.get(r.status, 0) + 1
        n = self.world.n_pills
        lost = int(sum(p.lost for p in self.world.pills()))
        summary = {
            "config": self.spec.config.id, "slice": self.spec.slice.id, "seed": self.spec.seed,
            "outcome": outcome, "pills": n, "placed": placed, "fraction_placed": placed / n,
            "success": placed == n, "lost_off_table": lost,
            "time_to_all_placed_s": None if self.placed_at is None else round(self.placed_at, 3), "simulated_duration_s": round(t, 3),
            "wall_duration_s": round(wall_s, 2), "horizon_s": self.spec.horizon_s,
            "skills": statuses, "skill_attempts": len(self.results),
            **self._planner_summary(t),
            "planner_wait_s": round(waited, 2),
            "network_outage_s": round(outage, 2),
            "motor_policy_available": self.spec.config.motor.available,
            "operator_pages": self.pages,
            "max_robot_env_contact_force_n": round(self.world.max_robot_contact_force, 2),
            "arm_arm_contacts": self.world.arm_arm_contacts,
            "max_arm_arm_contact_force_n": round(self.world.max_arm_arm_contact_force, 2),
            "rejected_decisions": self.rejected_decisions,
            "protective_stops": self.protective_stops,
            "max_bottle_tilt_deg": round(math.degrees(self.max_bottle_tilt), 2),
            "ik_fallbacks": sum(s.controller.ik_failures for s in self.arms.values()),
            "ik_jumps": sum(s.controller.ik_jumps for s in self.arms.values()),
            "settle": {k: round(v, 6) for k, v in self.settle_report.items()},
            "events": self.events[:50],
        }
        if self.real_calls:
            summary["network_outage_s"] = None  # no modelled link outage: calls go over the real network, measured
        return summary

    def _planner_summary(self, t: float) -> dict:
        if self.device:
            # Every real call counts; latency is the end-to-end round trip of the calls the model
            # answered (on-device latency beside it in device_planner). Nothing is modeled.
            device = self.skill_planner.stats(t)
            valid = device["by_result"].get("valid", 0)
            return {"planner_calls": device["calls"], "planner_failures": device["calls"] - valid,
                    "planner_latency_p50_ms": device["e2e_p50_ms"], "planner_latency_p95_ms": device["e2e_p95_ms"],
                    "skill_planner_p50_ms": device["e2e_p50_ms"], "failed_decisions": self.failed_decisions,
                    "device_planner": device}
        calls = self.skill_planner.calls + (self.task_planner.calls if self.task_planner else [])
        finished = [c for c in calls if c.status != "pending" and c.completes_s <= t + 1e-9]
        ok = [c.latency_s for c in finished if c.status == "ok"]
        skill_ok = [c.latency_s for c in finished if c.status == "ok" and c.role == "skill"]

        def ms(values: list[float], q: float) -> float | None:
            return round(float(np.percentile(values, q)) * 1000, 1) if values else None

        return {"planner_calls": len(finished), "planner_failures": sum(c.status != "ok" for c in finished),
                "planner_latency_p50_ms": ms(ok, 50), "planner_latency_p95_ms": ms(ok, 95),
                "skill_planner_p50_ms": ms(skill_ok, 50)}


class TaskPlanStandIn:
    """Content of the cloud task-planner call: an ordered pill list per arm.

    Recorded for traceability. The edge skill router does not depend on it in
    this fully observed task, so it cannot make a configuration look better.
    """

    def decide(self, request: dict) -> tuple[dict, None]:
        pills = [p for p in request["observation"]["pills"] if p["state"] == "on_mat"]
        left = sorted((p for p in pills if p["xy"][1] >= 0), key=lambda p: (p["xy"][0], -p["xy"][1]))
        right = sorted((p for p in pills if p["xy"][1] < 0), key=lambda p: (p["xy"][0], p["xy"][1]))
        return {"kind": "plan", "purpose": request.get("purpose"),
                "left": [p["id"] for p in left], "right": [p["id"] for p in right]}, None


def call_record(call: PlannerCall) -> dict:
    return {"call_id": call.call_id, "role": call.role, "arm": call.arm, "status": call.status,
            "submitted_s": round(call.submitted_s, 3), "started_s": round(call.started_s, 3),
            "latency_ms": round(call.latency_s * 1000, 1), "decision": call.decision,
            "stand_in_compute_ms": round(call.compute_ms, 3)}


def make_episode(spec: EpisodeSpec, recorder=None, planner=None, planner_log=None, **options) -> Episode:
    """The episode a configuration runs: a ``VisionEpisode`` when its planner reads the head camera (`options`:
    its ``image_dir``), else an ``Episode``."""
    if spec.config.skill_planner.source == "vision":
        from .vision_episode import VisionEpisode

        return VisionEpisode(spec, recorder, planner, planner_log, **options)
    return Episode(spec, recorder, planner, planner_log)


def run_episode(spec: EpisodeSpec, recorder=None, planner=None, planner_log=None) -> dict:
    return make_episode(spec, recorder, planner, planner_log).run()
