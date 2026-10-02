"""Planner hooks, the rule-based stand-in decision policy, and latency/outage models.

A *decision policy* chooses the next skill call from a JSON observation. The
included ``GreedyPillPlanner`` is deterministic code standing in for a language
model planner: the configurations that use it share the same decision rule, so
differences between them come only from where the planner runs, how long a call
takes, and what happens when the network is down. Swap in a real model through
the ``DecisionPolicy`` protocol; if it reports its own measured latency, that
replaces the modeled latency. "Edge Qwen" does not use the stand-in: its
decisions are real calls to the model on a connected device (``device_planner``).

Latency models are log-normal, fitted to a published or measured p50/p95 pair.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from . import physics as P
from .control import segment_point_distance

SKILL_ID = "pick_and_drop"
PUSH_SKILL_ID = "push_apart"
INSTRUCTION = "Put all the pills in the bottle"
ARM_SEPARATION_M = 0.12  # an arm does not work within this distance of the other arm's wrist, TCP or target


@dataclass(frozen=True)
class LatencyModel:
    p50_s: float
    p95_s: float
    floor_s: float = 0.0

    def sample(self, rng: np.random.Generator) -> float:
        sigma = math.log(self.p95_s / self.p50_s) / 1.6448536
        return max(self.floor_s, self.p50_s * math.exp(sigma * rng.standard_normal()))


@dataclass(frozen=True)
class NetworkModel:
    """Site-to-cloud link: round trip time and outage windows [start, end) in sim seconds."""

    rtt: LatencyModel = field(default_factory=lambda: LatencyModel(0.030, 0.090))
    outages: tuple[tuple[float, float], ...] = ()

    def up(self, t: float) -> bool:
        return not any(a <= t < b for a, b in self.outages)

    def first_down(self, t0: float, t1: float) -> float | None:
        hits = [max(a, t0) for a, b in self.outages if a < t1 and b > t0]
        return min(hits) if hits else None


@dataclass(frozen=True)
class PlannerProfile:
    """Where a planner runs and how long it takes. `evidence` names the source.

    `concurrency` is how many calls run at once: one for a model on the robot's
    single edge GPU (requests queue), one per arm for a hosted API. `source`
    "stand_in" is the rule-based ``GreedyPillPlanner`` with latency drawn from
    `latency`; "device" is a real model on a connected device
    (``device_planner``): no latency model, every call is measured.
    """

    name: str
    model: str
    placement: str  # "edge" | "cloud"
    latency: LatencyModel | None
    timeout_s: float
    connect_timeout_s: float = 3.0
    evidence: str = ""
    concurrency: int = 1
    source: str = "stand_in"  # "stand_in" | "device"


@dataclass
class PlannerCall:
    call_id: int
    arm: str
    role: str  # "skill" (choose the next skill call) | "task" (decompose / verify)
    submitted_s: float
    request: dict
    decision: dict | None = None
    status: str = "pending"  # ok | timeout | unreachable
    started_s: float = 0.0
    completes_s: float = 0.0
    latency_s: float = 0.0
    compute_ms: float = 0.0  # wall time of the decision code itself


class DecisionPolicy(Protocol):
    def decide(self, request: dict) -> tuple[dict, float | None]:
        """Return (decision, measured latency in seconds or None to use the model)."""


def _segment(pos: np.ndarray, yaw: float) -> tuple[np.ndarray, np.ndarray]:
    d = np.array([math.cos(yaw), math.sin(yaw)]) * P.PILL_HALF_LENGTH_M
    return pos - d, pos + d


def _point_segment(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    v = b - a
    t = float(np.clip(np.dot(p - a, v) / max(float(np.dot(v, v)), 1e-12), 0, 1))
    return float(np.linalg.norm(p - (a + t * v)))


def finger_clearance(pill: dict, others: list[dict]) -> float:
    """Free space [m] around the two open fingertips for a grasp across `pill`."""
    pos = np.array(pill["xy"])
    across = pill["yaw"] + math.pi / 2
    tips = [pos + sgn * 0.0145 * np.array([math.cos(across), math.sin(across)]) for sgn in (1, -1)]
    best = 1.0
    for other in others:
        q = np.array(other["xy"])
        if np.linalg.norm(q - pos) > 0.06:
            continue
        a, b = _segment(q, other["yaw"])
        for tip in tips:
            best = min(best, _point_segment(tip, a, b) - P.PILL_RADIUS_M - 0.008)
    return best


class GreedyPillPlanner:
    """Deterministic stand-in for a skill-routing planner (no learned competence).

    Each arm works its own half of the table (2 cm overlap at the midline). It
    may take a pill up to 10 cm into the other half only while the other arm is
    idle. A pill is skipped while the other arm's wrist or gripper, or the pill
    it is working on, is within 12 cm of it, or while the other arm holds the
    bottle zone and reaching the pill would put this arm over it. Among the
    rest it takes the pill nearest its gripper, preferring pills whose
    neighbours leave room for the open fingers. A pill whose grasp
    was blocked is first pushed apart (at most twice); a pill is given up after
    four pick attempts.
    """

    max_attempts = 4
    max_pushes = 2
    zone_m = 0.13

    def decide(self, request: dict) -> tuple[dict, None]:
        obs = request["observation"]
        arm = request["arm"]
        sign = 1.0 if arm == "left" else -1.0
        other = obs["arms"]["right" if arm == "left" else "left"]
        tcp = np.array(obs["arms"][arm]["tcp"][:2])
        shoulder = np.array(obs["arms"][arm]["shoulder"][:2])
        bottle = np.array(obs["bottle"]["xy"])
        loose = [p for p in obs["pills"] if p["state"] == "on_mat"]
        remaining = [p for p in obs["pills"] if p["state"] != "in_bottle"]
        if not remaining:
            return {"kind": "done", "reason": "all_pills_in_bottle"}, None
        if not obs["motor_policy"]["available"] and any(h["status"] == "policy_unavailable" for h in obs["history"]):
            return {"kind": "decline", "reason": "no_motor_policy_for_this_robot"}, None
        if not obs["bottle"].get("upright", True):
            return {"kind": "decline", "reason": "bottle_tipped_over"}, None
        other_points = [np.array(p) for p in other["links_xy"][1:]]
        if other.get("target_xy") is not None:
            other_points.append(np.array(other["target_xy"]))
        candidates = []
        for pill in loose:
            if pill["id"] == other.get("target") or pill["attempts"] >= self.max_attempts:
                continue
            push = pill.get("last_status") in ("no_clear_grasp", "blocked") and pill.get("pushes", 0) < self.max_pushes
            xy = np.array(pill["xy"])
            cross = sign * xy[1] < -0.02
            if cross and (other["busy"] or sign * xy[1] < -0.10):
                continue
            if min(np.linalg.norm(xy - q) for q in other_points) < ARM_SEPARATION_M:
                continue
            if obs["zone_owner"] not in (None, arm) and segment_point_distance(bottle, shoulder, xy) < self.zone_m:
                continue  # reaching it would put this arm over the zone the other arm is using
            if not 0.18 < np.linalg.norm(xy - shoulder) < 0.62:
                continue
            clearance = finger_clearance(pill, [p for p in loose if p["id"] != pill["id"]])
            score = (float(np.linalg.norm(xy - tcp)) + (0.15 if clearance < 0 else 0.0) + 0.05 * pill["attempts"]
                     + (0.10 if cross else 0.0) - (0.05 if push else 0.0))
            candidates.append((score, pill["id"], PUSH_SKILL_ID if push else SKILL_ID))
        if candidates:
            _, chosen, skill = min(candidates)
            return {"kind": "skill", "skill_id": skill, "parameters": {"pill": chosen, "arm": arm}}, None
        if [p for p in loose if p["attempts"] < self.max_attempts] or other["busy"]:
            return {"kind": "wait", "reason": "no_free_pill_for_this_arm"}, None
        return {"kind": "done", "reason": "remaining_pills_unreachable_or_failed"}, None


class PlannerEndpoint:
    """One planner deployment: a FIFO of requests and up to `profile.concurrency` calls in flight.

    Edge calls complete after the sampled model latency. Cloud calls also need
    the network: a call issued while the link is down fails after the connect
    timeout; a call whose response window overlaps an outage is lost and fails
    after the request timeout. Retries are the caller's decision.
    """

    def __init__(self, profile: PlannerProfile, network: NetworkModel, rng: np.random.Generator,
                 policy: DecisionPolicy, role: str = "skill"):
        self.profile, self.network, self.rng, self.policy, self.role = profile, network, rng, policy, role
        self.queue: list[PlannerCall] = []
        self.in_flight: list[PlannerCall] = []
        self.calls: list[PlannerCall] = []
        self._next_id = 1

    def submit(self, arm: str, request: dict, t: float) -> PlannerCall:
        call = PlannerCall(self._next_id, arm, self.role, t, request)
        self._next_id += 1
        self.queue.append(call)
        self.calls.append(call)
        return call

    def pending(self, arm: str) -> bool:
        return any(c.arm == arm for c in self.queue + self.in_flight)

    def _start(self, call: PlannerCall, t: float) -> None:
        import time

        call.started_s = t
        before = time.perf_counter()
        decision, measured = self.policy.decide(call.request)
        call.compute_ms = (time.perf_counter() - before) * 1000
        latency = measured if measured is not None else self.profile.latency.sample(self.rng)
        if self.profile.placement == "cloud":
            latency += self.network.rtt.sample(self.rng)
            if not self.network.up(t):
                call.status, call.completes_s = "unreachable", t + self.profile.connect_timeout_s
            elif self.network.first_down(t, t + latency) is not None or latency > self.profile.timeout_s:
                call.status, call.completes_s = "timeout", t + self.profile.timeout_s
            else:
                call.status, call.completes_s = "ok", t + latency
        elif latency > self.profile.timeout_s:
            call.status, call.completes_s = "timeout", t + self.profile.timeout_s
        else:
            call.status, call.completes_s = "ok", t + latency
        call.latency_s = call.completes_s - t
        call.decision = decision if call.status == "ok" else None
        self.in_flight.append(call)

    def poll(self, t: float) -> list[PlannerCall]:
        """Start queued calls while there is capacity; return the calls finished by `t`, in order."""
        done = []
        while True:
            while self.queue and len(self.in_flight) < max(1, self.profile.concurrency):
                self._start(self.queue.pop(0), t)
            finished = sorted((c for c in self.in_flight if c.completes_s <= t), key=lambda c: (c.completes_s, c.call_id))
            if not finished:
                return done
            for call in finished:
                self.in_flight.remove(call)
                done.append(call)
