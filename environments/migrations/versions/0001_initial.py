"""Initial schema: control-plane tables + the shared events table.

The readable, reviewable form of this DDL is convoy_environments/db/tables.py
(the events table docstring is the co-signed proposal). This bootstrap
migration materializes that metadata verbatim; subsequent migrations are
written explicitly against it.

Revision ID: 0001
Revises:
Create Date: 2026-08-02
"""

from alembic import op

from convoy_environments.db.base import Base
from convoy_environments.db import tables  # noqa: F401 — registers all tables

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
