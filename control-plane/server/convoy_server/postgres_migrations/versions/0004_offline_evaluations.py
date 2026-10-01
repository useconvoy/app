"""Owner-scoped offline evaluations and their episodes.

Frozen DDL: importing future ORM metadata must not change this migration. Episode frames are files in
the recording store, not rows; `stored_bytes` is each file's size for the per-account quota.
"""

from alembic import op

revision = "0004_offline_evaluations"
down_revision = "0003_workspace_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE offline_evaluations (
        id VARCHAR(32) NOT NULL,
        owner_user_id VARCHAR(32) NOT NULL,
        name VARCHAR(120) NOT NULL,
        task VARCHAR(120) NOT NULL,
        config_label VARCHAR(120) NOT NULL,
        policy_label VARCHAR(120) NOT NULL,
        summary JSON NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(owner_user_id) REFERENCES users (id)
    )
    """)
    op.execute("CREATE INDEX ix_offline_evaluations_owner_user_id ON offline_evaluations (owner_user_id)")
    op.execute("""
    CREATE TABLE offline_episodes (
        id VARCHAR(32) NOT NULL,
        evaluation_id VARCHAR(32) NOT NULL,
        owner_user_id VARCHAR(32) NOT NULL,
        seed BIGINT NOT NULL,
        outcome VARCHAR(16) NOT NULL,
        steps INTEGER NOT NULL,
        images INTEGER NOT NULL,
        action_dim INTEGER NOT NULL,
        metrics JSON NOT NULL,
        wall_seconds FLOAT,
        sim_seconds FLOAT,
        digest VARCHAR(64) NOT NULL,
        stored_bytes BIGINT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (evaluation_id, digest),
        FOREIGN KEY(evaluation_id) REFERENCES offline_evaluations (id),
        FOREIGN KEY(owner_user_id) REFERENCES users (id)
    )
    """)
    op.execute("CREATE INDEX ix_offline_episodes_owner_user_id ON offline_episodes (owner_user_id)")


def downgrade() -> None:
    raise RuntimeError("Offline evaluations are not automatically deleted; restore a qualified backup instead")
