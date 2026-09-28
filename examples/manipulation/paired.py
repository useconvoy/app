"""Actual SmolVLA physics with an explicitly controlled fixed-skill planner.

The planner is a deterministic protocol fixture, not a learned text model or a
Jetson execution claim. API, planner, action worker and simulator are processes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import signal
import sqlite3
from pathlib import Path

from convoy_contracts.execution import canonical_digest
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    CONTROLLED_PLANNER_RUNTIME,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    SKILL_ID,
    validate_release_manifest,
)
from convoy_lerobot.acceptance import collect_evidence
from convoy_lerobot.artifact import reference_manifest
from convoy_planner.controlled import BACKEND_KIND, controlled_descriptor


def paired_manifest():
    return validate_release_manifest({
        "schema_version": 2, "profile": PAIRED_PROFILE, "action_manifest": reference_manifest(),
        "planner": {"runtime": CONTROLLED_PLANNER_RUNTIME, "artifact_sha256": canonical_digest(controlled_descriptor()),
                    "protocol_sha256": PLANNER_PROTOCOL_SHA256},
        "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
        "planning": {"timeout_ms": 30000},
        "placement": {"policy": "development-local-cpu", "planner": "development-local-controlled"},
    })


def collect_pairing_evidence(output: Path, direct_report: Path | None, direct_trace: Path | None):
    collect_evidence(output, direct_report, direct_trace)
    result = json.loads((output / "visual-result.json").read_text())
    with sqlite3.connect(f"file:{output / 'robot/coordinator/execution.sqlite3'}?mode=ro", uri=True) as journal:
        plans = journal.execute("SELECT mission_id,request_json,result_json,state FROM plans ORDER BY mission_id").fetchall()
    result.update(planner_backend_kind=BACKEND_KIND, planner_artifact=controlled_descriptor(),
        qualification_scope="controlled fixed-skill admission plus actual pretrained SmolVLA and MuJoCo; "
                            "seed0 CPU offline lockstep; no text-model, Jetson, physical or real-time qualification",
        plans=[{"mission_id": mission, "request": json.loads(request),
                "result": json.loads(response) if response else None, "state": state}
               for mission, request, response, state in plans])
    if len(plans) != len(result["cases"]) or any(row[3] != "accepted" for row in plans):
        raise RuntimeError("paired rollout lacks an accepted durable plan for every mission")
    (output / "paired-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "planner_backend_kind": BACKEND_KIND,
                      "missions": len(result["cases"]), "plans": len(plans)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--serve", action="store_true", help="leave ready, idle services for console evaluation")
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--direct-report", type=Path)
    parser.add_argument("--direct-trace", type=Path)
    args = parser.parse_args()
    module_spec = importlib.util.spec_from_file_location("convoy_paired_pipeline", Path(__file__).with_name("pipeline.py"))
    pipeline = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(pipeline)

    def interrupted(*_):
        raise KeyboardInterrupt("paired pipeline interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        pipeline.run(args.output.resolve(), manifest=paired_manifest(), serve=args.serve, postgres=args.postgres,
                     runtime_factory="convoy_lerobot.runtime:smolvla",
                     coordinator_module="convoy_agent.coordinator.paired", policy_kind="pretrained_vision_language_action",
                     repeat=args.repeat, planner_command=("convoy_planner.controlled",),
                     planner_evidence="deterministic fixed-skill admission; no text-model inference",
                     planner_backend_kind=BACKEND_KIND)
    finally:
        signal.signal(signal.SIGTERM, previous)
    if not args.serve:
        collect_pairing_evidence(args.output.resolve(), args.direct_report, args.direct_trace)


if __name__ == "__main__":
    main()
