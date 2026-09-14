"""Device authority: intent, one reservation per device, delivery, grants, outcomes, reports.

Contracts (docs/DESIGN_REVIEW.md §8.4-8.5, review R7-R11):
* At most one unsettled mutating operation per device (`devices.active_operation_id`, CAS). Conflicting
  manual operations -> 409 naming the active operation. No latent queue.
* All references (release, plan, artifact) are resolved and checked together before anything is written.
  A deploy without a plan is a `bootstrap` and must say so explicitly.
* The reservation is released only by a validated terminal outcome, or by a *challenged* fresh
  reconciliation report at generation >= the operation's generation showing no active operation.
  Operator `abandon` keeps the slot blocked until such reconciliation.
* Grants bind operation, generation, boot id, nonce, the latest live report, the observed active and
  recovery releases, and the release/plan/artifact digests. TTL is bounded by the maintenance window.
* Reports carry a device-wide sequence. Only *live* kinds (heartbeat, reconcile) with a fresh source
  timestamp update the projection; `history` reports are stored and never refresh health or free slots.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from ..config import get_settings
from ..db import write_txn
from ..hardware import ORIN_NANO_8GB, effective_budget, profile
from ..ids import aware, iso, new_id, utcnow
from ..models import (
    Device,
    EvalResult,
    Failure,
    Grant,
    Installation,
    Operation,
    Plan,
    Release,
    Report,
    RuntimeArtifact,
)
from ..redaction import redact
from ..security import new_secret
from ..validation import as_int, parse_ts, typed_hardware, typed_runtime_evidence, typed_telemetry

MUTATING = {"deploy", "eval", "recover"}
TERMINAL = {"succeeded", "failed", "cancelled", "abandoned_unconfirmed"}
LIVE_STATUSES = ("pending", "delivered", "granted", "running", "uncertain")


def unsettled_predicate():
    """Operations that CURRENTLY hold their device: every live status, plus an abandonment the device
    has not yet reconciled (its `active_operation_id` still names the operation). A reconciled
    abandonment keeps its honest historical status but no longer counts as unsettled."""
    from sqlalchemy import and_, exists, or_

    from ..models import Device as _Device

    held = exists().where(_Device.active_operation_id == Operation.id)
    return or_(
        Operation.status.in_(LIVE_STATUSES),
        and_(Operation.status == "abandoned_unconfirmed", held),
    )


UNSETTLED = {"pending", "delivered", "granted", "running", "uncertain"}
OP_TYPES = ("deploy", "eval", "recover", "health", "collect")
LIVE_KINDS = ("heartbeat", "reconcile")
LIVE_MAX_AGE_S = 120  # a live (roundtrip-bound) observation older than this is not fresh enough for grants
LIVE_NONCE_TTL_S = 300  # a nonce issued longer ago than this cannot make a buffered report 'live'


_UNSET = object()


def _admitted(db: DbSession, dev: Device) -> None:
    """Every device-facing write re-validates the credential/binding admitted at request start inside
    its own transaction, so a rebind or revoke racing the request mutates nothing (restore race)."""
    from ..auth import StaleBinding, assert_admitted_binding

    try:
        assert_admitted_binding(db, dev)
    except StaleBinding as e:
        raise OperationError(str(e), 401) from e


class OperationError(ValueError):
    def __init__(self, msg: str, status: int = 400, active: Operation | None = None):
        super().__init__(msg)
        self.status = status
        self.active = active


def digest(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def dispatch_paused(db: DbSession) -> str | None:
    inst = db.get(Installation, 1)
    if inst and inst.quarantined_at:
        return "quarantine"
    if inst and inst.dispatch_paused_at:
        return inst.dispatch_pause_reason or "paused"
    return None


# ---------------------------------------------------------------- creation
def _check_platform_tuple(dev: Device, rel: Release, op_type: str) -> None:
    """Tracks that share a hardware profile are told apart by the exact tuple: a physical device must
    have REPORTED the architecture, full L4T release and CUDA major.minor the release was built for.
    Checked when the operation is created and again when the grant is issued (the inventory may have
    changed in between). Simulated devices carry no CUDA tuple and are not affected."""
    if dev.simulated or op_type not in ("deploy", "recover"):
        return
    from ..compat import tuple_mismatch

    target = (rel.spec.get("platform") or {}).get("target") or {}
    reason, _warnings = tuple_mismatch(target if isinstance(target, dict) else {}, dev.hardware or {})
    if reason:
        raise OperationError(
            f"platform tuple: {reason}; a release built for another track cannot run here", 409
        )


def _resolve_refs(
    db: DbSession, dev: Device, op_type: str, payload: dict[str, Any]
) -> tuple[Release | None, Plan | None, RuntimeArtifact | None]:
    """R8: resolve every supplied reference first; missing refs are errors, never silently None."""
    rel = plan = art = None
    if payload.get("plan_id"):
        plan = db.get(Plan, payload["plan_id"])
        if not plan:
            raise OperationError("plan not found", 404)
    if payload.get("release_id"):
        rel = db.get(Release, payload["release_id"])
        if not rel:
            raise OperationError("release not found", 404)
    if op_type == "deploy":
        if rel is None:
            raise OperationError("deploy needs release_id", 422)
        if plan is not None and plan.release_id != rel.id:
            raise OperationError("plan does not qualify this release", 422)
        if plan is None and not payload.get("bootstrap"):
            raise OperationError(
                "deploy without a qualification plan must set bootstrap=true explicitly", 422
            )
    elif op_type == "eval":
        if plan is None:
            raise OperationError("eval needs plan_id", 422)
        rel = db.get(Release, plan.release_id)
        if not rel:
            raise OperationError("plan's release not found", 404)
        if dev.observed_active_release_id != rel.id:
            raise OperationError(
                f"device runs {dev.observed_active_release_id or 'nothing'}, plan qualifies {rel.id}; eval evidence must match the active release",
                409,
            )
    elif op_type == "recover":
        if not dev.observed_recovery_release_id:
            raise OperationError("device reports no recovery release; nothing to restore to", 409)
        rel = db.get(Release, dev.observed_recovery_release_id)
        if not rel:
            raise OperationError("recovery release unknown to the server", 409)
        if rel.id == dev.observed_active_release_id:
            raise OperationError("recovery release is already active", 409)
    if rel is not None:
        if rel.retired_at and op_type == "deploy":
            raise OperationError("release is retired", 409)
        if rel.build_status != "ready":
            raise OperationError(
                "release has no concrete runtime artifact (build required); it cannot be deployed", 409
            )
        if rel.simulated != dev.simulated:
            raise OperationError("simulated releases target simulated devices only, and vice versa", 409)
        if rel.profile_id != dev.profile_id:
            raise OperationError(
                f"release profile {rel.profile_id} does not match device profile {dev.profile_id}", 409
            )
        _check_platform_tuple(dev, rel, op_type)
        art = db.get(RuntimeArtifact, rel.runtime_artifact_id) if rel.runtime_artifact_id else None
        if art is None and rel.build_status == "ready":
            raise OperationError("release artifact missing", 409)
        if art is not None and art.scope != "fleet" and art.scope != f"device:{dev.id}":
            raise OperationError(f"runtime artifact is scoped to {art.scope}, not this device", 409)
    if plan is not None:
        if plan.simulated != dev.simulated or plan.profile_id != dev.profile_id:
            raise OperationError("plan profile/simulation does not match the device", 409)
    if dev.retired_at:
        raise OperationError("device is retired", 409)
    if dev.credential_revoked_at:
        raise OperationError("device credential is revoked; rebind first", 409)
    return rel, plan, art


def create_operation(
    db: DbSession,
    dev: Device,
    op_type: str,
    payload: dict[str, Any],
    *,
    created_by: str | None,
    rollout_id: str | None = None,
    occurrence_id: str | None = None,
    deadline_s: int | None = None,
    window_end: datetime | None = None,
    baseline: str | None | object = _UNSET,
) -> Operation:
    """Must be called inside write_txn. Takes the device reservation by CAS for mutating types.
    R47: when a `baseline` is given (a rollout's frozen previous release for this device), the device
    must still run exactly that release; drift is refused instead of silently changing the transition."""
    if op_type not in OP_TYPES:
        raise OperationError(f"unknown operation type {op_type}")
    if baseline is not _UNSET and dev.observed_active_release_id != baseline:
        raise OperationError(
            f"baseline drift: device runs {dev.observed_active_release_id}, the frozen baseline is {baseline}",
            409,
        )
    rel, plan, art = _resolve_refs(db, dev, op_type, payload)
    mutating = op_type in MUTATING
    if mutating:
        res = db.execute(
            update(Device)
            .where(Device.id == dev.id, Device.active_operation_id.is_(None))
            .values(active_operation_id="__pending__")
        )
        if res.rowcount != 1:
            db.refresh(dev)
            active = (
                db.get(Operation, dev.active_operation_id)
                if dev.active_operation_id and dev.active_operation_id != "__pending__"
                else None
            )
            raise OperationError(
                f"device has an unsettled {active.type if active else 'operation'} ({dev.active_operation_id}) in state {active.status if active else 'unknown'}",
                409,
                active,
            )
        dev.generation = (dev.generation or 0) + 1
    generation = dev.generation
    expected_active = dev.observed_active_release_id  # R7: what is running now, for every type
    full_payload = {
        **{k: v for k, v in payload.items() if k not in ("release_id", "plan_id")},
        "release_id": rel.id if rel else None,
        "release_digest": rel.digest if rel else None,
        "artifact_sha256": art.archive_sha256 if art else None,
        "artifact_files": [
            {"path": f.get("path"), "sha256": f.get("sha256")} for f in (art.files if art else [])
        ],
        "simulated": bool(dev.simulated),
        "plan_id": plan.id if plan else None,
        "plan_digest": plan.digest if plan else None,
        "expected_active_release_id": expected_active,
        "recovery_release_id": dev.observed_recovery_release_id,
        "target_release_id": rel.id if op_type in ("deploy", "recover") else None,
        "qualification": ("plan" if plan else "bootstrap_no_plan") if op_type == "deploy" else None,
    }
    if op_type in ("deploy", "recover") and rel is not None:
        # the one budget policy (hardware.effective_budget): the same numbers and provenance the
        # planner shows for this device/release, frozen into the intent the device will execute
        full_payload["effective_budget"] = effective_budget(
            rel.spec, profile(dev.profile_id) or profile(ORIN_NANO_8GB), dev.settings or {}
        )
    op = Operation(
        id=new_id("op"), device_id=dev.id, type=op_type, payload=full_payload, payload_digest=digest(full_payload), generation=generation,
        expected_active_release_id=expected_active, plan_id=plan.id if plan else None, release_id=rel.id if rel else None, status="pending",
        deadline_at=(utcnow() + timedelta(seconds=deadline_s)) if deadline_s else window_end, rollout_id=rollout_id, occurrence_id=occurrence_id, created_by=created_by,
    )  # fmt: skip
    db.add(op)
    db.flush()
    if mutating:
        dev.active_operation_id = op.id
        if op_type in ("deploy", "recover"):
            dev.expected_active_release_id = rel.id
            dev.intent_updated_at = utcnow()
            dev.intent_source = {
                "operation_id": op.id,
                "by": created_by,
                "rollout_id": rollout_id,
                "occurrence_id": occurrence_id,
                "kind": op_type,
            }
    return op


# ---------------------------------------------------------------- cancel / abandon / finish
def request_cancel(db: DbSession, op: Operation, by: str | None) -> Operation:
    with write_txn(db):
        db.refresh(op)
        if op.status in TERMINAL:
            raise OperationError(f"operation already {op.status}", 409)
        now = utcnow()
        op.cancel_requested_at = now
        op.updated_at = now
        if op.status in ("pending", "delivered") and not op.grant_id:
            _finish(
                db, op, "cancelled", {"status": "cancelled", "reason": "cancelled_before_grant", "by": by}
            )
    return op


def abandon(db: DbSession, op: Operation, by: str | None, reason: str) -> Operation:
    with write_txn(db):
        db.refresh(op)
        if op.status in TERMINAL:
            raise OperationError(f"operation already {op.status}", 409)
        now = utcnow()
        op.status = "abandoned_unconfirmed"
        op.terminal_at = now
        op.updated_at = now
        op.outcome = {"status": "abandoned_unconfirmed", "by": by, "reason": reason, "at": iso(now)}
        op.progress = {**(op.progress or {}), "challenge": new_secret(12)}
    return op


def _finish(db: DbSession, op: Operation, status: str, outcome: dict[str, Any]) -> None:
    now = utcnow()
    op.status = status
    op.outcome = redact(outcome)
    op.outcome_digest = digest(op.outcome)
    op.terminal_at = now
    op.updated_at = now
    dev = db.get(Device, op.device_id)
    if dev and dev.active_operation_id == op.id and status != "abandoned_unconfirmed":
        dev.active_operation_id = None
    for g in db.scalars(select(Grant).where(Grant.operation_id == op.id, Grant.status == "issued")):
        g.status = "superseded"


# ---------------------------------------------------------------- delivery
def deliverable(db: DbSession, dev: Device) -> list[Operation]:
    q = (
        select(Operation)
        .where(Operation.device_id == dev.id, Operation.status.in_(tuple(UNSETTLED)))
        .order_by(Operation.created_at.asc())
    )
    return list(db.scalars(q))


def wire_operation(op: Operation) -> dict[str, Any]:
    return {
        "id": op.id, "type": op.type, "payload": op.payload, "payload_digest": op.payload_digest, "generation": op.generation,
        "expected_active_release_id": op.expected_active_release_id, "status": op.status, "cancel_requested": bool(op.cancel_requested_at),
        "deadline_at": iso(op.deadline_at), "grant_id": op.grant_id, "challenge": (op.progress or {}).get("challenge"),
    }  # fmt: skip


# ---------------------------------------------------------------- reports
def apply_report(db: DbSession, dev: Device, body: dict[str, Any]) -> dict[str, Any]:
    """Append-only storage; delivery of unsettled operations; projection only from a *live* report.

    R17 liveness contract: every report response carries `live_nonce`. A report is live when its kind is
    heartbeat|reconcile AND it echoes the nonce from the previous response (roundtrip-bound), or when it is
    the first report after a (re)binding (no nonce known yet). Live reports advance `live_seq` and set
    `live_at` (receipt time). `history` reports and nonce-less/mismatched reports only advance the
    ingestion cursor `last_report_seq`. Source timestamps and clock confidence are stored as metadata."""
    s = get_settings()
    now = utcnow()
    seq = as_int(body.get("seq"), "seq", ge=1)
    kind = str(body.get("kind") or "heartbeat")
    if kind not in LIVE_KINDS and kind != "history":
        raise OperationError("kind must be heartbeat|reconcile|history", 422)
    source_ts = parse_ts(body.get("source_ts"), "source_ts")
    obs = body.get("observed") or {}
    if not isinstance(obs, dict):
        raise OperationError("observed must be an object", 422)
    obs_generation = as_int(obs.get("generation"), "observed.generation")
    latch = obs.get("failed_generation_latch")
    if latch is not None:
        latch = as_int(latch, "observed.failed_generation_latch")
    telemetry = typed_telemetry(body.get("telemetry"))
    hardware = typed_hardware(body.get("hardware"))
    runtime_ev = typed_runtime_evidence(obs.get("runtime"))
    body = redact(body)
    echoed = body.get("live_nonce")
    with write_txn(db):
        _admitted(db, dev)
        dup = db.scalar(
            select(Report).where(
                Report.device_id == dev.id, Report.epoch == (dev.binding_epoch or 1), Report.seq == seq
            )
        )
        newer = seq > (dev.last_report_seq or 0)
        if dup is not None:
            newer = False  # exact retry: idempotent
        nonce_fresh = (
            dev.live_nonce_issued_at is not None
            and (now - aware(dev.live_nonce_issued_at)).total_seconds() <= LIVE_NONCE_TTL_S
        )
        live = bool(
            newer
            and kind in LIVE_KINDS
            and (dev.live_nonce is None or (echoed and echoed == dev.live_nonce and nonce_fresh))
        )
        if dup is None:
            db.add(
                Report(
                    device_id=dev.id,
                    epoch=(dev.binding_epoch or 1),
                    seq=seq,
                    boot_id=body.get("boot_id"),
                    kind=kind,
                    source_ts=source_ts,
                    receipt_ts=now,
                    applied=live,
                    body=body,
                )
            )
        dev.last_seen_at = now
        if body.get("agent_version"):
            dev.agent_version = str(body["agent_version"])[:32]
        if newer:
            dev.last_report_seq = seq
        if live:
            dev.live_seq = seq
            dev.live_at = now
            dev.boot_id = body.get("boot_id") or dev.boot_id
            dev.observed_at = now
            dev.observed_active_release_id = obs.get("active_release_id")
            dev.observed_recovery_release_id = obs.get("recovery_release_id")
            dev.observed_stage = str(obs.get("stage") or "unknown")[:32]
            dev.observed_health = str(obs.get("health") or "unknown")[:16]
            dev.observed_generation = obs_generation
            dev.observed_operation_id = obs.get("operation_id") or None
            dev.observed_latch_generation = latch
            dev.observed = {**obs, "runtime": runtime_ev, "source_ts": iso(source_ts)}
            dev.time_confidence = str(body.get("time_confidence") or "unknown")[:16]
            if hardware:
                dev.hardware = hardware
            if runtime_ev is not None:
                dev.runtime_evidence = runtime_ev
            if telemetry is not None:
                dev.last_telemetry = {**telemetry, "at": iso(now), "source_ts": iso(source_ts)}
            if obs_generation > (dev.generation or 0):
                dev.generation = obs_generation
            _reconcile_reservation(db, dev, obs, kind, body.get("challenge"))
        # a fresh nonce for the next live report (rotated on every accepted live report)
        if live or dev.live_nonce is None or not nonce_fresh:
            dev.live_nonce = new_secret(
                12
            )  # rotated on every accepted live report and whenever the old one expired
            dev.live_nonce_issued_at = now
        ops = deliverable(db, dev)
        for op in ops:
            if op.status == "pending":
                op.status = "delivered"
                op.delivered_at = now
            op.delivery_count += 1
            op.updated_at = now
        paused = dispatch_paused(db)
        challenge = None
        if dev.active_operation_id and dev.active_operation_id != "__pending__":
            active = db.get(Operation, dev.active_operation_id)
            if active and active.status in ("uncertain", "abandoned_unconfirmed"):
                if not (active.progress or {}).get("challenge"):
                    active.progress = {**(active.progress or {}), "challenge": new_secret(12)}
                challenge = active.progress["challenge"]
        live_nonce = dev.live_nonce
    return {
        "server_time": iso(now),
        "poll_interval_s": s.heartbeat_interval_s,
        "generation": dev.generation,
        "expected_active_release_id": dev.expected_active_release_id,
        "operations": [wire_operation(o) for o in ops] if not paused else [],
        "dispatch_paused": paused,
        "applied": live,
        "last_report_seq": dev.last_report_seq,
        "live_seq": dev.live_seq,
        "live_nonce": live_nonce,
        "quarantine": paused == "quarantine",
        "reconcile_challenge": challenge,
    }


def _reconcile_reservation(
    db: DbSession, dev: Device, obs: dict[str, Any], kind: str, challenge: Any
) -> None:
    """R11: free an uncertain/abandoned reservation only on a challenged `reconcile` report proving no
    active operation at a generation >= the reserved operation's generation."""
    if not dev.active_operation_id or dev.active_operation_id == "__pending__":
        return
    op = db.get(Operation, dev.active_operation_id)
    if op is None:
        dev.active_operation_id = None
        return
    if op.status not in ("uncertain", "abandoned_unconfirmed"):
        return
    expected = (op.progress or {}).get("challenge")
    if kind != "reconcile" or not expected or challenge != expected:
        return
    if obs.get("operation_id") in (None, "") and dev.observed_generation >= op.generation:
        if op.status == "abandoned_unconfirmed":
            dev.active_operation_id = None
            op.outcome = {
                **(op.outcome or {}),
                "reconciled_at": iso(utcnow()),
                "reconciled_generation": dev.observed_generation,
                "reconciled_active_release_id": obs.get("active_release_id"),
            }
        else:
            _finish(
                db,
                op,
                "failed",
                {
                    "status": "failed",
                    "failure": {
                        "code": "GRANT_UNCONFIRMED",
                        "message": "device reconciled with no active operation after an unacknowledged grant",
                    },
                    "generation": dev.observed_generation,
                    "active_release_id": obs.get("active_release_id"),
                },
            )


# ---------------------------------------------------------------- grants
def issue_grant(db: DbSession, dev: Device, op: Operation, req: dict[str, Any]) -> dict[str, Any]:
    """R9: bindings checked: boot id, nonce, latest live report seq, observed active/recovery ids,
    release/plan/artifact digests, generation, reservation, cancellation, pause, rollout state, window."""
    s = get_settings()
    now = utcnow()
    _settle_expired_grant(db, op)  # R16: committed in its own transaction, independent of the worker
    with write_txn(db):
        _admitted(db, dev)
        db.refresh(op)
        db.refresh(dev)
        if op.device_id != dev.id:
            raise OperationError("operation not found", 404)
        if op.status in TERMINAL:
            raise OperationError(f"operation is {op.status}", 409)
        if op.status == "uncertain":
            raise OperationError("operation is uncertain after an unacknowledged grant; reconcile first", 409)
        if op.cancel_requested_at:
            raise OperationError("cancellation requested", 409)
        if op.type in MUTATING and dev.active_operation_id != op.id:
            raise OperationError("operation no longer holds the device reservation", 409)
        if op.type in MUTATING and op.generation != dev.generation:
            raise OperationError("generation drift", 409)
        boot_id = req.get("boot_id")
        if not boot_id or boot_id != dev.boot_id:
            raise OperationError("boot id must match the latest live report", 409)
        req_seq = as_int(req.get("seq"), "seq", ge=1)
        if req_seq != dev.live_seq:
            raise OperationError(
                "grant must cite the latest LIVE report sequence (stale or non-live context)", 409
            )
        if dev.live_at is None or (now - aware(dev.live_at)).total_seconds() > LIVE_MAX_AGE_S:
            raise OperationError("no fresh live report from the device", 409)
        # R16: expiry is settled BEFORE this transaction (_settle_expired_grant) so the uncertainty
        # transition is committed even though this request is rejected; an outstanding unexpired grant
        # must be re-requested with the same nonce
        if op.grant_id:
            prev = db.get(Grant, op.grant_id)
            if prev and prev.status == "issued" and prev.nonce != str(req.get("nonce")):
                raise OperationError("an unexpired grant is outstanding; re-request with the same nonce", 409)
        p = op.payload
        if op.type in ("deploy", "recover") and p.get("target_release_id"):
            target_rel = db.get(Release, p["target_release_id"])
            if target_rel is not None:
                _check_platform_tuple(dev, target_rel, op.type)  # re-checked against the latest inventory
        if req.get("active_release_id") != dev.observed_active_release_id or req.get(
            "active_release_id"
        ) != p.get("expected_active_release_id"):
            raise OperationError(
                "observed active release does not match the operation's expected active release", 409
            )
        if op.type == "deploy" and (req.get("recovery_release_id") or None) != (
            p.get("recovery_release_id") or None
        ):
            raise OperationError("recovery release binding mismatch", 409)
        if p.get("release_digest") and req.get("release_digest") != p.get("release_digest"):
            raise OperationError("release digest mismatch", 409)
        if p.get("plan_digest") and req.get("plan_digest") != p.get("plan_digest"):
            raise OperationError("plan digest mismatch", 409)
        if p.get("artifact_sha256") and req.get("artifact_sha256") != p.get("artifact_sha256"):
            raise OperationError("runtime artifact digest mismatch", 409)
        paused = dispatch_paused(db)
        if paused:
            raise OperationError(f"dispatch paused: {paused}", 423)
        _check_intent_still_authorized(db, op)
        nonce = str(req.get("nonce"))
        existing = db.scalar(select(Grant).where(Grant.operation_id == op.id, Grant.nonce == nonce))
        if existing:
            if existing.status == "issued" and aware(existing.expires_at) > now:
                return _grant_wire(existing, op, dev)
            raise OperationError("grant nonce already used or expired", 409)
        ttl = s.grant_ttl_s
        window_end = aware(op.deadline_at) if op.deadline_at else None
        if window_end is not None:
            remaining = (window_end - now).total_seconds()
            if remaining <= 1:
                raise OperationError("maintenance window has ended; no start authorized", 423)
            ttl = int(max(1, min(ttl, remaining)))
        for g in db.scalars(select(Grant).where(Grant.operation_id == op.id, Grant.status == "issued")):
            g.status = "superseded"
        g = Grant(
            id=new_id("grant"),
            operation_id=op.id,
            device_id=dev.id,
            nonce=nonce,
            boot_id=boot_id,
            request_seq=req_seq,
            issued_at=now,
            ttl_s=ttl,
            expires_at=now + timedelta(seconds=ttl),
            window_end=window_end,
            status="issued",
        )
        db.add(g)
        op.grant_id = g.id
        op.status = "granted"
        op.updated_at = now
        return _grant_wire(g, op, dev)


def _check_intent_still_authorized(db: DbSession, op: Operation) -> None:
    """R43/R50: a grant activates intent, so the intent's source must still authorise it NOW.
    * rollout deploy operations: no new grants once the rollout is paused/aborted/failed/restored/completed
      (recover operations issued by an explicit restore are authorised by the restore itself)
    * scheduled operations: the schedule must still be enabled, unpaused, at the dispatched revision, and
      its owner must still be an enabled operator/admin.
    Already-consumed grants are unaffected: an honest outcome for work that already started is accepted."""
    from ..models import Occurrence, Rollout, Schedule, User

    if op.rollout_id:
        ro = db.get(Rollout, op.rollout_id)
        if (
            ro
            and op.type == "deploy"
            and ro.status
            in ("paused", "aborted", "failed", "restored", "restoring", "restore_failed", "completed")
        ):
            raise OperationError(f"rollout is {ro.status}; no new grants", 423)
        if ro and op.type == "recover" and ro.status not in ("restoring", "restored", "restore_failed"):
            raise OperationError(f"rollout is {ro.status}; restore was not issued", 423)
    if op.occurrence_id:
        occ = db.get(Occurrence, op.occurrence_id)
        s = db.get(Schedule, occ.schedule_id) if occ else None
        if occ is None or s is None:
            raise OperationError("scheduled intent no longer exists; no new grants", 423)
        if not s.enabled or s.paused_at is not None:
            raise OperationError(f"schedule is {'paused' if s.paused_at else 'disabled'}; no new grants", 423)
        if s.revision != occ.revision:
            raise OperationError(
                f"schedule edited after dispatch (revision {occ.revision} -> {s.revision}); stale intent, no new grants",
                423,
            )
        owner = db.get(User, s.owner_id) if s.owner_id else None
        if owner is None or owner.disabled or owner.role not in ("operator", "admin"):
            raise OperationError("schedule owner is no longer authorised; no new grants", 423)


def _settle_expired_grant(db: DbSession, op: Operation) -> bool:
    """If the operation's current grant is issued but past expiry, COMMIT: grant expired_unconfirmed,
    operation uncertain (+challenge). Safety does not depend on the worker sweep."""
    now = utcnow()
    with write_txn(db):
        db.refresh(op)
        if not op.grant_id:
            return False
        g = db.get(Grant, op.grant_id)
        if g is None or g.status != "issued" or aware(g.expires_at) > now:
            return False
        g.status = "expired_unconfirmed"
        if op.status == "granted":
            op.status = "uncertain"
            op.updated_at = now
            op.progress = {
                **(op.progress or {}),
                "note": "grant expired without acknowledgement; awaiting challenged device reconciliation",
                "challenge": new_secret(12),
            }
        return True


def _grant_wire(g: Grant, op: Operation, dev: Device) -> dict[str, Any]:
    return {
        "grant_id": g.id, "operation_id": op.id, "generation": op.generation, "boot_id": g.boot_id, "nonce": g.nonce, "ttl_s": g.ttl_s,
        "issued_at": iso(g.issued_at), "expires_at": iso(g.expires_at), "window_end": iso(g.window_end),
        "expected_active_release_id": op.payload.get("expected_active_release_id"), "recovery_release_id": op.payload.get("recovery_release_id"),
        "target_release_id": op.payload.get("target_release_id"), "release_digest": op.payload.get("release_digest"), "plan_digest": op.payload.get("plan_digest"),
        "artifact_sha256": op.payload.get("artifact_sha256"),
    }  # fmt: skip


def expire_grants(db: DbSession, worker: Any = None) -> int:
    now = utcnow()
    n = 0
    with write_txn(db):
        if worker is not None:
            worker.fenced(db)  # R53: a stalled ex-leader must not commit
        for g in db.scalars(select(Grant).where(Grant.status == "issued", Grant.expires_at < now)):
            g.status = "expired_unconfirmed"
            op = db.get(Operation, g.operation_id)
            if op and op.status == "granted":
                op.status = "uncertain"
                op.updated_at = now
                op.progress = {
                    **(op.progress or {}),
                    "note": "grant expired without acknowledgement; awaiting challenged device reconciliation",
                    "challenge": new_secret(12),
                }
            n += 1
    return n


# ---------------------------------------------------------------- outcomes
def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise OperationError(msg, 422)


def _resolve_eval_result(db: DbSession, op: Operation, eval_result_id: Any) -> EvalResult:
    """R15 follow-up: the cited eval result must already be accepted by the server and bound to THIS
    operation (device, operation, plan, release, generation) with a server verdict."""
    _require(
        isinstance(eval_result_id, str) and eval_result_id != "",
        "eval evidence must reference the uploaded eval result id",
    )
    evr = db.get(EvalResult, eval_result_id)
    _require(evr is not None, "eval result is not accepted by the server yet (flush the critical lane first)")
    _require(evr.device_id == op.device_id, "eval result belongs to another device")
    _require(evr.operation_id == op.id, "eval result was not produced by this operation")
    _require(evr.plan_id == op.plan_id, "eval result plan does not match the operation")
    _require(evr.release_id == op.release_id, "eval result release does not match the operation")
    _require((evr.coverage or {}).get("generation") == op.generation, "eval result generation does not match")
    return evr


def _validate_probation(db: DbSession, op: Operation, evidence: dict[str, Any], now: datetime) -> None:
    plan = db.get(Plan, op.plan_id) if op.plan_id else None
    sp = (plan.sample_policy if plan else None) or {}
    min_s = float(sp.get("probation_min_s", 60))
    min_req = int(sp.get("probation_min_requests", 0))
    pr = evidence.get("probation")
    _require(isinstance(pr, dict), "plan-qualified deploy requires probation evidence")
    el, served = pr.get("elapsed_s"), pr.get("requests_served")
    _require(
        isinstance(el, (int, float)) and not isinstance(el, bool) and math.isfinite(float(el)) and el >= 0,
        "probation elapsed_s must be a finite number",
    )
    _require(
        isinstance(served, int) and not isinstance(served, bool) and served >= 0,
        "probation requests_served must be an integer",
    )
    _require(float(el) >= min_s, f"probation too short: {el}s < plan minimum {min_s}s")
    _require(served >= min_req, f"probation served {served} requests < plan minimum {min_req}")
    g = db.get(Grant, op.grant_id) if op.grant_id else None
    if g is not None:
        wall = (now - aware(g.issued_at)).total_seconds()
        _require(
            float(el) <= wall + 2.0,
            f"probation elapsed_s {el} exceeds the time since the grant was issued ({wall:.1f}s)",
        )


def _validate_success(
    db: DbSession, op: Operation, body: dict[str, Any], now: datetime | None = None
) -> None:
    """R10/R15: type-specific success contracts, bound to the artifact executable hash, the exact plan
    digest, the already-accepted eval result for this operation, the plan's probation policy and (for
    physical profiles) positive full CUDA offload evidence."""
    now = now or utcnow()
    p = op.payload
    result = body.get("result") or {}
    evidence = body.get("evidence") or {}
    _require(isinstance(result, dict) and isinstance(evidence, dict), "result and evidence must be objects")
    if op.type in ("deploy", "recover"):
        _require(
            op.grant_id is not None and body.get("grant_id") == op.grant_id,
            "success requires the consumed grant id",
        )
        _require(
            as_int(body.get("grant_consumed_seq"), "grant_consumed_seq", ge=1) >= 1,
            "grant_consumed_seq required",
        )
        _require(
            result.get("active_release_id") == p.get("target_release_id"),
            "active_release_id must equal the operation target",
        )
        _require(result.get("release_digest") == p.get("release_digest"), "release digest must match")
        _require(result.get("generation") == op.generation, "generation must match")
        _require(evidence.get("health") == "ok", "health evidence must be ok")
        rt = typed_runtime_evidence(evidence.get("runtime")) or {}
        _require(
            isinstance(rt.get("build_info"), str) and rt["build_info"] != "",
            "runtime evidence build_info required",
        )
        expected_bin = _artifact_executable_sha(p)
        _require(bool(expected_bin), "operation has no runtime artifact executable hash")
        _require(
            rt.get("binary_sha256") == expected_bin,
            "runtime binary hash must equal the artifact's executable member hash",
        )
        if not _is_simulated_payload(p):
            _require(
                rt.get("intended_backend_ok") is True,
                "physical profile requires positive intended-backend (CUDA) evidence",
            )
            _require(
                isinstance(rt.get("gpu_offloaded_layers"), int)
                and rt["gpu_offloaded_layers"] > 0
                and rt.get("gpu_offloaded_layers") == rt.get("gpu_total_layers"),
                "physical profile requires full positive GPU layer offload evidence",
            )
        if op.type == "deploy":
            cm = evidence.get("cutover_ms")
            _require(
                isinstance(cm, (int, float))
                and not isinstance(cm, bool)
                and cm >= 0
                and math.isfinite(float(cm)),
                "cutover downtime measurement required (finite ms)",
            )
            _require(
                result.get("recovery_release_id") == p.get("expected_active_release_id"),
                "recovery release after deploy must be the previously active release",
            )
            if p.get("plan_id"):
                ev = evidence.get("eval")
                _require(
                    isinstance(ev, dict) and ev.get("verdict") == "passed",
                    "plan-qualified deploy requires a passed eval verdict in evidence",
                )
                _require(
                    ev.get("plan_digest") == p.get("plan_digest"),
                    "eval evidence must cite the exact plan digest",
                )
                evr = _resolve_eval_result(db, op, ev.get("eval_result_id"))
                _require(
                    evr.stage == "eval" and not evr.baseline,
                    "deploy qualification needs an eval-stage result",
                )
                _require(
                    evr.server_verdict == "passed",
                    f"server verdict for the cited eval result is {evr.server_verdict}",
                )
                _validate_probation(db, op, evidence, now)
    elif op.type == "eval":
        ev = evidence.get("eval") or {}
        _require(
            isinstance(ev, dict) and ev.get("verdict") in ("passed", "failed", "inconclusive"),
            "eval evidence with verdict required",
        )
        _require(ev.get("plan_digest") == p.get("plan_digest"), "eval evidence must cite the plan digest")
        _require(
            result.get("active_release_id") == p.get("release_id"), "eval must report the evaluated release"
        )
        evr = _resolve_eval_result(db, op, ev.get("eval_result_id") or result.get("eval_result_id"))
        _require(
            evr.server_verdict == ev.get("verdict"),
            "eval verdict in evidence must equal the server verdict of the cited result",
        )
    elif op.type == "health":
        _require(evidence.get("health") in ("ok", "degraded", "failed"), "health evidence required")


def _artifact_executable_sha(payload: dict[str, Any]) -> str | None:
    files = payload.get("artifact_files") or []
    for f in files:
        if str(f.get("path", "")).endswith(("llama-server", "llama-server.sim")):
            return f.get("sha256")
    return None


def _is_simulated_payload(payload: dict[str, Any]) -> bool:
    return bool(payload.get("simulated"))


def record_outcome(db: DbSession, dev: Device, op: Operation, body: dict[str, Any]) -> dict[str, Any]:
    """Idempotent terminal outcome. Identical duplicate -> same answer; conflicting duplicate -> 409."""
    status = body.get("status")
    if status not in ("running", "succeeded", "failed"):
        raise OperationError("status must be running|succeeded|failed", 422)
    now = utcnow()
    with write_txn(db):
        _admitted(db, dev)
        db.refresh(op)
        if op.device_id != dev.id:
            raise OperationError("operation not found", 404)
        if body.get("grant_id") and op.grant_id and body["grant_id"] != op.grant_id:
            raise OperationError("outcome cites a superseded grant", 409)
        if status == "running":
            if op.status in TERMINAL:
                raise OperationError(f"operation already {op.status}", 409)
            if op.type in MUTATING and op.status != "granted" and op.status != "running":
                raise OperationError("mutating operations run only after a grant", 409)
            op.status = "running"
            op.progress = {**(op.progress or {}), **redact(body.get("progress") or {})}
            if body.get("trace_id"):
                op.trace_id = body["trace_id"]
            op.updated_at = now
            return {"ok": True, "status": op.status}
        outcome = redact({k: v for k, v in body.items() if k not in ("grant_id",)})
        d = digest(outcome)
        if op.status in TERMINAL:
            if op.outcome_digest == d and op.status == status:
                return {"ok": True, "status": op.status, "duplicate": True}
            raise OperationError(f"conflicting duplicate outcome for {op.status} operation", 409)
        if status == "succeeded":
            if op.type in MUTATING and op.status not in ("granted", "running", "uncertain"):
                raise OperationError("success before a grant is not possible for mutating operations", 409)
            _validate_success(db, op, body, now)
        g = db.get(Grant, op.grant_id) if op.grant_id else None
        if body.get("grant_consumed_seq") and g and g.status in ("issued", "expired_unconfirmed"):
            g.status = "consumed"
            g.consumed_seq = int(body["grant_consumed_seq"])
        if body.get("trace_id"):
            op.trace_id = body["trace_id"]
        _finish(db, op, status, outcome)
        if status == "failed":
            f = outcome.get("failure") or {}
            _require(isinstance(f, dict) and f.get("code"), "failed outcome requires failure.code")
            db.add(
                Failure(
                    id=new_id("fail"),
                    device_id=dev.id,
                    operation_id=op.id,
                    release_id=op.release_id,
                    trace_id=op.trace_id,
                    code=str(f.get("code"))[:64],
                    stage=(str(f.get("stage"))[:32] if f.get("stage") else None),
                    message=str(f.get("message", "operation failed"))[:2000],
                    details=f.get("details") or {},
                    simulated=dev.simulated,
                )  # fmt: skip
            )
        return {"ok": True, "status": op.status}


def sweep_deadlines(db: DbSession, worker: Any = None) -> int:
    now = utcnow()
    n = 0
    with write_txn(db):
        if worker is not None:
            worker.fenced(db)  # R53
        for op in db.scalars(
            select(Operation).where(
                Operation.status.in_(("pending", "delivered")), Operation.deadline_at < now
            )
        ):
            _finish(db, op, "cancelled", {"status": "cancelled", "reason": "window_expired_before_start"})
            n += 1
    return n
