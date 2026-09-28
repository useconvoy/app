"""Run a registered simulated robot through the Convoy management pipeline.

Enroll using ``convoy-agent enroll --simulate`` first. This process reuses that
device credential and binds a dedicated durable coordinator journal to one robot.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
from pathlib import Path

from convoy_agent.agent import AgentConfig
from convoy_agent.coordinator import Coordinator, ExecutionJournal
from convoy_agent.coordinator.transport import JsonHTTP, WorkerHTTP
from convoy_contracts.execution import VISUAL_PROFILE

from .adapter import VisualMetaWorldAdapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="existing simulated device enrollment directory")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--worker-url", default="http://127.0.0.1:8091")
    parser.add_argument("--server", help="override the enrolled control-plane endpoint")
    parser.add_argument("--poll-seconds", type=float, default=0.1)
    parser.add_argument("--clock-uncertainty-seconds", type=float, default=0.25,
                        help="explicit maximum wall-clock uncertainty for this local simulator")
    parser.add_argument("--once", action="store_true", help="perform one management iteration")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    cfg = AgentConfig(args.data_dir)
    if not cfg.credential or not cfg.data.get("simulate"):
        parser.error("run convoy-agent enroll --simulate for this data directory first")
    probe_token = os.environ.get("CONVOY_WORKER_PROBE_TOKEN")
    if not probe_token:
        parser.error("CONVOY_WORKER_PROBE_TOKEN is required")
    control = JsonHTTP(args.server or cfg.data["server"], cfg.credential, ca_file=cfg.data.get("ca_file"))
    worker = WorkerHTTP(args.worker_url, probe_token)
    # One directory/lock for the enrolled device prevents accidentally assigning
    # two processes to its simulated executor under different robot identifiers.
    journal = ExecutionJournal(args.data_dir / "coordinator", args.robot_id, cfg.data["device_id"])
    try:
        coordinator = Coordinator(
            robot_id=args.robot_id, device_id=cfg.data["device_id"], journal=journal,
            control=control, worker=worker, adapter_factory=VisualMetaWorldAdapter, profile=VISUAL_PROFILE,
            poll_s=args.poll_seconds, clock_uncertainty_s=args.clock_uncertainty_seconds,
        )
        signal.signal(signal.SIGINT, lambda *_: coordinator.request_stop())
        signal.signal(signal.SIGTERM, lambda *_: coordinator.request_stop())
        coordinator.run(once=args.once)
    finally:
        journal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
