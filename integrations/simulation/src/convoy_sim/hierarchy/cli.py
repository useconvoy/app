"""Run the bounded Sawyer scheduling experiment without provisioning services."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .experiment import ExperimentConfig, run_episode
from .planner import DeterministicPlanner, FaultInjectedPlanner, TextPlanner


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory for measured evidence")
    parser.add_argument("--mode", choices=("local", "blocking", "async"), default="local")
    parser.add_argument("--planner-url", help="existing OpenAI-compatible model endpoint")
    parser.add_argument("--model", default="convoy-active")
    parser.add_argument("--controlled-planner", action="store_true", help="explicit deterministic remote-planner stand-in")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--duration", type=float, default=6)
    parser.add_argument("--target", choices=("A", "B"), default="A")
    parser.add_argument("--revise-at", type=float, default=1.5)
    parser.add_argument("--no-revision", action="store_true")
    parser.add_argument("--revised-target", choices=("A", "B"), default="B")
    parser.add_argument("--refresh-interval", type=float, default=.75)
    parser.add_argument("--planner-timeout", type=float, default=3)
    parser.add_argument("--max-proposal-age", type=float, default=1.5)
    parser.add_argument("--goal-validity", type=float, default=2)
    parser.add_argument("--command-validity", type=float, default=.1)
    parser.add_argument("--delay-ms", type=float, default=0, help="modeled extra delay on remote planner calls only")
    parser.add_argument("--jitter-ms", type=float, default=0)
    parser.add_argument("--outage-request", type=int, action="append", default=[], help="1-based remote calls to fail")
    parser.add_argument("--frames", action="store_true", help="render during execution; affects measured scheduler load")
    parser.add_argument("--require-success", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "local" and (args.planner_url or args.controlled_planner or args.delay_ms or args.jitter_ms or args.outage_request):
        parser.error("local mode uses the local reference planner without remote fault injection")
    if args.mode != "local" and bool(args.planner_url) == args.controlled_planner:
        parser.error("blocking/async mode needs exactly one of --planner-url or --controlled-planner")
    try:
        config = ExperimentConfig(
            mode=args.mode, seed=args.seed, duration_s=args.duration, task_target=args.target,
            revise_at_s=None if args.no_revision else args.revise_at, revised_target=args.revised_target,
            refresh_interval_s=args.refresh_interval, planner_timeout_s=args.planner_timeout,
            max_proposal_age_s=args.max_proposal_age, goal_validity_s=args.goal_validity,
            command_validity_s=args.command_validity, frames=args.frames,
        )
        planner = (TextPlanner(args.planner_url, args.model, timeout_s=args.planner_timeout,
                               bearer_token=os.environ.get("CONVOY_HIERARCHY_PLANNER_TOKEN"))
                   if args.planner_url else DeterministicPlanner())
        if args.mode != "local":
            planner = FaultInjectedPlanner(planner, delay_ms=args.delay_ms, jitter_ms=args.jitter_ms,
                                           outage_requests=args.outage_request, seed=args.seed)
        summary = run_episode(config, planner, args.output)
    except (OSError, ValueError) as error:
        parser.exit(2, f"convoy-sim-hierarchy: {error}\n")
    print(json.dumps({"output": str(args.output.resolve()), **summary}, indent=2, allow_nan=False))
    return 2 if summary["status"] == "error" else int(args.require_success and not summary["task_success"])


if __name__ == "__main__":
    raise SystemExit(main())
