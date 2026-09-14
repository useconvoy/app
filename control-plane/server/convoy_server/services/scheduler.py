"""Fenced scheduler (§8.1, §8.11): leader lease with owner + token + expiry checked by EVERY committing
worker transaction; civil occurrence identity (fold 0 only, gaps skipped visibly); missed-window policy
with catch-up bounds; per-target overlap skip / no_change / offline_deferred; occurrence status
aggregates child operations; owner revocation pauses dispatch."""

from __future__ import annotations

import logging
import secrets
import socket
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ..db import write_txn
from ..ids import aware, iso, new_id, utcnow
from ..models import Device, Occurrence, Operation, Plan, Release, Schedule, SchedulerLease, User
from ..serialize import device_status
from . import operations as ops

log = logging.getLogger("convoy.scheduler")
LEASE_TTL_S = 30
MIN_INTERVAL_S = 300
CIVIL_FMT = "%Y-%m-%dT%H:%M"


class ScheduleError(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


class FenceLost(Exception):
    pass


# ---------------------------------------------------------------- cron / civil time
def validate_schedule(cron: str, tz: str) -> None:
    try:
        ZoneInfo(tz)
    except Exception as e:
        raise ScheduleError(f"unknown IANA timezone {tz!r}") from e
    if not isinstance(cron, str) or len(cron.split()) != 5 or not croniter.is_valid(cron):
        raise ScheduleError(f"invalid 5-field cron expression {cron!r}")
    # minimum interval: sample the next 8 civil occurrences from a fixed reference
    ref = datetime(2026, 1, 5, 0, 0, tzinfo=ZoneInfo(tz))
    it = croniter(cron, ref)
    prev = None
    for _ in range(8):
        nxt = it.get_next(datetime)
        if prev is not None and (nxt - prev).total_seconds() < MIN_INTERVAL_S:
            raise ScheduleError(f"schedule fires more often than every {MIN_INTERVAL_S // 60} minutes")
        prev = nxt


def civil_key(local: datetime) -> str:
    return local.strftime(CIVIL_FMT)


def _exists(naive: datetime, zone: ZoneInfo) -> bool:
    """A wall time exists unless it falls in a spring-forward gap (round-tripping through UTC moves it)."""
    local = naive.replace(tzinfo=zone, fold=0)
    return local.astimezone(ZoneInfo("UTC")).astimezone(zone).replace(tzinfo=None) == naive


def next_occurrences(cron: str, tz: str, after: datetime, n: int = 5) -> list[dict[str, Any]]:
    """Civil occurrences strictly after `after` (UTC), R49. The expression is iterated in NAIVE local wall
    time so every ordinary match is found; each wall time is then classified: a non-existent local time
    (spring-forward gap) is recorded as skipped and never fired at a shifted instant; a repeated local
    time (fall-back fold) fires exactly once, at its first instant (fold 0). Every returned instant is
    strictly later than `after`, so a fold-0 instant that already passed is never re-issued."""
    zone = ZoneInfo(tz)
    utc = ZoneInfo("UTC")
    after = after.astimezone(utc)
    base = after.astimezone(zone).replace(tzinfo=None)
    it = croniter(cron, base)
    out: list[dict[str, Any]] = []
    fireable = 0
    guard = 0
    while fireable < n and guard < max(64, n * 64):
        guard += 1
        naive = it.get_next(datetime).replace(tzinfo=None)
        key = civil_key(naive)
        if not _exists(naive, zone):
            out.append({"civil": key, "utc": None, "offset": None, "note": "skipped_nonexistent (DST gap)"})
            continue
        canonical = naive.replace(tzinfo=zone, fold=0)
        instant = canonical.astimezone(utc)
        if instant <= after:
            continue  # e.g. fold-0 wall time whose first instant already passed
        out.append(
            {
                "civil": key,
                "utc": iso(instant),
                "offset": canonical.strftime("%z"),
                "note": "fold_0" if _is_ambiguous(canonical) else None,
                "_dt": instant,
            }
        )
        fireable += 1
    return out


def _is_ambiguous(dt: datetime) -> bool:
    return dt.replace(fold=0).utcoffset() != dt.replace(fold=1).utcoffset()


def compute_next(cron: str, tz: str, after: datetime) -> tuple[datetime | None, str | None]:
    for occ in next_occurrences(cron, tz, after, n=3):
        if occ["utc"] is not None:
            return occ["_dt"], occ["civil"]
    return None, None


# ---------------------------------------------------------------- leader lease + fence
def _fenced_refresh(db: DbSession, owner: str, token: str, fence: int) -> None:
    """Call inside every committing worker transaction: owner AND token AND fence AND unexpired; the
    lease expiry is refreshed as a side effect. Raises FenceLost when the identity no longer holds."""
    now = utcnow()
    res = db.execute(
        update(SchedulerLease)
        .where(
            SchedulerLease.name == "scheduler",
            SchedulerLease.owner == owner,
            SchedulerLease.token == token,
            SchedulerLease.fence == fence,
            SchedulerLease.expires_at > now,
        )
        .values(expires_at=now + timedelta(seconds=LEASE_TTL_S))
    )
    if res.rowcount != 1:
        raise FenceLost(f"{owner} lost the scheduler lease (fence {fence})")


class FenceToken:
    """An IMMUTABLE capture of a worker's identity (owner, token, fence) handed to a background task.
    The task publishes only through `fenced()`, which succeeds solely while that exact identity still
    holds the lease: after a loss, or after the same worker re-acquires with a higher fence, the
    captured token no longer matches and the task can neither publish, register nor prune. The mutable
    Worker is never shared with the task."""

    __slots__ = ("owner", "token", "fence")

    def __init__(self, owner: str, token: str, fence: int):
        self.owner, self.token, self.fence = owner, token, fence

    def fenced(self, db: DbSession) -> None:
        _fenced_refresh(db, self.owner, self.token, self.fence)

    def __repr__(self) -> str:
        return f"FenceToken({self.owner}, fence {self.fence})"


class Worker:
    def __init__(self, owner: str | None = None):
        self.owner = owner or f"{socket.gethostname()}:{new_id('w', 6)}"
        self.token = secrets.token_hex(8)
        self.fence = 0

    def token_snapshot(self) -> FenceToken:
        return FenceToken(self.owner, self.token, self.fence)

    def acquire(self, db: DbSession) -> bool:
        now = utcnow()
        with write_txn(db):
            row = db.get(SchedulerLease, "scheduler")
            if row is None:
                row = SchedulerLease(
                    name="scheduler",
                    owner=self.owner,
                    token=self.token,
                    fence=1,
                    expires_at=now + timedelta(seconds=LEASE_TTL_S),
                    acquired_at=now,
                )
                db.add(row)
                self.fence = 1
                return True
            if (row.owner == self.owner and row.token == self.token) or aware(row.expires_at) < now:
                if not (row.owner == self.owner and row.token == self.token):
                    row.fence += 1
                    row.acquired_at = now
                row.owner = self.owner
                row.token = self.token
                row.expires_at = now + timedelta(seconds=LEASE_TTL_S)
                self.fence = row.fence
                return True
            return False

    def fenced(self, db: DbSession) -> None:
        """Call inside every committing worker transaction: owner AND token AND fence AND unexpired."""
        _fenced_refresh(db, self.owner, self.token, self.fence)


# ---------------------------------------------------------------- dispatch
def _targets(db: DbSession, target: dict[str, Any]) -> list[Device]:
    q = select(Device).where(Device.retired_at.is_(None))
    if target.get("device_ids"):
        return list(db.scalars(q.where(Device.id.in_(list(target["device_ids"])))))
    if target.get("group"):
        return list(db.scalars(q.where(Device.group_name == target["group"])))
    return []


def fire(
    db: DbSession,
    worker: Worker | None,
    s: Schedule,
    scheduled_for: datetime,
    civil: str,
    *,
    expected_revision: int | None = None,
    window_end: datetime | None = None,
    catch_up: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inside one fenced write transaction: re-validate the selection (R50: still enabled, unpaused, due
    and at the revision the caller selected), insert the occurrence (unique civil identity), freeze the
    revision/targets/payload, issue per-device operations with overlap/no_change/offline handling.
    R54: a catch-up run keeps the occurrence's civil identity but gets an explicit execution window."""
    with write_txn(db):
        if worker:
            worker.fenced(db)
        db.refresh(s)
        if not s.enabled or s.paused_at is not None:
            return {"stale": "schedule paused or disabled after selection", "civil": civil}
        if expected_revision is not None and s.revision != expected_revision:
            return {
                "stale": f"schedule edited after selection (revision {expected_revision} -> {s.revision})",
                "civil": civil,
            }
        if s.next_run_at is None or aware(s.next_run_at) != aware(scheduled_for):
            return {"stale": "occurrence no longer due after selection", "civil": civil}
        occ = Occurrence(
            id=new_id("occ"),
            schedule_id=s.id,
            civil_key=civil,
            scheduled_utc=scheduled_for,
            revision=s.revision,
            frozen={
                "target": s.target,
                "payload": s.payload,
                "kind": s.kind,
                "actor": s.created_by,
                "owner_id": s.owner_id,
            },
        )
        db.add(occ)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return {"deduped": True, "civil": civil}
        owner = db.get(User, s.owner_id) if s.owner_id else None
        if owner is None or owner.disabled or owner.role not in ("operator", "admin"):
            s.paused_at = utcnow()
            s.pause_reason = "owner_revoked"
            occ.status = "paused"
            occ.results = {"reason": "owner no longer authorized; schedule paused"}
            occ.finished_at = utcnow()
            return {"paused": "owner_revoked", "occurrence_id": occ.id}
        paused = ops.dispatch_paused(db)
        if paused:
            occ.status = "paused"
            occ.results = {"reason": f"dispatch paused: {paused}"}
            occ.finished_at = utcnow()
            return {"paused": paused, "occurrence_id": occ.id}
        payload = dict(s.payload or {})
        plan = db.get(Plan, payload["plan_id"]) if payload.get("plan_id") else None
        rel = (
            db.get(Release, payload["release_id"])
            if payload.get("release_id")
            else (db.get(Release, plan.release_id) if plan else None)
        )
        results: list[dict[str, Any]] = []
        window_end = window_end or (scheduled_for + timedelta(seconds=s.window_s))
        if catch_up:
            occ.results = {"catch_up": {**catch_up, "execution_window_end": iso(window_end)}}
        for dev in _targets(db, s.target or {}):
            entry: dict[str, Any] = {"device_id": dev.id, "name": dev.name}
            if device_status(dev) != "online":
                entry["skipped"] = "offline_deferred"
            elif dev.active_operation_id:
                entry["skipped"] = "busy"
                entry["active_operation_id"] = dev.active_operation_id
            elif s.kind == "deploy":
                if rel is None or plan is None:
                    entry["error"] = "deploy schedules need a plan (pinned release + qualification)"
                elif (
                    dev.observed_active_release_id == rel.id
                    and dev.observed_health == "ok"
                    and dev.observed_stage == "active"
                ):
                    entry["skipped"] = "no_change"
                else:
                    try:
                        op = ops.create_operation(
                            db,
                            dev,
                            "deploy",
                            {"release_id": rel.id, "plan_id": plan.id},
                            created_by=f"schedule:{s.id}",
                            occurrence_id=occ.id,
                            window_end=window_end,
                        )
                        entry["operation_id"] = op.id
                    except ops.OperationError as e:
                        entry["error"] = str(e)
            elif s.kind == "eval":
                if plan is None:
                    entry["error"] = "eval schedules need a plan"
                elif dev.observed_active_release_id != plan.release_id:
                    entry["skipped"] = (
                        f"device runs {dev.observed_active_release_id}, plan qualifies {plan.release_id}"
                    )
                else:
                    try:
                        op = ops.create_operation(
                            db,
                            dev,
                            "eval",
                            {"plan_id": plan.id, "baseline": bool(payload.get("baseline"))},
                            created_by=f"schedule:{s.id}",
                            occurrence_id=occ.id,
                            window_end=window_end,
                        )
                        entry["operation_id"] = op.id
                    except ops.OperationError as e:
                        entry["error"] = str(e)
            elif s.kind in ("health", "collect"):
                try:
                    op = ops.create_operation(
                        db,
                        dev,
                        s.kind,
                        {},
                        created_by=f"schedule:{s.id}",
                        occurrence_id=occ.id,
                        window_end=window_end,
                    )
                    entry["operation_id"] = op.id
                except ops.OperationError as e:
                    entry["error"] = str(e)
            else:
                entry["error"] = f"unknown kind {s.kind}"
            results.append(entry)
        occ.results = {**(occ.results or {}), "devices": results}
        if not any("operation_id" in r for r in results):
            occ.status = (
                "completed"
                if all("skipped" in r for r in results)
                else ("failed" if any("error" in r for r in results) else "completed")
            )
            occ.finished_at = utcnow()
        else:
            occ.status = "dispatched"
        return {"occurrence_id": occ.id, "civil": civil, "devices": results}


def tick_schedules(db: DbSession, worker: Worker | None, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or utcnow()
    fired: list[dict[str, Any]] = []
    for s in list(
        db.scalars(
            select(Schedule).where(
                Schedule.enabled.is_(True),
                Schedule.paused_at.is_(None),
                Schedule.next_run_at.is_not(None),
                Schedule.next_run_at <= now,
            )
        )
    ):
        due = aware(s.next_run_at)
        civil = s.next_civil or civil_key(due.astimezone(ZoneInfo(s.timezone)).replace(tzinfo=None))
        late = (now - due).total_seconds()
        revision = s.revision
        if late > s.window_s:
            if s.missed_policy == "run_once" and late <= s.catchup_age_s:
                # R54: same civil occurrence, but a fresh bounded execution window starting now
                res = fire(
                    db, worker, s, due, civil,
                    expected_revision=revision,
                    window_end=now + timedelta(seconds=s.window_s),
                    catch_up={"late_s": int(late), "scheduled_utc": iso(due)},
                )  # fmt: skip
                res["late_s"] = int(late)
                res["catch_up"] = True
            else:
                res = {"missed": civil, "policy": "skip", "late_s": int(late)}
                # compress the skipped range
                with write_txn(db):
                    if worker:
                        worker.fenced(db)
                    db.refresh(s)
                    skipped = list((s.last_result or {}).get("skipped_ranges") or [])
                    skipped.append({"from": civil, "to": civil, "late_s": int(late)})
                    s.last_result = {**res, "skipped_ranges": skipped[-20:]}
        else:
            res = fire(db, worker, s, due, civil, expected_revision=revision)
            res["late_s"] = int(late)
        if res.get("stale"):
            fired.append({"schedule_id": s.id, **res})
            continue  # R50: selection went stale; leave next_run_at for the next tick to re-evaluate
        with write_txn(db):
            if worker:
                worker.fenced(db)
            db.refresh(s)
            s.last_run_at = now
            s.last_result = {**(s.last_result if "missed" in res else {}), **res}
            nxt, ncivil = compute_next(s.cron, s.timezone, now)
            s.next_run_at, s.next_civil = nxt, ncivil
            s.updated_at = now
        fired.append({"schedule_id": s.id, **res})
    return fired


def finalize_occurrences(db: DbSession, worker: Worker | None) -> int:
    n = 0
    for occ in list(db.scalars(select(Occurrence).where(Occurrence.status == "dispatched"))):
        ops_ = list(db.scalars(select(Operation).where(Operation.occurrence_id == occ.id)))
        if not ops_ or any(o.status in ops.UNSETTLED for o in ops_):
            continue
        with write_txn(db):
            if worker:
                worker.fenced(db)
            statuses = [o.status for o in ops_]
            occ.status = (
                "completed"
                if all(x == "succeeded" for x in statuses)
                else ("failed" if all(x != "succeeded" for x in statuses) else "partial")
            )
            occ.results = {**(occ.results or {}), "operations": {o.id: o.status for o in ops_}}
            occ.finished_at = utcnow()
            n += 1
    return n


# ---------------------------------------------------------------- CRUD helpers
def create_schedule(db: DbSession, data: dict[str, Any], owner_id: str) -> Schedule:
    validate_schedule(data["cron"], data.get("timezone", "UTC"))
    if data["kind"] not in ("eval", "health", "collect", "deploy"):
        raise ScheduleError("kind must be eval|health|collect|deploy")
    payload = dict(data.get("payload") or {})
    if data["kind"] in ("deploy", "eval"):
        plan = db.get(Plan, payload.get("plan_id")) if payload.get("plan_id") else None
        if plan is None:
            raise ScheduleError(f"{data['kind']} schedules must pin a plan (release + qualification)")
        payload["release_id"] = plan.release_id
    if not (data.get("target") or {}).get("device_ids") and not (data.get("target") or {}).get("group"):
        raise ScheduleError("target needs device_ids or group")
    nxt, civil = compute_next(data["cron"], data.get("timezone", "UTC"), utcnow())
    s = Schedule(
        id=new_id("sch"),
        name=data["name"],
        kind=data["kind"],
        cron=data["cron"],
        timezone=data.get("timezone", "UTC"),
        target=data.get("target") or {},
        payload=payload,
        missed_policy=data.get("missed_policy", "skip"),
        catchup_age_s=int(data.get("catchup_age_s", 3600)),
        window_s=int(data.get("window_s", 600)),
        enabled=bool(data.get("enabled", True)),
        owner_id=owner_id,
        revision=1,
        next_run_at=nxt,
        next_civil=civil,
        created_by=owner_id,
    )
    if s.missed_policy not in ("skip", "run_once"):
        raise ScheduleError("missed_policy must be skip|run_once")
    db.add(s)
    db.flush()
    return s


def update_schedule(db: DbSession, s: Schedule, data: dict[str, Any]) -> Schedule:
    """Partial edit (R55): only explicitly supplied fields change; null is never a replacement."""
    for k in ("cron", "timezone", "name", "kind"):
        if k in data and data[k] is None:
            raise ScheduleError(f"{k} cannot be null")
    if "kind" in data and data["kind"] != s.kind:
        raise ScheduleError("kind is immutable; create a new schedule")
    cron = data.get("cron", s.cron)
    tz = data.get("timezone", s.timezone)
    validate_schedule(cron, tz)
    if "missed_policy" in data and data["missed_policy"] not in ("skip", "run_once"):
        raise ScheduleError("missed_policy must be skip|run_once")
    if "target" in data and not (
        (data["target"] or {}).get("device_ids") or (data["target"] or {}).get("group")
    ):
        raise ScheduleError("target needs device_ids or group")
    for k in ("name", "target", "payload", "missed_policy", "catchup_age_s", "window_s", "enabled"):
        if k in data and data[k] is not None:
            setattr(s, k, data[k])
    s.cron, s.timezone = cron, tz
    s.revision += 1
    s.next_run_at, s.next_civil = compute_next(cron, tz, utcnow())
    s.updated_at = utcnow()
    return s


def schedule_out(s: Schedule) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "kind": s.kind,
        "cron": s.cron,
        "timezone": s.timezone,
        "target": s.target,
        "payload": s.payload,
        "missed_policy": s.missed_policy,
        "catchup_age_s": s.catchup_age_s,
        "window_s": s.window_s,
        "enabled": s.enabled,
        "paused_at": iso(s.paused_at),
        "pause_reason": s.pause_reason,
        "owner_id": s.owner_id,
        "revision": s.revision,
        "next_civil": s.next_civil,
        "next_run_at": iso(s.next_run_at),
        "last_run_at": iso(s.last_run_at),
        "last_result": s.last_result,
        "created_at": iso(s.created_at),
        "updated_at": iso(s.updated_at),
    }


def occurrence_out(o: Occurrence) -> dict[str, Any]:
    return {
        "id": o.id,
        "schedule_id": o.schedule_id,
        "civil_key": o.civil_key,
        "scheduled_utc": iso(o.scheduled_utc),
        "revision": o.revision,
        "frozen": o.frozen,
        "dispatched_at": iso(o.dispatched_at),
        "status": o.status,
        "results": o.results,
        "finished_at": iso(o.finished_at),
    }


def preview(s: Schedule, n: int = 5) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in occ.items() if not k.startswith("_")}
        for occ in next_occurrences(s.cron, s.timezone, utcnow(), n)
    ]
