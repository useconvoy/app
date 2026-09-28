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
    VISUAL_PROFILE,
    canonical_digest,
    validate_identity,
    validate_observation,
    validate_request,
    validate_result,
)
from convoy_contracts.pairing import (
    CONTROLLED_PLANNER_RUNTIME,
    PAIRED_PROFILE,
    action_manifest,
    release_profiles,
    validate_plan_request,
    validate_plan_result,
    validate_planner_identity,
    validate_release_manifest,
)

from .journal import ExecutionJournal
from .transport import RemoteError

log = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled", "unknown"}


@dataclass(frozen=True)
class StepResult:
    observation: list[float] | dict
    reward: float
    success: bool
    terminated: bool = False
    truncated: bool = False


class Adapter(Protocol):
    control_period_s: float

    def reset(self, seed: int) -> list[float] | dict: ...
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
        poll_s: float = 0.1, clock_uncertainty_s: float = 0.25, profile: str = PROFILE,
        planner=None,
    ):
        if not math.isfinite(poll_s) or poll_s <= 0:
            raise ValueError("poll interval must be positive and finite")
        if not math.isfinite(clock_uncertainty_s) or clock_uncertainty_s < 0:
            raise ValueError("clock uncertainty must be nonnegative and finite")
        if profile not in release_profiles():
            raise ValueError("unsupported adapter profile")
        if profile == PAIRED_PROFILE and planner is None:
            raise ValueError("paired execution requires a configured planner")
        self.profile = profile
        self.planner = planner
        self._planner_readiness = None
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
        self._cleanup_thread: threading.Thread | None = None
        self._ready: tuple[str, int, str] | None = None
        self.prefix = f"/api/agent/v1/robots/{robot_id}"
        journal.recover()

    @property
    def action_profile(self) -> str:
        return VISUAL_PROFILE if self.profile == PAIRED_PROFILE else self.profile

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
        if value["robot"]["profile"] != self.profile:
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
        manifest = validate_release_manifest(release["manifest"])
        if manifest["profile"] != self.profile:
            raise ValueError("release does not match the adapter profile")
        if canonical_digest(manifest) != release["digest"]:
            raise ValueError("release content does not match immutable digest")
        ready_key = (deployment["id"], deployment["generation"], release["digest"])
        if self._ready != ready_key:
            try:
                probe = self.worker.probe(release["digest"], manifest["profile"])
                if probe.get("release_digest") != release["digest"] or probe.get("profile") != self.profile:
                    raise ValueError("worker readiness identity mismatch")
                if self.profile == PAIRED_PROFILE:
                    policy = action_manifest(manifest)["policy"]
                    if probe.get("runtime") != policy["runtime"] or probe.get("artifact_sha256") != policy["artifact_sha256"]:
                        raise ValueError("loaded action policy does not match the paired release")
                    planner = self.planner.probe(release["digest"], self.profile)
                    if (planner.get("ready") is not True or planner.get("release_digest") != release["digest"]
                            or planner.get("profile") != self.profile
                            or planner.get("runtime") != manifest["planner"]["runtime"]
                            or planner.get("planner_artifact_sha256") != manifest["planner"]["artifact_sha256"]):
                        raise ValueError("planner readiness identity mismatch")
                    self._planner_readiness = validate_planner_identity({
                        key: planner[key] for key in (
                            "planner_artifact_sha256", "planner_incarnation", "runtime_generation",
                        )
                    })
            except Exception:
                self.control.post(self.prefix + f"/deployments/{deployment['id']}/report", {
                    "generation": deployment["generation"], "state": "blocked",
                    "release_digest": release["digest"], "detail": "application component readiness probe failed",
                })
                raise
            unclaimed_current_request = (
                mission is not None and mission["state"] == "requested" and mission.get("identity") is None
                and not mission.get("execution_started", False)
                and mission["deployment_id"] == deployment["id"]
                and mission["generation"] == deployment["generation"]
                and mission["release_digest"] == release["digest"]
            )
            if mission is None or unclaimed_current_request:
                self.control.post(self.prefix + f"/deployments/{deployment['id']}/report", {
                    "generation": deployment["generation"], "state": "ready", "release_digest": release["digest"],
                })
            elif deployment.get("state") != "ready":
                return  # never refresh readiness for an admitted or unresolved execution
            self._ready = ready_key
            # Fresh desired state avoids using a request cancelled during the probe.
            return
        if mission:
            if self._cleanup_thread and self._cleanup_thread.is_alive():
                return  # one bounded cleanup attempt; never accumulate remote close threads
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
        return self._call_bounded(request["deadline_monotonic_ns"], lambda: self.worker.decide(request, grant))

    def _call_bounded(self, deadline_ns: int, operation: Callable) -> dict:
        """Cancellation/deadline releases the supervisor even during blocked I/O.

        Only the supervisor can submit to the adapter. A late transport result
        stays in this call's private queue and cannot execute an action.
        """
        self._check_live(deadline_ns)
        inbox: queue.Queue = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                inbox.put((True, operation()))
            except Exception as error:
                inbox.put((False, error))

        self._inference_thread = threading.Thread(target=invoke, daemon=True)
        self._inference_thread.start()
        while True:
            self._check_live(deadline_ns)
            try:
                okay, result = inbox.get(timeout=min(0.025, max(
                    0.000001, (deadline_ns - time.monotonic_ns()) / 1e9)))
            except queue.Empty:
                continue
            if not okay:
                raise result
            return result

    def _plan(self, mission: dict, manifest: dict, identity: dict, grant: str,
              mission_deadline_ns: int, summary: dict) -> None:
        deadline = min(mission_deadline_ns, time.monotonic_ns() + manifest["planning"]["timeout_ms"] * 1_000_000)
        session = self._call_bounded(deadline, lambda: self.planner.start_session(identity, grant))
        expected = {"identity": identity, "next_sequence": 0, **(self._planner_readiness or {})}
        if session != expected:
            self._ready = None
            raise ValueError("planner session changed since paired readiness")
        self._check_live(deadline)
        request = {
            "identity": identity, "request_id": str(uuid.uuid4()),
            "observation_id": f"{mission['id']}:task-admission",
            "observation_digest": canonical_digest({
                "scope": "fixed_task_admission", "seed": mission["seed"],
                "task": manifest["task"], "catalog_sha256": manifest["catalog_sha256"],
            }),
            "deadline_monotonic_ns": deadline,
            "budget_ms": max(0.001, (deadline - time.monotonic_ns()) / 1e6),
        }
        validate_plan_request(request)
        # Persist the original proposal request before network I/O. Recovery never
        # regenerates a plan or resumes an accepted skill after an ambiguous crash.
        self.journal.request_plan(request)
        result = validate_plan_result(self._call_bounded(deadline, lambda: self.planner.propose(request, grant)))
        with self._submission:
            self._check_live(deadline)
            for key in ("identity", "request_id", "observation_id", "observation_digest", "deadline_monotonic_ns"):
                if result[key] != request[key]:
                    raise ValueError(f"planner result {key} does not match the original request")
            for key, value in self._planner_readiness.items():
                if result[key] != value:
                    self._ready = None
                    raise ValueError("planner runtime changed within the admitted session")
            accepted = result["decision"] == {
                "kind": "skill", "skill_id": manifest["task"]["skill_id"], "parameters": {},
            }
            self.journal.record_plan(request, result, accepted=accepted)
            summary.update(planner_result=result, planner_accepted=accepted)
            if not accepted:
                raise ValueError("planner declined the fixed supported task")

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
                validate_observation(outcome.observation, self.action_profile)
                if not math.isfinite(outcome.reward):
                    raise ValueError("adapter returned an invalid physical state")
                self.journal.command_outcome(request, "applied", asdict(outcome))
                return outcome
            except Exception as error:
                # The step may have happened. Preserve the intent and stop; an
                # uncertain physical command must never be blindly retried.
                raise ExecutionUnknown("adapter command outcome is uncertain") from error

    def _close_sessions(self, identity: dict, claim: dict) -> None:
        """Best-effort network cleanup after the local terminal outcome is durable.

        A stuck remote close cannot prevent cancellation reporting. New mission
        admission waits for this single thread, so failures cannot grow a queue
        of abandoned cleanup calls. Original grants are never extended.
        """
        def close() -> None:
            endpoints = [(self.worker, claim["grant"])] if self.action_profile == VISUAL_PROFILE else []
            if self.profile == PAIRED_PROFILE:
                endpoints.append((self.planner, claim["planner_grant"]))
            for endpoint, grant in endpoints:
                try:
                    endpoint.end_session(identity, grant)
                except Exception:
                    log.warning("component close not acknowledged; original grant expiry remains in force")

        self._cleanup_thread = threading.Thread(target=close, daemon=True)
        self._cleanup_thread.start()
        self._cleanup_thread.join(timeout=0.025)

    def _run_mission(self, mission: dict, manifest: dict) -> None:
        policy = action_manifest(manifest)
        identity = self.journal.identity(mission, self.device_id, self.robot_id)
        validate_identity(identity)
        self.journal.prepare(mission["id"], identity)
        adapter = None
        monitor = None
        done = threading.Event()
        self._cancel_reason, self._outstanding = None, None
        self._active_mission = mission["id"]
        summary = {
            "execution_mode": "lockstep_offline",
            "evidence_scope": ("simulated_physics_with_rgb_and_proprioception" if self.action_profile == VISUAL_PROFILE
                               else "simulated_physics_with_privileged_state"),
            "profile": self.profile,
            "seed": mission["seed"], "steps": 0, "ever_success": False, "final_success": False,
            "reward_sum": 0.0, "simulated_duration_s": 0.0, "policy_runtime": policy["policy"]["runtime"],
        }
        if self.profile == PAIRED_PROFILE:
            controlled = manifest["planner"]["runtime"] == CONTROLLED_PLANNER_RUNTIME
            summary.update(
                planner_accepted=False, planner_runtime=manifest["planner"]["runtime"],
                planner_backend_kind="controlled-text-planner" if controlled else "llamacpp-text-model",
                planner_scope=("deterministic fixed-skill admission; no text-model inference" if controlled
                               else "text-only fixed-task routing; no image understanding"),
                placement=manifest["placement"],
            )
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
            remaining = min(remaining, policy["execution"]["mission_timeout_s"])
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
            if self.profile == PAIRED_PROFILE:
                self._plan(mission, manifest, identity, claim["planner_grant"], deadline_ns, summary)
            if self.action_profile == VISUAL_PROFILE:
                session = self._call_bounded(deadline_ns, lambda: self.worker.start_session(identity, claim["grant"]))
                if session != {"identity": identity, "next_sequence": 0}:
                    raise ValueError("worker session identity or initial sequence mismatch")
                self._check_live(deadline_ns)
            adapter = self.adapter_factory()
            observation = adapter.reset(mission["seed"])
            for sequence in range(policy["execution"]["max_steps"]):
                request_deadline = min(deadline_ns, time.monotonic_ns() +
                                       policy["execution"]["decision_timeout_ms"] * 1_000_000)
                self._check_live(request_deadline)
                request = {
                    "identity": identity, "request_id": str(uuid.uuid4()),
                    "observation_id": f"{mission['id']}:{sequence}", "sequence": sequence,
                    "observation": observation, "deadline_monotonic_ns": request_deadline,
                    "budget_ms": max(0.001, (request_deadline - time.monotonic_ns()) / 1e6),
                }
                validate_request(request, self.action_profile)
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
                if (outcome.terminated or outcome.truncated or
                        (self.action_profile == VISUAL_PROFILE and outcome.success)):
                    break
            state = "completed"
            detail = ("benchmark success reached" if self.action_profile == VISUAL_PROFILE and summary["final_success"]
                      else "simulation horizon completed")
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
        if claimed and self.action_profile == VISUAL_PROFILE:
            self._close_sessions(identity, claim)
        self._flush_reports({"mission": mission})
