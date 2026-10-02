"""Offline evaluations on real PostgreSQL: the same HTTP contract plus the explicit 0004 upgrade."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from convoy_server import config, db
from convoy_server.migrations import migrate_database
from convoy_server.models import Base
from convoy_server.postgres import migration_config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from test_offline_evaluations import (  # reuse the same HTTP contract on the real backend
    evaluation,
    pipeline,
    test_an_episode_replays_in_the_hosted_replay_shape,
    test_body_is_read_only_after_authentication_ownership_and_admission,
    test_counts_are_bounded,
    test_episode_uploads_are_validated_whole,
    test_evaluations_are_owner_scoped_unsigned_and_need_the_operator_role,
    test_frames_share_the_recording_store_and_its_quota,
    test_interrupted_writes_are_settled_by_the_next_write,
    test_offline_files_count_against_hosted_recording_uploads,
    test_request_bodies_are_bounded_before_decoding,
    test_uploads_are_idempotent_deduplicated_audited_and_keep_compact_receipts,
    test_writes_are_rate_limited_per_account,
)

# Imported fixtures and scenarios are intentionally collected by pytest here.
__all__ = [
    "evaluation",
    "pipeline",
    "test_an_episode_replays_in_the_hosted_replay_shape",
    "test_body_is_read_only_after_authentication_ownership_and_admission",
    "test_counts_are_bounded",
    "test_episode_uploads_are_validated_whole",
    "test_evaluations_are_owner_scoped_unsigned_and_need_the_operator_role",
    "test_frames_share_the_recording_store_and_its_quota",
    "test_interrupted_writes_are_settled_by_the_next_write",
    "test_offline_files_count_against_hosted_recording_uploads",
    "test_request_bodies_are_bounded_before_decoding",
    "test_uploads_are_idempotent_deduplicated_audited_and_keep_compact_receipts",
    "test_writes_are_rate_limited_per_account",
]


def test_previous_revision_upgrade_adds_offline_evaluations(postgres_database, tmp_path):
    settings = config.Settings(database_url=postgres_database, data_dir=tmp_path)
    engine = db.make_engine(settings)
    # Bound values throughout: text() reads a colon followed by digits in a literal (JSON, times) as a
    # parameter.
    document = text(
        "INSERT INTO workspace_documents "
        "(id,owner_user_id,name,schema_version,revision,body,size_bytes,created_at,updated_at) "
        "VALUES ('wsd_kept','user-upgrade','configurations',1,1,:body,:size,now(),now())"
    )
    evaluation = text(
        "INSERT INTO offline_evaluations "
        "(id,owner_user_id,name,task,config_label,policy_label,summary,created_at,updated_at) "
        "VALUES (:id,'user-upgrade','Nominal','Generic task','Config r1','Policy v1',:summary,now(),now())"
    )
    episode = text(
        "INSERT INTO offline_episodes (id,evaluation_id,owner_user_id,seed,outcome,steps,images,action_dim,metrics,"
        "wall_seconds,sim_seconds,digest,stored_bytes,created_at) "
        "VALUES (:id,:evaluation,'user-upgrade',:seed,'success',54,55,14,:metrics,1.5,NULL,:digest,:size,now())"
    )
    try:
        with engine.begin() as connection:
            migration = migration_config()
            migration.attributes["connection"] = connection
            command.upgrade(migration, "0003_workspace_documents")
            connection.execute(
                text(
                    "INSERT INTO users (id,email,name,password_hash,role,disabled,password_reset_required,created_at) "
                    "VALUES ('user-upgrade','upgrade@example.com','Upgrade','unused','operator',false,false,now())"
                )
            )
            connection.execute(document, {"body": '{"schemaVersion":1}', "size": 18})
        assert "offline_evaluations" not in inspect(engine).get_table_names()
        result = migrate_database(settings)
        assert result["from_revision"] == "0003_workspace_documents"
        assert result["target_revision"] == "0005_robot_registry"
        with engine.begin() as connection:
            assert connection.execute(text("SELECT body FROM workspace_documents")).scalar() == '{"schemaVersion":1}'
            connection.execute(evaluation, {"id": "oev_upgrade00001", "summary": '{"episodes":1}'})
            values = {"id": "oep_upgrade00001", "evaluation": "oev_upgrade00001", "seed": 2**40, "metrics": '{"r":1}',
                      "digest": "a" * 64, "size": 2**33}
            connection.execute(episode, values)
            with pytest.raises(IntegrityError):  # one episode per evaluation and content digest
                with connection.begin_nested():
                    connection.execute(episode, {**values, "id": "oep_upgrade00002"})
            with pytest.raises(IntegrityError):  # an episode belongs to an existing evaluation
                with connection.begin_nested():
                    connection.execute(episode, {**values, "id": "oep_upgrade00003", "evaluation": "oev_missing00000"})
        with engine.connect() as connection:
            stored = connection.execute(text("SELECT seed, stored_bytes, metrics::text FROM offline_episodes")).one()
            assert stored == (2**40, 2**33, '{"r":1}')  # BIGINT columns keep 64-bit values
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert migrate_database(settings)["from_revision"] == "0005_robot_registry"
    finally:
        engine.dispose()
