"""Row -> JSON helpers (no secrets, ISO timestamps)."""

from __future__ import annotations

from typing import Any

from .config import get_settings
from .ids import aware, iso, utcnow
from .models import ApiToken, Device, EnrollmentToken, EvalResult, Operation, User


def user_out(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "role": u.role,
        "disabled": u.disabled,
        "password_reset_required": u.password_reset_required,
        "created_at": iso(u.created_at),
    }


def token_out(t: ApiToken) -> dict[str, Any]:
    return {
        "id": t.id, "name": t.name, "prefix": t.prefix, "created_at": iso(t.created_at), "expires_at": iso(t.expires_at),
        "revoked_at": iso(t.revoked_at), "last_used_at": iso(t.last_used_at),
    }  # fmt: skip


def enrollment_out(e: EnrollmentToken) -> dict[str, Any]:
    return {
        "id": e.id, "label": e.label, "group_name": e.group_name, "simulated": e.simulated, "rebind_device_id": e.rebind_device_id,
        "created_at": iso(e.created_at), "expires_at": iso(e.expires_at), "consumed_at": iso(e.consumed_at), "device_id": e.device_id,
        "revoked_at": iso(e.revoked_at),
        "status": "revoked" if e.revoked_at else "consumed" if e.consumed_at else "expired" if aware(e.expires_at) < utcnow() else "open",
    }  # fmt: skip


def _finite_dict(d: dict[str, Any] | None) -> dict[str, Any]:
    from .validation import finite

    out: dict[str, Any] = {}
    for k, v in (d or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            out[k] = v
        else:
            out[k] = finite(v)
    return out


def device_status(d: Device) -> str:
    if d.retired_at:
        return "retired"
    if d.credential_revoked_at:
        return "credential_revoked"
    if not d.last_seen_at:
        return "never_seen"
    if (utcnow() - aware(d.last_seen_at)).total_seconds() > get_settings().offline_after_s:
        return "offline"
    return "online"


def device_out(d: Device, brief: bool = False) -> dict[str, Any]:
    out = {
        "id": d.id, "name": d.name, "group_name": d.group_name, "profile_id": d.profile_id, "simulated": d.simulated,
        "status": device_status(d), "last_seen_at": iso(d.last_seen_at), "agent_version": d.agent_version,
        "enrolled_at": iso(d.enrolled_at), "rebound_at": iso(d.rebound_at),
        "credential_revoked_at": iso(d.credential_revoked_at), "credential_revoked_reason": d.credential_revoked_reason,
        "generation": d.generation, "active_operation_id": d.active_operation_id,
        "expected_active_release_id": d.expected_active_release_id, "intent_updated_at": iso(d.intent_updated_at), "intent_source": d.intent_source,
        "observed_active_release_id": d.observed_active_release_id, "observed_recovery_release_id": d.observed_recovery_release_id,
        "observed_stage": d.observed_stage, "observed_health": d.observed_health, "observed_generation": d.observed_generation,
        "observed_operation_id": d.observed_operation_id, "observed_latch_generation": d.observed_latch_generation,
        "observed_at": iso(d.observed_at), "last_report_seq": d.last_report_seq,
        "live_seq": d.live_seq,
        "live_at": iso(d.live_at), "boot_id": d.boot_id, "time_confidence": d.time_confidence,
        "last_telemetry": _finite_dict(d.last_telemetry), "retired_at": iso(d.retired_at),
    }  # fmt: skip
    if not brief:
        out.update(
            {
                "observed": d.observed,
                "hardware": d.hardware,
                "runtime_evidence": d.runtime_evidence,
                "settings": d.settings,
                "notes": d.notes,
            }
        )
    return out


def operation_out(o: Operation) -> dict[str, Any]:
    return {
        "id": o.id, "device_id": o.device_id, "type": o.type, "payload": o.payload, "payload_digest": o.payload_digest,
        "generation": o.generation, "expected_active_release_id": o.expected_active_release_id, "plan_id": o.plan_id,
        "release_id": o.release_id, "status": o.status, "cancel_requested_at": iso(o.cancel_requested_at),
        "deadline_at": iso(o.deadline_at), "delivered_at": iso(o.delivered_at), "delivery_count": o.delivery_count,
        "grant_id": o.grant_id, "outcome": o.outcome, "progress": o.progress, "trace_id": o.trace_id,
        "terminal_at": iso(o.terminal_at), "rollout_id": o.rollout_id, "occurrence_id": o.occurrence_id,
        "created_by": o.created_by, "created_at": iso(o.created_at), "updated_at": iso(o.updated_at),
    }  # fmt: skip


def eval_out(e: EvalResult, brief: bool = False) -> dict[str, Any]:
    out = {
        "id": e.id, "created_at": iso(e.created_at), "device_id": e.device_id, "operation_id": e.operation_id, "release_id": e.release_id,
        "plan_id": e.plan_id, "eval_set_id": e.eval_set_id, "device_verdict": e.device_verdict, "server_verdict": e.server_verdict,
        "gates": e.gates, "summary": e.summary, "coverage": e.coverage, "provenance": e.provenance, "simulated": e.simulated,
        "baseline": e.baseline, "stage": e.stage,
    }  # fmt: skip
    if not brief:
        out["cases"] = e.cases
    return out
