"""Control-plane tables + the shared `events` table.

The `events` table is the co-signed proposal: the first Postgres incarnation
of the append-only per-mission log (agent-evals' EventLog interface is
"shaped like the future Postgres events table" — this is that table). The
runtime (Aneesh) and the gateway (this package) both append to it; graders
and telemetry read it. Runs/missions themselves are runtime-owned and NOT
modeled here — the gateway learns run→environment from the per-run JWT.

JSON columns use JSONB on Postgres and plain JSON elsewhere (tests run on
SQLite). All ids are opaque strings minted by the application.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .base import Base

PortableJSON = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    idp_subject: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))  # admin | builder | member


class Secret(Base):
    """Write-only from every API. `ciphertext`/`dek_wrapped` are set only for
    the builtin backend (envelope encryption); external backends store a
    backend_ref (1Password item id, ARN, Vault path) and no material."""

    __tablename__ = "secrets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    backend: Mapped[str] = mapped_column(String(32))  # builtin | onepassword | aws_sm | vault
    backend_ref: Mapped[str] = mapped_column(String(512), nullable=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)
    dek_wrapped: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    rotated_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("organization_id", "name"),)


class Connection(Base):
    __tablename__ = "connections"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # mcp_managed | mcp_custom | aws_role | browser_identity
    provider: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(255))
    config: Mapped[dict] = mapped_column(PortableJSON, default=dict)  # non-secret only
    secret_ref: Mapped[str] = mapped_column(ForeignKey("secrets.id"), nullable=True)
    manifest: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    manifest_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | needs_reauth | revoked
    created_by: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Environment(Base):
    """Versioned-immutable: an edit inserts a new (id, version) row. Missions
    pin (environment_id, version); `policy_hash` feeds the certified tuple."""

    __tablename__ = "environments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    # Null for the legacy/base policy bundle attached directly to an
    # organization. Named execution environments point at that base bundle
    # and inherit its connector grants while owning runtime configuration
    # (sandbox template, browser policy, namespace) independently.
    parent_environment_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    backing_type: Mapped[str] = mapped_column(String(16), default="live")  # live | hermetic
    browser_policy: Mapped[dict] = mapped_column(PortableJSON, nullable=True)
    sandbox_template: Mapped[str] = mapped_column(String(128), default="")
    data_namespace: Mapped[str] = mapped_column(String(255), default="")
    policy_hash: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    __table_args__ = (UniqueConstraint("organization_id", "name", "version"),)


class EnvironmentConnection(Base):
    __tablename__ = "environment_connections"
    environment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"), primary_key=True)
    manifest_hash: Mapped[str] = mapped_column(String(64))  # pinned at env-version creation
    tool_allowlist: Mapped[list] = mapped_column(PortableJSON, default=list)
    promote_overrides: Mapped[list] = mapped_column(PortableJSON, default=list)


class EnvironmentGrant(Base):
    __tablename__ = "environment_grants"
    environment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))  # viewer | operator | env_admin


class BrowserProfile(Base):
    """Encrypted browser session snapshot (cookies/localStorage) per
    (environment, browser_identity connection). New capture = new version row."""

    __tablename__ = "browser_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment_id: Mapped[str] = mapped_column(String(64), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    state_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    dek_wrapped: Mapped[bytes] = mapped_column(LargeBinary)
    captured_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    captured_by_run_id: Mapped[str] = mapped_column(String(64), nullable=True)


class PlatformConnector(Base):
    """A declarative platform connector: a Convoy-hosted MCP server offered
    to every organization as a first-class provider. Connecting one creates
    an ordinary mcp_custom connection pointed at the platform URL, so the
    gateway's existing MCP execution path serves it unchanged. Rows are the
    breadth tier of the provider directory; code connectors are the depth
    tier."""

    __tablename__ = "platform_connectors"
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    mcp_url: Mapped[str] = mapped_column(String(512))
    manifest: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    credential_label: Mapped[str] = mapped_column(String(128), default="Bearer token")
    credential_placeholder: Mapped[str] = mapped_column(String(128), default="")
    credential_steps: Mapped[list] = mapped_column(PortableJSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EventRule(Base):
    """One event trigger: when `event_type` arrives on `connection_id`,
    start a run for `agent_id` from the frozen template below. The template
    mirrors the schedule template runtime-side: what the console resolved
    at save time (binding target, tools, instructions, budget)."""

    __tablename__ = "event_rules"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(128))
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    goal: Mapped[str] = mapped_column(Text)
    environment_id: Mapped[str] = mapped_column(String(128))
    budget_usd: Mapped[str] = mapped_column(String(32))
    tools: Mapped[list] = mapped_column(PortableJSON, default=list)
    instructions: Mapped[list] = mapped_column(PortableJSON, default=list)
    started_by: Mapped[str] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditLog(Base):
    """Control-plane changes only (connection created, env version added,
    grant changed, secret rotated). Runtime actions live in `events`."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Nullable: user-level events (e.g. idp linkage) have no organization scope.
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=True)
    actor_user_id: Mapped[str] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(128))
    diff: Mapped[dict] = mapped_column(PortableJSON, nullable=True)
    at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Event(Base):
    """THE shared append-only log — DDL proposal for the co-signed schema.

    `payload` is the full event JSON exactly as convoy_core.events serializes
    it (camelCase, exclude_none); the other columns are extracted indexes,
    never a second source of truth. (mission_id, seq) is the append-order
    invariant; readers order by (ts, seq) per the EventLog contract.
    organization_id is denormalized tenant attribution — the gateway knows it
    from the JWT; the runtime knows it from the mission registry."""

    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[str] = mapped_column(String(40))
    wall_ts: Mapped[str] = mapped_column(String(40))
    item_ref: Mapped[str] = mapped_column(String(255), nullable=True)
    step_id: Mapped[str] = mapped_column(String(64), nullable=True)
    attempt_id: Mapped[str] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(PortableJSON)
    __table_args__ = (
        UniqueConstraint("mission_id", "seq"),
        Index("ix_events_mission_order", "mission_id", "ts", "seq"),
    )
