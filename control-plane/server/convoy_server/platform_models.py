"""Additive application lifecycle records; legacy text release identities stay unchanged."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import INT64, TS, Base, _now


class Project(Base):
    __tablename__ = "platform_projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Robot(Base):
    __tablename__ = "platform_robots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("platform_projects.id"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    profile: Mapped[str] = mapped_column(String(80))
    generation: Mapped[int] = mapped_column(INT64, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Application(Base):
    __tablename__ = "platform_applications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("platform_projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class ApplicationRelease(Base):
    __tablename__ = "platform_releases"
    __table_args__ = (UniqueConstraint("application_id", "digest"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("platform_applications.id"), index=True)
    digest: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Deployment(Base):
    __tablename__ = "platform_deployments"
    __table_args__ = (UniqueConstraint("robot_id", "generation"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("platform_projects.id"), index=True)
    robot_id: Mapped[str] = mapped_column(ForeignKey("platform_robots.id"), index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey("platform_releases.id"))
    generation: Mapped[int] = mapped_column(INT64)
    state: Mapped[str] = mapped_column(String(24), default="requested")
    detail: Mapped[str] = mapped_column(Text, default="")
    observed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Mission(Base):
    __tablename__ = "platform_missions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("platform_projects.id"), index=True)
    robot_id: Mapped[str] = mapped_column(ForeignKey("platform_robots.id"), index=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("platform_deployments.id"))
    release_id: Mapped[str] = mapped_column(ForeignKey("platform_releases.id"))
    release_digest: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(INT64)
    seed: Mapped[int] = mapped_column(INT64)
    expires_at: Mapped[datetime] = mapped_column(TS)
    state: Mapped[str] = mapped_column(String(24), default="requested")
    identity: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    grant: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_started: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    terminal_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    episode_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    updated_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Episode(Base):
    __tablename__ = "platform_episodes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("platform_projects.id"), index=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("platform_missions.id"), unique=True)
    release_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    identity: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class MutationReceipt(Base):
    __tablename__ = "platform_mutation_receipts"
    __table_args__ = (UniqueConstraint("owner_user_id", "route", "key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    route: Mapped[str] = mapped_column(String(255))
    key: Mapped[str] = mapped_column(String(128))
    payload_digest: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
