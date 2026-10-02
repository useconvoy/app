"""Skill decisions from pixels: a cloud vision model points at a pill in the head camera image.

The cloud vision configuration (``cloud_luna_vision``, "Cloud GPT-6 Luna (vision)") asks GPT-6 Luna
(OpenAI, low reasoning effort) for every skill decision through the Responses API, called from the
machine that runs the simulation (``openai_responses``). Nothing the model gets comes from the
simulator's state: each request is the head camera image (``head_camera``: the work-area window,
512 × 384, with pixel ticks on its borders) and a short instruction naming the task, the arms, the
bottle, what the other arm is doing (from the executive's own commands) and the free arm's last pick
(from its gripper and wrist force sensing). The model answers with one JSON object: a pixel to pick at
and the arm, or wait, or done. The executive back-projects the pixel with the depth from the same frame
and the camera calibration to a point on the table, and the scripted grasp goes to that point
(``point_skills.PickAtPoint``): it never moves to the nearest pill, so pointing at bare mat is a grasp
on empty space. Nothing falls back to the rule-based stand-in and nothing repairs a reply:

* ``instruction``, ``request_image`` and ``build_content`` write the request; ``parse_reply`` accepts
  exactly one JSON object of the declared shape (``REPLY_SCHEMA``, also sent as the API's strict
  structured-output format); ``check_point`` refuses a pixel the executive cannot act on (outside the
  image, not on the mat, out of the arm's reach, where two picks already failed, next to the other
  arm), from the frame's depth, the robot's own geometry and its own record of failed picks only, and
  chooses the finger yaw from the same frame's depth (``choose_finger_yaw``).
* ``VisionFailurePolicy`` decides what follows a refused reply, an HTTP or transport error, a timeout,
  an incomplete response or a model refusal.
* ``VisionPlannerEndpoint`` makes the calls, at most one per arm at a time (a hosted API serves both
  arms at once). The requesting arm holds in simulated time for each call's measured round trip; the
  other arm keeps working.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from . import physics as P
from .device_planner import REACH_MAX_M, REACH_MIN_M, REACH_OVERLAP_M, _pairs, _percentile, decision_counts
from .head_camera import RENDER_SIZE, WINDOW, Frame, encode_jpeg, finger_obstruction, grasp_axis, with_ticks
from .openai_responses import EFFORT, MODEL, PROVIDER, TRANSPORT, ResponseOutcome, SpendCapReached
from .planning import ARM_SEPARATION_M, PlannerCall, PlannerProfile

PROMPT_VERSION = "luna-vision-v3"
SKILL_ID = "pick_at_point"
ARM_CODE = {"left": "L", "right": "R"}
SIDE = {"L": "left", "R": "right"}
OTHER = {"left": "right", "right": "left"}
PICK, WAIT, DONE = "pick", "wait", "done"
ACTIONS = (PICK, WAIT, DONE)
IMAGE_DETAIL = "high"
# A pointed pixel must see the mat or something on it no taller than a pill (8 mm; the cap is 16 mm).
ON_TABLE_MAX_M, ON_TABLE_MIN_M = 0.012, -0.004
# Call results the export groups (the metric cap): no usable object in the reply, and no reply at all.
INVALID_FORMAT = ("invalid_json", "invalid_schema", "model_refusal")
CALL_FAILURES = ("api_error", "http_error", "transport_error", "timeout")
# What stops the episode (and the evaluation) instead of being re-asked.
STOPPING_HTTP = {401: "access_refused", 403: "access_refused", 404: "model_unavailable"}

REPLY_SCHEMA = {
    "type": "json_schema", "name": "pill_pick", "strict": True,
    "schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "arm": {"type": "string", "enum": ["L", "R"]},
            "action": {"type": "string", "enum": list(ACTIONS)},
            "u": {"type": ["integer", "null"]},
            "v": {"type": ["integer", "null"]},
        },
        "required": ["arm", "action", "u", "v"],
    },
}


# ---- the request ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmState:
    """What the executive knows about one arm: proprioception and its own commands, no scene state."""

    shoulder_xy: tuple[float, float]
    links_xy: tuple[tuple[float, float], ...]  # elbow, wrist, fingertips projected on the table
    busy: str | None = None  # None (idle), "picking" or "carrying"
    target_xy: tuple[float, float] | None = None  # the table point the arm is picking at
    target_px: tuple[int, int] | None = None  # the pixel that point came from
    last_result: str | None = None  # the arm's last pick in words, e.g. "closed on nothing at (212, 140)"


@dataclass(frozen=True)
class FailedSpot:
    """A table spot where the executive's picks failed (its own record: the pixel it was given, the point it went to)."""

    pixel: tuple[int, int]
    xy: tuple[float, float]
    failures: int


@dataclass(frozen=True)
class VisionRequest:
    """One decision request: a frame from the head camera and the robot's own state."""

    side: str
    frame: Frame
    arms: dict[str, ArmState]
    table_z: float = P.MAT_TOP_M  # the mat's surface height (a fixture of the station)
    failed_spots: tuple[FailedSpot, ...] = ()

    def given_up(self) -> list[FailedSpot]:
        return [spot for spot in self.failed_spots if spot.failures >= GIVE_UP_AFTER]


# The executive stops picking at a spot after this many failed picks there (within GIVE_UP_RADIUS_M), as the device
# planner gives a pill up after its picks failed.
GIVE_UP_AFTER, GIVE_UP_RADIUS_M = 2, 0.010
# The arms' halves of the image, for the instruction: where the table line y = +/-REACH_OVERLAP_M crosses the
# window's middle row (the camera's calibration, nothing seen).
FINGER_SPACE_PX = 20  # the open fingers come down up to ~17 mm either side of a pill: about 20 px here


def reach_columns(frame: Frame, table_z: float) -> dict[str, int]:
    """For each arm, the image column its reach ends at on the window's middle row (left arm: pills left of it)."""
    k = frame.intrinsics
    v = k.height // 2

    def y_at(u: float) -> float:
        ray = np.asarray(frame.rotation, dtype=float) @ np.array([(u + 0.5 - k.cx) / k.fx, -(v + 0.5 - k.cy) / k.fy, -1.0])
        return float(frame.position[1] + ray[1] * (table_z - frame.position[2]) / ray[2])

    def column(y: float) -> int:
        lo, hi = 0.0, float(k.width - 1)
        decreasing = y_at(lo) > y_at(hi)
        for _ in range(40):
            mid = (lo + hi) / 2
            if (y_at(mid) > y) == decreasing:
                lo = mid
            else:
                hi = mid
        return int(round(lo))

    return {"left": column(-REACH_OVERLAP_M), "right": column(REACH_OVERLAP_M)}


def _other_text(request: VisionRequest) -> str:
    other_side = OTHER[request.side]
    other = request.arms[other_side]
    code = ARM_CODE[other_side]
    if other.busy == "picking" and other.target_px is not None:
        u, v = other.target_px
        return f"Arm {code} is picking up the pill at ({u}, {v}): choose a pill at least 120 pixels away from it."
    if other.busy == "carrying":
        return f"Arm {code} is carrying a pill to the bottle."
    return f"Arm {code} is idle."


def instruction(request: VisionRequest, feedback: str | None = None) -> str:
    """The request text for the free arm (sent with the image as one user message)."""
    me = ARM_CODE[request.side]
    w, h = request.frame.intrinsics.width, request.frame.intrinsics.height
    columns = reach_columns(request.frame, request.table_z)
    lines = [
        "You plan for a two-arm robot that puts pills into a bottle. The image is its head camera looking down "
        "at the table: small white capsules (the pills) lie on a black mat; the white bottle, open at the top, "
        "stands near the bottom middle and its red cap lies beside it. The robot's arms may be in view.",
        f"The left arm (arm L) picks pills left of about column {columns['left']}; the right arm (arm R) picks pills "
        f"right of about column {columns['right']}.",
        f"Arm {me} is free: choose the pill it picks up next and drops into the bottle.",
        _other_text(request),
    ]
    last = request.arms[request.side].last_result
    if last:
        lines.append(f"Arm {me}'s last pick: {last}.")
    given_up = request.given_up()
    if given_up:
        spots = ", ".join(f"({s.pixel[0]}, {s.pixel[1]})" for s in given_up)
        lines.append(f"Picks failed twice at {spots}: do not pick there again.")
    lines += [
        f"Prefer a pill with free mat around it (the open fingers come down up to about {FINGER_SPACE_PX} pixels to "
        "either side of it). A pill touching another pill or standing next to the bottle is harder to pick: when no "
        f"free pill is left on arm {me}'s side, pick one of those anyway.",
        f"The image is {w} x {h} pixels: u is the column from the left edge (0 to {w - 1}), v the row from the "
        f"top edge (0 to {h - 1}). The yellow ticks on the borders are every 32 pixels, labelled every 64.",
        "Reply with one JSON object:",
        f'{{"arm": "{me}", "action": "pick", "u": U, "v": V}} with (U, V) the pixel at the centre of one pill '
        f"arm {me} can take;",
        f'{{"arm": "{me}", "action": "wait", "u": null, "v": null}} only if every pill arm {me} could take is '
        "within 120 pixels of the other arm or the pill it is picking (that arm moves away soon);",
        f'{{"arm": "{me}", "action": "done", "u": null, "v": null}} only if no pill at all is left on arm {me}\'s '
        "side of the mat: look along the image edges and around the bottle before you say done.",
    ]
    if feedback:
        lines += ["", feedback]
    return "\n".join(lines)


def request_image(frame: Frame) -> bytes:
    """The image sent: the window with pixel ticks on its borders, as JPEG."""
    return encode_jpeg(with_ticks(frame.rgb))


def build_content(text: str, image: bytes) -> list[dict]:
    """The user message's content: the instruction, then the image as a data URL."""
    url = "data:image/jpeg;base64," + base64.b64encode(image).decode()
    return [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": url, "detail": IMAGE_DETAIL}]


def refusal_feedback(reply: str | None, reason: str) -> str:
    shown = (reply or "").strip().replace("\n", " ")
    if len(shown) > 160:
        shown = shown[:157] + "..."
    return f"Your previous reply {json.dumps(shown)} was refused: {reason}. Reply again with one JSON object."


# ---- strict parsing and the executive's check -------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    arm: str  # "L" | "R"
    action: str  # pick | wait | done
    u: int | None = None
    v: int | None = None

    def as_dict(self) -> dict:
        return {"arm": self.arm, "action": self.action, "u": self.u, "v": self.v}


class ReplyError(ValueError):
    """A reply that is not one JSON object of the declared shape (``kind``: invalid_json | invalid_schema)."""

    def __init__(self, kind: str, reason: str):
        super().__init__(reason)
        self.kind, self.reason = kind, reason


def _constant(name: str):
    raise ValueError(f"{name} is not JSON")


def parse_reply(text: str | None) -> Action:
    """Exactly one JSON object (whitespace around it allowed): {"arm": "L"|"R", "action": "pick", "u": int,
    "v": int} or {"arm": "L"|"R", "action": "wait"|"done", "u": null, "v": null}. Anything else raises
    ReplyError; nothing is repaired."""
    if text is None or not text.strip():
        raise ReplyError("invalid_json", "the reply was empty")
    try:
        value = json.loads(text.strip(), object_pairs_hook=_pairs, parse_constant=_constant)
    except ValueError:
        fenced = text.strip().startswith("```")
        raise ReplyError("invalid_json", "the JSON object must not be wrapped in a ``` code block" if fenced
                         else "the reply was not one JSON object and nothing else") from None
    if not isinstance(value, dict):
        raise ReplyError("invalid_schema", "the reply must be a JSON object")
    if set(value) != {"arm", "action", "u", "v"}:
        raise ReplyError("invalid_schema", "the reply has exactly the keys action, arm, u, v")
    if value["arm"] not in ("L", "R"):
        raise ReplyError("invalid_schema", 'arm must be "L" or "R"')
    action, u, v = value["action"], value["u"], value["v"]
    if action not in ACTIONS:
        raise ReplyError("invalid_schema", f"action must be one of {', '.join(ACTIONS)}")
    if action == PICK:
        if type(u) is not int or type(v) is not int:
            raise ReplyError("invalid_schema", "a pick has integer pixel coordinates u and v")
        return Action(value["arm"], PICK, u, v)
    if u is not None or v is not None:
        raise ReplyError("invalid_schema", f"a {action} reply has u and v null")
    return Action(value["arm"], action)


def reaches(shoulder_xy, side: str, xy) -> bool:
    """Whether `side`'s arm reaches table point `xy`: its own half (+2 cm) and 18-62 cm from its shoulder
    (the device planner's rule)."""
    xy = np.asarray(xy, dtype=float)
    sign = 1.0 if side == "left" else -1.0
    distance = float(np.linalg.norm(xy - np.asarray(shoulder_xy, dtype=float)))
    return sign * xy[1] >= -REACH_OVERLAP_M and REACH_MIN_M < distance < REACH_MAX_M


@dataclass(frozen=True)
class Target:
    """An accepted pick: the pixel, the table point it sees and the finger yaw chosen from the depth around it."""

    pixel: tuple[int, int]
    point: tuple[float, float, float]
    finger_yaw: float | None  # the fingers' closing direction; None: the skill's default (across the reach)
    axis_seen: bool = False  # a pill-like blob's long axis was seen under the pixel
    obstruction: int | None = None  # raised depth pixels under the chosen fingertips (0: clear)

    def as_dict(self) -> dict:
        return {"pixel": list(self.pixel), "point": [round(c, 4) for c in self.point],
                "finger_yaw": None if self.finger_yaw is None else round(self.finger_yaw, 4),
                "axis_seen": self.axis_seen, "obstruction": self.obstruction}


# Finger yaws tried around the one across the seen axis (radians), in order of preference.
YAW_OFFSETS = (0.0, 0.2, -0.2, 0.4, -0.4, 0.6, -0.6)


def choose_finger_yaw(frame: Frame, u: int, v: int, point, side_shoulder_xy, table_z: float) -> tuple[float, bool, int]:
    """The fingers' closing direction for a pick at (u, v), from the same frame's depth only: across the long axis
    of the raised blob under the pixel (else across the reach direction), turned by up to 0.6 rad to the first yaw
    whose fingertips would come down on clear mat (else the least obstructed). (yaw, axis seen, obstruction)."""
    axis = grasp_axis(frame, u, v, table_z)
    if axis is not None:
        base = axis + math.pi / 2
    else:
        reach = np.asarray(point[:2], dtype=float) - np.asarray(side_shoulder_xy, dtype=float)
        base = math.atan2(reach[1], reach[0]) + math.pi / 2
    best = None
    for offset in YAW_OFFSETS:
        yaw = math.remainder(base + offset, 2 * math.pi)
        blocked = finger_obstruction(frame, u, v, point[:2], yaw, table_z)
        if blocked == 0:
            return yaw, axis is not None, 0
        if best is None or blocked < best[1]:
            best = (yaw, blocked)
    return best[0], axis is not None, best[1]


def check_point(action: Action, request: VisionRequest) -> tuple[str, str] | Target | None:
    """For a pick: the Target it points at, or (code, reason) when the executive cannot act on it. None for an
    acceptable wait or done; (code, reason) for an action of the other arm. Reads the frame's depth and the
    robot's own geometry, never a pill pose."""
    side = request.side
    me = ARM_CODE[side]
    if action.arm != me:
        return "wrong_arm", f"arm {action.arm} was not asked; arm {me} is the free arm"
    if action.action != PICK:
        return None
    frame, u, v = request.frame, action.u, action.v
    w, h = frame.intrinsics.width, frame.intrinsics.height
    if not frame.contains(u, v):
        return "outside_image", f"({u}, {v}) is outside the {w} x {h} image"
    point = frame.point(u, v)
    if point is None:
        return "no_depth", f"the camera measures no depth at ({u}, {v})"
    height = float(point[2]) - request.table_z
    if not ON_TABLE_MIN_M <= height <= ON_TABLE_MAX_M:
        return "not_on_mat", (f"({u}, {v}) sees something {height * 1000:.0f} mm above the table (the bottle, the cap "
                              "or an arm), not a pill on the mat")
    xy = point[:2]
    if not reaches(request.arms[side].shoulder_xy, side, xy):
        return "out_of_reach", f"({u}, {v}) is out of reach of arm {me}"
    for spot in request.given_up():
        if float(np.linalg.norm(xy - np.asarray(spot.xy, dtype=float))) < GIVE_UP_RADIUS_M:
            return "given_up", (f"({u}, {v}) is where {spot.failures} picks already failed "
                                f"(at ({spot.pixel[0]}, {spot.pixel[1]})); choose another pill")
    other_side = OTHER[side]
    other = request.arms[other_side]
    near = [np.asarray(p, dtype=float) for p in other.links_xy[1:]]  # wrist and fingertips
    if other.target_xy is not None:
        near.append(np.asarray(other.target_xy, dtype=float))
    if near and min(float(np.linalg.norm(xy - p)) for p in near) < ARM_SEPARATION_M:
        return "next_to_other_arm", f"({u}, {v}) is next to arm {ARM_CODE[other_side]} or the pill it is picking"
    yaw, seen, obstruction = choose_finger_yaw(frame, u, v, point, request.arms[side].shoulder_xy, request.table_z)
    return Target((u, v), (float(point[0]), float(point[1]), float(point[2])), yaw, seen, obstruction)


# ---- the declared failure policy ---------------------------------------------------------------------


@dataclass(frozen=True)
class VisionFailurePolicy:
    """What follows a call that brings no usable action. Fixed before an evaluation and recorded with it.

    * A decision (one action for one free arm) takes at most `calls_per_decision` calls: the first ask,
      then re-asks, each with a fresh frame. After a refused reply (invalid JSON or schema, a model
      refusal, or a pixel ``check_point`` refuses) the re-ask also quotes the reply and the reason; after
      an HTTP or transport error, an API-side failure or a timeout it is the plain request again.
    * A decision without a usable action after those calls is a *failed decision*: the arm parks and asks
      again after the other arm's next skill result or `hold_after_failed_decision_s` of simulated time.
    * An episode makes at most `budget_per_pill` x pills + `budget_extra` calls; then it ends
      (``planner_call_budget_exhausted``).
    * The spend cap (``openai_responses.SpendLedger``) refusing the next call, or the API refusing the key
      or the model (401, 403, 404), stops the episode and the evaluation (``spend_cap_reached``,
      ``access_refused``, ``model_unavailable``): recorded, never retried around.
    * A call without a response after `client_deadline_s` (the client's socket timeout) is a timeout; its
      elapsed time counts like any round trip.
    * When an accepted pick arrives, the executive re-checks the separation rules on the current state
      (the other arm kept moving during the round trip). If its only conflict is that the other arm now
      uses the bottle zone, the pick is held until the zone is free (at most 6 s) and checked again; any
      other conflict, or a hold that runs out, rejects it as stale and a new decision starts at once.
    """

    calls_per_decision: int = 3
    hold_after_failed_decision_s: float = 10.0
    budget_per_pill: int = 3
    budget_extra: int = 12
    client_deadline_s: float = 60.0

    def budget(self, pills: int) -> int:
        return self.budget_per_pill * pills + self.budget_extra


# ---- the planner endpoint ---------------------------------------------------------------------------------


class VisionTransport(Protocol):
    transport: str
    model: str
    effort: str

    def send(self, content: list[dict], *, text_format: dict | None = None, label: str = "") -> ResponseOutcome: ...


@dataclass
class _Decision:
    call: PlannerCall
    calls: int = 0
    feedback: str | None = None
    index: int = 0


@dataclass
class _InFlight:
    decision: _Decision
    record: dict
    completes_s: float
    action: Action | None
    target: Target | None = None
    stop: str | None = None


class VisionPlannerEndpoint:
    """Skill decisions from the cloud vision model, at most one call per arm at a time.

    An arm's request is made when the arm is free: the endpoint renders the frame at that moment
    (``observe(side)`` returns a ``VisionRequest``), makes the call (the simulation waits in wall-clock
    time), and delivers the decision once the call's measured round trip has passed in simulated time (at
    the next 10 ms control tick). The other arm's call can be in flight at the same time, as with any
    hosted API. `records` holds every call; `on_record(record)` sees each one when it completes in
    simulated time; `image_dir` keeps every image sent (local only).
    """

    def __init__(self, profile: PlannerProfile, transport: VisionTransport, observe, pills: int, *,
                 policy: VisionFailurePolicy | None = None, network=None, on_record=None, label: str = "", log=None,
                 image_dir: Path | None = None):
        self.profile, self.transport, self.observe = profile, transport, observe
        self.policy = policy or VisionFailurePolicy()
        self.network, self.on_record, self.label = network, on_record, label
        self.log = log or (lambda message: None)
        self.image_dir = image_dir
        self.transport_name = getattr(transport, "transport", "unknown")
        self.budget = self.policy.budget(pills)
        self.queue: list[_Decision] = []
        self.current: dict[str, _InFlight] = {}
        self.calls: list[PlannerCall] = []
        self.records: list[dict] = []
        self.stopped: str | None = None
        self.decisions = 0
        self._next_id = 1
        self._last_record: dict[int, dict] = {}

    # -- the PlannerEndpoint interface --
    def submit(self, arm: str, request: dict, t: float) -> PlannerCall:
        call = PlannerCall(self._next_id, arm, "skill", t, {})
        self._next_id += 1
        self.calls.append(call)
        self.queue.append(_Decision(call))
        return call

    def pending(self, arm: str) -> bool:
        return any(d.call.arm == arm for d in self.queue) or arm in self.current

    def poll(self, t: float) -> list[PlannerCall]:
        delivered: list[PlannerCall] = []
        for side in sorted(self.current, key=lambda s: (self.current[s].completes_s, s)):
            if self.current[side].completes_s <= t:
                delivered += self._settle(self.current.pop(side), t)
        for decision in [d for d in self.queue if d.call.arm not in self.current]:
            self.queue.remove(decision)
            self._start(decision, t)
            flight = self.current.get(decision.call.arm)
            if flight is not None and flight.completes_s <= t:  # stopped without a call
                delivered += self._settle(self.current.pop(decision.call.arm), t)
        return delivered

    # -- calls --
    def _start(self, decision: _Decision, t: float) -> None:
        side = decision.call.arm
        if decision.calls == 0:
            decision.call.started_s = t
        if self.stopped is None and len(self.records) >= self.budget:
            self.stopped = "planner_call_budget_exhausted"
        if self.stopped is not None:
            self.current[side] = _InFlight(decision, {}, t, None, stop=self.stopped)
            return
        request = self.observe(side)
        if decision.calls == 0:
            self.decisions += 1
            decision.index = self.decisions
        decision.calls += 1
        text = instruction(request, decision.feedback)
        image = request_image(request.frame)
        number = len(self.records) + 1
        label = f"{self.label} call {number} arm {ARM_CODE[side]}".strip()
        try:
            outcome = self.transport.send(build_content(text, image), text_format=REPLY_SCHEMA, label=label)
        except SpendCapReached as refused:
            self.stopped = "spend_cap_reached"
            self.log(f"{self.label} spend cap: {refused}")
            self.current[side] = _InFlight(decision, {}, t, None, stop=self.stopped)
            return
        digest = hashlib.sha256(image).hexdigest()
        if self.image_dir is not None:
            self.image_dir.mkdir(parents=True, exist_ok=True)
            (self.image_dir / f"{number:04d}.jpg").write_bytes(image)
        record = {
            "call": number, "decision": decision.index, "attempt": decision.calls, "arm": ARM_CODE[side],
            "sim_start_s": round(t, 3), "prompt_version": PROMPT_VERSION, "transport": self.transport_name,
            "provider": getattr(self.transport, "provider", PROVIDER), "requested_model": getattr(self.transport, "model", None),
            "effort": getattr(self.transport, "effort", None), "instruction": text,
            "image": {"sha256": digest, "bytes": len(image), "file": f"{number:04d}.jpg" if self.image_dir else None,
                      "detail": IMAGE_DETAIL, "size": [request.frame.intrinsics.width, request.frame.intrinsics.height]},
            **asdict(outcome),
        }
        action = target = None
        if outcome.status == "completed" and outcome.refusal and not outcome.text:
            record["result"], record["refusal_code"], record["refusal"] = "model_refusal", "model_refusal", outcome.refusal
        elif outcome.status in ("completed", "incomplete"):
            try:
                action = parse_reply(outcome.text)
                checked = check_point(action, request)
                if isinstance(checked, tuple):
                    record["result"], record["refusal_code"], record["refusal"] = "invalid_choice", *checked
                    action = None
                else:
                    record["result"], target = "valid", checked
                    if target is not None:
                        record["target"] = target.as_dict()
            except ReplyError as error:
                record["result"], record["refusal_code"], record["refusal"] = error.kind, error.kind, error.reason
        elif outcome.status == "http_error":
            record["result"] = "http_error"
            if outcome.http_status in STOPPING_HTTP:
                self.stopped = STOPPING_HTTP[outcome.http_status]
        elif outcome.status in ("timeout", "transport_error"):
            record["result"] = outcome.status
        else:  # failed, cancelled or an unknown status: the API answered without a result
            record["result"] = "api_error"
        record["action"] = None if action is None else action.as_dict()
        record["sim_end_s"] = round(t + outcome.e2e_ms / 1000.0, 3)
        self.records.append(record)
        self.log(self._line(record))
        self.current[side] = _InFlight(decision, record, t + outcome.e2e_ms / 1000.0, action, target)

    def _line(self, r: dict) -> str:
        action = r.get("action")
        if action and action["action"] == PICK:
            what = f"pick ({action['u']}, {action['v']})"
        else:
            what = action["action"] if action else f"{r['result']}" + (f" {r['refusal_code']}" if r.get("refusal_code") else "")
        tokens = f"{r.get('input_tokens') or 0}+{r.get('output_tokens') or 0} tok"
        return (f"{self.label} call {r['call']:3d} arm {r['arm']} t={r['sim_start_s']:7.2f}s  {what:34s} "
                f"e2e {r['e2e_ms'] / 1000:5.2f} s  {tokens:>13s}  ${r.get('cost_usd') or 0:.6f}")

    def _settle(self, flight: _InFlight, t: float) -> list[PlannerCall]:
        decision, record = flight.decision, flight.record
        call = decision.call
        if record:
            record["delivered_sim_s"] = round(t, 3)
            if self.on_record is not None:
                self.on_record(self.trace_record(record))
        if flight.stop is not None:
            return self._deliver(call, t, {"kind": "stop", "reason": flight.stop}, record, "stopped")
        if flight.action is not None:
            a = flight.action
            if a.action == PICK and flight.target is not None:
                value = {"kind": "skill", "skill_id": SKILL_ID, "parameters": {"arm": SIDE[a.arm], **flight.target.as_dict()}}
            else:
                value = {"kind": a.action}
            return self._deliver(call, t, value, record, "delivered")
        if self.stopped is not None:
            return self._deliver(call, t, {"kind": "stop", "reason": self.stopped}, record, "stopped")
        if decision.calls < self.policy.calls_per_decision:
            record["next"] = "re-asked"
            refused = record.get("result") in (*INVALID_FORMAT, "invalid_choice")
            reply = record.get("text") if record.get("text") is not None else record.get("refusal")
            decision.feedback = refusal_feedback(reply, record["refusal"]) if refused else None
            self.queue.insert(0, decision)
            return []
        return self._deliver(call, t, {"kind": "failed", "reason": f"no usable action after {decision.calls} calls"},
                             record, "failed_decision")

    def _deliver(self, call: PlannerCall, t: float, decision: dict, record: dict, next_step: str) -> list[PlannerCall]:
        if record:
            record["next"] = next_step
        call.decision, call.status, call.completes_s = decision, "ok", t
        call.latency_s = t - call.started_s
        if record:
            self._last_record[call.call_id] = record
        return [call]

    def mark_held(self, call: PlannerCall, held_s: float) -> None:
        """The executive held this delivered pick until the bottle zone was free, then started it."""
        record = self._last_record.get(call.call_id)
        if record:
            record["held_for_zone_s"] = held_s

    def mark_stale(self, call: PlannerCall, reason: str) -> None:
        """The executive rejected this delivered pick on the current state (see VisionFailurePolicy)."""
        record = self._last_record.get(call.call_id)
        if record:
            record["next"] = f"rejected_stale: {reason}"

    def mark_asked_again(self, call: PlannerCall, reason: str) -> None:
        """The executive did not act on this delivered wait or done and asks again (vision_episode)."""
        record = self._last_record.get(call.call_id)
        if record:
            record["next"] = f"asked_again: {reason}"

    def trace_record(self, record: dict) -> dict:
        """The per-step view of a call (recordings): what was chosen and how long it took."""
        return {"call_id": record["call"], "role": "skill", "arm": record["arm"], "transport": record["transport"],
                "model": record.get("model") or record.get("requested_model"),
                "status": "ok" if record["result"] == "valid" else record["result"],
                "latency_ms": record["e2e_ms"], "processing_ms": record.get("processing_ms"),
                "tokens_in": record.get("input_tokens"), "tokens_out": record.get("output_tokens"),
                "cost_usd": record.get("cost_usd"), "response_id": record.get("response_id"),
                "decision": record.get("action"), "target": record.get("target"),
                "submitted_s": record["sim_start_s"], "started_s": record["sim_start_s"]}

    # -- summary --
    def stats(self, t_end: float) -> dict:
        """Counts, measured latencies, tokens and cost of the episode's calls (nothing modeled)."""
        r = self.records
        by_result: dict[str, int] = {}
        for record in r:
            by_result[record["result"]] = by_result.get(record["result"], 0) + 1
        answered = [x for x in r if x.get("http_status") == 200]
        e2e = [x["e2e_ms"] for x in answered]
        processing = [x["processing_ms"] for x in answered if x.get("processing_ms") is not None]
        tokens_in = [x["input_tokens"] for x in answered if x.get("input_tokens") is not None]
        tokens_out = [x["output_tokens"] for x in answered if x.get("output_tokens") is not None]
        reasoning = [x["reasoning_tokens"] for x in answered if x.get("reasoning_tokens") is not None]
        refusals: dict[str, int] = {}
        for x in r:
            if x.get("refusal_code"):
                refusals[x["refusal_code"]] = refusals.get(x["refusal_code"], 0) + 1
        nexts = [x.get("next", "") for x in r]
        held = [x["held_for_zone_s"] for x in r if x.get("held_for_zone_s") is not None]
        delivered = [x["action"]["action"] for x in r if x.get("next") == "delivered" and x.get("action")]
        models = sorted({x["model"] for x in answered if x.get("model")})
        return {
            "calls": len(r), "decisions": self.decisions, "by_result": by_result, "refusals": refusals,
            "actions": {name: delivered.count(name) for name in ACTIONS if name in delivered},
            "failed_decisions": sum(n == "failed_decision" for n in nexts),
            "stale_rejections": sum(n.startswith("rejected_stale") for n in nexts),
            "asked_again_from_rest": sum(n.startswith("asked_again") for n in nexts),
            "held_for_zone": len(held), "held_for_zone_s": round(sum(held), 2),
            "not_delivered": sum(x.get("delivered_sim_s") is None for x in r),
            "e2e_p50_ms": _percentile(e2e, 50), "e2e_p95_ms": _percentile(e2e, 95),
            "e2e_max_ms": round(max(e2e), 1) if e2e else None,
            "processing_p50_ms": _percentile(processing, 50), "processing_p95_ms": _percentile(processing, 95),
            "tokens_in_p50": _percentile(tokens_in, 50), "tokens_out_p50": _percentile(tokens_out, 50),
            "reasoning_tokens_p50": _percentile(reasoning, 50),
            "tokens_in": int(sum(x.get("input_tokens") or 0 for x in r)),
            "cached_tokens": int(sum(x.get("cached_tokens") or 0 for x in r)),
            "tokens_out": int(sum(x.get("output_tokens") or 0 for x in r)),
            "reasoning_tokens": int(sum(x.get("reasoning_tokens") or 0 for x in r)),
            "cost_usd": round(sum(float(x.get("cost_usd") or 0.0) for x in r), 6),
            "sim_time_in_calls_s": round(sum(x["e2e_ms"] for x in r) / 1000.0, 2),
            "response_ids": [x["response_id"] for x in r if x.get("response_id")],
            "budget": self.budget, "stopped": self.stopped, "prompt_version": PROMPT_VERSION,
            "policy": asdict(self.policy), "transport": self.transport_name,
            "provider": getattr(self.transport, "provider", PROVIDER),
            "requested_model": getattr(self.transport, "model", None), "effort": getattr(self.transport, "effort", None),
            "models_reported": models, "decision_counts": decision_counts(r),
        }


def settings(transport=None) -> dict:
    """The frozen request, camera and failure settings, as an evaluation records them."""
    client = transport.settings() if transport is not None and hasattr(transport, "settings") else {
        "provider": PROVIDER, "model": MODEL, "reasoning_effort": EFFORT, "transport": TRANSPORT}
    return {
        "prompt_version": PROMPT_VERSION, "reply_schema": REPLY_SCHEMA, "policy": asdict(VisionFailurePolicy()),
        "client": client,
        "camera": {"camera": "head_camera (RGB-D)", "render": list(RENDER_SIZE), "window": list(WINDOW),
                   "sent": f"{WINDOW[2]}x{WINDOW[3]} JPEG, pixel ticks on the borders every 32 px", "detail": IMAGE_DETAIL,
                   "depth": "MuJoCo depth buffer from the same camera, metres along the optical axis"},
        "pixel_to_point": "back-projection of the pointed pixel with its depth, the window intrinsics and the camera pose",
        "grasp": ("scripted IK to the pointed point (no snapping to a pill); finger yaw across the long axis of the raised "
                  "depth blob under the pixel (none seen: across the reach direction), turned by up to 0.6 rad to the "
                  "first yaw whose fingertips come down on clear mat in the same frame's depth"),
        "yaw_offsets_rad": list(YAW_OFFSETS),
        "checks": {"on_mat_height_m": [ON_TABLE_MIN_M, ON_TABLE_MAX_M],
                   "reach": {"own_half_overlap_m": REACH_OVERLAP_M, "shoulder_distance_m": [REACH_MIN_M, REACH_MAX_M]},
                   "separation_m": ARM_SEPARATION_M,
                   "given_up": {"after_failed_picks": GIVE_UP_AFTER, "radius_m": GIVE_UP_RADIUS_M}},
        "executive": ("a failed pick parks the arm at rest (outside the camera window) before it asks again; a wait or "
                      "done said away from rest is asked again from rest, and counts only from there"),
        "sim_time": "each call's measured round trip from this machine to the API (request sent to response received)",
        "concurrency": "one call per arm at a time",
    }
