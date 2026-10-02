"""Independent, wall-clock-paced joint physics; policy latency never pauses this process.

All capture/admission times use one host monotonic clock. This is a measured
software simulation, not a hard-real-time or physical safety controller.
"""
from __future__ import annotations

import math
import multiprocessing
import os
import time
from dataclasses import asdict

from convoy_agent.coordinator.engine import TimedStepResult, TimingRejected
from convoy_contracts.registered import validate_action


def distribution(values):
    ordered = sorted(values)
    return {"count": len(ordered), **{name: ordered[math.ceil(len(ordered) * q) - 1] if ordered else None
            for name, q in (("p50", .5), ("p95", .95), ("p99", .99), ("max", 1))}}


def _physics(connection, manifest, xml, assets, seed):
    from .registered import JointAdapter

    driver = None
    try:
        driver = JointAdapter(manifest, xml, assets)
        observation = driver.reset(seed)
        period = driver.control_period_s
        timing = manifest["execution"]["timing"]
        started = captured = time.monotonic_ns()
        maximum = manifest["execution"]["max_steps"]
        reward_sum = 0.0
        ticks = fallback_ticks = steady_fallback_ticks = applied = rejected = 0
        last_action = None
        valid_until = 0
        hold = observation["positions"][:]
        in_fallback = True
        dispatch_lag, completion_lag, ages = [], [], []
        outcome = {"observation": observation, "reward": 0, "success": False, "terminated": False, "truncated": False}
        terminal = False
        fault = None
        pending = None
        waiting_observation = None
        issued_capture = None
        finished = None
        commands = set()
        connection.send({"ready": True, "observation": observation})

        def respond_observation(request):
            nonlocal issued_capture
            issued_capture = captured
            connection.send({"observation": outcome["observation"], "captured_ns": captured, "tick": ticks,
                             "terminal": outcome if terminal else None, "fault": fault})

        def snapshot():
            return {"physics_pid": os.getpid(), "physics_control_steps": ticks,
                    "simulated_duration_s": ticks * period,
                    "physics_wall_s": ((finished or time.monotonic_ns()) - started) / 1e9,
                    "physics_fault": fault, "final_success": outcome["success"], "reward_sum": reward_sum, "fallback_ticks": fallback_ticks,
                    "steady_fallback_ticks": steady_fallback_ticks, "applied_actions": applied,
                    "rejected_actions": rejected, "dispatch_lag_ms": distribution(dispatch_lag),
                    "completion_lag_ms": distribution(completion_lag), "observation_to_action_ms": distribution(ages)}

        while True:
            next_tick = started + round((ticks + 1) * period * 1e9)
            timeout = .05 if terminal else max(0, min(.05, (next_tick - time.monotonic_ns()) / 1e9))
            if connection.poll(timeout):
                request = connection.recv()
                if request["kind"] == "close":
                    # Cleanup must not hide a scheduler stall just because no
                    # subsequent observation was requested after inference failed.
                    finished = finished or time.monotonic_ns()
                    if not terminal and (finished - next_tick) / 1e6 > timing["max_physics_lag_ms"]:
                        fault = "physics_dispatch_lag"
                        dispatch_lag.append((finished - next_tick) / 1e6)
                    connection.send(snapshot())
                    break
                if request["kind"] == "observe":
                    if terminal or (ticks > request["after_tick"] and time.monotonic_ns() < next_tick):
                        respond_observation(request)
                    else:
                        waiting_observation = request
                elif request["kind"] == "action":
                    if terminal:
                        connection.send({"outcome": outcome, "applied": False})
                    else:
                        validate_action(request["action"], manifest)
                        if pending is not None or request["command_id"] in commands or request["captured_ns"] != issued_capture:
                            raise ValueError("invalid action observation or repeated command")
                        pending = request
            if terminal or time.monotonic_ns() < next_tick:
                continue
            dispatched = time.monotonic_ns()
            lag_ms = (dispatched - next_tick) / 1e6
            dispatch_lag.append(lag_ms)
            if lag_ms > timing["max_physics_lag_ms"]:
                fault, terminal, finished = "physics_dispatch_lag", True, dispatched
                outcome.update(truncated=True)
            else:
                accepted = False
                if pending:
                    deadline = min(pending["deadline_ns"], pending["captured_ns"] + timing["max_observation_age_ms"] * 1_000_000)
                    if dispatched >= deadline or dispatched < pending["captured_ns"]:
                        rejected += 1
                        connection.send({"rejected": True})
                        pending = None
                    else:
                        last_action, valid_until = pending["action"], deadline
                        ages.append((dispatched - pending["captured_ns"]) / 1e6)
                        commands.add(pending["command_id"])
                        applied += 1
                        accepted = True
                fallback = last_action is None or dispatched >= valid_until
                if fallback:
                    if not in_fallback:
                        hold = [max(bounds[0], min(bounds[1], value)) for value, bounds in zip(
                            outcome["observation"]["positions"], manifest["interface"]["action_bounds"], strict=True)]
                    fallback_ticks += 1
                    steady_fallback_ticks += int(applied > 0)
                action = hold if fallback else last_action
                in_fallback = fallback
                outcome = asdict(driver.step(action, f"physics:{ticks}"))
                ticks += 1
                reward_sum += outcome["reward"]
                captured = time.monotonic_ns()
                completion_ms = (captured - next_tick) / 1e6
                completion_lag.append(completion_ms)
                if completion_ms > timing["max_physics_lag_ms"]:
                    fault = "physics_completion_lag"
                terminal = bool(fault or outcome["terminated"] or ticks >= maximum)
                if terminal:
                    finished = captured
                    outcome["truncated"] = not outcome["terminated"]
                if accepted:
                    connection.send({"outcome": outcome, "applied": True})
                    pending = None
            if terminal and pending:
                connection.send({"outcome": outcome, "applied": False})
                pending = None
            if waiting_observation:
                respond_observation(waiting_observation)
                waiting_observation = None
    except (EOFError, BrokenPipeError):
        pass
    except Exception as error:
        try:
            connection.send({"error": type(error).__name__})
        except (EOFError, BrokenPipeError):
            pass
    finally:
        if driver:
            driver.close()
        connection.close()


class RealtimeJointAdapter:
    def __init__(self, manifest, xml, assets):
        self.manifest, self.xml, self.assets = manifest, xml, assets
        self.control_period_s = 1 / manifest["interface"]["control_rate_hz"]
        self.process = self.connection = None
        self.tick = -1
        self.captured_ns = None
        self.measured = None
        self.waits, self.response_ages = [], []
        self.deadline_misses = 0

    def _receive(self, timeout=2):
        if not self.connection.poll(timeout):
            raise RuntimeError("physics process response exceeded its budget")
        value = self.connection.recv()
        if "error" in value:
            raise RuntimeError("physics process failed")
        return value

    def reset(self, seed):
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_physics, args=(child, self.manifest, self.xml, self.assets, seed))
        self.process.start()
        child.close()
        value = self._receive(timeout=20)
        if value.get("ready") is not True:
            raise RuntimeError("physics process did not become ready")
        return value["observation"]

    def capture(self):
        self.connection.send({"kind": "observe", "after_tick": self.tick})
        value = self._receive()
        self.tick, self.captured_ns = value["tick"], value["captured_ns"]
        if value["fault"]:
            raise RuntimeError("physics could not maintain the requested timing")
        terminal = TimedStepResult(**value["terminal"], applied=False) if value["terminal"] else None
        return value["observation"], self.captured_ns, terminal

    def step_timed(self, action, command_id, deadline_ns):
        self.connection.send({"kind": "action", "action": action, "command_id": command_id,
                              "captured_ns": self.captured_ns, "deadline_ns": deadline_ns})
        value = self._receive()
        if value.get("rejected"):
            raise TimingRejected("action exceeded its observation-age budget before physics admission")
        return TimedStepResult(**value["outcome"], applied=value["applied"])

    def record_policy_wait(self, started_ns, finished_ns, captured_ns, received):
        self.waits.append((finished_ns - started_ns) / 1e6)
        if received:
            self.response_ages.append((finished_ns - captured_ns) / 1e6)

    def record_deadline_miss(self):
        self.deadline_misses += 1

    def close(self):
        if self.process is None:
            return
        clean = False
        try:
            if self.process.is_alive():
                self.connection.send({"kind": "close"})
                value = self._receive()
                if "physics_control_steps" not in value:
                    raise RuntimeError("physics cleanup response did not contain timing evidence")
                self.measured = value
                self.process.join(timeout=2)
                clean = not self.process.is_alive()
        finally:
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=2)
            self.connection.close()
        if not clean or self.process.is_alive():
            raise RuntimeError("physics cleanup was not acknowledged")

    def execution_summary(self):
        if self.measured is None:
            raise RuntimeError("timing evidence unavailable")
        evidence = self.measured
        reasons = []
        if evidence["physics_fault"]:
            reasons.append(evidence["physics_fault"])
        if len(self.waits) > len(self.response_ages):
            reasons.append("policy_response_unavailable")
        if self.deadline_misses or evidence["rejected_actions"]:
            reasons.append("policy_deadline_missed")
        if evidence["steady_fallback_ticks"]:
            reasons.append("policy_actions_expired_between_results")
        enough = evidence["physics_control_steps"] >= 200 and evidence["applied_actions"] >= 10
        return {"physics_control_steps": evidence["physics_control_steps"],
                "final_success": evidence["final_success"], "ever_success": evidence["final_success"], "reward_sum": evidence["reward_sum"],
                "simulated_duration_s": evidence["simulated_duration_s"],
                "timing": {**evidence, "status": "failed" if reasons else "observed_deadlines_met" if enough else "insufficient_evidence",
                           "reasons": reasons, "deadline_misses": self.deadline_misses,
                           "contract": {**self.manifest["execution"]["timing"], "minimum_physics_steps": 200, "minimum_applied_actions": 10},
                           "policy_wait_ms": distribution(self.waits), "observation_to_result_ms": distribution(self.response_ages),
                           "clock_scope": "one host monotonic clock; policy wait includes transport and queueing",
                           "evidence_scope": "registered joint-state simulation; not physical or learned-policy qualification"}}
