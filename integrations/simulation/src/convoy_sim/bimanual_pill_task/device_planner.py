"""Skill decisions from a language model on the robot's edge computer, through Convoy's device chat API.

The "Edge Qwen" configuration asks Qwen2.5-1.5B-Instruct (Q4_K_M, llama.cpp CUDA) on a Jetson Orin
Nano for every skill decision. The model reads text only, so each request carries a compact text
description of the simulator state: pill ids and positions, which pills are already in the bottle,
the bottle position, both arms' state and which arm can reach which pill. The model answers with one
JSON action and the scripted skills execute it on the simulator state. Nothing here falls back to the
rule-based stand-in or repairs a reply:

* ``build_prompt`` writes the request; ``parse_reply`` accepts exactly one JSON object of the declared
  shape (no code fence, no text around it); ``check_choice`` refuses an action that the scene in the
  same request rules out (a pill already in the bottle, out of the arm's reach, next to the other arm).
* ``FailurePolicy`` decides what follows a refused reply, a device error, an HTTP error or a timeout.
* ``DevicePlannerEndpoint`` makes the calls one at a time. The requesting arm holds in simulated time
  for the measured end-to-end round trip of each call; the other arm keeps working.
* ``PortalChatClient`` is the transport: the website's device chat routes (``/api/portal/chat``), relayed
  by the control plane to the agent on the device. It paces requests to the routes' rate limit and
  never logs the session.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import numpy as np

from .control import segment_point_distance
from .planning import ARM_SEPARATION_M, PUSH_SKILL_ID, SKILL_ID, PlannerCall, PlannerProfile
from .skills import ZONE_M

PROMPT_VERSION = "pill-planner-v1"
MAX_TOKENS = 32  # a valid reply is ~20 tokens; the release caps output at 128
ARM_CODE = {"left": "L", "right": "R"}
SIDE = {"L": "left", "R": "right"}
OTHER = {"left": "right", "right": "left"}
WAIT, DONE = "wait", "done"
SKILLS = (SKILL_ID, PUSH_SKILL_ID)
ACTIONS = (*SKILLS, WAIT, DONE)
# An arm reaches its own half of the table plus this overlap, between these distances from its shoulder.
REACH_OVERLAP_M = 0.02
REACH_MIN_M, REACH_MAX_M = 0.18, 0.62
ZONE_CHECK_M = round(ZONE_M + 0.02, 3)  # the executive's bottle-zone rule (Episode._arm_conflict)
MAX_PILL_ID = 999
TERMINAL = ("succeeded", "failed", "expired")


# ---- the scene as text -------------------------------------------------------------------------------


def pill_number(pill_id: str) -> int:
    return int(pill_id.split("_")[1])


def reaches(obs: dict, side: str, xy) -> bool:
    """Whether `side`'s arm reaches a pill at `xy` [m]: its own half (+2 cm) and 18-62 cm from its shoulder."""
    xy = np.asarray(xy, dtype=float)
    sign = 1.0 if side == "left" else -1.0
    shoulder = np.asarray(obs["arms"][side]["shoulder"][:2], dtype=float)
    return sign * xy[1] >= -REACH_OVERLAP_M and REACH_MIN_M < float(np.linalg.norm(xy - shoulder)) < REACH_MAX_M


def blocked_by_other_arm(obs: dict, side: str, xy) -> str | None:
    """The executive's two-arm separation rules (as Episode._arm_conflict applies them), on this observation."""
    xy = np.asarray(xy, dtype=float)
    other_side = OTHER[side]
    other = obs["arms"][other_side]
    if obs.get("zone_owner") == other_side:
        shoulder = np.asarray(obs["arms"][side]["shoulder"][:2], dtype=float)
        if segment_point_distance(np.asarray(obs["bottle"]["xy"], dtype=float), shoulder, xy) < ZONE_CHECK_M:
            return f"bottle zone in use by arm {ARM_CODE[other_side]}"
    points = [np.asarray(p, dtype=float) for p in other["links_xy"][1:]]  # wrist and fingertips
    if other.get("target_xy") is not None:
        points.append(np.asarray(other["target_xy"], dtype=float))
    if min(float(np.linalg.norm(xy - p)) for p in points) < ARM_SEPARATION_M:
        return f"next to arm {ARM_CODE[other_side]}"
    return None


@dataclass(frozen=True)
class PillLine:
    number: int
    state: str  # on_mat | in_bottle | held | lost | elsewhere
    xy_cm: tuple[int, int]
    reach: str  # "L", "R", "L+R" or "none"
    available: bool  # for the requesting arm
    note: str  # why it is not available, or the last try's result


@dataclass(frozen=True)
class Scene:
    side: str
    pills: dict[int, PillLine]
    in_bottle: list[int]
    held: dict[int, str]  # pill -> arm code holding/working on it
    other_target: int | None

    @property
    def on_table(self) -> list[int]:
        return [n for n, p in self.pills.items() if p.state == "on_mat"]

    @property
    def available(self) -> list[int]:
        return [n for n, p in self.pills.items() if p.available]


def scene(obs: dict, side: str) -> Scene:
    """What the request tells the model, computed from the observation for the requesting arm."""
    other_side = OTHER[side]
    other_target = obs["arms"][other_side].get("target")
    other_number = pill_number(other_target) if other_target else None
    lines: dict[int, PillLine] = {}
    held: dict[int, str] = {}
    for side_name in ("left", "right"):
        target = obs["arms"][side_name].get("target")
        if target:
            held[pill_number(target)] = ARM_CODE[side_name]
    for pill in obs["pills"]:
        number = pill_number(pill["id"])
        xy = pill["xy"]
        reach = "+".join(ARM_CODE[s] for s in ("left", "right") if reaches(obs, s, xy)) or "none"
        note, available = "", False
        if pill["state"] == "on_mat":
            if not reaches(obs, side, xy):
                note = "out of reach"
            else:
                note = blocked_by_other_arm(obs, side, xy) or ""
                available = not note
            if available and pill.get("last_status"):
                note = f"last try {pill['last_status']}"
        lines[number] = PillLine(number, pill["state"], (round(xy[0] * 100), round(xy[1] * 100)), reach, available, note)
    in_bottle = sorted(n for n, p in lines.items() if p.state == "in_bottle")
    return Scene(side, lines, in_bottle, held, other_number)


def _ids(numbers: list[int]) -> str:
    return ", ".join(str(n) for n in numbers) if numbers else "none"


def _other_arm_text(obs: dict, side: str) -> str:
    other = obs["arms"][OTHER[side]]
    if other.get("target"):
        return f"busy with pill {pill_number(other['target'])} ({other.get('phase') or 'working'})"
    return "idle"


def build_prompt(obs: dict, side: str, feedback: str | None = None) -> str:
    """The request text for the free arm `side` (one user message)."""
    me, them = ARM_CODE[side], ARM_CODE[OTHER[side]]
    s = scene(obs, side)
    bottle = obs["bottle"]["xy"]
    tcp = obs["arms"][side]["tcp"]
    lines = [
        "You plan for a two-arm robot at a table. Task: put all the pills into the bottle.",
        f"Arm {me} is free. Choose the next action for arm {me}.",
        "",
        "Scene (positions in cm, x forward, y to the left):",
        f"Bottle at ({round(bottle[0] * 100)}, {round(bottle[1] * 100)}).",
        f"Arm {me}: free, gripper at ({round(tcp[0] * 100)}, {round(tcp[1] * 100)}).",
        f"Arm {them}: {_other_arm_text(obs, side)}.",
        f"In the bottle: {_ids(s.in_bottle)} ({len(s.in_bottle)} of {len(s.pills)} pills).",
    ]
    table = [p for p in s.pills.values() if p.state == "on_mat"]
    if table:
        lines.append(f"Pills on the table (id: position, which arm reaches it, status for arm {me}):")
        for p in sorted(table, key=lambda p: p.number):
            status = "available" if p.available else "not available"
            note = f", {p.note}" if p.note else ""
            lines.append(f"{p.number}: ({p.xy_cm[0]}, {p.xy_cm[1]}), reach {p.reach}, {status}{note}")
    else:
        lines.append("No pill is left on the table.")
    gone = [p.number for p in s.pills.values() if p.state in ("lost", "elsewhere")]
    if gone:
        lines.append(f"Not on the table and not in the bottle: {_ids(sorted(gone))}.")
    lines += [
        "",
        "Actions:",
        f'{{"arm": "{me}", "skill": "pick_and_drop", "pill": ID}} puts an available pill into the bottle.',
        f'{{"arm": "{me}", "skill": "push_apart", "pill": ID}} slides an available pill away from its '
        "neighbours; use it when that pill's last try was no_clear_grasp or blocked.",
        f'{{"arm": "{me}", "skill": "wait"}} when no pill on the table is available for arm {me}.',
        f'{{"arm": "{me}", "skill": "done"}} when no pill is left on the table.',
        "Reply with exactly one of these JSON objects, ID being a pill id, and nothing else.",
    ]
    if feedback:
        lines += ["", feedback]
    return "\n".join(lines)


def build_messages(obs: dict, side: str, feedback: str | None = None) -> list[dict]:
    return [{"role": "user", "content": build_prompt(obs, side, feedback)}]


def refusal_feedback(reply: str | None, reason: str) -> str:
    shown = (reply or "").strip().replace("\n", " ")
    if len(shown) > 160:
        shown = shown[:157] + "..."
    return f"Your previous reply {json.dumps(shown)} was refused: {reason}. Reply again with one JSON object."


# ---- strict parsing and the choice check ---------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    arm: str  # "L" | "R"
    skill: str  # pick_and_drop | push_apart | wait | done
    pill: int | None = None

    def as_dict(self) -> dict:
        return {"arm": self.arm, "skill": self.skill, **({} if self.pill is None else {"pill": self.pill})}


class ReplyError(ValueError):
    """A reply that is not one JSON object of the declared shape (``kind``: invalid_json | invalid_schema)."""

    def __init__(self, kind: str, reason: str):
        super().__init__(reason)
        self.kind, self.reason = kind, reason


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _constant(name: str):
    raise ValueError(f"{name} is not JSON")


def parse_reply(text: str | None) -> Action:
    """Exactly one JSON object (whitespace around it allowed):
    {"arm": "L"|"R", "skill": "pick_and_drop"|"push_apart", "pill": <integer id>} or
    {"arm": "L"|"R", "skill": "wait"|"done"}. Anything else raises ReplyError; nothing is repaired."""
    if text is None or not text.strip():
        raise ReplyError("invalid_json", "the reply was empty")
    try:
        value = json.loads(text.strip(), object_pairs_hook=_pairs, parse_constant=_constant)
    except ValueError:
        raise ReplyError("invalid_json", "the reply was not one JSON object and nothing else") from None
    if not isinstance(value, dict):
        raise ReplyError("invalid_schema", "the reply must be a JSON object")
    skill = value.get("skill")
    if skill not in ACTIONS:
        raise ReplyError("invalid_schema", f"skill must be one of {', '.join(ACTIONS)}")
    expected = {"arm", "skill", "pill"} if skill in SKILLS else {"arm", "skill"}
    if set(value) != expected:
        raise ReplyError("invalid_schema", f"a {skill} reply has exactly the keys {', '.join(sorted(expected))}")
    if value["arm"] not in ("L", "R"):
        raise ReplyError("invalid_schema", 'arm must be "L" or "R"')
    pill = value.get("pill")
    if skill in SKILLS and (type(pill) is not int or not 0 <= pill <= MAX_PILL_ID):
        raise ReplyError("invalid_schema", "pill must be an integer pill id")
    return Action(value["arm"], skill, pill)


def check_choice(action: Action, obs: dict, side: str) -> tuple[str, str] | None:
    """(code, reason) when the scene sent with the request rules the action out, else None."""
    me = ARM_CODE[side]
    if action.arm != me:
        return "wrong_arm", f"arm {action.arm} was not asked; arm {me} is the free arm"
    s = scene(obs, side)
    if action.skill in SKILLS:
        pill = s.pills.get(action.pill)
        if pill is None:
            return "unknown_pill", f"there is no pill {action.pill}"
        if pill.state == "in_bottle":
            return "pill_in_bottle", f"pill {action.pill} is already in the bottle"
        if pill.state == "held" or action.pill == s.other_target:
            return "taken_by_other_arm", f"pill {action.pill} is taken by arm {ARM_CODE[OTHER[side]]}"
        if pill.state != "on_mat":
            return "pill_not_on_table", f"pill {action.pill} is not on the table"
        if me not in pill.reach.split("+"):
            return "out_of_reach", f"pill {action.pill} is out of reach of arm {me}"
        if not pill.available:
            return "pill_blocked", f"pill {action.pill} is not available ({pill.note})"
        return None
    if action.skill == WAIT and s.available:
        return "wait_with_pill_available", f"wait is only for when no pill on the table is available for arm {me}"
    if action.skill == DONE and s.on_table:
        return "done_with_pills_on_table", f"done is only for when no pill is left on the table ({len(s.on_table)} are)"
    return None


# ---- the declared failure policy -----------------------------------------------------------------------


@dataclass(frozen=True)
class FailurePolicy:
    """What follows a call that brings no usable action. Fixed before an evaluation and recorded with it.

    * A decision (one action for one free arm) takes at most `calls_per_decision` calls: the first ask,
      then re-asks with a fresh scene. After a refused reply the re-ask also names the reply and why it
      was refused; after a device error, HTTP error or timeout it is the plain request again.
    * A decision without a usable action after those calls is a *failed decision*: the arm parks and
      asks again after the other arm's next skill result or `hold_after_failed_decision_s` of simulated
      time, whichever comes first (as after a "wait").
    * An episode makes at most `budget_per_pill` x pills + `budget_extra` calls; then it ends
      (`planner_call_budget_exhausted`).
    * The device going offline or out of chat eligibility, the active model changing, or the session
      ending stops the episode and the evaluation (recorded, never retried around).
    * A call that reaches no terminal result within `client_deadline_s` is a timeout. Its elapsed time
      counts like any round trip; before the next call the client waits, in wall-clock time only, for
      that request to finish or expire so that the device never has two requests.
    * When an accepted action is delivered, the executive re-checks the separation rules on the current
      simulator state (the other arm kept moving during the round trip); a conflict rejects it as stale
      and a new decision starts at once.
    """

    calls_per_decision: int = 3
    hold_after_failed_decision_s: float = 10.0
    budget_per_pill: int = 2
    budget_extra: int = 12
    client_deadline_s: float = 45.0

    def budget(self, pills: int) -> int:
        return self.budget_per_pill * pills + self.budget_extra


# ---- transport -------------------------------------------------------------------------------------------


@dataclass
class ChatOutcome:
    """One device chat request as measured by the client. No credentials."""

    request_id: str
    status: str  # succeeded | failed | expired | timeout | http_error | transport_error
    e2e_ms: float  # POST sent -> terminal result received (or the failure)
    sent_at: str
    finished_at: str
    http_status: int | None = None
    error_code: str | None = None
    content: str | None = None
    finish_reason: str | None = None
    trace_id: str | None = None
    release_id: str | None = None
    device_latency_ms: float | None = None
    ttft_ms: float | None = None
    queue_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    post_ms: float | None = None
    polls: int = 0
    paced_wait_s: float = 0.0  # wall-clock wait before the POST for the route's rate limit (not sim time)


@dataclass(frozen=True)
class DeviceState:
    online: bool
    eligible: bool
    reason: str | None
    release_id: str | None
    status: str | None
    max_tokens: int | None
    context_window: int | None
    checked_at: str
    http_status: int


class ChatTransport(Protocol):
    def device(self) -> DeviceState: ...

    def send(self, messages: list[dict], max_tokens: int) -> ChatOutcome: ...


class SessionEnded(RuntimeError):
    """The signed-in session was refused (401): nothing more can be sent."""


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a session is never sent anywhere else
        return None


class PortalChatClient:
    """The website's device chat routes with one signed-in operator session.

    ``POST /api/portal/chat`` takes ``{request_id, expected_release_id, messages, max_tokens}`` (user and
    assistant messages only, at most 16 and 8 KiB, 1-128 output tokens; no temperature or seed: decoding
    is the active release's) and answers 202; ``GET /api/portal/chat/{request_id}`` reads it until it is
    succeeded, failed or expired. The routes allow 6 sends and 180 reads per minute per session (20 and
    1200 for the site); ``GET /api/portal/snapshot`` (30 per minute) says whether the device is online
    and eligible. Sends are spaced `min_interval_s` apart; reads poll every `poll_s` for the first
    `fast_poll_s`, then every second.
    """

    CHAT = "/api/portal/chat"
    SNAPSHOT = "/api/portal/snapshot"

    def __init__(self, server: str, cookie: str, *, min_interval_s: float = 10.5, poll_s: float = 0.25,
                 fast_poll_s: float = 5.0, deadline_s: float = 45.0, expire_wait_s: float = 130.0,
                 opener=None, clock=time.monotonic, sleep=time.sleep, wall=time.time, log=None):
        origin = server.rstrip("/")
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?|http://(localhost|127\.0\.0\.1)(:\d+)?", origin):
            raise ValueError("server must be an https origin (or http on localhost)")
        if not re.fullmatch(r"convoy_session=cvs_[A-Za-z0-9_-]{16,128}", cookie):
            raise ValueError("cookie must be a convoy_session value")
        self.server, self._cookie = origin, cookie
        self.min_interval_s, self.poll_s, self.fast_poll_s = min_interval_s, poll_s, fast_poll_s
        self.deadline_s, self.expire_wait_s = deadline_s, expire_wait_s
        self.opener = opener or urllib.request.build_opener(_NoRedirect)
        self.clock, self.sleep, self.wall = clock, sleep, wall
        self.log = log or (lambda message: None)
        self.release_id: str | None = None
        self._last_post: float | None = None
        self._not_before = 0.0
        self._unfinished: str | None = None
        self.late_results: list[dict] = []  # timed-out requests read after their deadline
        self.sends = 0
        self.reads = 0

    def __repr__(self) -> str:  # never shows the session
        return f"PortalChatClient({self.server!r})"

    @classmethod
    def from_session_file(cls, server: str, path, **kwargs) -> PortalChatClient:
        from pathlib import Path

        return cls(server, Path(path).read_text().strip(), **kwargs)

    # -- HTTP --
    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict, dict]:
        headers = {"Accept": "application/json", "X-Convoy-Client": "web", "Cookie": self._cookie}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
            headers["Origin"] = self.server
        request = urllib.request.Request(self.server + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=20) as response:
                raw, status, response_headers = response.read(), response.status, dict(response.headers)
        except urllib.error.HTTPError as error:
            raw, status, response_headers = error.read() or b"", error.code, dict(error.headers or {})
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            parsed = {}
        if status == 401:
            raise SessionEnded("the session was refused (401)")
        return status, parsed if isinstance(parsed, dict) else {}, response_headers

    def device(self) -> DeviceState:
        """The configured device's chat availability (``GET /api/portal/snapshot``)."""
        status, data, _ = self._request("GET", self.SNAPSHOT)
        self.reads += 1
        chat, device = data.get("chat") or {}, data.get("device") or {}
        state = DeviceState(
            online=status == 200 and chat.get("online") is True, eligible=status == 200 and chat.get("eligible") is True,
            reason=chat.get("reason") or (None if status == 200 else f"snapshot HTTP {status}"),
            release_id=chat.get("release_id"), status=device.get("status"), max_tokens=chat.get("max_tokens"),
            context_window=chat.get("context_window"), checked_at=_utc(self.wall()), http_status=status)
        if state.online and state.eligible and state.release_id:
            self.release_id = self.release_id or state.release_id
        return state

    def _pace(self) -> float:
        now = self.clock()
        ready = max(self._not_before, (self._last_post + self.min_interval_s) if self._last_post is not None else now)
        wait = max(0.0, ready - now)
        if wait:
            self.sleep(wait)
        return wait

    def _read(self, request_id: str) -> tuple[int, dict, dict]:
        self.reads += 1
        return self._request("GET", f"{self.CHAT}/{request_id}")

    def _settle_unfinished(self) -> None:
        """Before a new send: the timed-out request must be finished or expired (one request at a time)."""
        if not self._unfinished:
            return
        request_id, started = self._unfinished, self.clock()
        result: dict = {"request_id": request_id, "status": "unknown"}
        while self.clock() - started < self.expire_wait_s:
            self.sleep(1.0)
            status, data, _ = self._read(request_id)
            if status == 200 and data.get("status") in TERMINAL:
                result = {"request_id": request_id, "status": data["status"], "trace_id": data.get("trace_id"),
                          "content": data.get("content"), "read_at": _utc(self.wall())}
                break
        self.late_results.append(result)
        self.log(f"late result of {request_id}: {result['status']}")
        self._unfinished = None

    def send(self, messages: list[dict], max_tokens: int) -> ChatOutcome:
        if self.release_id is None:
            raise RuntimeError("check the device first: no active release is known")
        self._settle_unfinished()
        paced = self._pace()
        request_id = str(uuid.uuid4())
        body = {"request_id": request_id, "expected_release_id": self.release_id, "messages": messages,
                "max_tokens": max_tokens}
        sent_wall, t0 = self.wall(), self.clock()
        self._last_post = t0
        self.sends += 1

        def outcome(status: str, **values) -> ChatOutcome:
            return ChatOutcome(request_id=request_id, status=status, e2e_ms=round((self.clock() - t0) * 1000, 1),
                               sent_at=_utc(sent_wall), finished_at=_utc(self.wall()), paced_wait_s=round(paced, 3),
                               **values)

        try:
            status, data, headers = self._request("POST", self.CHAT, body)
        except SessionEnded:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            return outcome("transport_error", error_code=type(error).__name__)
        post_ms = round((self.clock() - t0) * 1000, 1)
        if status != 202:
            retry = str(headers.get("Retry-After", ""))
            if status == 429:
                self._not_before = self.clock() + (int(retry) if retry.isdigit() else 60)
            code = (data.get("error") or {}).get("code") if isinstance(data.get("error"), dict) else None
            return outcome("http_error", http_status=status, error_code=code, post_ms=post_ms)
        polls, errors = 0, 0
        while data.get("status") not in TERMINAL:
            elapsed = self.clock() - t0
            if elapsed >= self.deadline_s:
                self._unfinished = request_id
                return outcome("timeout", http_status=202, post_ms=post_ms, polls=polls)
            self.sleep(self.poll_s if elapsed < self.fast_poll_s else 1.0)
            try:
                read_status, read, read_headers = self._read(request_id)
            except SessionEnded:
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                read_status, read, read_headers = 0, {}, {}
            polls += 1
            if read_status == 200:
                data, errors = read, 0
                continue
            errors += 1
            if read_status == 429:
                retry = str(read_headers.get("Retry-After", ""))
                self.sleep(min(30, int(retry) if retry.isdigit() else 5))
            if errors >= 5:
                self._unfinished = request_id
                return outcome("http_error", http_status=read_status or None, error_code="poll_failed",
                               post_ms=post_ms, polls=polls)
        usage, metrics = data.get("usage") or {}, data.get("metrics") or {}
        error = data.get("error") or {}
        return outcome(
            data["status"], http_status=202, error_code=error.get("code") if isinstance(error, dict) else None,
            content=data.get("content"), finish_reason=data.get("finish_reason"), trace_id=data.get("trace_id"),
            release_id=data.get("release_id"), device_latency_ms=metrics.get("latency_ms"),
            ttft_ms=metrics.get("ttft_ms"), queue_ms=metrics.get("queue_ms"), tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"), post_ms=post_ms, polls=polls)


# ---- the planner endpoint -------------------------------------------------------------------------------


@dataclass
class _Decision:
    call: PlannerCall  # the episode's view: requested at submitted_s, answered at completes_s
    calls: int = 0
    feedback: str | None = None


@dataclass
class _InFlight:
    decision: _Decision
    record: dict
    completes_s: float
    action: Action | None
    stop: str | None = None


def _percentile(values: list[float], q: float) -> float | None:
    return round(float(np.percentile(values, q)), 1) if values else None


class DevicePlannerEndpoint:
    """Skill decisions from the model on the device, one call at a time.

    The episode submits a request when an arm is free; the call is made when the endpoint is idle,
    with a scene read at that moment (``observe(side)``), and its decision is delivered once the call's
    measured end-to-end round trip has passed in simulated time (at the next 10 ms control tick).
    `records` holds every call for the audit; `on_record(record)` sees each one when it completes in
    simulated time.
    """

    def __init__(self, profile: PlannerProfile, transport: ChatTransport, observe, pills: int, *,
                 policy: FailurePolicy | None = None, network=None, on_record=None, max_tokens: int = MAX_TOKENS,
                 label: str = "", log=None):
        self.profile, self.transport, self.observe = profile, transport, observe
        self.policy = policy or FailurePolicy()
        self.network, self.on_record, self.max_tokens, self.label = network, on_record, max_tokens, label
        self.log = log or (lambda message: None)
        self.budget = self.policy.budget(pills)
        self.queue: list[_Decision] = []
        self.current: _InFlight | None = None
        self.calls: list[PlannerCall] = []
        self.records: list[dict] = []
        self.stopped: str | None = None
        self.device_checks: list[dict] = []
        self.decisions = 0
        self._next_id = 1
        self._last_record: dict[int, dict] = {}  # call id -> the last record of its decision

    # -- the PlannerEndpoint interface --
    def submit(self, arm: str, request: dict, t: float) -> PlannerCall:
        call = PlannerCall(self._next_id, arm, "skill", t, {})
        self._next_id += 1
        self.calls.append(call)
        self.queue.append(_Decision(call))
        return call

    def pending(self, arm: str) -> bool:
        return any(d.call.arm == arm for d in self.queue) or (self.current is not None and self.current.decision.call.arm == arm)

    def poll(self, t: float) -> list[PlannerCall]:
        if self.current is not None:
            if self.current.completes_s > t:
                return []
            flight, self.current = self.current, None
            return self._settle(flight, t)
        if self.queue:
            self._start(self.queue.pop(0), t)
            if self.current is not None and self.current.completes_s <= t:  # a refusal made without a call
                flight, self.current = self.current, None
                return self._settle(flight, t)
        return []

    # -- calls --
    def _check_device(self, why: str) -> DeviceState | None:
        try:
            state = self.transport.device()
        except SessionEnded:
            self.stopped = "session_ended"
            return None
        except Exception as error:  # noqa: BLE001 - an unreadable status is recorded, not hidden
            self.device_checks.append({"why": why, "error": type(error).__name__})
            return None
        self.device_checks.append({"why": why, **asdict(state)})
        if not (state.online and state.eligible):
            self.stopped = "device_offline" if not state.online else "device_not_eligible"
        elif getattr(self.transport, "release_id", None) and state.release_id != self.transport.release_id:
            self.stopped = "device_model_changed"
        return state

    def _start(self, decision: _Decision, t: float) -> None:
        side = decision.call.arm
        if decision.calls == 0:
            decision.call.started_s = t
        if self.stopped is None and len(self.records) >= self.budget:
            self.stopped = "planner_call_budget_exhausted"
        if self.stopped is not None:
            self.current = _InFlight(decision, {}, t, None, stop=self.stopped)
            return
        if decision.calls == 0:
            self.decisions += 1
        decision.calls += 1
        obs = self.observe(side)
        messages = build_messages(obs, side, decision.feedback)
        try:
            outcome = self.transport.send(messages, self.max_tokens)
        except SessionEnded:
            self.stopped = "session_ended"
            self.current = _InFlight(decision, {}, t, None, stop=self.stopped)
            return
        record = {
            "call": len(self.records) + 1, "decision": self.decisions, "attempt": decision.calls,
            "arm": ARM_CODE[side], "sim_start_s": round(t, 3), "prompt_version": PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest(),
            "messages": messages, "max_tokens": self.max_tokens, **asdict(outcome),
        }
        action = None
        if outcome.status == "succeeded":
            try:
                action = parse_reply(outcome.content)
                refusal = check_choice(action, obs, side)
                if refusal is None:
                    record["result"] = "valid"
                else:
                    record["result"], record["refusal_code"], record["refusal"] = "invalid_choice", *refusal
                    action = None
            except ReplyError as error:
                record["result"], record["refusal_code"], record["refusal"] = error.kind, error.kind, error.reason
        else:
            record["result"] = {"failed": "device_error", "expired": "device_error"}.get(outcome.status, outcome.status)
            if outcome.status in ("http_error", "transport_error", "timeout", "expired") or outcome.http_status == 409:
                self._check_device(f"after {outcome.status}")
        record["action"] = None if action is None else action.as_dict()
        record["sim_end_s"] = round(t + outcome.e2e_ms / 1000.0, 3)
        self.records.append(record)
        self.log(self._line(record))
        self.current = _InFlight(decision, record, t + outcome.e2e_ms / 1000.0, action)

    def _line(self, r: dict) -> str:
        action = r.get("action")
        what = (f"{action['skill']}" + (f" {action['pill']}" if "pill" in action else "")) if action else r["result"]
        device = f"{r['device_latency_ms']:.0f} ms" if r.get("device_latency_ms") is not None else "-"
        return (f"{self.label} call {r['call']:3d} arm {r['arm']} t={r['sim_start_s']:7.2f}s  {what:24s} "
                f"e2e {r['e2e_ms'] / 1000:5.2f} s  device {device:>8s}  {r.get('trace_id') or ''}")

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
            if a.skill in SKILLS:
                decision_value = {"kind": "skill", "skill_id": a.skill,
                                  "parameters": {"pill": f"pill_{a.pill:02d}", "arm": SIDE[a.arm]}}
            else:
                decision_value = {"kind": a.skill}
            return self._deliver(call, t, decision_value, record, "delivered")
        if self.stopped is not None:
            return self._deliver(call, t, {"kind": "stop", "reason": self.stopped}, record, "stopped")
        if decision.calls < self.policy.calls_per_decision:
            record["next"] = "re-asked"
            decision.feedback = (refusal_feedback(record.get("content"), record["refusal"])
                                 if record.get("result") in ("invalid_json", "invalid_schema", "invalid_choice") else None)
            self.queue.insert(0, decision)
            return []
        return self._deliver(call, t, {"kind": "failed", "reason": "no usable action after "
                                       f"{decision.calls} calls"}, record, "failed_decision")

    def _deliver(self, call: PlannerCall, t: float, decision: dict, record: dict, next_step: str) -> list[PlannerCall]:
        if record:
            record["next"] = next_step
        call.decision, call.status, call.completes_s = decision, "ok", t
        call.latency_s = t - call.started_s
        if record:
            self._last_record[call.call_id] = record
        return [call]

    def mark_stale(self, call: PlannerCall, reason: str) -> None:
        """The executive rejected this delivered action on the current state (see FailurePolicy)."""
        record = self._last_record.get(call.call_id)
        if record:
            record["next"] = f"rejected_stale: {reason}"

    @staticmethod
    def trace_record(record: dict) -> dict:
        """The per-step view of a call (recordings): what was chosen and how long it took."""
        return {"call_id": record["call"], "role": "skill", "arm": record["arm"],
                "status": "ok" if record["result"] == "valid" else record["result"],
                "latency_ms": record["e2e_ms"], "device_latency_ms": record.get("device_latency_ms"),
                "ttft_ms": record.get("ttft_ms"), "tokens_in": record.get("tokens_in"),
                "tokens_out": record.get("tokens_out"), "trace_id": record.get("trace_id"),
                "decision": record.get("action"), "submitted_s": record["sim_start_s"],
                "started_s": record["sim_start_s"]}

    # -- summary --
    def stats(self, t_end: float) -> dict:
        """Counts and measured latencies of the episode's calls (nothing modeled)."""
        r = self.records
        by_result: dict[str, int] = {}
        for record in r:
            by_result[record["result"]] = by_result.get(record["result"], 0) + 1
        answered = [x for x in r if x["status"] == "succeeded"]
        e2e = [x["e2e_ms"] for x in answered]
        device = [x["device_latency_ms"] for x in answered if x.get("device_latency_ms") is not None]
        ttft = [x["ttft_ms"] for x in answered if x.get("ttft_ms") is not None]
        tokens_in = [x["tokens_in"] for x in answered if x.get("tokens_in") is not None]
        tokens_out = [x["tokens_out"] for x in answered if x.get("tokens_out") is not None]
        refusals: dict[str, int] = {}
        for x in r:
            if x.get("refusal_code"):
                refusals[x["refusal_code"]] = refusals.get(x["refusal_code"], 0) + 1
        nexts = [x.get("next", "") for x in r]
        delivered = [x["action"]["skill"] for x in r if x.get("next") == "delivered" and x.get("action")]
        return {
            "calls": len(r), "decisions": self.decisions, "by_result": by_result, "refusals": refusals,
            "delivered": [skill for skill in delivered if skill in SKILLS],
            "actions": {name: delivered.count(name) for name in ACTIONS if name in delivered},
            "failed_decisions": sum(n == "failed_decision" for n in nexts),
            "stale_rejections": sum(n.startswith("rejected_stale") for n in nexts),
            "not_delivered": sum(x.get("delivered_sim_s") is None for x in r),
            "e2e_p50_ms": _percentile(e2e, 50), "e2e_p95_ms": _percentile(e2e, 95),
            "e2e_max_ms": round(max(e2e), 1) if e2e else None,
            "device_p50_ms": _percentile(device, 50), "device_p95_ms": _percentile(device, 95),
            "ttft_p50_ms": _percentile(ttft, 50), "ttft_p95_ms": _percentile(ttft, 95),
            "tokens_in_p50": _percentile(tokens_in, 50), "tokens_out_p50": _percentile(tokens_out, 50),
            "sim_time_in_calls_s": round(sum(x["e2e_ms"] for x in r) / 1000.0, 2),
            "trace_ids": [x["trace_id"] for x in r if x.get("trace_id")],
            "budget": self.budget, "stopped": self.stopped, "device_checks": self.device_checks,
            "prompt_version": PROMPT_VERSION, "max_tokens": self.max_tokens, "policy": asdict(self.policy),
        }


def settings() -> dict:
    """The frozen request and failure settings, as an evaluation records them."""
    return {"prompt_version": PROMPT_VERSION, "max_tokens": MAX_TOKENS, "policy": asdict(FailurePolicy()),
            "reach": {"own_half_overlap_m": REACH_OVERLAP_M, "shoulder_distance_m": [REACH_MIN_M, REACH_MAX_M]},
            "separation_m": ARM_SEPARATION_M, "bottle_zone_check_m": ZONE_CHECK_M,
            "decoding": "not settable through the device chat API: the active release's own settings apply",
            "sim_time": "each call's measured end-to-end round trip (client POST to terminal result)"}

