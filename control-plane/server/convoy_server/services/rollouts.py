"""Rollout controller (§8.10, §1 row 31): one plan, ordered immutable targets, canaries that must ALL
pass qualification + probation, explicit promotion re-checked in a transaction, then serial expansion
(max_in_flight 1). Pause blocks new grants; abort cancels ungranted work; restore is tracked separately.
Missing/offline targets stay in the denominator. Simulated plans never touch physical devices."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..config import get_settings
from ..db import write_txn
from ..ids import aware, iso, new_id, utcnow
from ..models import Device, EvalResult, Operation, Plan, Release, Rollout
from . import operations as ops

ACTIVE = ("canary", "awaiting_promotion", "expanding", "paused")
LIVE_HEALTH_MAX_AGE_S = 120  # R46: health must come from a challenged live report this recent


class RolloutError(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _targets(db: DbSession, target: dict[str, Any]) -> list[Device]:
    q = select(Device).where(Device.retired_at.is_(None))
    if target.get("device_ids"):
        ids = list(dict.fromkeys(target["device_ids"]))
        devs = {d.id: d for d in db.scalars(q.where(Device.id.in_(ids)))}
        missing = [i for i in ids if i not in devs]
        if missing:
            raise RolloutError(f"unknown devices: {missing}", 404)
        return [devs[i] for i in ids]  # immutable order as given
    if target.get("group"):
        return list(db.scalars(q.where(Device.group_name == target["group"]).order_by(Device.name)))
    raise RolloutError("target needs device_ids or group")


def create_rollout(db: DbSession, data: dict[str, Any], created_by: str | None) -> Rollout:
    plan = db.get(Plan, data["plan_id"])
    if not plan:
        raise RolloutError("plan not found", 404)
    rel = db.get(Release, plan.release_id)
    if not rel or rel.retired_at or rel.build_status != "ready":
        raise RolloutError("plan's release is not deployable (retired or build required)", 409)
    devs = _targets(db, data.get("target") or {})
    if not devs:
        raise RolloutError("no target devices")
    if any(d.simulated != plan.simulated for d in devs) or any(d.profile_id != plan.profile_id for d in devs):
        raise RolloutError("every target must match the plan's profile and simulation flag", 409)
    ids = [d.id for d in devs]
    canaries = [c for c in (data.get("canary_device_ids") or ids[:1]) if c in ids]
    if not canaries:
        raise RolloutError("at least one canary from the targets is required")
    r = Rollout(
        id=new_id("ro"), name=data["name"], plan_id=plan.id, release_id=rel.id, targets=ids, canaries=canaries, status="draft",
        device_states={d.id: {"status": "queued"} for d in devs}, previous={d.id: d.observed_active_release_id for d in devs},
        cursor=0, simulated=plan.simulated, created_by=created_by,
    )  # fmt: skip
    db.add(r)
    db.flush()
    return r


def _issue(db: DbSession, r: Rollout, device_id: str, by: str | None) -> str:
    """Issue the rollout deploy to one target against its FROZEN baseline (R47). Returns the new state."""
    dev = db.get(Device, device_id)
    states = dict(r.device_states)
    if dev is None or dev.retired_at:
        states[device_id] = {"status": "skipped", "reason": "device missing or retired"}
        r.device_states = states
        return "skipped"
    baseline = (r.previous or {}).get(device_id)
    try:
        op = ops.create_operation(
            db,
            dev,
            "deploy",
            {"release_id": r.release_id, "plan_id": r.plan_id},
            created_by=by,
            rollout_id=r.id,
            baseline=baseline,
        )
    except ops.OperationError as e:
        if "baseline drift" in str(e):
            states[device_id] = {
                "status": "failed",
                "reason": str(e),
                "baseline": baseline,
                "observed_active_release_id": dev.observed_active_release_id,
            }
            r.device_states = states
            return "failed"
        states[device_id] = {
            "status": "blocked",
            "reason": str(e),
            "active_operation_id": dev.active_operation_id,
        }
        r.device_states = states
        return "blocked"
    states[device_id] = {"status": "deploying", "operation_id": op.id, "started_at": iso(utcnow())}
    r.device_states = states
    return "deploying"


def start(db: DbSession, r: Rollout, by: str | None) -> Rollout:
    with write_txn(db):
        db.refresh(r)
        if r.status != "draft":
            raise RolloutError(f"rollout is {r.status}", 409)
        r.previous = {
            d: (db.get(Device, d).observed_active_release_id if db.get(Device, d) else None)
            for d in r.targets
        }
        r.status = "canary"
        r.message = "canary deploys issued"
        r.updated_at = utcnow()
        for c in r.canaries:
            if _issue(db, r, c, by) == "failed":
                _fail(r, "canary", [c])
                break
    return r


def _fail(r: Rollout, prior: str, failed: list[str]) -> None:
    r.status = "failed"
    r.message = (
        "canary failed; promotion blocked" if prior == "canary" else "expansion failed; rollout stopped"
    ) + f": {', '.join(failed)}"
    r.finished_at = utcnow()


def _fresh_eval(
    db: DbSession, device_id: str, release_id: str, plan: Plan, op: Operation | None
) -> EvalResult | None:
    """R15 follow-up: the qualifying eval must be the server-verified, eval-stage result produced BY the
    rollout's own deploy operation (device, operation, plan, release, generation), still within the plan's
    freshness window. An ordinary recent passing eval for the same release does not qualify."""
    if op is None or op.status != "succeeded":
        return None
    max_age = int((plan.sample_policy or {}).get("fresh_eval_max_age_s", 3600))
    now = utcnow()
    q = (
        select(EvalResult)
        .where(
            EvalResult.device_id == device_id,
            EvalResult.operation_id == op.id,
            EvalResult.release_id == release_id,
            EvalResult.plan_id == plan.id,
            EvalResult.stage == "eval",
        )
        .order_by(EvalResult.created_at.desc())
        .limit(3)
    )
    cited = ((op.outcome or {}).get("evidence") or {}).get("eval", {}).get("eval_result_id")
    for e in db.scalars(q):
        if cited and e.id != cited:
            continue
        if (now - aware(e.created_at)).total_seconds() > max_age:
            return None
        if (e.coverage or {}).get("generation") != op.generation:
            return None
        return e if e.server_verdict == "passed" else None
    return None


def _probation_ok(op: Operation | None, plan: Plan) -> bool:
    """The deploy's accepted success outcome must carry probation evidence meeting the plan policy
    (the server validated it at outcome time; re-read here so promotion never trusts a bare status)."""
    if op is None or op.status != "succeeded":
        return False
    pr = ((op.outcome or {}).get("evidence") or {}).get("probation") or {}
    sp = plan.sample_policy or {}
    try:
        return float(pr.get("elapsed_s", -1)) >= float(sp.get("probation_min_s", 60)) and int(
            pr.get("requests_served", -1)
        ) >= int(sp.get("probation_min_requests", 0))
    except (TypeError, ValueError):
        return False


def _healthy_now(dev: Device, release_id: str, generation: int | None = None) -> bool:
    """R46: health counts only from a challenged LIVE report (not history uploads, not transport
    receipt time) that is recent, on the rollout release, at the deploy's generation."""
    if dev.live_at is None:
        return False
    max_age = min(LIVE_HEALTH_MAX_AGE_S, get_settings().offline_after_s)
    if (utcnow() - aware(dev.live_at)).total_seconds() > max_age:
        return False
    if generation is not None and (dev.observed_generation or 0) != generation:
        return False
    return (
        dev.observed_active_release_id == release_id
        and dev.observed_health == "ok"
        and dev.observed_stage == "active"
        and not dev.credential_revoked_at
        and not dev.retired_at
    )


def advance(db: DbSession, r: Rollout, by: str | None = "worker", worker: Any = None) -> None:
    """Idempotent step. Runs inside its own write transaction (fenced when a worker drives it, R53)."""
    with write_txn(db):
        if worker is not None:
            worker.fenced(db)
        db.refresh(r)
        if r.status == "restoring":
            _advance_restore(db, r)
            return
        if r.status not in ("canary", "expanding"):
            return
        plan = db.get(Plan, r.plan_id)
        states = dict(r.device_states)
        failed: list[str] = []
        for did, st in states.items():
            if st.get("status") != "deploying":
                continue
            op = db.get(Operation, st.get("operation_id")) if st.get("operation_id") else None
            dev = db.get(Device, did)
            if op is None or op.status in ops.UNSETTLED or op.status == "abandoned_unconfirmed":
                if op is not None and op.status in ("uncertain", "abandoned_unconfirmed"):
                    states[did] = {**st, "note": f"operation {op.status}; awaiting device reconciliation"}
                continue
            if op.status != "succeeded":
                states[did] = {
                    **st,
                    "status": "failed",
                    "reason": f"deploy {op.status}",
                    "failure": (op.outcome or {}).get("failure"),
                    "recovery": ((op.outcome or {}).get("failure") or {}).get("details", {}).get("recovery"),
                }
                failed.append(did)
                continue
            ev = _fresh_eval(db, did, r.release_id, plan, op)
            if ev is None:
                states[did] = {
                    **st,
                    "status": "failed",
                    "reason": "no fresh passing server-verified eval bound to this deploy operation",
                }
                failed.append(did)
                continue
            if not _probation_ok(op, plan):
                states[did] = {
                    **st,
                    "status": "failed",
                    "reason": "probation evidence does not meet the plan policy",
                }
                failed.append(did)
                continue
            if dev is None or not _healthy_now(dev, r.release_id, op.generation):
                states[did] = {
                    **st,
                    "status": "awaiting_health",
                    "eval_result_id": ev.id,
                    "generation": op.generation,
                    "note": "deploy succeeded; waiting for a live healthy report on the new release",
                }
                continue
            states[did] = {
                **st,
                "status": "passed",
                "eval_result_id": ev.id,
                "generation": op.generation,
                "passed_at": iso(utcnow()),
            }
        for did, st in states.items():
            if st.get("status") == "awaiting_health":
                dev = db.get(Device, did)
                if dev is not None and _healthy_now(dev, r.release_id, st.get("generation")):
                    states[did] = {**st, "status": "passed", "passed_at": iso(utcnow())}
                elif (
                    dev is not None
                    and dev.observed_active_release_id == r.release_id
                    and dev.observed_health in ("failed", "degraded")
                ):
                    states[did] = {
                        **st,
                        "status": "failed",
                        "reason": f"health {dev.observed_health} after deploy",
                    }
                    failed.append(did)
        r.device_states = states
        r.updated_at = utcnow()
        if failed:
            _fail(r, r.status, failed)
            return
        if r.status == "canary":
            if all(states[c].get("status") == "passed" for c in r.canaries):
                rest = [d for d in r.targets if d not in r.canaries]
                if not rest:
                    r.status = "completed"
                    r.message = "all targets (canaries only) passed"
                    r.finished_at = utcnow()
                else:
                    r.status = "awaiting_promotion"
                    r.message = "all canaries passed qualification and probation; explicit promotion required"
            return
        if r.status == "expanding":
            order = [d for d in r.targets if d not in r.canaries]
            current = order[r.cursor - 1] if 0 < r.cursor <= len(order) else None
            if current and states[current].get("status") in ("deploying", "awaiting_health"):
                return
            if current and states[current].get("status") == "blocked":
                dev = db.get(Device, current)
                if dev and dev.active_operation_id is None:
                    if _issue(db, r, current, by) == "failed":  # retry once the slot is free
                        _fail(r, "expanding", [current])
                return
            if r.cursor >= len(order):
                r.status = "completed"
                r.message = "serial expansion complete"
                r.finished_at = utcnow()
                return
            nxt = order[r.cursor]
            r.cursor += 1
            if _issue(db, r, nxt, by) == "failed":
                _fail(r, "expanding", [nxt])


def _advance_restore(db: DbSession, r: Rollout) -> None:
    """R43 follow-up: restoration completes only from acknowledged recover outcomes of EVERY required
    target; an issuance error or a failed/cancelled recover outcome makes the rollout `restore_failed`."""
    states = dict(r.device_states)
    pending, failed = [], []
    for did, st in states.items():
        if did.startswith("__"):
            continue
        rs = dict(st.get("restore") or {})
        if not rs or rs.get("status") in ("skipped", "succeeded"):
            continue
        if rs.get("status") == "error":
            failed.append(did)
            continue
        op = db.get(Operation, rs.get("operation_id")) if rs.get("operation_id") else None
        if op is None or op.status in ops.UNSETTLED or op.status in ("uncertain", "abandoned_unconfirmed"):
            pending.append(did)
            continue
        rs["status"] = "succeeded" if op.status == "succeeded" else "failed"
        rs["operation_status"] = op.status
        if op.status != "succeeded":
            rs["reason"] = ((op.outcome or {}).get("failure") or {}).get("message") or op.status
            failed.append(did)
        states[did] = {**st, "restore": rs, "restore_status": op.status}
    r.device_states = states
    r.updated_at = utcnow()
    if pending:
        return
    if failed:
        r.status = "restore_failed"
        r.message = f"restore finished with failures (retry restore): {', '.join(failed)}"
    else:
        r.status = "restored"
        r.message = "restore complete: every required target acknowledged its recover operation"
    r.finished_at = utcnow()


def promote(db: DbSession, r: Rollout, by: str | None) -> Rollout:
    with write_txn(db):
        db.refresh(r)
        if r.status != "awaiting_promotion":
            raise RolloutError(f"rollout is {r.status}; promotion requires every canary to have passed", 409)
        plan = db.get(Plan, r.plan_id)
        for c in r.canaries:
            dev = db.get(Device, c)
            st = r.device_states.get(c) or {}
            op = db.get(Operation, st.get("operation_id")) if st.get("operation_id") else None
            if dev is None or not _healthy_now(dev, r.release_id, op.generation if op else None):
                raise RolloutError(
                    f"canary {c} has no recent live healthy report on the release at the deploy generation",
                    409,
                )
            if _fresh_eval(db, c, r.release_id, plan, op) is None:
                raise RolloutError(
                    f"canary {c} has no fresh server-verified passing eval bound to its deploy", 409
                )
            if not _probation_ok(op, plan):
                raise RolloutError(f"canary {c} probation evidence does not meet the plan policy", 409)
        r.status = "expanding"
        r.cursor = 0
        r.message = f"promoted by {by or 'operator'}; serial expansion (max_in_flight 1)"
        r.updated_at = utcnow()
    advance(db, r, by)
    return r


def pause(db: DbSession, r: Rollout, by: str | None) -> Rollout:
    with write_txn(db):
        db.refresh(r)
        if r.status not in ("canary", "expanding", "awaiting_promotion"):
            raise RolloutError(f"cannot pause a {r.status} rollout", 409)
        r.device_states = {**r.device_states, "__paused_from__": {"status": r.status}}
        r.status = "paused"
        r.message = f"paused by {by or 'operator'}: no new grants; granted work may still activate"
        r.updated_at = utcnow()
    return r


def resume(db: DbSession, r: Rollout, by: str | None) -> Rollout:
    with write_txn(db):
        db.refresh(r)
        if r.status != "paused":
            raise RolloutError("not paused", 409)
        prev = (r.device_states.get("__paused_from__") or {}).get("status", "canary")
        states = dict(r.device_states)
        states.pop("__paused_from__", None)
        r.device_states = states
        r.status = prev
        r.message = f"resumed by {by or 'operator'}"
        r.updated_at = utcnow()
    return r


def abort(db: DbSession, r: Rollout, by: str | None) -> Rollout:
    with write_txn(db):
        db.refresh(r)
        if r.status not in ACTIVE and r.status != "draft":
            raise RolloutError(f"cannot abort a {r.status} rollout", 409)
        states = dict(r.device_states)
        for did, st in states.items():
            if did.startswith("__"):
                continue
            op = db.get(Operation, st.get("operation_id")) if st.get("operation_id") else None
            if op is not None and op.status in ("pending", "delivered") and not op.grant_id:
                ops._finish(
                    db, op, "cancelled", {"status": "cancelled", "reason": "rollout aborted", "by": by}
                )
                states[did] = {**st, "status": "cancelled"}
            elif op is not None and op.status in ("granted", "running"):
                op.cancel_requested_at = utcnow()
                states[did] = {**st, "note": "cancellation requested; activation may already be in progress"}
        states.pop("__paused_from__", None)
        r.device_states = states
        r.status = "aborted"
        r.message = f"aborted by {by or 'operator'}; updated devices were NOT restored automatically"
        r.finished_at = utcnow()
        r.updated_at = utcnow()
    return r


RESTORABLE = (
    "completed",
    "failed",
    "aborted",
    "awaiting_promotion",
    "expanding",
    "canary",
    "paused",
    "restored",
    "restore_failed",
)


def restore(db: DbSession, r: Rollout, by: str | None) -> dict[str, Any]:
    """Explicit, tracked restore (R43 follow-up): every target this rollout updated and that still runs
    the rollout release is REQUIRED. Each required target's issuance, error and acknowledged result is
    persisted in device_states["restore"]; the rollout is `restoring` while recover operations are
    outstanding, `restored` only when every required target succeeded, otherwise `restore_failed`
    (re-POST restore to retry the targets that are still required)."""
    results: dict[str, Any] = {}
    with write_txn(db):
        db.refresh(r)
        if r.status == "draft":
            raise RolloutError("nothing to restore", 409)
        if r.status == "restoring":
            raise RolloutError("restore already in progress", 409)
        if r.status not in RESTORABLE:
            raise RolloutError(f"cannot restore a {r.status} rollout", 409)
        states = dict(r.device_states)
        issued = errors = 0
        for did, st in states.items():
            if did.startswith("__"):
                continue
            prev = st.get("restore") or {}
            if prev.get("status") == "succeeded":
                results[did] = {"skipped": "already restored"}
                continue
            if st.get("status") not in ("passed", "awaiting_health", "failed", "deploying"):
                results[did] = {"skipped": f"not updated by this rollout (state {st.get('status')})"}
                continue
            dev = db.get(Device, did)
            if dev is None or dev.observed_active_release_id != r.release_id:
                results[did] = {"skipped": "device is not running the rollout release"}
                states[did] = {
                    **st,
                    "restore": {"status": "skipped", "reason": results[did]["skipped"], "at": iso(utcnow())},
                }
                continue
            try:
                op = ops.create_operation(
                    db,
                    dev,
                    "recover",
                    {"reason": f"rollout {r.id} restore", "rollout_id": r.id},
                    created_by=by,
                    rollout_id=r.id,
                )
                results[did] = {"operation_id": op.id}
                states[did] = {
                    **st,
                    "restore_operation_id": op.id,
                    "restore": {"status": "issued", "operation_id": op.id, "at": iso(utcnow())},
                }
                issued += 1
            except ops.OperationError as e:
                results[did] = {"error": str(e)}
                states[did] = {
                    **st,
                    "restore": {
                        "status": "error",
                        "reason": str(e),
                        "at": iso(utcnow()),
                        "active_operation_id": dev.active_operation_id,
                    },
                }
                errors += 1
        r.device_states = states
        if issued:
            r.status = "restoring"
            r.message = f"restore issued by {by or 'operator'}; awaiting acknowledged recover outcomes" + (
                f" ({errors} target(s) could not be issued; retry restore)" if errors else ""
            )
            r.finished_at = None
        elif errors:
            r.status = "restore_failed"
            r.message = f"restore by {by or 'operator'} could not be issued to {errors} target(s); fix and retry restore"
            r.finished_at = utcnow()
        else:
            r.status = "restored"
            r.message = f"restore by {by or 'operator'}: no target still runs the rollout release"
            r.finished_at = r.finished_at or utcnow()
        r.updated_at = utcnow()
    return {"rollout": r.id, "status": r.status, "results": results}


def advance_all(db: DbSession, worker: Any = None) -> int:
    n = 0
    for r in list(
        db.scalars(select(Rollout).where(Rollout.status.in_(("canary", "expanding", "restoring"))))
    ):
        advance(db, r, worker=worker)
        n += 1
    return n


def rollout_out(r: Rollout) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "plan_id": r.plan_id,
        "release_id": r.release_id,
        "targets": r.targets,
        "canaries": r.canaries,
        "status": r.status,
        "device_states": {k: v for k, v in r.device_states.items() if not k.startswith("__")},
        "previous": r.previous,
        "cursor": r.cursor,
        "message": r.message,
        "simulated": r.simulated,
        "created_by": r.created_by,
        "created_at": iso(r.created_at),
        "updated_at": iso(r.updated_at),
        "finished_at": iso(r.finished_at),
    }
