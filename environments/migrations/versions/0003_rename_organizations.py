"""Rename the organizations table to its real name.

The table holding organizations was historically named ``workspaces`` (and
its foreign keys ``workspace_id``) from before the vocabulary ruling that
made "workspace" a different console object (a bundle of system grants —
the ``environments`` table). Decision (Aneesh, Aug 2026): no legacy names —
the table is ``organizations`` and every column pointing at it is
``organization_id``. Constraint and index identifiers created by the 0001
bootstrap keep their auto-generated names; they follow the renamed columns
by attribute, so behavior is unchanged.

Revision ID: 0003_rename_organizations
Revises: 0002_execution_environments
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_TABLES_WITH_ORG_FK = [
    "memberships",
    "secrets",
    "connections",
    "environments",
    "audit_log",
    "events",
]


def upgrade() -> None:
    op.rename_table("workspaces", "organizations")
    for table in _TABLES_WITH_ORG_FK:
        op.alter_column(table, "workspace_id", new_column_name="organization_id")


def downgrade() -> None:
    for table in _TABLES_WITH_ORG_FK:
        op.alter_column(table, "organization_id", new_column_name="workspace_id")
    op.rename_table("organizations", "workspaces")
