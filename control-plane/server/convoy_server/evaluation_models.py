"""Durable simulation evaluations. Robot commands remain ordinary fenced missions."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import INT64, TS, Base, _now


class EvaluationSuite(Base):
    __tablename__ = "platform_evaluation_suites"
    __table_args__ = (UniqueConstraint("application_id", "digest"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("platform_projects.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("platform_applications.id"))
    digest: Mapped[str] = mapped_column(String(64))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class EvaluationRun(Base):
    __tablename__ = "platform_evaluations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("platform_projects.id"), index=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("platform_evaluation_suites.id"))
    release_id: Mapped[str] = mapped_column(ForeignKey("platform_releases.id"))
    robot_id: Mapped[str] = mapped_column(ForeignKey("platform_robots.id"), index=True)
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("platform_deployments.id"), nullable=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    credential_kind: Mapped[str] = mapped_column(String(16))
    credential_id: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_epoch: Mapped[int] = mapped_column(INT64, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TS)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    updated_at: Mapped[datetime] = mapped_column(TS, default=_now)


class EvaluationCase(Base):
    __tablename__ = "platform_evaluation_cases"
    __table_args__ = (UniqueConstraint("evaluation_id", "position"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("platform_evaluations.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(INT64)
    mission_id: Mapped[str | None] = mapped_column(
        ForeignKey("platform_missions.id"), unique=True, nullable=True
    )
    episode_id: Mapped[str | None] = mapped_column(
        ForeignKey("platform_episodes.id"), unique=True, nullable=True
    )


class EvaluationGate(Base):
    __tablename__ = "platform_evaluation_gates"
    application_id: Mapped[str] = mapped_column(ForeignKey("platform_applications.id"), primary_key=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("platform_evaluation_suites.id"))
    generation: Mapped[int] = mapped_column(INT64, default=1)


class ReleasePromotion(Base):
    __tablename__ = "platform_release_promotions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("platform_projects.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("platform_applications.id"))
    release_id: Mapped[str] = mapped_column(ForeignKey("platform_releases.id"), index=True)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("platform_evaluations.id"), unique=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("platform_evaluation_suites.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
