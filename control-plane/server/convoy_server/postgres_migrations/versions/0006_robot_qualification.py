"""Device-bound simulator qualification requests and immutable evidence."""
from alembic import op

revision = "0006_robot_qualification"
down_revision = "0005_robot_registry"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
CREATE TABLE robot_qualifications (
	id VARCHAR(32) NOT NULL,
	project_id VARCHAR(32) NOT NULL,
	robot_id VARCHAR(32) NOT NULL,
	profile_id VARCHAR(32) NOT NULL,
	profile_digest VARCHAR(64) NOT NULL,
	binding_epoch BIGINT NOT NULL,
	generation BIGINT NOT NULL,
	engine VARCHAR(16) NOT NULL,
	state VARCHAR(16) NOT NULL,
	report JSON,
	report_digest VARCHAR(64),
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	UNIQUE (robot_id, generation),
	FOREIGN KEY(project_id) REFERENCES platform_projects (id),
	FOREIGN KEY(robot_id) REFERENCES platform_robots (id),
	FOREIGN KEY(profile_id) REFERENCES robot_profiles (id)
)
    """)
    op.execute("""
CREATE INDEX ix_robot_qualifications_project_id ON robot_qualifications (project_id)
    """)
    op.execute("""
CREATE INDEX ix_robot_qualifications_robot_id ON robot_qualifications (robot_id)
    """)


def downgrade() -> None:
    raise RuntimeError("Restore a qualified backup; qualification evidence is not automatically deleted")
