"""Add named execution environments beneath console workspaces.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("environments")}
    if "parent_environment_id" in columns:
        return
    op.add_column(
        "environments",
        sa.Column("parent_environment_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_environments_parent_environment_id",
        "environments",
        ["parent_environment_id"],
    )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("environments")}
    if "parent_environment_id" not in columns:
        return
    op.drop_index("ix_environments_parent_environment_id", table_name="environments")
    op.drop_column("environments", "parent_environment_id")
