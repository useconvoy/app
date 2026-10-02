"""One foreground service for connection health, verification and registered simulation tasks."""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import signal
import threading
import time
from contextlib import ExitStack
from pathlib import Path

from convoy_agent.agent import Agent, AgentConfig
from convoy_agent.coordinator import Coordinator, ExecutionJournal
from convoy_agent.coordinator.transport import JsonHTTP, RemoteError
from convoy_contracts.grants import GrantVerifier
from convoy_contracts.registered import REGISTERED_PROFILE

from .managed_worker import ManagedWorker, _write
from .qualification.runner import reconcile
from .registered import RegisteredBundle

LOG = logging.getLogger(__name__)
EXPLICIT_TRUST = ("CONVOY_ACTION_VERIFICATION_KEYS_FILE", "CONVOY_EXECUTION_SECRET")


def refresh_action_keys(control, destination):
    """Trust the enrolled API's HTTPS origin; never accept a release-supplied key URL."""
    document = control.get("/api/agent/v1/action-verification-keys")
    if destination.exists() and json.loads(destination.read_text()) == document:
        GrantVerifier(destination, purpose="action")
        return
    candidate = destination.with_suffix(".candidate.json")
    try:
        _write(candidate, document)
        new = GrantVerifier(candidate, purpose="action")
        if destination.exists():
            old = GrantVerifier(destination, purpose="action")
            if (old.issuer, old.audience) != (new.issuer, new.audience):
                raise ValueError("execution trust anchors changed; operator review required")
            if json.loads(destination.read_text()) == document:
                return
        _write(destination, document)
    finally:
        candidate.unlink(missing_ok=True)


class SimulatorService:
    def __init__(self, directory, assets=None):
        self.directory = Path(directory).absolute()
        self.assets = Path(assets).absolute() if assets else self.directory / "robot-assets"
        self.cfg = AgentConfig(self.directory)
        if not self.cfg.credential or not self.cfg.data.get("simulate"):
            raise ValueError("enroll a simulated robot computer first; physical devices are unsupported")
        if not self.cfg.data.get("host_inventory"):
            raise ValueError("use simulator enrollment with --host-inventory to report the actual host")
        self.control = JsonHTTP(self.cfg.data["server"], self.cfg.credential, ca_file=self.cfg.data.get("ca_file"))
        self.state_dir = self.directory / "simulator-service"
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.keys = None if any(name in os.environ for name in EXPLICIT_TRUST) else self.state_dir / "action-keys.json"
        self.agent = self.coordinator = None
        self.stopping = False
        self.agent_failure = None
        self.binding = None
        self.last_status = None

    def status(self, phase, detail):
        value = {"phase": phase, "detail": detail, "robot_id": self.binding[0] if self.binding else None}
        if value != self.last_status:
            _write(self.state_dir / "status.json", value)
            self.last_status = value
            LOG.info("Simulator service: %s", phase)

    def request_stop(self, *_):
        self.stopping = True
        if self.agent is not None:
            self.agent._signal_stop()  # The agent's existing lock-free signal-handler path.
        if self.coordinator is not None:
            self.coordinator.request_stop()

    def _agent_loop(self):
        try:
            result = self.agent.run()
            if not result.get("complete"):
                self.agent_failure = "connection agent shutdown was incomplete"
        except BaseException as error:
            self.agent_failure = f"connection agent stopped ({type(error).__name__})"
        finally:
            self.stopping = True
            if self.coordinator is not None:
                self.coordinator.request_stop()

    def _bind(self, registered, resources):
        robot, profile = registered["robot"], registered["profile"]
        if robot["profile"] != REGISTERED_PROFILE or robot["simulation_engine"] != "mujoco":
            raise ValueError("this service requires a registered MuJoCo joint-position robot")
        binding = (robot["id"], profile["digest"], robot["simulation_engine"])
        if self.binding is not None:
            if self.binding != binding:
                raise ValueError("robot binding changed; restart the simulator service")
            return
        journal = ExecutionJournal(self.directory / "coordinator", robot["id"], self.cfg.data["device_id"])
        resources.callback(journal.close)
        owner = resources.enter_context(ManagedWorker(self.directory / "managed-worker", verification_keys_file=self.keys))
        bundle = RegisteredBundle(profile, robot["simulation_engine"], self.assets, worker_owner=owner, control=self.control,
                                  recording_directory=self.directory / "trajectories")
        self.coordinator = Coordinator(robot_id=robot["id"], device_id=self.cfg.data["device_id"], journal=journal,
            control=self.control, worker=None, adapter_factory=None, bundle_owner=bundle, profile=REGISTERED_PROFILE, poll_s=1)
        self.binding = binding

    def run(self):
        # This lock also covers the interval before a robot exists and has an execution journal.
        with (self.state_dir / "service.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.agent = Agent(self.directory, robot_sim=False)
            heartbeat = threading.Thread(target=self._agent_loop, name="robot-connection", daemon=True)
            previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            for sig in previous:
                signal.signal(sig, self.request_stop)
            heartbeat.start()
            failure = None
            try:
                with ExitStack() as resources:
                    while not self.stopping:
                        try:
                            registered = self.control.get("/api/agent/v1/registry")
                            if self.stopping:
                                break
                            if not registered.get("robot"):
                                if self.binding is not None:
                                    raise ValueError("registered robot was removed; restart after registration")
                                self.status("waiting-for-registration", "Finish registering this computer in the project.")
                            else:
                                if self.keys is not None:
                                    refresh_action_keys(self.control, self.keys)
                                self._bind(registered, resources)
                                if self.stopping:
                                    break
                                # The coordinator settles prior execution before maintenance can run.
                                deferred = False
                                try:
                                    self.coordinator.tick()
                                except Exception as error:
                                    deferred = True
                                    self.status("execution-deferred", f"Coordinator will retry ({type(error).__name__}).")
                                qualification = registered.get("qualification")
                                if qualification and qualification["state"] == "requested" and self.coordinator.idle_for_maintenance():
                                    self.status("verifying", "Checking the requested robot model.")
                                    reconcile(self.control, self.assets, stop_requested=lambda: self.stopping)
                                elif not deferred:
                                    self.status("running", "Connected; watching for verification, deployment and task requests.")
                        except InterruptedError:
                            if not self.stopping:
                                raise
                        except RemoteError as error:
                            if error.status in {401, 403}:
                                raise
                            self.status("waiting-for-api", f"Control-plane setup unavailable (HTTP {error.status}).")
                        except (ValueError, RuntimeError):
                            raise  # Local ownership/binding/trust errors require an explicit restart.
                        except Exception as error:
                            self.status("retrying", f"Iteration deferred ({type(error).__name__}).")
                        for _ in range(10):
                            if self.stopping:
                                break
                            time.sleep(.1)
            except BaseException as error:
                failure = f"Service stopped ({type(error).__name__})."
                raise
            finally:
                self.request_stop()
                self.agent.request_stop()
                heartbeat.join(timeout=self.agent.shutdown_budget_s + 2)
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
                if heartbeat.is_alive():
                    self.agent_failure = "connection agent did not stop within its shutdown budget"
                failure = failure or self.agent_failure
                self.status("failed" if failure else "stopped", failure or "Service stopped.")
            if self.agent_failure:
                raise RuntimeError(self.agent_failure)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--assets", type=Path, help="shared model cache; defaults inside the enrollment directory")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        SimulatorService(args.data_dir, args.assets).run()
    except Exception as error:
        LOG.error("Simulator service stopped (%s). Check enrollment, execution trust and local ownership.", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
