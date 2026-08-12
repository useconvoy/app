"""Event rules: provider webhooks become runs.

One row per trigger: when `event_type` arrives on `connection_id`, start a
run for `agent_id` from the frozen run template. See gateway/hooks.py.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_rules",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), sa.ForeignKey("organizations.id"), index=True),
        sa.Column("connection_id", sa.String(64), sa.ForeignKey("connections.id"), index=True),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False, index=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("environment_id", sa.String(128), nullable=False),
        sa.Column("budget_usd", sa.String(32), nullable=False),
        sa.Column("tools", sa.JSON(), nullable=False),
        sa.Column("instructions", sa.JSON(), nullable=False),
        sa.Column("started_by", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("event_rules")
