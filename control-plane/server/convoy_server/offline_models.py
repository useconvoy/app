"""Owner-scoped offline evaluations: simulation episodes recorded outside Convoy's hosted runner.

Additive storage only. An offline evaluation is its owner's own upload: Convoy neither ran nor signed it,
it carries no fleet or execution authority, and no other table refers to it. Episode frames live in the
recording store next to hosted recordings (`data_dir/recordings/<episode id>.sqlite3`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import INT64, TS, Base, _now


class OfflineEvaluation(Base):
    __tablename__ = "offline_evaluations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    task: Mapped[str] = mapped_column(String(120))
    config_label: Mapped[str] = mapped_column(String(120))
    policy_label: Mapped[str] = mapped_column(String(120))
    # Computed by the server from the evaluation's episodes after every upload or removal.
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    updated_at: Mapped[datetime] = mapped_column(TS, default=_now)


class OfflineEpisode(Base):
    __tablename__ = "offline_episodes"
    # Identical content uploaded again is the same episode.
    __table_args__ = (UniqueConstraint("evaluation_id", "digest"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Indexed through the unique (evaluation_id, digest) constraint, whose leading column it is.
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("offline_evaluations.id"))
    # The evaluation's owner, kept on the episode for the per-account storage quota.
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    seed: Mapped[int] = mapped_column(INT64)
    outcome: Mapped[str] = mapped_column(String(16))
    steps: Mapped[int] = mapped_column(Integer)
    images: Mapped[int] = mapped_column(Integer)
    action_dim: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    wall_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    sim_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # sha256 of the canonical upload; the recording file repeats it.
    digest: Mapped[str] = mapped_column(String(64))
    # Size of the episode's recording file, counted against the account's offline quota.
    stored_bytes: Mapped[int] = mapped_column(INT64)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
