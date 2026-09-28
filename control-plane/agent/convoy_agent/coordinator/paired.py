"""Run the paired fixed-task profile on an enrolled simulator.

The installed adapter factory is a trusted local integration, not a remote import.
The initial visual adapter runs MuJoCo locally; this does not move a physical arm.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import signal
from contextlib import ExitStack
from pathlib import Path

from convoy_contracts.pairing import PAIRED_PROFILE

from convoy_agent.agent import AgentConfig

from .engine import Coordinator
from .journal import ExecutionJournal
from .transport import JsonHTTP, PlannerHTTP, WorkerHTTP


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--worker-url")
    parser.add_argument("--planner-url")
    parser.add_argument("--local-registry", type=Path,
                        help="trusted local SmolVLA/Qwen recipe registry; loads the requested paired bundle")
    parser.add_argument("--worker-ca-file")
    parser.add_argument("--planner-ca-file")
    parser.add_argument("--adapter-factory", default="convoy_lerobot.adapter:VisualMetaWorldAdapter")
    parser.add_argument("--server")
    parser.add_argument("--poll-seconds", type=float, default=0.1)
    parser.add_argument("--clock-uncertainty-seconds", type=float, default=0.25)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.local_registry:
        if any((args.worker_url, args.planner_url, args.worker_ca_file, args.planner_ca_file)):
            parser.error("local registry and externally managed model endpoints are mutually exclusive")
        if args.adapter_factory != "convoy_lerobot.adapter:VisualMetaWorldAdapter":
            parser.error("local activation currently requires the qualified visual simulator adapter")
    elif not args.worker_url or not args.planner_url:
        parser.error("configure both worker/planner URLs or a local registry")
    cfg = AgentConfig(args.data_dir)
    if not cfg.credential or not cfg.data.get("simulate"):
        parser.error("enroll this simulator before starting its paired coordinator")
    worker_token, planner_token = (os.environ.get(key) for key in (
        "CONVOY_WORKER_PROBE_TOKEN", "CONVOY_PLANNER_PROBE_TOKEN",
    ))
    if not worker_token or not planner_token:
        parser.error("both action-worker and planner probe credentials are required")
    module, separator, name = args.adapter_factory.partition(":")
    if not separator or not module or not name:
        parser.error("adapter factory must be an installed module:factory")
    factory = getattr(importlib.import_module(module), name)
    logging.basicConfig(level=logging.INFO)
    control = JsonHTTP(args.server or cfg.data["server"], cfg.credential, ca_file=cfg.data.get("ca_file"))
    with ExitStack() as resources:
        journal = ExecutionJournal(args.data_dir / "coordinator", args.robot_id, cfg.data["device_id"])
        resources.callback(journal.close)
        bundle_owner = None
        if args.local_registry:
            # The optional integration owns only installed, fixed launchers. It
            # acquires role locks here; mission recovery gates any later launch.
            from convoy_lerobot.local_bundle import LocalBundleOwner

            bundle_owner = resources.enter_context(LocalBundleOwner(
                args.data_dir / "local-bundle", args.local_registry,
                worker_execution_secret=os.environ.get("CONVOY_EXECUTION_SECRET", ""),
                planner_execution_secret=os.environ.get("CONVOY_PLANNER_EXECUTION_SECRET", ""),
                worker_probe_token=worker_token, planner_probe_token=planner_token,
            ))
            worker, planner = None, None
        else:
            worker = WorkerHTTP(args.worker_url, worker_token, ca_file=args.worker_ca_file)
            planner = PlannerHTTP(args.planner_url, planner_token, ca_file=args.planner_ca_file)
        coordinator = Coordinator(robot_id=args.robot_id, device_id=cfg.data["device_id"], journal=journal,
                                  control=control, worker=worker, planner=planner, adapter_factory=factory,
                                  bundle_owner=bundle_owner,
                                  profile=PAIRED_PROFILE, poll_s=args.poll_seconds,
                                  clock_uncertainty_s=args.clock_uncertainty_seconds)
        signal.signal(signal.SIGINT, lambda *_: coordinator.request_stop())
        signal.signal(signal.SIGTERM, lambda *_: coordinator.request_stop())
        coordinator.run(once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
