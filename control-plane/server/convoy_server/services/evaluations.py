"""Repeatable evaluation jobs over normal deployments and missions.

Each case is allocated before execution. Retrying/restarting the job observes its
original mission; it never selects only successful episodes or replays a command.
All job steps are short database transactions; model code runs in the worker.
"""

from __future__ import annotations

import math
from datetime import timedelta
from statistics import median

from convoy_contracts.execution import PROFILE, canonical_digest
from convoy_contracts.pairing import (
    PAIRED_PROFILE,
    action_manifest,
    evaluation_contract,
    validate_plan_result,
)
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import Principal, assert_live_principal, audit
from ..db import session_scope, write_txn
from ..evaluation_models import (
    EvaluationCase,
    EvaluationGate,
    EvaluationRun,
    EvaluationSuite,
    ReleasePromotion,
)
from ..ids import aware, iso, new_id, utcnow
from ..models import User
from ..platform_models import Application, ApplicationRelease, Deployment, Episode, Mission, Robot
from . import platform

TERMINAL = ("completed", "failed", "cancelled")
LEASE_SECONDS = 15


def contract(manifest: dict) -> dict:
    return evaluation_contract(manifest)


def suite_out(row: EvaluationSuite) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "application_id": row.application_id,
        "digest": row.digest,
        "spec": row.spec,
        "created_at": iso(row.created_at),
    }


def cases_for(db: Session, run_id: str) -> list[EvaluationCase]:
    return list(
        db.scalars(
            select(EvaluationCase)
            .where(EvaluationCase.evaluation_id == run_id)
            .order_by(EvaluationCase.position)
        )
    )


def run_out(db: Session, row: EvaluationRun) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "suite_id": row.suite_id,
        "release_id": row.release_id,
        "robot_id": row.robot_id,
        "deployment_id": row.deployment_id,
        "state": row.state,
        "detail": row.detail,
        "report": row.report,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        "cases": [
            {
                "position": case.position,
                "seed": case.seed,
                "mission_id": case.mission_id,
                "episode_id": case.episode_id,
            }
            for case in cases_for(db, row.id)
        ],
    }


def create_suite(db: Session, p: Principal, application_id: str, data: dict) -> dict:
    app = platform.resource_for(db, Application, application_id, p)
    release = db.get(ApplicationRelease, data["reference_release_id"])
    if release is None or release.application_id != app.id:
        raise HTTPException(404, "reference release not found")
    seeds = data["seeds"]
    if len(set(seeds)) != len(seeds) or data["min_successes"] > len(seeds):
        raise HTTPException(422, "seeds must be distinct and min_successes cannot exceed case count")
    spec = {
        "schema_version": 1,
        "name": data["name"],
        "contract": contract(release.manifest),
        "seeds": seeds,
        "min_successes": data["min_successes"],
        "scorer": "final-success-v1",
        "scope": "lockstep_simulation; no hardware or real-time qualification",
    }
    digest = canonical_digest(spec)
    row = db.scalar(
        select(EvaluationSuite).where(
            EvaluationSuite.application_id == app.id, EvaluationSuite.digest == digest
        )
    )
    if row is None:
        row = EvaluationSuite(
            id=new_id("esu"), project_id=app.project_id, application_id=app.id, digest=digest, spec=spec
        )
        db.add(row)
        db.flush()
    return suite_out(row)


def active_for_robot(db: Session, robot_id: str) -> EvaluationRun | None:
    return db.scalar(
        select(EvaluationRun).where(EvaluationRun.robot_id == robot_id, EvaluationRun.state.not_in(TERMINAL))
    )


def require_available(db: Session, robot_id: str, evaluation_id: str | None = None) -> None:
    active = active_for_robot(db, robot_id)
    if active and active.id != evaluation_id:
        raise HTTPException(409, "robot is reserved by an active or unresolved evaluation")


def require_promotion(db: Session, release: ApplicationRelease) -> None:
    gate = db.get(EvaluationGate, release.application_id)
    if gate and not db.scalar(
        select(ReleasePromotion.id).where(
            ReleasePromotion.release_id == release.id,
            ReleasePromotion.suite_id == gate.suite_id,
        )
    ):
        raise HTTPException(409, "release needs a passing promotion for the application's current suite")


def create_run(db: Session, p: Principal, data: dict) -> dict:
    platform.dispatch_allowed(db)
    suite = platform.resource_for(db, EvaluationSuite, data["suite_id"], p)
    robot = platform.resource_for(db, Robot, data["robot_id"], p)
    release = db.get(ApplicationRelease, data["release_id"])
    if (
        release is None
        or release.application_id != suite.application_id
        or robot.project_id != suite.project_id
    ):
        raise HTTPException(404, "release or robot not found in suite application/project")
    if contract(release.manifest) != suite.spec["contract"] or robot.profile != release.manifest["profile"]:
        raise HTTPException(409, "candidate interface, environment or execution envelope differs from suite")
    if release.manifest["profile"] == PAIRED_PROFILE:
        # Only the API admits this run and issues later mission grants. The
        # separate evaluation job needs database access, never either signer.
        platform.validate_execution_signing(paired=True)
    require_available(db, robot.id)
    if platform.unresolved_mission(db, robot):
        raise HTTPException(409, "robot has an active or unresolved mission")
    ttl = min(300, action_manifest(release.manifest)["execution"]["mission_timeout_s"])
    row = EvaluationRun(
        id=new_id("eva"),
        project_id=suite.project_id,
        suite_id=suite.id,
        robot_id=robot.id,
        release_id=release.id,
        requested_by=p.user.id,
        credential_kind=p.via,
        credential_id=p.token_id,
        expires_at=utcnow() + timedelta(seconds=120 + len(suite.spec["seeds"]) * (ttl + 10)),
    )
    db.add(row)
    db.flush()
    for position, seed in enumerate(suite.spec["seeds"]):
        db.add(EvaluationCase(id=new_id("eca"), evaluation_id=row.id, position=position, seed=seed))
    db.flush()
    return run_out(db, row)


def cancel_run(db: Session, p: Principal, run_id: str, data: dict) -> dict:
    row = platform.resource_for(db, EvaluationRun, run_id, p)
    if row.state not in TERMINAL:
        row.state = "cancel_requested"
        row.detail = data["reason"] or "operator requested cancellation"
        row.updated_at = utcnow()
        # Fence future job admission and request active mission cancellation in
        # the same transaction; the device acknowledgement still owns the outcome.
        for case in cases_for(db, row.id):
            mission = db.get(Mission, case.mission_id) if case.mission_id else None
            if mission and mission.state not in platform.TERMINAL:
                mission.state = "cancel_requested"
                mission.detail = "evaluation cancellation requested"
                mission.updated_at = utcnow()
    return run_out(db, row)


def set_gate(db: Session, p: Principal, application_id: str, data: dict) -> dict:
    platform.resource_for(db, Application, application_id, p)
    suite = platform.resource_for(db, EvaluationSuite, data["suite_id"], p)
    if suite.application_id != application_id:
        raise HTTPException(404, "suite not found in application")
    row = db.get(EvaluationGate, application_id)
    if (row.generation if row else 0) != data["expected_generation"]:
        raise HTTPException(409, "evaluation gate generation changed")
    if row is None:
        row = EvaluationGate(application_id=application_id, suite_id=suite.id, generation=1)
        db.add(row)
    else:
        row.suite_id, row.generation = suite.id, row.generation + 1
    return {"application_id": application_id, "suite_id": row.suite_id, "generation": row.generation}


def promote(db: Session, p: Principal, run_id: str) -> dict:
    run = platform.resource_for(db, EvaluationRun, run_id, p)
    if run.state != "completed" or not run.report or run.report.get("passed") is not True:
        raise HTTPException(409, "only a complete passing evaluation can promote a release")
    suite = db.get(EvaluationSuite, run.suite_id)
    row = db.scalar(select(ReleasePromotion).where(ReleasePromotion.evaluation_id == run.id))
    if row is None:
        row = ReleasePromotion(
            id=new_id("pro"),
            project_id=run.project_id,
            application_id=suite.application_id,
            release_id=run.release_id,
            evaluation_id=run.id,
            suite_id=suite.id,
            created_by=p.user.id,
        )
        db.add(row)
        db.flush()
    return {
        "id": row.id,
        "release_id": row.release_id,
        "evaluation_id": row.evaluation_id,
        "suite_id": row.suite_id,
        "created_at": iso(row.created_at),
    }


def compare(db: Session, p: Principal, run_id: str, baseline_id: str) -> dict:
    candidate = platform.resource_for(db, EvaluationRun, run_id, p)
    baseline = platform.resource_for(db, EvaluationRun, baseline_id, p)
    if candidate.suite_id != baseline.suite_id:
        raise HTTPException(409, "comparison requires the same immutable suite")
    if candidate.report is None or baseline.report is None:
        raise HTTPException(409, "both evaluations must have terminal reports")
    return {
        "candidate": run_out(db, candidate),
        "baseline": run_out(db, baseline),
        "success_count_delta": candidate.report["successes"] - baseline.report["successes"],
        "scope": "paired fixed seeds, not statistical proof of general reliability",
    }


def finish_run(db: Session, row: EvaluationRun, state: str, detail: str) -> None:
    suite = db.get(EvaluationSuite, row.suite_id)
    release = db.get(ApplicationRelease, row.release_id)
    policy = action_manifest(release.manifest)
    cases = []
    durations = []
    for case in cases_for(db, row.id):
        episode = db.get(Episode, case.episode_id) if case.episode_id else None
        summary = episode.summary if episode else {}
        steps = summary.get("steps")
        duration = summary.get("wall_duration_s")
        maximum = policy["execution"]["max_steps"]
        planner_valid = True
        if release.manifest["profile"] == PAIRED_PROFILE:
            planner_valid = False
            try:
                proposal = validate_plan_result(summary.get("planner_result"))
                planner_valid = (
                    summary.get("planner_accepted") is True
                    and proposal["identity"] == episode.identity
                    and proposal["planner_artifact_sha256"] == release.manifest["planner"]["artifact_sha256"]
                    and proposal["decision"] == {
                        "kind": "skill", "skill_id": release.manifest["task"]["skill_id"], "parameters": {},
                    }
                )
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                pass
        valid = (
            episode is not None
            and episode.release_digest == release.digest
            and type(summary.get("seed")) is int
            and summary["seed"] == case.seed
            and summary.get("execution_mode") == "lockstep_offline"
            and summary.get("policy_runtime") == policy["policy"]["runtime"]
            and planner_valid
            and type(steps) is int
            and 1 <= steps <= maximum
            and (policy["profile"] != PROFILE or steps == maximum)
            and type(duration) in (int, float)
            and 0 <= duration <= 86400
            and math.isfinite(duration)
        )
        passed = bool(valid and episode.state == "completed" and summary.get("final_success") is True)
        if valid:
            durations.append(duration)
        cases.append(
            {
                "seed": case.seed,
                "mission_id": case.mission_id,
                "episode_id": case.episode_id,
                "state": episode.state if episode else "not_completed",
                "passed": passed,
                "evidence_valid": valid,
                "steps": steps if type(steps) is int and 0 <= steps <= maximum else None,
                "wall_duration_s": duration if valid else None,
            }
        )
    successes = sum(case["passed"] for case in cases)
    row.state, row.detail = state, detail
    row.report = {
        "schema_version": 1,
        "suite_digest": suite.digest,
        "release_digest": release.digest,
        "scorer": suite.spec["scorer"],
        "case_count": len(cases),
        "successes": successes,
        "min_successes": suite.spec["min_successes"],
        "passed": state == "completed" and successes >= suite.spec["min_successes"],
        "cases": cases,
        "median_wall_s": median(durations) if durations else None,
        "scope": suite.spec["scope"],
        "policy": policy["policy"],
    }
    if release.manifest["profile"] == PAIRED_PROFILE:
        row.report = {**row.report, "planner": release.manifest["planner"]}
    row.lease_owner = row.lease_until = None
    row.updated_at = utcnow()
    audit(db, None, "evaluation.finished", row.id, state=state, passed=row.report["passed"])


def db_now(db: Session):
    clock = func.clock_timestamp() if db.get_bind().dialect.name == "postgresql" else func.current_timestamp()
    return aware(db.scalar(select(clock)))


def claim_job(owner: str) -> tuple[str, int] | None:
    with session_scope() as db, write_txn(db):
        now = db_now(db)
        row = db.scalar(
            select(EvaluationRun)
            .where(
                EvaluationRun.state.not_in(TERMINAL),
                or_(
                    EvaluationRun.lease_until.is_(None),
                    EvaluationRun.lease_until <= now,
                    EvaluationRun.lease_owner == owner,
                ),
            )
            .order_by(EvaluationRun.updated_at, EvaluationRun.id)
            .limit(1)
        )
        if row is None:
            return None
        if row.lease_owner != owner or row.lease_until is None or aware(row.lease_until) <= now:
            row.lease_epoch += 1
        row.lease_owner, row.lease_until = owner, now + timedelta(seconds=LEASE_SECONDS)
        return row.id, row.lease_epoch


def step_job(run_id: str, owner: str, epoch: int) -> bool:
    """A fenced transaction reconciles one step; no network or model call holds the lock."""
    with session_scope() as db, write_txn(db):
        row = db.get(EvaluationRun, run_id)
        now = db_now(db)
        if (
            row is None
            or row.state in TERMINAL
            or row.lease_owner != owner
            or row.lease_epoch != epoch
            or row.lease_until is None
            or aware(row.lease_until) <= now
        ):
            return False
        row.updated_at = now
        cases = cases_for(db, row.id)
        current = next((case for case in cases if case.episode_id is None), None)
        mission = db.get(Mission, current.mission_id) if current and current.mission_id else None
        # Always collect a known outcome before deciding whether another case can
        # start. Unknown authority never turns into a retry or a new mission.
        if mission and mission.state in platform.TERMINAL:
            current.episode_id = mission.episode_id
            db.flush()
            if row.state == "unknown":
                finish_run(db, row, "failed", "execution reconciled after unknown; no further cases admitted")
                return True
            current = next((case for case in cases if case.episode_id is None), None)
            mission = db.get(Mission, current.mission_id) if current and current.mission_id else None
        user = db.get(User, row.requested_by)
        p = Principal(user, row.credential_kind, row.credential_id) if user else None
        try:
            if p is None:
                raise HTTPException(401, "requester unavailable")
            assert_live_principal(db, p, "operator")
        except HTTPException:
            row.state, row.detail = "cancel_requested", "evaluation authorization is no longer valid"
        if aware(row.expires_at) <= now and row.state not in ("cancel_requested", "unknown"):
            row.state, row.detail = "cancel_requested", "evaluation time budget expired"
        if row.state == "cancel_requested":
            if mission and mission.state not in platform.TERMINAL:
                mission.state, mission.detail = "cancel_requested", "evaluation stopped"
                mission.updated_at = now
            else:
                finish_run(db, row, "cancelled", row.detail)
                return True
        if mission:
            if mission.identity is None and (
                mission.state == "cancel_requested" or aware(mission.expires_at) <= now
            ):
                # No executor has acquired authority; transaction serialization
                # prevents a racing claim from appearing after this outcome.
                state = "cancelled" if mission.state == "cancel_requested" else "failed"
                platform.finish(
                    db,
                    mission,
                    {
                        "identity": None,
                        "state": state,
                        "detail": "unclaimed evaluation case stopped",
                        "summary": {},
                    },
                )
                return True
            if mission.state == "unknown" or now > aware(mission.expires_at) + timedelta(seconds=10):
                row.state, row.detail = "unknown", "mission outcome unresolved; robot remains reserved"
            return True
        if row.state == "unknown":
            return True
        if current is None:
            finish_run(db, row, "completed", "all allocated cases have terminal outcomes")
            return True
        try:
            platform.dispatch_allowed(db)
        except HTTPException:
            return True
        robot = db.get(Robot, row.robot_id)
        if row.deployment_id is None:
            deployed = platform.create_deployment(
                db,
                p,
                {"robot_id": robot.id, "release_id": row.release_id, "expected_generation": robot.generation},
                evaluation_id=row.id,
            )
            row.deployment_id, row.state = deployed["id"], "deploying"
            return True
        deployment = db.get(Deployment, row.deployment_id)
        if deployment.state == "blocked" or deployment.generation != robot.generation:
            finish_run(db, row, "failed", "evaluation release could not become ready")
            return True
        if deployment.state != "ready":
            return True
        release = db.get(ApplicationRelease, row.release_id)
        started = platform.create_mission(
            db,
            p,
            robot.id,
            {
                "deployment_id": deployment.id,
                "expected_generation": deployment.generation,
                "seed": current.seed,
                "ttl_s": min(300, action_manifest(release.manifest)["execution"]["mission_timeout_s"]),
            },
            evaluation_id=row.id,
        )
        current.mission_id, row.state = started["id"], "running"
        return True
