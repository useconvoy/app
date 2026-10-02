"""Real PostgreSQL case jobs, upgrade and concurrent lease fencing."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from alembic import command
from convoy_server import config, db
from convoy_server.migrations import migrate_database
from convoy_server.postgres import migration_config
from convoy_server.services.evaluations import claim_job, step_job
from sqlalchemy import inspect, text
from test_evaluation_lifecycle import (
    evaluate,
    pipeline,
    suite,
    test_cancel_unknown_and_revocation_do_not_start_more_cases,
    test_expired_job_lease_and_restart_never_allocate_duplicate_mission,
    test_suite_contract_scope_and_unclaimed_expiry,
    test_suite_jobs_account_for_all_cases_and_gate_promotions,
)

__all__ = [
    "pipeline",
    "test_suite_jobs_account_for_all_cases_and_gate_promotions",
    "test_cancel_unknown_and_revocation_do_not_start_more_cases",
    "test_expired_job_lease_and_restart_never_allocate_duplicate_mission",
    "test_suite_contract_scope_and_unclaimed_expiry",
]


def test_previous_revision_upgrade_preserves_existing_project(postgres_database, tmp_path):
    settings = config.Settings(database_url=postgres_database, data_dir=tmp_path)
    engine = db.make_engine(settings)
    try:
        with engine.begin() as connection:
            migration = migration_config()
            migration.attributes["connection"] = connection
            command.upgrade(migration, "0001_fleet")
            connection.execute(
                text(
                    "INSERT INTO users (id,email,name,password_hash,role,disabled,password_reset_required,created_at) "
                    "VALUES ('user-upgrade','upgrade@example.com','Upgrade','unused','admin',false,false,now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO platform_projects (id,owner_user_id,name,created_at) "
                    "VALUES ('project-upgrade','user-upgrade','preserved',now())"
                )
            )
        assert "platform_evaluations" not in inspect(engine).get_table_names()
        result = migrate_database(settings)
        assert result["from_revision"] == "0001_fleet"
        assert result["target_revision"] == "0005_robot_registry"
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT name FROM platform_projects WHERE id='project-upgrade'")
                ).scalar()
                == "preserved"
            )
        assert "platform_evaluation_cases" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_competing_evaluation_workers_have_one_admission(pipeline):  # noqa: F811
    run = evaluate(pipeline, suite(pipeline))
    barrier = Barrier(2)

    def claim(owner):
        barrier.wait(timeout=5)
        return owner, claim_job(owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [
            job.result(timeout=10) for job in [pool.submit(claim, "worker-a"), pool.submit(claim, "worker-b")]
        ]
    winners = [(owner, claim) for owner, claim in (first, second) if claim is not None]
    assert len(winners) == 1
    owner, claim = winners[0]
    assert claim[0] == run["id"]
    assert step_job(claim[0], owner, claim[1])
    loser = "worker-b" if owner == "worker-a" else "worker-a"
    assert not step_job(claim[0], loser, claim[1])


def test_evaluation_clock_advances_after_transaction_start(settings):
    from convoy_server.services.evaluations import db_now

    db.init_engine(settings)
    with db.session_scope() as session, db.write_txn(session):
        before = db_now(session)
        session.execute(text("SELECT pg_sleep(0.05)"))
        assert (db_now(session) - before).total_seconds() >= 0.04
