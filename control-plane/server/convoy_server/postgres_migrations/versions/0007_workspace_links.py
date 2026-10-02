"""Owner document associations with project applications; no execution authority."""
from alembic import op

revision = "0007_workspace_links"
down_revision = "0006_robot_qualification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE workspace_configuration_links (
    id VARCHAR(32) PRIMARY KEY,
    document_id VARCHAR(32) NOT NULL REFERENCES workspace_documents(id) ON DELETE CASCADE,
    configuration_id VARCHAR(64) NOT NULL,
    application_id VARCHAR(32) NOT NULL REFERENCES platform_applications(id) ON DELETE CASCADE,
    source_revision BIGINT NOT NULL,
    source_digest VARCHAR(64) NOT NULL,
    source_name VARCHAR(120) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    UNIQUE (document_id, configuration_id)
)
    """)
    op.execute("CREATE INDEX ix_workspace_configuration_links_application_id ON workspace_configuration_links (application_id)")


def downgrade() -> None:
    raise RuntimeError("Restore a qualified backup; configuration links are not automatically deleted")
