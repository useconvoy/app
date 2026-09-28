"""CLI for the first isolated Convoy physics integration."""

import argparse
import json
from pathlib import Path

from .policies import POLICIES
from .runner import RunConfig, run


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the MetaWorld pick-place action-policy reference")
    parser.add_argument("--output", type=Path, required=True, help="new directory for run evidence")
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--steps", type=int, default=500)
    parser.add_argument("--policy", choices=POLICIES, default="scripted")
    parser.add_argument("--video", action="store_true", help="record MP4; requires the video extra and GL")
    parser.add_argument("--require-success", action="store_true", help="exit 1 unless every episode succeeds at its end")
    args = parser.parse_args()
    try:
        config = RunConfig(args.episodes, args.seed, args.steps, args.policy, args.video)
        summary = run(config, args.output)
    except (ValueError, OSError) as error:
        parser.exit(2, f"convoy-sim: {error}\n")
    print(json.dumps({"output": str(args.output.resolve()), **summary}, indent=2))
    if summary["status"] != "completed":
        return 2
    return int(args.require_success and summary["final_success_rate"] < 1)
