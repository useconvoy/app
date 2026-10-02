"""Owner-scoped workspace documents: JSON content a user keeps for their own workspace views.

Documents and their navigation links carry no fleet or execution authority.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import INT64, TS, Base, _now


class WorkspaceDocument(Base):
    __tablename__ = "workspace_documents"
    __table_args__ = (UniqueConstraint("owner_user_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[int] = mapped_column(Integer)
    # 1 on creation and +1 on every replacement; writes name the revision they replace (If-Match).
    revision: Mapped[int] = mapped_column(INT64, default=1)
    # The body as compact UTF-8 JSON text: exactly the size_bytes counted against the limit. A JSON
    # column would re-encode it with ASCII escapes and separator spaces (up to three times larger).
    body: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(INT64)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    updated_at: Mapped[datetime] = mapped_column(TS, default=_now)


class WorkspaceConfigurationLink(Base):
    __tablename__ = "workspace_configuration_links"
    __table_args__ = (UniqueConstraint("document_id", "configuration_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("workspace_documents.id", ondelete="CASCADE"))
    configuration_id: Mapped[str] = mapped_column(String(64))
    application_id: Mapped[str] = mapped_column(ForeignKey("platform_applications.id", ondelete="CASCADE"), index=True)
    source_revision: Mapped[int] = mapped_column(INT64)
    source_digest: Mapped[str] = mapped_column(String(64))
    source_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
