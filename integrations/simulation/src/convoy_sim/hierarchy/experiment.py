"""Independent wall-clock physics, bounded planner mailbox, and an edge executive.

All age/deadline comparisons use this host's monotonic clock. Remote model RTT is
measured around the call; remote timestamps are never subtracted. Late physics
slots are counted and skipped, never replayed in an unbounded catch-up burst.
"""

from __future__ import annotations

import json
import math
import platform
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path

import numpy as np

from convoy_sim.policies import validate_action
from convoy_sim.runner import _source_revision

from .scene import SawyerReference, SawyerScene


def distribution(values: list[float]) -> dict:
    return {"count": len(values), **{key: float(np.percentile(values, percentile)) if values else None
            for key, percentile in (("p50", 50), ("p95", 95), ("p99", 99), ("max", 100))}}


@dataclass(frozen=True)
class ExperimentConfig:
    mode: str = "async"
    seed: int = 0
    duration_s: float = 6.0
    task_target: str = "A"
    revise_at_s: float | None = 1.5
    revised_target: str = "B"
    refresh_interval_s: float = 0.75
    planner_timeout_s: float = 3.0
    max_proposal_age_s: float = 1.5
    goal_validity_s: float = 2.0
    command_validity_s: float = 0.1
    success_distance_m: float = 0.07
    success_ticks: int = 10
    frames: bool = False
    frame_interval_s: float = 0.25

    def __post_init__(self):
        if self.mode not in {"local", "blocking", "async"}:
            raise ValueError("mode must be local, blocking, or async")
        if self.task_target not in {"A", "B"} or self.revised_target not in {"A", "B"}:
            raise ValueError("only named targets A/B are supported")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or not 0 <= self.seed < 2**32:
            raise ValueError("seed must be an unsigned 32-bit integer")
        bounds = {"duration_s": (0.05, 60), "refresh_interval_s": (0.01, 30),
                  "planner_timeout_s": (0.01, 30), "max_proposal_age_s": (0.01, 30),
                  "goal_validity_s": (0.01, 60), "command_validity_s": (0.0125, 1),
                  "success_distance_m": (0.001, 0.075), "frame_interval_s": (0.05, 10)}
        for key, (minimum, maximum) in bounds.items():
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"{key} must be finite and between {minimum} and {maximum}")
        if self.revise_at_s is not None:
            if (isinstance(self.revise_at_s, bool) or not isinstance(self.revise_at_s, int | float)
                    or not math.isfinite(self.revise_at_s) or not 0 <= self.revise_at_s < self.duration_s):
                raise ValueError("task revision must occur inside the episode")
        if not isinstance(self.success_ticks, int) or isinstance(self.success_ticks, bool) or not 1 <= self.success_ticks <= 100:
            raise ValueError("success_ticks must be between 1 and 100")


@dataclass(frozen=True)
class PlanRequest:
    context: dict
    observed_ns: int
    submitted_ns: int
    expires_ns: int


class ProposalGate:
    """Authority is local: task revision, identity, age, expiry, and cancellation."""

    def __init__(self, max_age_s: float):
        self.revision = 0
        self.cancelled = False
        self.max_age_ns = round(max_age_s * 1e9)
        self.accepted: set[str] = set()

    def admit(self, request: PlanRequest, decision, now_ns: int) -> str:
        if self.cancelled:
            return "cancelled"
        if request.context["task_revision"] != self.revision:
            return "stale_task_revision"
        if any(type(getattr(decision, key, None)) is not type(request.context[key])
               or getattr(decision, key, None) != request.context[key]
               for key in ("request_id", "observation_seq", "task_revision")):
            return "identity_mismatch"
        if request.context["request_id"] in self.accepted:
            return "duplicate"
        if now_ns >= request.expires_ns:
            return "expired"
        if now_ns < request.observed_ns or now_ns - request.observed_ns > self.max_age_ns:
            return "stale_observation"
        if not ((getattr(decision, "skill", None) == "pick_place" and getattr(decision, "target", None) in {"A", "B"})
                or (getattr(decision, "skill", None) == "hold" and getattr(decision, "target", "invalid") is None)):
            return "invalid_decision"
        self.accepted.add(request.context["request_id"])
        return "accepted"


class PlannerCall:
    """A single daemon call and one-slot mailbox; cancellation fences publication."""

    def __init__(self, planner, request: PlanRequest):
        self.request = request
        self.mailbox: queue.Queue = queue.Queue(maxsize=1)
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.cancelled = False
        self.late_discarded = 0
        self.timeout_recorded = False

        def invoke():
            try:
                result, error = planner.plan(json.loads(json.dumps(request.context))), None
            except Exception as exc:
                result, error = None, exc
            completed = time.monotonic_ns()
            with self.lock:
                if self.cancelled:
                    self.late_discarded += 1
                else:
                    self.mailbox.put_nowait((result, error, completed))
            self.done.set()

        self.thread = threading.Thread(target=invoke, name="convoy-hierarchy-planner", daemon=True)
        self.thread.start()

    def poll(self):
        try:
            return self.mailbox.get_nowait()
        except queue.Empty:
            return None

    def cancel(self):
        with self.lock:
            self.cancelled = True
            while not self.mailbox.empty():
                self.mailbox.get_nowait()


def admit_command(command: dict | None, epoch: int, applied_ns: int, previous_grip: float):
    """Select the latest command at physical admission, after any lock wait.

    Fences and expired observations retain only the already applied gripper
    command; neither can repeat an old motion or adopt an unapplied grip target.
    """
    hold = ([0.0, 0.0, 0.0, previous_grip], None)
    if command is None or command["epoch"] != epoch:
        return *hold, "idle_hold", False
    if not command["captured_ns"] <= applied_ns < command["expires_ns"]:
        return *hold, "expired_hold", False
    return command["action"], command["target"], command["source"], True


class PhysicsLoop:
    """Physics owns the environment; the executive publishes only the latest command."""

    def __init__(self, config: ExperimentConfig, *, scene_factory=None):
        self.config = config
        self.scene_factory = scene_factory or SawyerScene
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="convoy-hierarchy-physics", daemon=True)
        self.latest = None
        self.command = None
        self.epoch = 0
        self.targets = {}
        self.started_ns = 0
        self.error = None
        self.trace: list[dict] = []
        self.images: list[tuple[str, object]] = []
        self.hierarchy = {"planner_state": "idle", "task_revision": 0,
                          "active_skill": "none", "target": None}

    def start(self):
        self.thread.start()
        if not self.ready.wait(20):
            self.stopped.set()
            raise RuntimeError("physics initialization exceeded its budget")
        if self.error:
            raise RuntimeError(self.error)
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            return dict(self.latest) if self.latest is not None else None

    def describe(self, values: dict):
        with self.lock:
            self.hierarchy = dict(values)

    def publish(self, action, target, observation_seq, captured_ns, *, source="reference", valid_until_ns=None):
        values = validate_action(action).tolist()
        expires = captured_ns + round(self.config.command_validity_s * 1e9)
        if valid_until_ns is not None:
            expires = min(expires, valid_until_ns)
        with self.lock:
            self.command = {"action": values, "target": target, "observation_seq": observation_seq,
                            "captured_ns": captured_ns, "expires_ns": expires,
                            "epoch": self.epoch, "source": source}

    def fence(self, *, revision=None):
        with self.lock:
            self.epoch += 1
            self.command = None
            if revision is not None:
                self.hierarchy.update(task_revision=revision, active_skill="none", target=None)

    def close(self):
        self.fence()
        self.stopped.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("physics failed to stop within its budget")

    def _run(self):
        scene = None
        try:
            maximum = math.ceil(self.config.duration_s / 0.0125)
            scene = self.scene_factory(self.config.seed, max_steps=maximum, frames=self.config.frames)
            period_ns = round(scene.control_period_s * 1e9)
            self.targets = scene.targets
            self.started_ns = time.monotonic_ns()
            state = scene.state()
            self.latest = {**state, "sequence": 0, "captured_ns": self.started_ns, "elapsed_s": 0.0,
                           "gripper_command": 0.0}
            initial = {**state, "type": "reset", "sequence": 0, "elapsed_s": 0.0, "simulated_s": 0.0,
                       "action": [0.0] * 4, "hierarchy": dict(self.hierarchy)}
            self.trace.append(initial)
            self.ready.set()
            slot, sequence, grip, next_frame = 1, 0, 0.0, 0.0
            while not self.stopped.is_set() and sequence < maximum:
                scheduled = self.started_ns + slot * period_ns
                if self.stopped.wait(max(0, (scheduled - time.monotonic_ns()) / 1e9)):
                    break
                # The lock fences task changes against the complete physical command.
                # Only short MuJoCo stepping is inside it, never planner/network I/O.
                with self.lock:
                    command = self.command
                    hierarchy = dict(self.hierarchy)
                    applied_ns = time.monotonic_ns()
                    if self.stopped.is_set() or (applied_ns - self.started_ns) / 1e9 >= self.config.duration_s:
                        break
                    action, target, source, valid = admit_command(command, self.epoch, applied_ns, grip)
                    outcome = scene.step(action, target)
                    grip = action[3]
                    sequence += 1
                    captured = time.monotonic_ns()
                    elapsed = (captured - self.started_ns) / 1e9
                    self.latest = {**outcome, "sequence": sequence, "captured_ns": captured, "elapsed_s": elapsed,
                                   "gripper_command": grip}
                raw_lag_ms = max(0, (applied_ns - scheduled) / 1e6)
                dropped = max(0, (applied_ns - scheduled) // period_ns)
                slot += dropped
                image_name = None
                render_ms = 0.0
                if self.config.frames and elapsed >= next_frame:
                    before = time.monotonic_ns()
                    image_name = f"frames/{sequence:06d}.png"
                    self.images.append((image_name, scene.render().copy()))
                    render_ms = (time.monotonic_ns() - before) / 1e6
                    next_frame = elapsed + self.config.frame_interval_s
                hierarchy["physics_lag_ms"] = raw_lag_ms
                self.trace.append({**outcome, "type": "step", "sequence": sequence, "elapsed_s": elapsed,
                                   "simulated_s": sequence * scene.control_period_s,
                                   "action": action, "action_source": source,
                                   "action_applied_elapsed_s": (applied_ns - self.started_ns) / 1e9,
                                   "command_observation_seq": command["observation_seq"] if valid else None,
                                   "observation_to_action_ms": (applied_ns - command["captured_ns"]) / 1e6 if valid else None,
                                   "dispatch_lag_ms": raw_lag_ms,
                                   "completion_lag_ms": (captured - scheduled) / 1e6,
                                   "render_ms": render_ms, "dropped_scheduler_slots": int(dropped),
                                   "image": image_name, "hierarchy": hierarchy})
                slot += 1
                if outcome["terminated"] or outcome["truncated"]:
                    break
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"[:1000]
        finally:
            if scene is not None:
                try:
                    scene.close()
                except Exception as error:
                    self.error = self.error or f"scene close failed: {type(error).__name__}"
            self.ready.set()
            self.stopped.set()


def _write(path: Path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def run_episode(config: ExperimentConfig, planner, output: Path, *, stop_event=None, scene_factory=None,
                provenance: dict | None = None, cleanup_calls: list | None = None) -> dict:
    """Run one bounded real-time episode; ``planner.plan(context)`` executes off-thread.

    Local mode requires a local planner supplied by the caller. Fault injection
    belongs to the remote planner wrapper, never to the physics or S1 controller.
    Evidence is written into a new directory and cannot overwrite previous runs.
    ``cleanup_calls`` receives any outstanding fenced call; a matrix can await
    its ``done`` event between trials without delaying local cancellation.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    stop_event = stop_event or threading.Event()
    physics = PhysicsLoop(config, scene_factory=scene_factory)
    reference = SawyerReference()
    gate = ProposalGate(config.max_proposal_age_s)
    events, controller_gaps, controller_ages, planner_latencies = [], [], [], []
    pending = active = None
    target, revised = config.task_target, config.revise_at_s is None
    next_plan_ns, previous_control_ns, previous_seq = 0, None, -1
    planner_state, last_latency, last_age = "idle", None, None
    stable, terminal, rejected = 0, "duration_completed", 0
    error = None
    metadata = {"schema_version": 1, "experiment": "sawyer-system1-system2-v1", "config": asdict(config),
                "execution_mode": "independent_monotonic_wall_clock_physics",
                "evidence_scope": "simulated_physics_with_privileged_state",
                "policy": {"kind": "scripted_reference", "runtime": "metaworld-SawyerPickPlaceV3Policy",
                           "learned": False, "observation": "privileged-metaworld-state39",
                           "action": "normalized-cartesian-delta-and-gripper4"},
                "environment": {"name": "pick-place-v3", "custom_named_targets": True,
                                "control_period_s": 0.0125, "custom_horizon_steps": math.ceil(config.duration_s / .0125)},
                "success_criterion": {"kind": "puck_in_final_target_region", "distance_m": config.success_distance_m,
                                      "consecutive_observed_snapshots": config.success_ticks,
                                      "after_final_task_revision": True, "requires_release": False,
                                      "upstream_benchmark_distance_m": .07},
                "clock": "one host monotonic clock; no cross-host timestamp subtraction",
                "planner": {"backend": getattr(planner, "backend", type(planner).__name__)},
                "provenance": provenance or {},
                "packages": {name: version(name) for name in ("metaworld", "mujoco", "numpy")},
                "python": platform.python_version(), "platform": platform.platform(), "source": _source_revision()}
    _write(output / "metadata.json", metadata)

    def emit(kind, now_ns=None, **values):
        now_ns = now_ns or time.monotonic_ns()
        events.append({"type": kind, "elapsed_s": (now_ns - physics.started_ns) / 1e9 if physics.started_ns else 0,
                       "task_revision": gate.revision, **values})

    def revise_task(now_ns):
        nonlocal revised, target, active, stable, next_plan_ns
        if not revised and now_ns >= physics.started_ns + round(config.revise_at_s * 1e9):
            revised, target, active = True, config.revised_target, None
            gate.revision += 1
            physics.fence(revision=gate.revision)
            stable = 0
            next_plan_ns = 0
            emit("task_revised", target=target, pending_request_id=pending.request.context["request_id"] if pending else None)

    try:
        physics.start()
        metadata["environment"]["targets"] = physics.targets
        _write(output / "metadata.json", metadata)
        while not physics.stopped.is_set():
            now = time.monotonic_ns()
            if stop_event.is_set():
                terminal = "cancelled"
                break
            revise_task(now)
            now = time.monotonic_ns()
            if active is not None and now >= active[1]:
                emit("goal_expired", now, request_id=active[0].request_id)
                active = None
                physics.fence()
                next_plan_ns = 0
            if pending is not None:
                reply = pending.poll()
                if reply is not None:
                    result, failure, completed_ns = reply
                    request = pending.request
                    last_latency = (completed_ns - request.submitted_ns) / 1e6
                    # A physics fence may have waited. Authority/age use fresh
                    # admission time, never the earlier loop timestamp.
                    revise_task(time.monotonic_ns())
                    now = time.monotonic_ns()
                    last_age = (now - request.observed_ns) / 1e6
                    planner_latencies.append(last_latency)
                    if failure is not None:
                        planner_state = "error"
                        emit("planner_error", now, request_id=request.context["request_id"],
                             code=getattr(failure, "code", type(failure).__name__), message=str(failure)[:500],
                             latency_ms=last_latency, model_rtt_ms=getattr(failure, "model_rtt_ms", None),
                             injected_delay_ms=getattr(failure, "injected_delay_ms", 0),
                             fault_mode=getattr(failure, "fault_mode", None))
                    else:
                        revise_task(time.monotonic_ns())
                        now = time.monotonic_ns()
                        last_age = (now - request.observed_ns) / 1e6
                        reason = gate.admit(request, result.decision, now)
                        planner_state = "accepted" if reason == "accepted" else "stale"
                        emit("planner_result", now, request_id=request.context["request_id"],
                             admission=reason, decision=asdict(result.decision), latency_ms=last_latency,
                             observation_age_ms=last_age, model_rtt_ms=result.model_rtt_ms,
                             injected_delay_ms=result.injected_delay_ms, fault_mode=result.fault_mode,
                             backend=result.backend)
                        if reason == "accepted":
                            changed = active is None or (active[0].skill, active[0].target) != (result.decision.skill, result.decision.target)
                            active = (result.decision, now + round(config.goal_validity_s * 1e9))
                            if changed:
                                physics.fence()
                                stable = 0
                        else:
                            rejected += 1
                    pending = None
                    next_plan_ns = now + round(config.refresh_interval_s * 1e9)
                    if active is None:
                        next_plan_ns = now + round(min(.1, config.refresh_interval_s) * 1e9)
                elif now >= pending.request.expires_ns and not pending.timeout_recorded:
                    pending.timeout_recorded = True
                    planner_state = "error"
                    emit("planner_timeout", now, request_id=pending.request.context["request_id"])
                    # Keep the one request in flight. No retry backlog is allowed.
            snapshot = physics.snapshot()
            if snapshot is None:
                raise RuntimeError("physics produced no observation")
            if pending is None and now >= next_plan_ns:
                revise_task(time.monotonic_ns())
                now = time.monotonic_ns()
                context = {"request_id": uuid.uuid4().hex, "observation_seq": snapshot["sequence"],
                           "task_revision": gate.revision, "instruction": f"Pick and place the puck at target {target}.",
                           "task_target": target, "available_skills": ["pick_place", "hold"],
                           "available_targets": ["A", "B"], "targets": physics.targets,
                           "robot": {"hand_position": snapshot["observation"][:3], "gripper": snapshot["observation"][3]},
                           "puck": {"position": snapshot["observation"][4:7]},
                           "current_skill": active[0].skill if active else None,
                           "current_target": active[0].target if active else None}
                request = PlanRequest(context, snapshot["captured_ns"], now, now + round(config.planner_timeout_s * 1e9))
                if config.mode == "blocking":
                    physics.fence()
                pending = PlannerCall(planner, request)
                planner_state = "pending"
                emit("planner_requested", now, **context,
                     expires_elapsed_s=(request.expires_ns - physics.started_ns) / 1e9)
            revise_task(time.monotonic_ns())
            if active is not None and time.monotonic_ns() >= active[1]:
                emit("goal_expired", request_id=active[0].request_id)
                active = None
                physics.fence()
                next_plan_ns = 0
            blocked = config.mode == "blocking" and pending is not None
            if snapshot["sequence"] != previous_seq:
                previous_seq = snapshot["sequence"]
                if active is not None and not blocked:
                    before = time.monotonic_ns()
                    decision = active[0]
                    action = (reference.action(snapshot["observation"], physics.targets[decision.target])
                              if decision.skill == "pick_place" else [0, 0, 0, snapshot["gripper_command"]])
                    physics.publish(action, decision.target, snapshot["sequence"], snapshot["captured_ns"],
                                    source="reference" if decision.skill == "pick_place" else "requested_hold",
                                    valid_until_ns=active[1])
                    controller_ages.append((before - snapshot["captured_ns"]) / 1e6)
                    if previous_control_ns is not None:
                        controller_gaps.append((before - previous_control_ns) / 1e6)
                    previous_control_ns = before
                distance = float(np.linalg.norm(np.asarray(snapshot["observation"][4:7]) - physics.targets[target]))
                stable = stable + 1 if revised and distance <= config.success_distance_m else 0
                if stable >= config.success_ticks:
                    terminal = "task_success"
                    break
            physics.describe({"planner_state": planner_state, "task_revision": gate.revision,
                              "active_skill": active[0].skill if active else "none",
                              "target": active[0].target if active else None,
                              "planner_latency_ms": last_latency, "observation_age_ms": last_age})
            stop_event.wait(.002)
        if physics.error:
            raise RuntimeError(physics.error)
    except KeyboardInterrupt:
        terminal = "cancelled"
    except Exception as exc:
        terminal, error = "error", f"{type(exc).__name__}: {exc}"[:1000]
    finally:
        gate.cancelled = True
        if pending is not None:
            pending.cancel()
            if cleanup_calls is not None:
                cleanup_calls.append(pending)
            emit("request_cancelled", request_id=pending.request.context["request_id"], reason="mission_closed")
        try:
            physics.close()
        except Exception as exc:
            terminal, error = "error", f"{type(exc).__name__}: {exc}"[:1000]
        emit("mission_closed", state=terminal)
    steps = [step for step in physics.trace if step["type"] == "step"]
    final = physics.snapshot()
    wall_s = (time.monotonic_ns() - physics.started_ns) / 1e9 if physics.started_ns else 0
    final_distance = (float(np.linalg.norm(np.asarray(final["observation"][4:7]) - physics.targets[target]))
                      if final is not None else None)
    summary = {"schema_version": 1, "status": terminal, "error": error, "mode": config.mode,
               "seed": config.seed, "task_revision": gate.revision, "final_target": target,
               "task_success": terminal == "task_success", "final_target_distance_m": final_distance,
               "final_benchmark_success": bool(final.get("success", False)) if final else False,
               "final_grasp_success": bool(final.get("grasp_success", False)) if final else False,
               "final_goal_target": final.get("goal_target") if final else None,
               "final_goal_position": final.get("goal_position") if final else None,
               "physics_steps": len(steps), "wall_duration_s": wall_s,
               "simulated_duration_s": len(steps) * .0125,
               "simulation_wall_lag_s": max(0, wall_s - len(steps) * .0125),
               "dropped_scheduler_slots": sum(step["dropped_scheduler_slots"] for step in steps),
               "hold_ticks": sum(step["action_source"] != "reference" for step in steps),
               "fallback_ticks": sum(step["action_source"] in {"idle_hold", "expired_hold"} for step in steps),
               "physics_dispatch_lag_ms": distribution([step["dispatch_lag_ms"] for step in steps]),
               "physics_completion_lag_ms": distribution([step["completion_lag_ms"] for step in steps]),
               "observation_to_action_ms": distribution([step["observation_to_action_ms"] for step in steps if step["observation_to_action_ms"] is not None]),
               "controller_gap_ms": distribution(controller_gaps), "controller_observation_age_ms": distribution(controller_ages),
               "planner_latency_ms": distribution(planner_latencies),
               "planner_requests": sum(event["type"] == "planner_requested" for event in events),
               "accepted_plans": sum(event.get("admission") == "accepted" for event in events),
               "rejected_plans": rejected, "planner_timeouts": sum(event["type"] == "planner_timeout" for event in events),
               "cancelled_inflight_requests": sum(event["type"] == "request_cancelled" for event in events),
               "max_planner_inflight": int(any(event["type"] == "planner_requested" for event in events)),
               "measurement_source": "actual local monotonic wall clock; independent MuJoCo physics; scripted privileged-state reference policy"}
    _write(output / "summary.json", summary)
    for filename, rows in (("steps.jsonl", physics.trace), ("planner-events.jsonl", events)):
        (output / filename).write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in rows))
    if physics.images:
        from PIL import Image

        (output / "frames").mkdir()
        for name, pixels in physics.images:
            Image.fromarray(pixels).save(output / name)
    return summary
