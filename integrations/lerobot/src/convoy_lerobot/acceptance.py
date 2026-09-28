"""Run the real managed visual lifecycle and retain an inspectable action trace."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

from convoy_contracts.execution import canonical_digest

from .artifact import ARTIFACT, reference_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), default=1,
                        help="repeat seed0 to check per-mission policy reset")
    parser.add_argument("--direct-report", type=Path, help="optional previous direct qualification report")
    parser.add_argument("--direct-trace", type=Path, help="optional previous direct qualification trace")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location("convoy_reference_pipeline", root / "examples/manipulation/pipeline.py")
    pipeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline)
    pipeline.run(args.output.resolve(), manifest=reference_manifest(),
                          runtime_factory="convoy_lerobot.runtime:smolvla",
                          coordinator_module="convoy_lerobot.managed", policy_kind="pretrained_vision_language_action", repeat=args.repeat)
    collect_evidence(args.output, args.direct_report, args.direct_trace)


def collect_evidence(output: Path, direct_report: Path | None = None, direct_trace: Path | None = None):
    result = json.loads((output / "pipeline-result.json").read_text())
    result["artifact"] = ARTIFACT
    result["qualification_scope"] = "one seed, CPU float32, offline lockstep; not real-time or physical qualification"
    journal_path = output / "robot/coordinator/execution.sqlite3"
    with sqlite3.connect(f"file:{journal_path}?mode=ro", uri=True) as journal:
        rows = journal.execute("SELECT mission_id,request_json,result_json,observation_json,state FROM commands ORDER BY mission_id,sequence").fetchall()
    trace = []
    for mission_id, request_json, result_json, outcome_json, state in rows:
        request, decision, outcome = json.loads(request_json), json.loads(result_json), json.loads(outcome_json)
        trace.append({"mission_id": mission_id, "sequence": request["sequence"], "observation_sha256": canonical_digest(request["observation"]),
                      "action": decision["action"], "policy_duration_ms": decision["policy_duration_ms"],
                      "reward": outcome["reward"], "success": outcome["success"], "command_state": state})
    (output / "actions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in trace))
    first_mission = result["cases"][0]["mission"]["id"]
    first_trace = [row for row in trace if row["mission_id"] == first_mission]
    if len(result["cases"]) > 1:
        actions = [row["action"] for row in first_trace]
        result["per_mission_reset_check"] = {
            "missions": len(result["cases"]), "same_seed": 0,
            "exact_actions_equal": all(
                [row["action"] for row in trace if row["mission_id"] == case["mission"]["id"]] == actions
                for case in result["cases"]
            ),
        }
    if direct_report and direct_trace:
        direct = json.loads(direct_report.read_text())
        direct_records = [json.loads(line) for line in direct_trace.read_text().splitlines()]
        actions_equal = len(first_trace) == len(direct_records) and all(
            managed["action"] == raw["applied_action"] for managed, raw in zip(first_trace, direct_records, strict=False)
        )
        result["direct_comparison"] = {
            "report_sha256": hashlib.sha256(direct_report.read_bytes()).hexdigest(),
            "trace_sha256": hashlib.sha256(direct_trace.read_bytes()).hexdigest(),
            "direct_steps": direct["episode"]["steps"], "managed_steps": len(first_trace),
            "direct_success": direct["episode"]["final_success"],
            "exact_actions_equal": actions_equal,
            "note": "Same checkpoint, seed0, upstream camera and one-action replanning. Different processes/timing.",
        }
    (output / "visual-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "steps": len(trace), "success": trace[-1]["success"],
                      "direct_comparison": result.get("direct_comparison")}))


if __name__ == "__main__":
    main()
