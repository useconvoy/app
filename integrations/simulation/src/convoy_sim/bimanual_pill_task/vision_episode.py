"""One pills_to_bottle episode planned from the head camera by a cloud vision model (``cloud_luna_vision``).

The episode loop, physics, safety layer, bottle-zone rule and recording are ``Episode``'s. What changes:

* a free arm's request carries no observation: the planner endpoint renders the head camera (colour and
  depth) when it makes the call, and adds the robot's own state (joints, its commands, the last pick's
  gripper and force readings), never a pill pose (``vision_planner``);
* a decision targets a pointed table location, not a pill: the executive re-checks the two-arm rules on
  that point when the decision arrives, and ``PickAtPoint`` grasps there (``point_skills``);
* the summary adds the grasp outcomes: picks, grasps on empty space, grasps stopped by contact, pills
  lifted and picks that put a pill in the bottle (the last two measured on the simulator after the fact).
"""

from __future__ import annotations

import numpy as np

from .configs import EpisodeSpec
from .episode import Episode
from .head_camera import HeadCamera
from .point_skills import PickAtPoint, result_words
from .robot import SIDES
from .skills import rest_position
from .vision_planner import (
    GIVE_UP_RADIUS_M,
    SKILL_ID,
    ArmState,
    FailedSpot,
    Target,
    VisionPlannerEndpoint,
    VisionRequest,
)

# Pick outcomes by what the gripper did.
NOT_CLOSED = ("unreachable", "no_clear_grasp", "zone_busy", "blocked")  # never closed the fingers on the point
# Outcomes that count against the spot (the executive's own record; see vision_planner.GIVE_UP_AFTER). A pick that
# waited out the bottle zone or was stopped for safety says nothing about the spot.
FAILED_AT_SPOT = ("blocked", "empty_grasp", "dropped", "unreachable", "no_clear_grasp")
REST_TOLERANCE_M = 0.02  # an arm this close to its rest position is at rest (Episode._park's rule)


class VisionEpisode(Episode):
    def __init__(self, spec: EpisodeSpec, recorder=None, planner=None, planner_log=None, image_dir=None):
        """`planner` is the cloud client (``openai_responses.OpenAIResponsesClient``); there is no stand-in.
        `image_dir` keeps every image sent to the model (local evidence, not uploaded)."""
        self.image_dir = image_dir
        self.camera: HeadCamera | None = None
        self.arm_targets: dict[str, Target | None] = {side: None for side in SIDES}
        self.last_results: dict[str, str | None] = {side: None for side in SIDES}
        self.picks: list[dict] = []
        self.failed_spots: list[FailedSpot] = []
        super().__init__(spec, recorder, planner, planner_log)

    def _make_skill_planner(self, planner, planner_log, network, rng):
        cfg, sl = self.spec.config, self.spec.slice
        if planner is None:
            raise ValueError(f"{cfg.id} asks a cloud vision model for every decision: pass its client (there is no "
                             "stand-in for this configuration)")
        self.camera = HeadCamera(self.world)
        return VisionPlannerEndpoint(cfg.skill_planner, planner, self.vision_request, sl.pills, network=network,
                                     on_record=lambda record: self.trace.planner_calls.append(record),
                                     label=f"{sl.id}/{self.spec.seed}", log=planner_log, image_dir=self.image_dir)

    # --- the request: pixels and the robot's own state ------------------------------------------------------
    def vision_request(self, side: str) -> VisionRequest:
        frame = self.camera.capture(round(self.world.time, 3))
        arms = {}
        for name, slot in self.arms.items():
            target, busy = None, None
            if slot.skill is not None and not slot.skill.done:
                carrying = getattr(slot.skill, "held", False)  # once it holds a pill the spot it left is free
                target, busy = (None, "carrying") if carrying else (self.arm_targets[name], "picking")
            elif slot.held is not None:  # a decision waiting for the bottle zone
                target, busy = slot.held[2], "picking"
            controller = slot.controller
            arms[name] = ArmState(
                shoulder_xy=tuple(float(v) for v in controller.kin.shoulder_pos[:2]),
                links_xy=tuple(tuple(float(c) for c in p) for p in controller.links_xy()),
                busy=busy, target_xy=None if target is None else tuple(target.point[:2]),
                target_px=None if target is None else tuple(target.pixel), last_result=self.last_results[name])
        return VisionRequest(side, frame, arms, failed_spots=tuple(self.failed_spots))

    def request(self, side: str, t: float) -> None:
        """An arm that is returning to rest asks once it is there, so its next frame shows the table it left."""
        slot = self.arms[side]
        if slot.parking is not None:
            return
        slot.call = self.skill_planner.submit(side, {"arm": side}, t)

    def _at_rest(self, side: str) -> bool:
        slot = self.arms[side]
        return slot.parking is None and float(np.linalg.norm(slot.controller.pos - rest_position(side))) <= REST_TOLERANCE_M

    def _on_skill_call(self, call, t: float) -> None:
        """A wait or done said while the arm stands in the camera's view (after a pick it retreats only out of the
        bottle zone, beside it) may come from pills its own gripper hides: the arm parks at rest, outside the
        window, and asks again from there. A wait or done said from rest counts."""
        kind = (call.decision or {}).get("kind")
        if call.status == "ok" and kind in ("wait", "done") and not self._at_rest(call.arm):
            slot = self.arms[call.arm]
            slot.call = None
            self.skill_planner.mark_asked_again(call, "from the rest pose, out of the camera's view")
            self.events.append({"t": round(t, 3), "event": f"{kind}_asked_again_from_rest", "arm": call.arm})
            self._park(slot, t)
            slot.next_request_s = t
            return
        super()._on_skill_call(call, t)

    # --- what a decision targets: a pointed table location -------------------------------------------------
    def _decision_target(self, decision: dict) -> Target:
        p = decision["parameters"]
        return Target(tuple(p["pixel"]), tuple(p["point"]), p["finger_yaw"], bool(p.get("axis_seen")),
                      p.get("obstruction"))

    def _target_xy(self, target: Target) -> np.ndarray:
        return np.asarray(target.point[:2], dtype=float)

    def _target_label(self, target: Target) -> str:
        return f"point ({target.pixel[0]}, {target.pixel[1]})"

    def _skill_label(self, skill) -> str:
        return f"{skill.skill_id}:({skill.pixel[0]},{skill.pixel[1]}):{skill.phase}"

    def _start_skill(self, call, decision: dict, target: Target, t: float) -> None:
        slot = self.arms[call.arm]
        slot.parking = None
        self.space.targets[call.arm] = np.asarray(target.point[:2], dtype=float)
        self.arm_targets[call.arm] = target
        slot.skill = PickAtPoint(self.world, slot.controller, slot.probe, np.asarray(target.point, dtype=float),
                                 target.finger_yaw, self.space, t, tuple(target.pixel))
        self.trace.skill_events.append({"arm": call.arm, "skill": SKILL_ID, "pixel": list(target.pixel),
                                        "point": [round(c, 4) for c in target.point], "event": "start"})

    def _record_result(self, result, t: float) -> None:
        skill = self.arms[result.side].skill
        self.results.append(result)
        for slot in self.arms.values():
            if slot.waiting:
                slot.waiting, slot.next_request_s = False, t
        if result.status == "placed":
            self.completed_skills += 1
        pixel = tuple(skill.pixel) if isinstance(skill, PickAtPoint) else None
        target = self.arm_targets[result.side]
        pick = {"arm": result.side, "pixel": None if pixel is None else list(pixel), "status": result.status,
                "detail": result.detail, "started_s": round(result.started_s, 3), "finished_s": round(t, 3)}
        if isinstance(skill, PickAtPoint):
            pick.update(point=[round(float(c), 4) for c in skill.point],
                        finger_yaw=None if skill.finger_yaw is None else round(skill.finger_yaw, 4),
                        axis_seen=None if target is None else target.axis_seen,
                        obstruction=None if target is None else target.obstruction,
                        grasp_yaw=None if skill.grasp is None else round(float(skill.grasp.yaw), 4),
                        held=skill.held, lifted=[f"pill_{i:02d}" for i in skill.lifted])
        self.picks.append(pick)
        self.last_results[result.side] = result_words(result.status, pixel)
        self.arm_targets[result.side] = None
        self.history.append({"arm": result.side, "pixel": pick["pixel"], "status": result.status})
        self.trace.skill_events.append({"arm": result.side, "skill": result.skill, "pixel": pick["pixel"],
                                        "event": result.status, "detail": result.detail})
        if isinstance(skill, PickAtPoint) and result.status in FAILED_AT_SPOT:
            self._count_failure(pixel, skill.point[:2])
            self._park(self.arms[result.side], t)  # out of the camera's way before the arm asks again

    def _count_failure(self, pixel: tuple[int, int], xy) -> None:
        xy = (round(float(xy[0]), 4), round(float(xy[1]), 4))
        for i, spot in enumerate(self.failed_spots):
            if float(np.linalg.norm(np.subtract(spot.xy, xy))) < GIVE_UP_RADIUS_M:
                self.failed_spots[i] = FailedSpot(pixel, xy, spot.failures + 1)
                return
        self.failed_spots.append(FailedSpot(pixel, xy, 1))

    # --- summary --------------------------------------------------------------------------------------------
    def run(self) -> dict:
        try:
            return super().run()
        finally:
            if self.camera is not None:
                self.camera.close()

    def _planner_summary(self, t: float) -> dict:
        stats = self.skill_planner.stats(t)
        valid = stats["by_result"].get("valid", 0)
        return {"planner_calls": stats["calls"], "planner_failures": stats["calls"] - valid,
                "planner_latency_p50_ms": stats["e2e_p50_ms"], "planner_latency_p95_ms": stats["e2e_p95_ms"],
                "skill_planner_p50_ms": stats["e2e_p50_ms"], "failed_decisions": self.failed_decisions,
                "vision_planner": stats}

    def _summary(self, t: float, outcome: str, placed: int, wall_s: float) -> dict:
        summary = super()._summary(t, outcome, placed, wall_s)
        picks = self.picks
        summary["grasps"] = {
            "picks": len(picks),
            "closed": sum(p["status"] not in NOT_CLOSED for p in picks),
            "empty": sum(p["status"] == "empty_grasp" for p in picks),
            "blocked": sum(p["status"] == "blocked" for p in picks),
            "dropped": sum(p["status"] == "dropped" for p in picks),
            "not_started": sum(p["status"] in ("unreachable", "no_clear_grasp", "zone_busy") for p in picks),
            "picks_lifting_a_pill": sum(bool(p.get("lifted")) for p in picks),
            "pills_lifted": sum(len(p.get("lifted") or ()) for p in picks),
            "picks_placed": sum(p["status"] == "placed" for p in picks),
            "axis_seen": sum(bool(p.get("axis_seen")) for p in picks),
            "fingers_clear": sum(p.get("obstruction") == 0 for p in picks),
        }
        summary["picks"] = picks
        summary["given_up_spots"] = [{"pixel": list(s.pixel), "xy": list(s.xy), "failures": s.failures}
                                     for s in self.failed_spots if s.failures >= 2]
        return summary
