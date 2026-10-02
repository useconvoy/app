"""Additive robot registry; existing execution and device identities remain intact."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import INT64, TS, Base, _now


class RobotProfile(Base):
    __tablename__ = "robot_profiles"
    __table_args__ = (UniqueConstraint("project_id", "name", "revision"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("platform_projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(INT64)
    digest: Mapped[str] = mapped_column(String(64))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class RobotRegistration(Base):
    __tablename__ = "robot_registrations"
    robot_id: Mapped[str] = mapped_column(ForeignKey("platform_robots.id"), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("robot_profiles.id"))
    kind: Mapped[str] = mapped_column(String(16))
    source_robot_id: Mapped[str | None] = mapped_column(ForeignKey("platform_robots.id"), nullable=True)
    simulation_engine: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Fleet(Base):
    __tablename__ = "robot_fleets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("platform_projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class FleetMember(Base):
    __tablename__ = "robot_fleet_members"
    robot_id: Mapped[str] = mapped_column(ForeignKey("platform_robots.id"), primary_key=True)
    fleet_id: Mapped[str] = mapped_column(ForeignKey("robot_fleets.id"), index=True)
