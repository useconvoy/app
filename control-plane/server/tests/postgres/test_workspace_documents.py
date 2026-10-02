"""Workspace documents on real PostgreSQL: the same HTTP contract plus the explicit 0003 upgrade."""

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
from test_workspace_documents import (  # reuse the same HTTP contract on the real backend
    test_bodies_are_stored_as_compact_utf8,
    test_body_is_read_and_decoded_only_after_authentication_and_admission,
    test_conditional_writes_create_replace_and_refuse_stale_revisions,
    test_document_receipts_expire_and_are_capped_per_account,
    test_documents_are_owner_scoped_for_every_role_and_credential,
    test_size_and_document_count_limits,
    test_validation_errors_use_the_standard_envelope,
    test_writes_are_idempotent_audited_and_keep_compact_receipts,
    test_writes_are_rate_limited_per_account,
)

# Imported scenarios are intentionally collected by pytest here.
__all__ = [
    "test_bodies_are_stored_as_compact_utf8",
    "test_body_is_read_and_decoded_only_after_authentication_and_admission",
    "test_conditional_writes_create_replace_and_refuse_stale_revisions",
    "test_document_receipts_expire_and_are_capped_per_account",
    "test_documents_are_owner_scoped_for_every_role_and_credential",
    "test_size_and_document_count_limits",
    "test_validation_errors_use_the_standard_envelope",
    "test_writes_are_idempotent_audited_and_keep_compact_receipts",
    "test_writes_are_rate_limited_per_account",
]


def test_previous_revision_upgrade_adds_owner_scoped_documents(postgres_database, tmp_path):
    settings = config.Settings(database_url=postgres_database, data_dir=tmp_path)
    engine = db.make_engine(settings)
    try:
        with engine.begin() as connection:
            migration = migration_config()
            migration.attributes["connection"] = connection
            command.upgrade(migration, "0002_evaluations")
            connection.execute(
                text(
                    "INSERT INTO users (id,email,name,password_hash,role,disabled,password_reset_required,created_at) "
                    "VALUES ('user-upgrade','upgrade@example.com','Upgrade','unused','viewer',false,false,now())"
                )
            )
        assert "workspace_documents" not in inspect(engine).get_table_names()
        result = migrate_database(settings)
        assert result["from_revision"] == "0002_evaluations"
        assert result["target_revision"] == "0007_workspace_links"
        # Bound values: a colon inside a literal of text() would be taken for a parameter.
        insert = text(
            "INSERT INTO workspace_documents "
            "(id,owner_user_id,name,schema_version,revision,body,size_bytes,created_at,updated_at) "
            "VALUES (:id,'user-upgrade','configurations',1,1,:body,:size,now(),now())"
        )
        body = '{"schemaVersion":1,"name":"Bänk"}'
        with engine.begin() as connection:
            assert connection.execute(text("SELECT email FROM users WHERE id='user-upgrade'")).scalar() == (
                "upgrade@example.com"
            )
            connection.execute(insert, {"id": "wsd_first", "body": body, "size": len(body.encode())})
            with pytest.raises(IntegrityError):  # one document per owner and name
                with connection.begin_nested():
                    connection.execute(insert, {"id": "wsd_second", "body": body, "size": len(body.encode())})
        with engine.connect() as connection:
            # The compact text is stored verbatim: its bytes are the counted size.
            stored = connection.execute(text("SELECT body, octet_length(body), size_bytes FROM workspace_documents"))
            assert stored.one() == (body, len(body.encode()), len(body.encode()))
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert migrate_database(settings)["from_revision"] == "0007_workspace_links"
    finally:
        engine.dispose()
