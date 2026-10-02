"""Bounded timing qualification matrix; stop when baseline cannot support the workload."""

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

from .report import summarize
from .run import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets", default="/assets")
    parser.add_argument("--precision", choices=("float32", "float16", "bfloat16"), default="float32")
    parser.add_argument("--camera-size", type=int, choices=(256, 480), default=480)
    parser.add_argument("--vision-size", type=int, choices=(256, 512), default=512)
    parser.add_argument("--denoise-steps", type=int, choices=(1, 5, 10), default=10)
    parser.add_argument("--shadow-size", type=int, choices=(0, 512, 1024, 2048), default=0)
    parser.add_argument("--render-samples", type=int, choices=(-1, 0, 2, 4, 8), default=-1)
    parser.add_argument("--seeds", default="0,1,2")
    parser.add_argument("--delays-ms", default="0,100,300")
    parser.add_argument("--drop-every", type=int, default=3)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    delays = [float(s) for s in args.delays_ms.split(",")]
    seeds = [int(s) for s in args.seeds.split(",")]
    if (not 1 <= len(delays) <= 6 or delays[0] != 0 or len(set(delays)) != len(delays)
            or any(not math.isfinite(v) or not 0 <= v <= 1000 for v in delays)
            or not 1 <= len(seeds) <= 5 or len(set(seeds)) != len(seeds)
            or any(s < 0 or s >= 2**32 for s in seeds) or args.drop_every < 0):
        parser.error("use 1–6 distinct delays beginning at 0, at most 1000 ms, and 1–5 distinct seeds")
    args.output.mkdir(parents=True, exist_ok=False)
    matrix = {"status": "running", "runs": [], "stopped_reason": None,
              "scope": "local action-policy delivery faults, not measured cloud or wireless latency"}
    paths = []
    cases = [(f"delay-{i}", delay, 0) for i, delay in enumerate(delays)]
    if args.drop_every:
        cases.append((f"drop-{args.drop_every}", 0, args.drop_every))
    try:
        for name, delay, drop in cases:
            output = args.output / name
            command = [sys.executable, "-m", "jetson_timing.run", "--output", str(output),
                       "--assets", args.assets, "--mode", "realtime", "--policy", "smolvla",
                       "--device", "cuda", "--precision", args.precision, "--seeds", args.seeds,
                       "--steps", "500", "--sustain", "--policy-delay-ms", str(delay), "--drop-every", str(drop)]
            for name_option in ("camera_size", "vision_size", "denoise_steps", "shadow_size", "render_samples"):
                command.extend(["--" + name_option.replace("_", "-"), str(getattr(args, name_option))])
            if args.record:
                command.append("--record")
            with (args.output / f"{name}.log").open("w") as log:
                # New process group permits cleanup of both model/camera children
                # if the outer deadline is exceeded.
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    returncode = process.wait(timeout=300 + len(seeds) * 180)
                except BaseException:
                    import os
                    import signal
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise
            if (output / "summary.json").exists() and (output / "manifest.json").exists():
                result = summarize(output)
                paths.append(output)
            else:
                result = {"run": name, "status": "runtime_error", "error": f"process exit {returncode}; no complete manifest"}
            matrix["runs"].append(result)
            write_json(args.output / "matrix.json", matrix)
            print(json.dumps(result), flush=True)
            if returncode or (name == "delay-0" and not result.get("overall_qualified")):
                matrix["stopped_reason"] = ("runtime_failure" if returncode else "baseline_task_failure"
                    if result["status"] == "passed_observed_contract" else "baseline_not_qualified")
                break
        matrix["status"] = "completed"
    except BaseException as error:
        matrix.update(status="error", stopped_reason=f"{type(error).__name__}: {error}")
        raise
    finally:
        delay_runs = [r for r in matrix["runs"] if r.get("config", {}).get("drop_every") == 0]
        passed = [r["config"]["policy_delay_ms"] for r in delay_runs
                  if r.get("overall_qualified")]
        failed = [r["config"]["policy_delay_ms"] for r in delay_runs if r["status"] == "failed"]
        best = max(passed) if passed else None
        matrix["measured_delay_bracket_ms"] = {
            "largest_tested_passing_delay": best,
            "next_tested_failing_delay": min((v for v in failed if best is None or v > best), default=None),
            "scope": "tested cases only; no interpolation, no claim about real network latency",
        }
        write_json(args.output / "matrix.json", matrix)
        if paths:
            subprocess.run([sys.executable, "-m", "jetson_timing.report", "--output", str(args.output / "report.html"),
                            *map(str, paths)], check=True)


if __name__ == "__main__":
    main()
