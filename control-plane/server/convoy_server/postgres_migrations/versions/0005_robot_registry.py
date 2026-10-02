"""Immutable robot profiles, registration lineage and fleet membership (frozen DDL)."""
from alembic import op

revision = "0005_robot_registry"
down_revision = "0004_offline_evaluations"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
CREATE TABLE robot_profiles (
	id VARCHAR(32) NOT NULL,
	project_id VARCHAR(32) NOT NULL,
	name VARCHAR(120) NOT NULL,
	revision BIGINT NOT NULL,
	digest VARCHAR(64) NOT NULL,
	spec JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (project_id, name, revision),
	FOREIGN KEY(project_id) REFERENCES platform_projects (id)
)
    """)
    op.execute("""
CREATE INDEX ix_robot_profiles_project_id ON robot_profiles (project_id)
    """)
    op.execute("""
CREATE TABLE robot_registrations (
	robot_id VARCHAR(32) NOT NULL,
	profile_id VARCHAR(32) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	source_robot_id VARCHAR(32),
	simulation_engine VARCHAR(16),
	PRIMARY KEY (robot_id),
	FOREIGN KEY(robot_id) REFERENCES platform_robots (id),
	FOREIGN KEY(profile_id) REFERENCES robot_profiles (id),
	FOREIGN KEY(source_robot_id) REFERENCES platform_robots (id)
)
    """)
    op.execute("""
CREATE TABLE robot_fleets (
	id VARCHAR(32) NOT NULL,
	project_id VARCHAR(32) NOT NULL,
	name VARCHAR(120) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES platform_projects (id)
)
    """)
    op.execute("""
CREATE INDEX ix_robot_fleets_project_id ON robot_fleets (project_id)
    """)
    op.execute("""
CREATE TABLE robot_fleet_members (
	robot_id VARCHAR(32) NOT NULL,
	fleet_id VARCHAR(32) NOT NULL,
	PRIMARY KEY (robot_id),
	FOREIGN KEY(robot_id) REFERENCES platform_robots (id),
	FOREIGN KEY(fleet_id) REFERENCES robot_fleets (id)
)
    """)
    op.execute("""
CREATE INDEX ix_robot_fleet_members_fleet_id ON robot_fleet_members (fleet_id)
    """)


def downgrade() -> None:
    raise RuntimeError("Restore a qualified backup; robot identities are not automatically deleted")
