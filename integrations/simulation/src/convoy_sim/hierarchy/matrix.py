"""Compare bounded local, blocking remote, and asynchronous remote execution.

This is a scheduling experiment with a scripted expert, not a learned-policy
benchmark. Extra transport delays and outages are controlled faults, not measured
WAN service. Physics and control remain on the machine running this command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .experiment import ExperimentConfig, run_episode
from .planner import DeterministicPlanner, FaultInjectedPlanner, TextPlanner

PROFILES = {
    "nominal": {"delay_ms": 0, "jitter_ms": 0, "outage_requests": []},
    "latency": {"delay_ms": 500, "jitter_ms": 150, "outage_requests": []},
    "stale-outage": {"delay_ms": 2500, "jitter_ms": 0, "outage_requests": [2]},
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    parser.add_argument("--planner-url", required=True)
    parser.add_argument("--model", default="convoy-qwen-lab")
    parser.add_argument("--token-file", type=Path, help="read a private token without putting it in argv")
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1])
    parser.add_argument("--profiles", choices=tuple(PROFILES), nargs="+", default=list(PROFILES))
    parser.add_argument("--duration", type=float, default=12)
    parser.add_argument("--edge-host", default="experiment host")
    parser.add_argument("--planner-host", default="remote model endpoint")
    parser.add_argument("--network-path", default="operator supplied endpoint; deployment topology unverified")
    parser.add_argument("--source-revision", default="not supplied")
    parser.add_argument("--model-sha256", default="not supplied")
    args = parser.parse_args(argv)
    if not 3 < args.duration <= 24:
        parser.error("duration must be >3 and <=24 seconds (bounded replay size)")
    if not 1 <= len(args.seeds) <= 4 or len(set(args.seeds)) != len(args.seeds):
        parser.error("use one to four distinct seeds")
    if any(type(seed) is not int or not 0 <= seed < 2**32 for seed in args.seeds):
        parser.error("seed must be an unsigned 32-bit integer")
    if len(set(args.profiles)) != len(args.profiles):
        parser.error("profiles must be distinct")
    args.output.mkdir(parents=True, exist_ok=False)
    token = args.token_file.read_text().strip() if args.token_file else os.environ.get("CONVOY_HIERARCHY_PLANNER_TOKEN")
    model = TextPlanner(args.planner_url, args.model, timeout_s=4, bearer_token=token)
    bundle = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        bundle.update(path.name.encode() + b"\0" + path.read_bytes())
    common = {"edge_host": args.edge_host, "planner_host": args.planner_host,
              "network_path": args.network_path, "model": args.model,
              "model_sha256": args.model_sha256, "source_revision": args.source_revision,
              "hierarchy_source_sha256": bundle.hexdigest(),
              "rendering": "disabled during capture; reconstructed from recorded states afterward"}
    trials = [("local", "local-reference", seed) for seed in args.seeds]
    trials += [(mode, profile, seed) for profile in args.profiles
               for seed in args.seeds for mode in ("blocking", "async")]
    results = []
    incomplete = False
    for mode, profile, seed in trials:
        relative = Path(f"{mode}-{profile}") / f"seed-{seed}"
        planner = (DeterministicPlanner() if mode == "local" else
                   FaultInjectedPlanner(model, seed=seed, **PROFILES[profile]))
        provenance = {**common, "fault_profile": profile,
                      "fault_source": "none" if profile in {"nominal", "local-reference"} else "controlled delay/outage wrapper",
                      "fault_parameters": PROFILES.get(profile, {}),
                      "planner_host": args.edge_host if mode == "local" else args.planner_host,
                      "model": "deterministic local reference" if mode == "local" else args.model,
                      "model_sha256": "not applicable" if mode == "local" else args.model_sha256}
        pending = []
        config = ExperimentConfig(mode=mode, seed=seed, duration_s=args.duration,
                                  revise_at_s=2.5, refresh_interval_s=.5,
                                  planner_timeout_s=4, max_proposal_age_s=3.5,
                                  goal_validity_s=4)
        summary = run_episode(config, planner, args.output / relative,
                              provenance=provenance, cleanup_calls=pending)
        results.append({"directory": str(relative), "profile": profile, **summary})
        print(json.dumps({"trial": str(relative), "status": summary["status"],
                          "physics_steps": summary["physics_steps"],
                          "wall_s": round(summary["wall_duration_s"], 3),
                          "hold_ticks": summary["hold_ticks"],
                          "accepted_plans": summary["accepted_plans"],
                          "rejected_plans": summary["rejected_plans"]}), flush=True)
        # Stop/revision fences robot commands immediately. Wait only after physics
        # closes so a cancelled network call cannot contaminate the next trial.
        if any(not call.done.wait(7) for call in pending):
            incomplete = True
            print("Planner transport did not drain; matrix stopped to avoid cross-trial queueing", flush=True)
        (args.output / "matrix.json").write_text(json.dumps(
            {"schema_version": 1, "provenance": common, "complete": not incomplete and len(results) == len(trials),
             "planned_trials": len(trials), "trials": results}, indent=2, allow_nan=False) + "\n")
        if incomplete:
            break
    return int(incomplete or any(trial["status"] == "error" for trial in results))


if __name__ == "__main__":
    raise SystemExit(main())
