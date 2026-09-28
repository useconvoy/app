"""Qualify an actual owned text gateway together with pretrained visual actions.

No model is substituted or downloaded. The explicit gateway stays owned by its
existing process; this harness owns only its API/planner/action/simulator services.
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
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
    validate_plan_result,
    validate_release_manifest,
)
from convoy_lerobot.acceptance import collect_evidence
from convoy_lerobot.artifact import reference_manifest
from convoy_planner.artifact import artifact_descriptor
from convoy_planner.backend import GatewayBackend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gateway-url", required=True, help="owned loopback gateway or explicit local tunnel")
    parser.add_argument("--planner-placement", required=True,
                        choices=("development-local", "development-jetson-lan"))
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--direct-report", type=Path)
    parser.add_argument("--direct-trace", type=Path)
    args = parser.parse_args()
    identity = GatewayBackend(args.gateway_url).inspect()
    descriptor = artifact_descriptor(identity)  # rejects missing/simulated model provenance
    manifest = validate_release_manifest({
        "schema_version": 2, "profile": PAIRED_PROFILE, "action_manifest": reference_manifest(),
        "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": canonical_digest(descriptor),
                    "protocol_sha256": PLANNER_PROTOCOL_SHA256},
        "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
        "planning": {"timeout_ms": 30000},
        "placement": {"policy": "development-local-cpu", "planner": args.planner_placement},
    })
    spec = importlib.util.spec_from_file_location("convoy_real_pipeline", Path(__file__).with_name("pipeline.py"))
    pipeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline)
    output = args.output.resolve()

    def interrupted(*_):
        raise KeyboardInterrupt("real planner pipeline interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        pipeline.run(output, manifest=manifest, serve=args.serve, repeat=args.repeat,
                     runtime_factory="convoy_lerobot.runtime:smolvla",
                     coordinator_module="convoy_agent.coordinator.paired", policy_kind="pretrained_vision_language_action",
                     planner_command=("convoy_planner.cli", "serve", "--gateway-url", args.gateway_url),
                     planner_evidence="actual pinned text-model inference through the owned gateway",
                     planner_backend_kind="llamacpp-text-model")
    finally:
        signal.signal(signal.SIGTERM, previous)
    # Only write into the directory after this run has successfully owned it.
    # A reused output path must fail without changing a previous run's evidence.
    (output / "planner-identity.json").write_text(json.dumps({
        "gateway_identity": identity, "artifact": descriptor,
        "configured_placement": manifest["placement"],
    }, indent=2) + "\n")
    if args.serve:
        return
    collect_evidence(output, args.direct_report, args.direct_trace)
    result = json.loads((output / "visual-result.json").read_text())
    with sqlite3.connect(f"file:{output / 'robot/coordinator/execution.sqlite3'}?mode=ro", uri=True) as journal:
        rows = journal.execute("SELECT mission_id,request_json,result_json,state FROM plans ORDER BY mission_id").fetchall()
    if len(rows) != len(result["cases"]):
        raise ValueError("every mission requires its original recorded planner proposal")
    plans = []
    for mission, request, response, state in rows:
        plan = validate_plan_result(json.loads(response))
        if state != "accepted" or plan["planner_artifact_sha256"] != manifest["planner"]["artifact_sha256"]:
            raise ValueError("planner evidence does not match the qualified real model")
        plans.append({"mission_id": mission, "request": json.loads(request), "result": plan, "state": state})
    result.update(planner_backend_kind="llamacpp-text-model", planner_artifact=descriptor,
                  gateway_identity_before=identity, plans=plans,
                  qualification_scope="actual text-model admission plus SmolVLA/MuJoCo; fixed task, seed0, "
                                      "offline lockstep; placement is explicit, not hardware or real-time qualification")
    (output / "real-planner-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "planner_backend_kind": "llamacpp-text-model",
                      "missions": len(result["cases"]), "plans": len(plans)}))


if __name__ == "__main__":
    main()
