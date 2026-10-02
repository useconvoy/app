"""Hook for learned motor policies (VLA / ACT style), on the robot or remote.

A ``SkillPolicy`` turns observations into *chunks* of joint-space commands for
one skill call. ``LearnedSkill`` executes them as a deployed runtime would:

* inference latency (measured by the policy, or sampled from its profile)
  elapses in simulated time while the arm keeps executing its current chunk;
* every chunk is anchored at the observation it was computed from, and an
  action older than the validity horizon (1 s by default) is dropped, never
  executed stale; with no valid action the arm holds its pose;
* a new inference is requested once half of the current chunk is used.

``UnavailablePolicy`` is what the demo uses for SmolVLA: the public checkpoint
is a single-arm Sawyer policy with a 4-D Cartesian action, so it is declared
unavailable for this two-arm robot instead of being given invented competence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .planning import LatencyModel
from .skills import ArmController, SkillResult
from .world import World

EMBODIMENT = "wheeled-bimanual-2x6dof-parallel-gripper"


@dataclass(frozen=True)
class ActionChunk:
    q: np.ndarray  # (n, 6) joint position targets for the arm
    grip: np.ndarray  # (n,) gripper opening targets [m]
    dt: float  # seconds between consecutive actions
    done: bool = False  # the policy considers the skill complete after this chunk


class SkillPolicy(Protocol):
    name: str

    def availability(self, embodiment: str) -> tuple[bool, str]:
        """Whether a checkpoint exists for this embodiment, and why not."""

    def start(self, skill_id: str, parameters: dict, arm: str) -> None:
        """Reset per skill call."""

    def infer(self, observation: dict) -> tuple[ActionChunk, float | None]:
        """Return the next chunk and the measured latency in seconds (None: use the profile)."""


@dataclass
class UnavailablePolicy:
    """A policy with no checkpoint for this embodiment (e.g. SmolVLA here)."""

    name: str
    reason: str

    def availability(self, embodiment: str) -> tuple[bool, str]:
        return False, self.reason

    def start(self, skill_id: str, parameters: dict, arm: str) -> None:
        raise RuntimeError(self.reason)

    def infer(self, observation: dict) -> tuple[ActionChunk, float | None]:
        raise RuntimeError(self.reason)


_REGISTRY: dict[str, object] = {}


def register_policy(name: str, factory) -> None:
    """Make a learned policy available by name: ``factory()`` returns a ``SkillPolicy``."""
    _REGISTRY[name] = factory


def make_policy(name: str, reason: str) -> SkillPolicy:
    factory = _REGISTRY.get(name)
    return factory() if factory is not None else UnavailablePolicy(name, reason)


@dataclass
class LearnedSkill:
    """Run one skill call with a learned policy; same interface as the scripted skills."""

    world: World
    arm: ArmController
    policy: SkillPolicy
    skill_id: str
    pill: int
    t0: float
    latency: LatencyModel
    rng: np.random.Generator
    validity_s: float = 1.0
    timeout_s: float = 30.0
    phase: str = "inference"
    result: SkillResult | None = None
    chunk: ActionChunk | None = None
    anchor: float = 0.0
    pending: tuple[ActionChunk, float, float] | None = None  # chunk, observation time, arrival time
    stale_dropped: int = 0
    executed: int = 0
    inference_ms: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        ok, reason = self.policy.availability(EMBODIMENT)
        if not ok:
            self.result = SkillResult("policy_unavailable", self.pill, self.arm.side, self.t0, self.t0, reason,
                                      self.skill_id)
            self.phase = "done"
            return
        self.policy.start(self.skill_id, {"pill": f"pill_{self.pill:02d}"}, self.arm.side)
        self._request(self.t0)

    @property
    def done(self) -> bool:
        return self.result is not None

    def observation(self) -> dict:
        w = self.world
        view = w.pill(self.pill)
        bottle, _ = w.bottle_pose()
        return {"q": self.arm.q.tolist(), "grip": self.arm.grip, "tcp": self.arm.tcp.tolist(),
                "pill": view.pos.tolist(), "pill_axis_yaw": view.axis_yaw, "bottle": bottle.tolist()}

    def _request(self, t: float) -> None:
        chunk, measured = self.policy.infer(self.observation())
        delay = measured if measured is not None else self.latency.sample(self.rng)
        self.inference_ms.append(delay * 1000)
        self.pending = (chunk, t, t + delay)

    def update(self, t: float, dt: float) -> None:
        arm = self.arm
        if self.done:
            arm.hold(dt)
            return
        if self.pending is not None and t >= self.pending[2]:
            chunk, observed_at, _ = self.pending
            self.pending = None
            # Slots whose time has passed are skipped; slots more than validity_s
            # after the observation are never valid.
            late = min(int((t - observed_at) / chunk.dt), len(chunk.q))
            valid_slots = min(len(chunk.q), int(math.floor(self.validity_s / chunk.dt + 1e-9)) + 1)
            if valid_slots - late > 0:
                self.chunk, self.anchor, self.phase = chunk, observed_at, "acting"
                self.stale_dropped += late
            else:
                self.stale_dropped += len(chunk.q)
                if chunk.done:
                    self._finish(t, "timeout")
                    return
                self._request(t)  # the whole chunk expired in flight: ask again from now
        index = None
        if self.chunk is not None:
            k = int((t - self.anchor) / self.chunk.dt)
            if k < len(self.chunk.q) and t - self.anchor <= self.validity_s:
                index = k
        if index is None:
            arm.hold(dt)  # no valid action: hold rather than act on stale commands
            if self.chunk is not None and self.chunk.done and self.pending is None:
                self._finish(t)
                return
        else:
            q = np.asarray(self.chunk.q[index], dtype=float)
            arm.q = q
            arm.pos, _ = arm.kin.fk(q)  # keep the commanded TCP for recording and planning
            arm.grip = float(self.chunk.grip[index])
            arm.servo.apply(self.world.data, arm.q, dt)
            self.world.data.ctrl[arm.servo.gripper] = arm.grip
            self.executed += 1
            used = (index + 1) / len(self.chunk.q)
            if self.pending is None and not self.chunk.done and used >= 0.5:
                self._request(t)
        if t - self.t0 > self.timeout_s:
            self._finish(t, "timeout")

    def stop(self, t: float, detail: str) -> None:
        """Protective stop (called after the arm was frozen)."""
        self._finish(t, "protective_stop")

    def _finish(self, t: float, status: str | None = None) -> None:
        view = self.world.pill(self.pill)
        status = status or ("placed" if view.in_bottle else "missed")
        detail = f"{self.executed} actions, {self.stale_dropped} stale actions dropped"
        self.result = SkillResult(status, self.pill, self.arm.side, self.t0, t, detail, self.skill_id)
        self.phase = "done"
