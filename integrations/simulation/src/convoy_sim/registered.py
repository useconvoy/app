"""Execute registered MuJoCo joints through the shared deployment/mission coordinator.

This is functional lockstep simulation. Real-time timing qualification is a
separate mode; this runner never connects to physical motor controllers.
"""
from __future__ import annotations

import argparse
import copy
import logging
import os
import signal
import uuid
from contextlib import ExitStack
from pathlib import Path

import mujoco
import numpy as np
from convoy_agent.agent import AgentConfig
from convoy_agent.coordinator import Coordinator, ExecutionJournal, StepResult
from convoy_agent.coordinator.binding import BindingObservation, PreparedBinding
from convoy_agent.coordinator.transport import JsonHTTP, WorkerHTTP
from convoy_contracts.execution import canonical_digest
from convoy_contracts.registered import REGISTERED_PROFILE, validate_action, validate_robot_binding

from .qualification.assets import mujoco_files, read_asset
from .qualification.runner import check


class JointAdapter:
    def __init__(self, manifest, xml, assets):
        self.manifest = copy.deepcopy(manifest)
        self.model = mujoco.MjModel.from_xml_string(xml, assets)
        self.data = mujoco.MjData(self.model)
        self.control_period_s = 1 / manifest["interface"]["control_rate_hz"]
        self.substeps = round(self.control_period_s / self.model.opt.timestep)
        self.joints = [mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
                       for name in manifest["interface"]["joint_names"]]
        self.qpos = [int(self.model.jnt_qposadr[j]) for j in self.joints]
        self.qvel = [int(self.model.jnt_dofadr[j]) for j in self.joints]
        self.actuators = [int(np.flatnonzero((self.model.actuator_trnid[:, 0] == j)
                                           & (self.model.actuator_trntype == mujoco.mjtTrn.mjTRN_JOINT))[0])
                          for j in self.joints]
        self.commands = set()
        self.closed = False

    def observation(self):
        return {"positions": self.data.qpos[self.qpos].tolist(), "velocities": self.data.qvel[self.qvel].tolist(),
                "simulation_time_s": float(self.data.time)}

    def reset(self, seed):
        # This task's initial state is the pinned model's qpos0; seeds are retained
        # in mission evidence but do not imply randomized scenarios.
        mujoco.mj_resetData(self.model, self.data)
        mujoco.mj_forward(self.model, self.data)
        self.commands.clear()
        return self.observation()

    def step(self, action, command_id):
        if self.closed or command_id in self.commands:
            raise ValueError("adapter closed or command already applied")
        validate_action(action, self.manifest)
        for target, aid in zip(action, self.actuators, strict=True):
            if self.model.actuator_ctrllimited[aid] and not self.model.actuator_ctrlrange[aid, 0] <= target <= self.model.actuator_ctrlrange[aid, 1]:
                raise ValueError("joint target exceeds the actuator control range")
        self.commands.add(command_id)
        self.data.ctrl[self.actuators] = action
        for _ in range(self.substeps):
            mujoco.mj_step(self.model, self.data)
            if np.any(self.data.warning.number) or not np.all(np.isfinite(self.data.qpos)) or not np.all(np.isfinite(self.data.qvel)):
                raise ValueError("simulation became unstable")
        task = self.manifest["task"]
        error = float(np.max(np.abs(self.data.qpos[self.qpos] - task["target_joint_positions"])))
        speed = float(np.max(np.abs(self.data.qvel[self.qvel])))
        success = error <= task["position_tolerance"] and speed <= task["velocity_tolerance"]
        return StepResult(self.observation(), -error, success, terminated=success)

    def close(self):
        self.closed = True


class RegisteredBundle:
    """Load immutable robot bytes; the existing worker owns model installation/inference."""
    def __init__(self, profile, engine, assets, worker=None, *, worker_owner=None):
        self.profile, self.engine = copy.deepcopy(profile), engine
        self.assets, self.worker, self.worker_owner = Path(assets), worker, worker_owner
        if (worker is None) == (worker_owner is None):
            raise ValueError("configure one external worker or one managed worker owner")
        self.incarnation = str(uuid.uuid4())
        self.checked = set()

    def prepare(self, deployment, manifest):
        spec = self.profile["spec"]
        if canonical_digest(spec) != self.profile["digest"]:
            raise ValueError("robot profile content changed")
        model = validate_robot_binding(manifest, self.profile["digest"], spec, self.engine)
        asset = self.assets / model["asset"]["sha256"]
        payload = read_asset(asset, model["asset"]["sha256"])
        if model["asset"]["sha256"] not in self.checked:
            result = check(spec, model, asset)
            if result["state"] != "passed":
                raise ValueError(result["detail"])
            self.checked.add(model["asset"]["sha256"])
        xml, assets = mujoco_files(payload, model["asset"]["format"])
        worker = self.worker_owner.prepare(deployment, manifest) if self.worker_owner else self.worker
        probe = worker.probe(deployment["release"]["digest"], REGISTERED_PROFILE)
        worker_incarnation = probe.get("worker_incarnation")
        if not isinstance(worker_incarnation, str) or not 1 <= len(worker_incarnation) <= 128:
            raise ValueError("worker must report its process incarnation")
        binding_id = canonical_digest([self.incarnation, worker_incarnation, deployment["release"]["digest"]])
        return PreparedBinding(binding_id=binding_id, worker=worker, planner=None,
                               observation=BindingObservation(deployment["release"]["digest"], REGISTERED_PROFILE,
                                                              manifest["policy"]["artifact_sha256"], None),
                               adapter_factory=lambda: JointAdapter(manifest, xml, assets))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--worker-url", help="operator-managed policy worker")
    mode.add_argument("--manage-worker", action="store_true", help="prepare and own the local joint-reference worker")
    parser.add_argument("--worker-ca-file")
    parser.add_argument("--clock-uncertainty-seconds", type=float, default=0.25)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    cfg = AgentConfig(args.data_dir)
    if not cfg.credential or not cfg.data.get("simulate"):
        parser.error("enroll a simulator first")
    token = os.environ.get("CONVOY_WORKER_PROBE_TOKEN")
    if not args.manage_worker and not token:
        parser.error("CONVOY_WORKER_PROBE_TOKEN is required")
    control = JsonHTTP(cfg.data["server"], cfg.credential, ca_file=cfg.data.get("ca_file"))
    registered = control.get("/api/agent/v1/registry")
    robot = registered.get("robot")
    if not robot or robot["profile"] != REGISTERED_PROFILE:
        parser.error("register a robot with a joint-position profile first")
    if args.manage_worker and args.worker_ca_file:
        parser.error("the managed worker uses an owned loopback endpoint")
    with ExitStack() as resources:
        journal = ExecutionJournal(args.data_dir / "coordinator", robot["id"], cfg.data["device_id"])
        resources.callback(journal.close)
        worker_owner = None
        if args.manage_worker:
            from .managed_worker import ManagedWorker

            worker_owner = resources.enter_context(ManagedWorker(args.data_dir / "managed-worker"))
            worker = None
        else:
            worker = WorkerHTTP(args.worker_url, token, ca_file=args.worker_ca_file)
        owner = RegisteredBundle(registered["profile"], robot["simulation_engine"], args.assets, worker, worker_owner=worker_owner)
        coordinator = Coordinator(robot_id=robot["id"], device_id=cfg.data["device_id"], journal=journal,
                                  control=control, worker=worker, adapter_factory=None, bundle_owner=owner,
                                  profile=REGISTERED_PROFILE, poll_s=1,
                                  clock_uncertainty_s=args.clock_uncertainty_seconds)
        signal.signal(signal.SIGTERM, lambda *_: coordinator.request_stop())
        signal.signal(signal.SIGINT, lambda *_: coordinator.request_stop())
        coordinator.run(once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
