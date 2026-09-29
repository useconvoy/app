"""Shared durable plan and task-outcome checks; no service or container ownership."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from convoy_contracts.execution import canonical_digest
from convoy_contracts.pairing import PLAN_ECHO_FIELDS, SKILL_ID, validate_plan_result


def collect_plan(output: Path, manifest: dict, probe: dict) -> dict:
    with sqlite3.connect(f"{(output / 'robot/coordinator/execution.sqlite3').as_uri()}?mode=ro", uri=True) as journal:
        rows = journal.execute("SELECT mission_id,request_json,result_json,state FROM plans").fetchall()
    if len(rows) != 1:
        raise RuntimeError("the one mission must have exactly one durable planner request")
    mission, request, response, state = rows[0]
    request, result = json.loads(request), validate_plan_result(json.loads(response))
    if (state != "accepted" or result["identity"]["mission_id"] != mission
            or any(result[key] != request[key] for key in PLAN_ECHO_FIELDS)
            or any(result[key] != value for key, value in probe.items())
            or result["identity"]["release_digest"] != canonical_digest(manifest)
            or result["decision"] != {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}}):
        raise RuntimeError("durable plan differs from the live qualified planner or mission")
    return {"mission_id": mission, "state": state, "request": request, "result": result}


def record_outcome(evidence: dict, result: dict, *, require_comparison: bool) -> dict:
    """Retain the measured comparison even when it disqualifies this run."""
    case = result["cases"][0]
    comparison = result.get("direct_comparison")
    evidence.update(mission=case["mission"], episode=case["episode"], direct_comparison=comparison)
    summary = case["episode"]["summary"]
    if case["mission"]["state"] != "completed" or summary["final_success"] is not True:
        raise RuntimeError("the managed mission did not complete the qualified task")
    steps = summary["steps"]
    if require_comparison and (
        not isinstance(comparison, dict) or comparison.get("exact_actions_equal") is not True
        or comparison.get("direct_success") is not True or type(steps) is not int or steps < 1
        or comparison.get("direct_steps") != steps or comparison.get("managed_steps") != steps
    ):
        raise RuntimeError("the supplied direct baseline differs from the successful managed rollout")
    return case

