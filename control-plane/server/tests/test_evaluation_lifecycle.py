"""Exercise durable job admission, case accounting and promotion through real API/DB."""

from datetime import timedelta

import pytest
from conftest import login, make_user
from convoy_server.db import session_scope, write_txn
from convoy_server.evaluation_models import EvaluationRun
from convoy_server.ids import utcnow
from convoy_server.platform_models import Mission
from convoy_server.services.evaluations import claim_job, step_job
from fastapi.testclient import TestClient
from test_platform_lifecycle import pipeline as _pipeline
from test_platform_lifecycle import post

pipeline = _pipeline


def suite(p, seeds=None, minimum=2):
    return post(
        p["admin"],
        f"/api/v1/applications/{p['application']['id']}/evaluation-suites",
        {
            "name": "fixed regression",
            "reference_release_id": p["release"]["id"],
            "seeds": seeds or [0, 1],
            "min_successes": minimum,
        },
    )


def evaluate(p, spec, key="evaluation"):
    return post(
        p["admin"],
        "/api/v1/evaluations",
        {"suite_id": spec["id"], "release_id": p["release"]["id"], "robot_id": p["robot"]["id"]},
        key=key,
    )


def tick(owner="jobs-one"):
    claim = claim_job(owner)
    assert claim
    assert step_job(claim[0], owner, claim[1])
    return claim


def ready(p, run):
    tick()
    row = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    desired = p["agent"].client.get(f"{p['base']}/desired").json()
    deployment = desired["deployment"]
    assert (
        p["agent"]
        .client.post(
            f"{p['base']}/deployments/{row['deployment_id']}/report",
            json={
                "generation": deployment["generation"],
                "state": "ready",
                "release_digest": p["release"]["digest"],
            },
        )
        .status_code
        == 200
    )
    tick()


def finish_case(p, run, success, overrides=None):
    row = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    case = next(case for case in row["cases"] if case["episode_id"] is None)
    path = f"{p['base']}/missions/{case['mission_id']}"
    claimed = p["agent"].client.post(
        path + "/claim",
        json={
            "boot_id": "boot",
            "incarnation": "coordinator",
            "authority_epoch": 1,
        },
    )
    assert claimed.status_code == 200, claimed.text
    identity = claimed.json()["identity"]
    assert (
        p["agent"].client.post(path + "/report", json={"identity": identity, "state": "running"}).status_code
        == 200
    )
    response = p["agent"].client.post(
        path + "/report",
        json={
            "identity": identity,
            "state": "completed",
            "summary": {
                "seed": case["seed"],
                "execution_mode": "lockstep_offline",
                "final_success": success,
                "wall_duration_s": 1.0,
                "steps": 100,
                "policy_runtime": p["release"]["manifest"]["policy"]["runtime"],
                **(overrides or {}),
            },
        },
    )
    assert response.status_code == 200, response.text
    return case["mission_id"]


def test_suite_jobs_account_for_all_cases_and_gate_promotions(pipeline):
    p = pipeline
    spec = suite(p)
    assert spec == suite(p)
    gate_path = f"/api/v1/applications/{p['application']['id']}/evaluation-gate"
    post(p["admin"], gate_path, {"suite_id": spec["id"], "expected_generation": 0}, expected=200)
    request = {"robot_id": p["robot"]["id"], "release_id": p["release"]["id"], "expected_generation": 1}
    post(p["admin"], "/api/v1/deployments", request, key="unqualified", expected=409)
    failed = evaluate(p, spec)
    assert failed == evaluate(p, spec)
    assert len(failed["cases"]) == 2 and all(case["mission_id"] is None for case in failed["cases"])
    post(p["admin"], "/api/v1/deployments", request, key="reserved", expected=409)
    ready(p, failed)
    first = finish_case(p, failed, True)
    tick()
    second = finish_case(p, failed, False)
    assert first != second
    tick()
    failed_result = p["admin"].get(f"/api/v1/evaluations/{failed['id']}").json()
    assert failed_result["state"] == "completed"
    assert failed_result["report"]["successes"] == 1 and failed_result["report"]["case_count"] == 2
    assert failed_result["report"]["passed"] is False
    post(p["admin"], f"/api/v1/evaluations/{failed['id']}/promote", {}, expected=409)
    passed = evaluate(p, spec, key="repeat")
    ready(p, passed)
    finish_case(p, passed, True)
    tick()
    finish_case(p, passed, True)
    tick()
    promoted = post(p["admin"], f"/api/v1/evaluations/{passed['id']}/promote", {})
    assert promoted["release_id"] == p["release"]["id"]
    assert (
        p["admin"]
        .get(f"/api/v1/evaluations/{passed['id']}?baseline_id={failed['id']}")
        .json()["success_count_delta"]
        == 1
    )
    request["expected_generation"] = 3
    assert post(p["admin"], "/api/v1/deployments", request, key="qualified")["generation"] == 4


def test_expired_job_lease_and_restart_never_allocate_duplicate_mission(pipeline):
    p = pipeline
    run = evaluate(p, suite(p))
    ready(p, run)
    before = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    old = claim_job("jobs-one")
    assert claim_job("jobs-two") is None
    with session_scope() as db, write_txn(db):
        db.get(EvaluationRun, run["id"]).lease_until = utcnow() - timedelta(seconds=2)
    new = claim_job("jobs-two")
    assert new[1] > old[1]
    assert step_job(old[0], "jobs-one", old[1]) is False
    assert step_job(new[0], "jobs-two", new[1]) is True
    after = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    assert before["cases"] == after["cases"]
    assert len(p["admin"].get(f"/api/v1/missions?project_id={p['project']['id']}").json()) == 1


def test_cancel_unknown_and_revocation_do_not_start_more_cases(pipeline):
    p = pipeline
    run = evaluate(p, suite(p))
    ready(p, run)
    row = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    path = f"{p['base']}/missions/{row['cases'][0]['mission_id']}"
    identity = (
        p["agent"]
        .client.post(
            path + "/claim",
            json={
                "boot_id": "boot",
                "incarnation": "coordinator",
                "authority_epoch": 1,
            },
        )
        .json()["identity"]
    )
    p["agent"].client.post(path + "/report", json={"identity": identity, "state": "unknown"})
    tick()
    unknown = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    assert unknown["state"] == "unknown" and unknown["cases"][1]["mission_id"] is None
    post(
        p["admin"],
        "/api/v1/evaluations",
        {"suite_id": run["suite_id"], "release_id": p["release"]["id"], "robot_id": p["robot"]["id"]},
        key="conflicting",
        expected=409,
    )
    post(p["admin"], f"/api/v1/evaluations/{run['id']}/cancel", {"reason": "stop"}, expected=200)
    tick()
    assert p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()["state"] == "cancel_requested"
    p["agent"].client.post(path + "/report", json={"identity": identity, "state": "cancelled"})
    tick()
    final = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    assert final["state"] == "cancelled" and final["report"]["passed"] is False
    assert final["cases"][1]["mission_id"] is None
    next_run = evaluate(p, suite(p), key="revoked")
    post(p["admin"], "/api/v1/auth/logout", {}, expected=200)
    tick()
    with session_scope() as db:
        assert db.get(EvaluationRun, next_run["id"]).state == "cancelled"


def test_suite_contract_scope_and_unclaimed_expiry(pipeline, app):
    p = pipeline
    spec = suite(p)
    make_user(p["admin"], "other-eval@example.com", "operator")
    with TestClient(app) as other:
        login(other, "other-eval@example.com", "password-123")
        assert other.get(f"/api/v1/evaluation-suites/{spec['id']}").status_code == 404
    manifest = {
        **p["release"]["manifest"],
        "execution": {**p["release"]["manifest"]["execution"], "max_steps": 90},
    }
    release = post(
        p["admin"],
        f"/api/v1/applications/{p['application']['id']}/releases",
        {"manifest": manifest},
        key="changed",
    )
    post(
        p["admin"],
        "/api/v1/evaluations",
        {"suite_id": spec["id"], "robot_id": p["robot"]["id"], "release_id": release["id"]},
        expected=409,
    )
    run = evaluate(p, spec, key="expire")
    ready(p, run)
    current = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()["cases"][0]
    with session_scope() as db, write_txn(db):
        db.get(Mission, current["mission_id"]).expires_at = utcnow() - timedelta(seconds=1)
    tick()
    tick()
    row = p["admin"].get(f"/api/v1/evaluations/{run['id']}").json()
    assert row["cases"][0]["episode_id"] is not None
    assert row["cases"][1]["mission_id"] != current["mission_id"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"steps": 0},
        {"steps": 1},
        {"seed": True},
        {"policy_runtime": "other"},
        {"wall_duration_s": None},
        {"wall_duration_s": 10**400},
    ],
)
def test_incomplete_or_mismatched_evidence_never_passes(pipeline, overrides):
    p = pipeline
    run = evaluate(p, suite(p, seeds=[1], minimum=1))
    ready(p, run)
    finish_case(p, run, True, overrides)
    tick()
    report = p["admin"].get("/api/v1/evaluations/" + run["id"]).json()["report"]
    assert report["passed"] is False and report["cases"][0]["evidence_valid"] is False
