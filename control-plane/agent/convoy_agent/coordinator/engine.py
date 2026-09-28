"""Single-owner execution loop with separate management polling during inference.

The adapter is called only by the supervisor thread. A simulator can run in
lockstep; monotonic deadlines still reject late decisions, but this is not a
qualification of real-time physical control.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from convoy_contracts.execution import (
    PROFILE,
    canonical_digest,
    validate_identity,
    validate_manifest,
    validate_request,
    validate_result,
)

from .journal import ExecutionJournal
from .transport import RemoteError

log = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled", "unknown"}


@dataclass(frozen=True)
class StepResult:
    observation: list[float]
    reward: float
    success: bool
    terminated: bool = False
    truncated: bool = False


class Adapter(Protocol):
    control_period_s: float

    def reset(self, seed: int) -> list[float]: ...
    def step(self, action: list[float], command_id: str) -> StepResult: ...
    def close(self) -> None: ...


class Cancelled(Exception):
    pass


class DecisionExpired(Exception):
    pass


class ExecutionUnknown(Exception):
    pass


class Coordinator:
    def __init__(
        self, *, robot_id: str, device_id: str, journal: ExecutionJournal,
        control, worker, adapter_factory: Callable[[], Adapter],
        poll_s: float = 0.1, clock_uncertainty_s: float = 0.25,
    ):
        if not math.isfinite(poll_s) or poll_s <= 0:
            raise ValueError("poll interval must be positive and finite")
        if not math.isfinite(clock_uncertainty_s) or clock_uncertainty_s < 0:
            raise ValueError("clock uncertainty must be nonnegative and finite")
        self.robot_id, self.device_id = robot_id, device_id
        self.journal, self.control, self.worker = journal, control, worker
        self.adapter_factory = adapter_factory
        self.poll_s, self.clock_uncertainty_s = poll_s, clock_uncertainty_s
        self.stop = threading.Event()
        self._submission = threading.RLock()
        self._cancel_reason: str | None = None
        self._active_mission: str | None = None
        self._outstanding: dict | None = None
        self._inference_thread: threading.Thread | None = None
        self._ready: tuple[str, int, str] | None = None
        self.prefix = f"/api/agent/v1/robots/{robot_id}"
        journal.recover()

    def request_stop(self) -> None:
        self.stop.set()

    def cancel(self, reason: str, mission_id: str | None = None) -> None:
        with self._submission:
            if mission_id is not None and mission_id != self._active_mission:
                return
            self._cancel_reason = reason

    def _desired(self) -> dict:
        value = self.control.get(self.prefix + "/desired")
        if value["robot"]["id"] != self.robot_id or value["robot"]["device_id"] != self.device_id:
            raise ValueError("control plane returned the wrong robot/device binding")
        if value["robot"]["profile"] != PROFILE:
            raise ValueError("unsupported robot execution profile")
        return value

    def _report(self, mission_id: str, payload: dict):
        return self.control.post(self.prefix + f"/missions/{mission_id}/report", payload)

    def _claim(self, mission_id: str, identity: dict):
        return self.control.post(self.prefix + f"/missions/{mission_id}/claim", {
            key: identity[key] for key in ("boot_id", "incarnation", "authority_epoch")
        })

    def _flush_reports(self, desired: dict) -> bool:
        """Retry the exact durable payload. No retry path calls a robot adapter."""
        for row in self.journal.pending():
            payload = json.loads(row["report_json"])
            try:
                self._report(row["id"], payload)
            except RemoteError as error:
                mission = desired.get("mission")
                if error.status == 409 and mission and mission["id"] == row["id"]:
                    if mission["state"] in TERMINAL:
                        # A previous terminal HTTP response could have been lost.
                        # The server's immutable terminal outcome wins; never rerun.
                        self.journal.acknowledge(row["id"])
                        continue
                    if payload["state"] == "unknown" and mission["state"] in {"requested", "starting"}:
                        try:
                            self._claim(row["id"], payload["identity"])
                        except RemoteError as claim_error:
                            if claim_error.status == 410:
                                if mission.get("identity") is None:
                                    # Backend expires an unclaimed request atomically.
                                    self.journal.acknowledge(row["id"])
                                    continue
                                # An admitted but expired claim remains unresolved.
                                # Report the durable previous identity as unknown.
                                self._report(row["id"], payload)
                                self.journal.acknowledge(row["id"])
                                continue
                            raise
                        self._report(row["id"], payload)
                    elif (payload["state"] == "unknown" and mission["state"] == "cancel_requested"
                          and mission.get("identity") is None):
                        # Lost claim response reconciled as never admitted. The
                        # durable unknown record remains; remote cancellation is
                        # acknowledged without ever constructing an adapter.
                        self._report(row["id"], {"identity": None, "state": "cancelled",
                                                "detail": "recovery confirmed cancellation before admission",
                                                "summary": {"steps": 0, "recovered_after_restart": True}})
                    else:
                        raise
                else:
                    raise
            self.journal.acknowledge(row["id"])
        return not self.journal.pending()

    def tick(self) -> None:
        desired = self._desired()
        if not self._flush_reports(desired):
            return
        deployment = desired.get("deployment")
        mission = desired.get("mission")
        if mission:
            recorded = self.journal.get(mission["id"])
            remotely_resolved = mission["state"] in {"completed", "failed", "cancelled"}
            if mission["state"] == "unknown" or (recorded and recorded["state"] == "unknown"
                                                  and not (recorded["reported"] and remotely_resolved)):
                return  # explicit external recovery is required
            if recorded and recorded["report_json"] is None:
                return
            if (recorded and recorded["reported"]) or mission["state"] in {"completed", "failed", "cancelled"}:
                # A retained terminal mission fences replay, not future releases.
                mission = None
        if mission and mission["state"] == "cancel_requested":
            # A cancelled queued mission has no authority identity to echo.
            self.journal.prepare(mission["id"], None)
            self.journal.finish(mission["id"], {
                "identity": None, "state": "cancelled", "detail": "cancelled before local admission",
                "summary": {"steps": 0, "execution_mode": "lockstep_offline"},
            })
            self._flush_reports(desired)
            return
        if mission and mission["state"] not in {"requested"}:
            return  # do not seize another incarnation's execution
        if deployment is None:
            return
        release = deployment["release"]
        manifest = validate_manifest(release["manifest"])
        if canonical_digest(manifest) != release["digest"]:
            raise ValueError("release content does not match immutable digest")
        ready_key = (deployment["id"], deployment["generation"], release["digest"])
        if self._ready != ready_key:
            try:
                probe = self.worker.probe(release["digest"], manifest["profile"])
                if probe.get("release_digest") != release["digest"] or probe.get("profile") != PROFILE:
                    raise ValueError("worker readiness identity mismatch")
            except Exception:
                self.control.post(self.prefix + f"/deployments/{deployment['id']}/report", {
                    "generation": deployment["generation"], "state": "blocked",
                    "release_digest": release["digest"], "detail": "inference worker readiness probe failed",
                })
                raise
            if mission is None:
                self.control.post(self.prefix + f"/deployments/{deployment['id']}/report", {
                    "generation": deployment["generation"], "state": "ready", "release_digest": release["digest"],
                })
            elif deployment.get("state") != "ready":
                return  # readiness may only be reported while idle
            self._ready = ready_key
            # Fresh desired state avoids using a request cancelled during the probe.
            return
        if mission:
            if self._inference_thread and self._inference_thread.is_alive():
                return  # no unbounded abandoned requests after cancellation/timeouts
            if (mission["deployment_id"] != deployment["id"] or
                    mission["generation"] != deployment["generation"] or
                    mission["release_digest"] != release["digest"]):
                raise ValueError("mission does not match the ready deployment")
            self._run_mission(mission, manifest)

    def run(self, *, once: bool = False) -> None:
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as error:
                # Don't print untrusted remote payloads or credentials.
                log.warning("coordinator iteration deferred (%s)", type(error).__name__)
            if once:
                break
            self.stop.wait(self.poll_s)

    def _monitor(self, mission: dict, done: threading.Event) -> None:
        while not done.wait(self.poll_s):
            try:
                desired = self._desired()
                if done.is_set():
                    return
                current = desired.get("mission")
                deployment = desired.get("deployment")
                if not current or current["id"] != mission["id"] or current["state"] in TERMINAL:
                    self.cancel("management authority changed", mission["id"])
                elif current["state"] == "cancel_requested":
                    self.cancel("cancellation acknowledged locally", mission["id"])
                elif not deployment or deployment["generation"] != mission["generation"]:
                    self.cancel("deployment generation changed", mission["id"])
            except RemoteError as error:
                if error.status in {401, 403, 404, 409}:
                    self.cancel("management authorization lost", mission["id"])
            except Exception:
                # A management outage does not refresh or extend the mission's
                # fixed authority deadline. Execution ends at that deadline.
                pass

    def _check_live(self, deadline_ns: int) -> None:
        if self.stop.is_set() or self._cancel_reason:
            raise Cancelled(self._cancel_reason or "coordinator stopping")
        if time.monotonic_ns() >= deadline_ns:
            raise DecisionExpired("local decision deadline expired")

    def _decide(self, request: dict, grant: str) -> dict:
        """Cancellation/deadline releases the supervisor even during blocked I/O.

        Only the supervisor can submit to the adapter. A late transport result
        stays in this call's private queue and cannot execute an action.
        """
        inbox: queue.Queue = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                inbox.put((True, self.worker.decide(request, grant)))
            except Exception as error:
                inbox.put((False, error))

        self._inference_thread = threading.Thread(target=invoke, daemon=True)
        self._inference_thread.start()
        while True:
            self._check_live(request["deadline_monotonic_ns"])
            try:
                okay, result = inbox.get(timeout=min(0.025, max(
                    0.000001, (request["deadline_monotonic_ns"] - time.monotonic_ns()) / 1e9)))
            except queue.Empty:
                continue
            if not okay:
                raise result
            return result

    def _apply(self, adapter: Adapter, request: dict, raw_result: dict) -> StepResult:
        result = validate_result(raw_result)
        with self._submission:
            if self._outstanding != request:
                raise ValueError("request was already consumed or is not outstanding")
            for key in ("identity", "request_id", "observation_id", "sequence", "deadline_monotonic_ns"):
                if result[key] != request[key]:
                    raise ValueError(f"inference result {key} does not match request")
            self._check_live(request["deadline_monotonic_ns"])
            self._outstanding = None
            self.journal.intend(request, result)
            try:
                self._check_live(request["deadline_monotonic_ns"])
            except (Cancelled, DecisionExpired):
                self.journal.command_outcome(request, "not_applied")
                raise
            try:
                outcome = adapter.step(result["action"], request["request_id"])
                if (len(outcome.observation) != 39 or
                        not all(math.isfinite(v) for v in outcome.observation) or
                        not math.isfinite(outcome.reward)):
                    raise ValueError("adapter returned an invalid physical state")
                self.journal.command_outcome(request, "applied", asdict(outcome))
                return outcome
            except Exception as error:
                # The step may have happened. Preserve the intent and stop; an
                # uncertain physical command must never be blindly retried.
                raise ExecutionUnknown("adapter command outcome is uncertain") from error

    def _run_mission(self, mission: dict, manifest: dict) -> None:
        identity = self.journal.identity(mission, self.device_id, self.robot_id)
        validate_identity(identity)
        self.journal.prepare(mission["id"], identity)
        adapter = None
        monitor = None
        done = threading.Event()
        self._cancel_reason, self._outstanding = None, None
        self._active_mission = mission["id"]
        summary = {
            "execution_mode": "lockstep_offline", "evidence_scope": "simulated_physics_with_privileged_state",
            "seed": mission["seed"], "steps": 0, "ever_success": False, "final_success": False,
            "reward_sum": 0.0, "simulated_duration_s": 0.0, "policy_runtime": manifest["policy"]["runtime"],
        }
        state, detail = "unknown", "claim outcome is uncertain"
        started = time.monotonic()
        claimed = False
        report_identity = identity
        try:
            claim = self._claim(mission["id"], identity)
            if claim["identity"] != identity:
                raise ValueError("claim returned a different execution identity")
            claimed = True
            remaining = float(mission["expires_at"]) - time.time() - self.clock_uncertainty_s
            remaining = min(remaining, manifest["execution"]["mission_timeout_s"])
            deadline_ns = time.monotonic_ns() + int(remaining * 1e9)
            self._check_live(deadline_ns)
            acknowledgement = self._report(mission["id"], {"identity": identity, "state": "running"})
            acknowledged_state = acknowledgement.get("mission", {}).get("state")
            if acknowledged_state == "cancel_requested":
                raise Cancelled("cancelled during running acknowledgement")
            if acknowledged_state != "running":
                raise ValueError("running acknowledgement did not confirm execution authority")
            self.journal.mark_running(mission["id"])
            monitor = threading.Thread(target=self._monitor, args=(mission, done), daemon=True)
            monitor.start()
            adapter = self.adapter_factory()
            observation = adapter.reset(mission["seed"])
            for sequence in range(manifest["execution"]["max_steps"]):
                request_deadline = min(deadline_ns, time.monotonic_ns() +
                                       manifest["execution"]["decision_timeout_ms"] * 1_000_000)
                self._check_live(request_deadline)
                request = {
                    "identity": identity, "request_id": str(uuid.uuid4()),
                    "observation_id": f"{mission['id']}:{sequence}", "sequence": sequence,
                    "observation": observation, "deadline_monotonic_ns": request_deadline,
                    "budget_ms": max(0.001, (request_deadline - time.monotonic_ns()) / 1e6),
                }
                validate_request(request)
                with self._submission:
                    self._check_live(request_deadline)
                    self._outstanding = request
                outcome = self._apply(adapter, request, self._decide(request, claim["grant"]))
                observation = outcome.observation
                summary["steps"] = sequence + 1
                summary["reward_sum"] += outcome.reward
                summary["final_success"] = outcome.success
                summary["ever_success"] |= outcome.success
                summary["simulated_duration_s"] = (sequence + 1) * adapter.control_period_s
                if outcome.terminated or outcome.truncated:
                    break
            state, detail = "completed", "simulation horizon completed"
        except Cancelled as error:
            state, detail = "cancelled", str(error)
        except ExecutionUnknown as error:
            state, detail = "unknown", str(error)
        except RemoteError as error:
            if error.status == 410 and not claimed:
                # The backend committed expiry and an immutable failed episode.
                state, detail = "failed", "mission authorization expired before claim"
                self.journal.finish(mission["id"], {"identity": identity, "state": state,
                                                     "detail": detail, "summary": summary})
                self.journal.acknowledge(mission["id"])
                return
            state = "failed" if claimed else "unknown"
            detail = "inference or management request rejected"
            if error.status == 409 and not claimed:
                try:
                    current = self._desired().get("mission")
                except Exception:
                    current = None  # persist unknown and reconcile on the next tick
                if (current and current["id"] == mission["id"] and
                        current["state"] == "cancel_requested" and current.get("identity") is None):
                    state, detail, report_identity = "cancelled", "cancelled before local admission", None
        except Exception as error:
            state = "failed" if claimed else "unknown"
            detail = type(error).__name__ + ": execution stopped without retrying a command"
        finally:
            done.set()
            if monitor:
                monitor.join(timeout=0.2)
            if adapter:
                try:
                    adapter.close()
                except Exception:
                    state, detail = "unknown", "adapter cleanup outcome is uncertain"
            self._outstanding = None
            self._active_mission = None
        summary["wall_duration_s"] = time.monotonic() - started
        self.journal.finish(mission["id"], {
            "identity": report_identity, "state": state, "detail": detail[:1000], "summary": summary,
        })
        self._flush_reports({"mission": mission})
