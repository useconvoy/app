"""Platform connectors: the declarative breadth tier of the provider
directory. Convoy-authored MCP-backed providers offered to every
organization; connecting one materializes an ordinary mcp_custom
connection, so nothing in the gateway changes."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_connectors",
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("mcp_url", sa.String(512), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("credential_label", sa.String(128), nullable=False, server_default="Bearer token"),
        sa.Column("credential_placeholder", sa.String(128), nullable=False, server_default=""),
        sa.Column("credential_steps", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("platform_connectors")
