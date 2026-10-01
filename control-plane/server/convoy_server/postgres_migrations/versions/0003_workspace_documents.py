"""Owner-scoped workspace documents.

Frozen DDL: importing future ORM metadata must not change this migration.
"""

from alembic import op

revision = "0003_workspace_documents"
down_revision = "0002_evaluations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE workspace_documents (
        id VARCHAR(32) NOT NULL,
        owner_user_id VARCHAR(32) NOT NULL,
        name VARCHAR(64) NOT NULL,
        schema_version INTEGER NOT NULL,
        body JSON NOT NULL,
        size_bytes BIGINT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (owner_user_id, name),
        FOREIGN KEY(owner_user_id) REFERENCES users (id)
    )
    """)
    op.execute("CREATE INDEX ix_workspace_documents_owner_user_id ON workspace_documents (owner_user_id)")


def downgrade() -> None:
    raise RuntimeError("Workspace documents are not automatically deleted; restore a qualified backup instead")
