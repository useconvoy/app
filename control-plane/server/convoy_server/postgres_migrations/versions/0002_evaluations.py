"""Durable simulation evaluation jobs and release qualification gates.

Frozen DDL: importing future ORM metadata must not change this migration.
"""

from alembic import op

revision = "0002_evaluations"
down_revision = "0001_fleet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE platform_evaluation_suites (
        id VARCHAR(32) NOT NULL,
        project_id VARCHAR(32) NOT NULL,
        application_id VARCHAR(32) NOT NULL,
        digest VARCHAR(64) NOT NULL,
        spec JSON NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (application_id, digest),
        FOREIGN KEY(project_id) REFERENCES platform_projects (id),
        FOREIGN KEY(application_id) REFERENCES platform_applications (id)
    )
    """)
    op.execute('CREATE INDEX ix_platform_evaluation_suites_project_id ON platform_evaluation_suites (project_id)')
    op.execute("""
    CREATE TABLE platform_evaluation_gates (
        application_id VARCHAR(32) NOT NULL,
        suite_id VARCHAR(32) NOT NULL,
        generation BIGINT NOT NULL,
        PRIMARY KEY (application_id),
        FOREIGN KEY(application_id) REFERENCES platform_applications (id),
        FOREIGN KEY(suite_id) REFERENCES platform_evaluation_suites (id)
    )
    """)
    op.execute("""
    CREATE TABLE platform_evaluations (
        id VARCHAR(32) NOT NULL,
        project_id VARCHAR(32) NOT NULL,
        suite_id VARCHAR(32) NOT NULL,
        release_id VARCHAR(32) NOT NULL,
        robot_id VARCHAR(32) NOT NULL,
        deployment_id VARCHAR(32),
        requested_by VARCHAR(32) NOT NULL,
        credential_kind VARCHAR(16) NOT NULL,
        credential_id VARCHAR(32) NOT NULL,
        state VARCHAR(24) NOT NULL,
        detail TEXT NOT NULL,
        lease_owner VARCHAR(64),
        lease_epoch BIGINT NOT NULL,
        lease_until TIMESTAMP WITH TIME ZONE,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        report JSON,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(project_id) REFERENCES platform_projects (id),
        FOREIGN KEY(suite_id) REFERENCES platform_evaluation_suites (id),
        FOREIGN KEY(release_id) REFERENCES platform_releases (id),
        FOREIGN KEY(robot_id) REFERENCES platform_robots (id),
        FOREIGN KEY(deployment_id) REFERENCES platform_deployments (id),
        FOREIGN KEY(requested_by) REFERENCES users (id)
    )
    """)
    op.execute('CREATE INDEX ix_platform_evaluations_project_id ON platform_evaluations (project_id)')
    op.execute('CREATE INDEX ix_platform_evaluations_robot_id ON platform_evaluations (robot_id)')
    op.execute('CREATE INDEX ix_platform_evaluations_state ON platform_evaluations (state)')
    op.execute("""
    CREATE TABLE platform_release_promotions (
        id VARCHAR(32) NOT NULL,
        project_id VARCHAR(32) NOT NULL,
        application_id VARCHAR(32) NOT NULL,
        release_id VARCHAR(32) NOT NULL,
        evaluation_id VARCHAR(32) NOT NULL,
        suite_id VARCHAR(32) NOT NULL,
        created_by VARCHAR(32) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(project_id) REFERENCES platform_projects (id),
        FOREIGN KEY(application_id) REFERENCES platform_applications (id),
        FOREIGN KEY(release_id) REFERENCES platform_releases (id),
        UNIQUE (evaluation_id),
        FOREIGN KEY(evaluation_id) REFERENCES platform_evaluations (id),
        FOREIGN KEY(suite_id) REFERENCES platform_evaluation_suites (id),
        FOREIGN KEY(created_by) REFERENCES users (id)
    )
    """)
    op.execute('CREATE INDEX ix_platform_release_promotions_project_id ON platform_release_promotions (project_id)')
    op.execute('CREATE INDEX ix_platform_release_promotions_release_id ON platform_release_promotions (release_id)')
    op.execute("""
    CREATE TABLE platform_evaluation_cases (
        id VARCHAR(32) NOT NULL,
        evaluation_id VARCHAR(32) NOT NULL,
        position INTEGER NOT NULL,
        seed BIGINT NOT NULL,
        mission_id VARCHAR(32),
        episode_id VARCHAR(32),
        PRIMARY KEY (id),
        UNIQUE (evaluation_id, position),
        FOREIGN KEY(evaluation_id) REFERENCES platform_evaluations (id),
        UNIQUE (mission_id),
        FOREIGN KEY(mission_id) REFERENCES platform_missions (id),
        UNIQUE (episode_id),
        FOREIGN KEY(episode_id) REFERENCES platform_episodes (id)
    )
    """)
    op.execute('CREATE INDEX ix_platform_evaluation_cases_evaluation_id ON platform_evaluation_cases (evaluation_id)')


def downgrade() -> None:
    raise RuntimeError("Evaluation history is not automatically deleted; restore a qualified backup instead")
