"""Settled schema (see docs/ARCHITECTURE.md and docs/DESIGN_REVIEW.md §8)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .ids import utcnow


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


def _now() -> datetime:
    return utcnow()


TS = DateTime(timezone=True)


class Installation(Base):
    __tablename__ = "installation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    quarantined_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    quarantine_reason: Mapped[str] = mapped_column(Text, default="")
    dispatch_paused_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    dispatch_pause_reason: Mapped[str] = mapped_column(Text, default="")
    restored_from: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    password_hash: Mapped[str] = mapped_column(Text)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    password_reset_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class Throttle(Base):
    __tablename__ = "throttles"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(TS)
    count: Mapped[int] = mapped_column(Integer, default=0)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS, default=_now, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(255), default="")
    group_name: Mapped[str] = mapped_column(String(64), default="default")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    rebind_device_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    expires_at: Mapped[datetime] = mapped_column(TS)
    consumed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    consumed_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consumed_secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    group_name: Mapped[str] = mapped_column(String(64), default="default", index=True)
    profile_id: Mapped[str] = mapped_column(String(64), default="jetson-orin-nano-8gb")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    credential_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    credential_issued_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    credential_revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    credential_revoked_reason: Mapped[str] = mapped_column(String(64), default="")
    enrolled_at: Mapped[datetime] = mapped_column(TS, default=_now)
    rebound_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(TS, nullable=True, index=True)
    agent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    boot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_report_seq: Mapped[int] = mapped_column(Integer, default=0)  # ingestion cursor (any kind)
    live_seq: Mapped[int] = mapped_column(Integer, default=0)  # last roundtrip-bound live report applied
    live_nonce: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # expected in the next live report
    live_nonce_issued_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    binding_epoch: Mapped[int] = mapped_column(Integer, default=1)  # incremented on every (re)binding
    live_at: Mapped[datetime | None] = mapped_column(
        TS, nullable=True
    )  # receipt time of the last live report
    generation: Mapped[int] = mapped_column(Integer, default=0)  # monotonic floor
    active_operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # intent
    expected_active_release_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    intent_updated_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    intent_source: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # projection from the newest report
    observed_active_release_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    observed_recovery_release_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observed_stage: Mapped[str] = mapped_column(String(32), default="unknown")
    observed_health: Mapped[str] = mapped_column(String(16), default="unknown")
    observed_generation: Mapped[int] = mapped_column(Integer, default=0)
    observed_operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observed_latch_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    observed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    hardware: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    runtime_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_telemetry: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    time_confidence: Mapped[str] = mapped_column(String(16), default="unknown")
    notes: Mapped[str] = mapped_column(Text, default="")
    retired_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    epoch: Mapped[int] = mapped_column(Integer, default=1)
    seq: Mapped[int] = mapped_column(Integer)
    boot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="heartbeat")
    source_ts: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    receipt_ts: Mapped[datetime] = mapped_column(TS, default=_now, index=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("device_id", "epoch", "seq", name="uq_report_seq"),)


class EvalSet(Base):
    __tablename__ = "eval_sets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64), index=True)
    cases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    scorer: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    __table_args__ = (UniqueConstraint("name", "version", name="uq_evalset_name_version"),)


class BuildRecipe(Base):
    __tablename__ = "build_recipes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    runtime_name: Mapped[str] = mapped_column(String(64), default="llama.cpp")
    source_repo: Mapped[str] = mapped_column(String(255), default="https://github.com/ggml-org/llama.cpp")
    commit: Mapped[str] = mapped_column(String(64))
    tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cmake_flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    target: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # arch, os, cuda, compute_capability, l4t
    backend: Mapped[str] = mapped_column(String(32), default="cuda")  # cuda | cpu | simulated
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class RuntimeArtifact(Base):
    __tablename__ = "runtime_artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("build_recipes.id"), index=True)
    archive_sha256: Mapped[str] = mapped_column(String(64), index=True)
    archive_size: Mapped[int] = mapped_column(Integer)
    files: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{path, sha256, size, kind}]
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scope: Mapped[str] = mapped_column(String(64), default="fleet")  # fleet | device:<id>
    storage: Mapped[str] = mapped_column(String(16), default="device")  # device | server
    receipt: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verified: Mapped[str] = mapped_column(
        String(32), default="receipt_only"
    )  # receipt_only | archive_verified
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    __table_args__ = (UniqueConstraint("archive_sha256", "scope", name="uq_artifact_scope"),)


class Release(Base):
    __tablename__ = "releases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    version: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # release_spec_v1 (the identity)
    build_status: Mapped[str] = mapped_column(String(32), default="build_required")  # ready | build_required
    runtime_artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recipe_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    eval_set_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    profile_id: Mapped[str] = mapped_column(String(64), default="jetson-orin-nano-8gb")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    weights_qualification: Mapped[str] = mapped_column(String(32), default="needs_qualification")
    notes: Mapped[str] = mapped_column(Text, default="")
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now, index=True)
    retired_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    __table_args__ = (UniqueConstraint("name", "version", name="uq_release_name_version"),)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    release_id: Mapped[str] = mapped_column(ForeignKey("releases.id"), index=True)
    baseline_release_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    eval_set_id: Mapped[str] = mapped_column(ForeignKey("eval_sets.id"))
    gates: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{metric, op, limit, required, evidence}]
    workload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sample_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    profile_id: Mapped[str] = mapped_column(String(64))
    evaluator_version: Mapped[str] = mapped_column(String(16))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    type: Mapped[str] = mapped_column(String(16), index=True)  # deploy | eval | recover | health | collect
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload_digest: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(Integer)
    expected_active_release_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    release_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    # pending | delivered | granted | running | succeeded | failed | uncertain | abandoned_unconfirmed | cancelled
    cancel_requested_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    delivery_count: Mapped[int] = mapped_column(Integer, default=0)
    grant_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    outcome_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    terminal_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    rollout_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    occurrence_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Grant(Base):
    __tablename__ = "grants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    nonce: Mapped[str] = mapped_column(String(64))
    boot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_seq: Mapped[int] = mapped_column(Integer, default=0)
    issued_at: Mapped[datetime] = mapped_column(TS, default=_now)
    ttl_s: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(TS)
    window_end: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), default="issued"
    )  # issued | consumed | expired_unconfirmed | superseded
    consumed_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint("operation_id", "nonce", name="uq_grant_nonce"),)


class EvalResult(Base):
    __tablename__ = "eval_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now, index=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    release_id: Mapped[str] = mapped_column(String(32), index=True)
    plan_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    eval_set_id: Mapped[str] = mapped_column(String(32), index=True)
    device_verdict: Mapped[str] = mapped_column(String(16))  # passed | failed | inconclusive
    server_verdict: Mapped[str] = mapped_column(String(16))
    gates: Mapped[list[Any]] = mapped_column(JSON, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    stage: Mapped[str] = mapped_column(String(16), default="eval")  # eval | probation


class Failure(Base):
    __tablename__ = "failures"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts: Mapped[datetime] = mapped_column(TS, default=_now, index=True)
    device_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    release_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class LogEntry(Base):
    __tablename__ = "log_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS, index=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(8), default="info")
    source: Mapped[str] = mapped_column(String(32), default="agent")
    message: Mapped[str] = mapped_column(Text)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Span(Base):
    __tablename__ = "spans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    span_id: Mapped[str] = mapped_column(String(32))
    parent_span_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), default="internal")
    status: Mapped[str] = mapped_column(String(16), default="ok")
    start_ts: Mapped[datetime] = mapped_column(TS, index=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("trace_id", "span_id", name="uq_span"),)


class TelemetrySample(Base):
    __tablename__ = "telemetry_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS, index=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    mem_total_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_available_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpu_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    gpu_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_max_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_free_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    runtime_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    clock_confidence: Mapped[str] = mapped_column(String(16), default="unknown")
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


Index("ix_telemetry_device_ts", TelemetrySample.device_id, TelemetrySample.ts)
Index("ix_logs_device_ts", LogEntry.device_id, LogEntry.ts)


class LaneCursor(Base):
    __tablename__ = "lane_cursors"
    device_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    lane: Mapped[str] = mapped_column(String(16), primary_key=True)
    committed_seq: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(TS, default=_now)


class LossRange(Base):
    __tablename__ = "loss_ranges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    lane: Mapped[str] = mapped_column(String(16))
    from_seq: Mapped[int] = mapped_column(Integer)
    to_seq: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # binding + restoration context
    reported_at: Mapped[datetime] = mapped_column(TS, default=_now)


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    metric: Mapped[str] = mapped_column(String(32))
    value: Mapped[float] = mapped_column(Float, default=0.0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("day", "device_id", "metric", name="uq_usage"),)


class UsageUnknownInterval(Base):
    """An explicit UNOBSERVED interval a schema-2 usage record declared (an agent restart before a durable
    checkpoint). Kept as the producer reported it: bounds, seconds, reason and the incarnation it ended.
    `usage_daily.unknown_coverage_s` keeps the daily sum; this table keeps the intervals themselves."""

    __tablename__ = "usage_unknown_intervals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    from_ts: Mapped[datetime] = mapped_column(TS)
    to_ts: Mapped[datetime] = mapped_column(TS)
    seconds: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(64))
    previous_incarnation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # spool record seq
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Rollout(Base):
    __tablename__ = "rollouts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), index=True)
    release_id: Mapped[str] = mapped_column(String(32), index=True)
    targets: Mapped[list[Any]] = mapped_column(JSON, default=list)  # ordered device ids (immutable)
    canaries: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    # draft | canary | awaiting_promotion | expanding | paused | completed | failed | aborted | restored
    device_states: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    previous: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cursor: Mapped[int] = mapped_column(Integer, default=0)  # index into targets for serial expansion
    message: Mapped[str] = mapped_column(Text, default="")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))  # eval | health | collect | deploy
    cron: Mapped[str] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    target: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # plan_id, release_id pinned
    missed_policy: Mapped[str] = mapped_column(String(16), default="skip")
    catchup_age_s: Mapped[int] = mapped_column(Integer, default=3600)
    window_s: Mapped[int] = mapped_column(
        Integer, default=600
    )  # dispatch/activation window after the civil time
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    pause_reason: Mapped[str] = mapped_column(String(64), default="")
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    next_civil: Mapped[str | None] = mapped_column(String(32), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(TS, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    updated_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Occurrence(Base):
    __tablename__ = "occurrences"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"), index=True)
    civil_key: Mapped[str] = mapped_column(String(32))  # canonical civil time, fold 0
    scheduled_utc: Mapped[datetime] = mapped_column(TS)
    revision: Mapped[int] = mapped_column(Integer)
    frozen: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # targets, payload, actor at dispatch
    dispatched_at: Mapped[datetime] = mapped_column(TS, default=_now)
    status: Mapped[str] = mapped_column(String(24), default="dispatched")
    # dispatched | completed | partial | failed | skipped_nonexistent | missed_skipped | paused
    results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    __table_args__ = (UniqueConstraint("schedule_id", "civil_key", name="uq_occurrence_civil"),)


class SchedulerLease(Base):
    __tablename__ = "scheduler_leases"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    token: Mapped[str] = mapped_column(String(64))
    fence: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(TS)
    acquired_at: Mapped[datetime] = mapped_column(TS, default=_now)


class Backup(Base):
    __tablename__ = "backups"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(TS, default=_now)


class FixtureModel(Base):
    __tablename__ = "fixture_models"
    repo: Mapped[str] = mapped_column(String(255), primary_key=True)
    revision: Mapped[str] = mapped_column(String(64), primary_key=True)
    files: Mapped[list[Any]] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=_now)


# Register additive lifecycle tables with the same migration metadata.
from . import platform_models as _platform_models  # noqa: E402, F401
