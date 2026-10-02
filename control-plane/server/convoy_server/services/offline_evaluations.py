"""Owner-scoped offline evaluations: simulation episodes recorded outside the hosted runner.

An offline evaluation is its owner's own import. Convoy did not run it and nothing in it is signed or
verified against a release: responses say so (`source: "offline"`, `signed: false`, `scope`), and the
website labels it "Offline sim". Every read and write is scoped to the caller's account; administrators
cannot read or change another user's offline evaluations. Writes need the operator role.

An evaluation carries labels (name, task, configuration, policy) and a summary the server computes from
its episodes. An episode upload is one JSON request in the replay format: the replay manifest's fields
plus every frame, `{index, image_png_base64, action, reward, success, policy_ms}` for index 0..steps,
where frame 0 is the camera before the first action and frame k the camera after action k. Actions
have one width per episode (1–MAX_ACTION_DIM values). Images are PNG or JPEG of at most MAX_DIMENSION
pixels a side; at most MAX_IMAGES frames carry one (frame 0 must), and a frame without one replays the
latest earlier image. The replay endpoints answer in the hosted replay shape, so the same player reads
both.

Writes follow the workspace-document pattern: the router authenticates the caller, checks the headers,
the evaluation's ownership and the account's write budget (WRITE_LIMIT per WRITE_WINDOW_S) before it
reads the body; each write needs an Idempotency-Key and uses the durable receipts (metadata only,
pruned after RECEIPT_TTL or beyond MAX_RECEIPTS per account) and the audit log, which never records
bodies. Identical episode content uploaded again is the stored episode, not a second one.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import statistics
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from convoy_contracts.execution import canonical_digest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, ValidationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..auth import Principal, assert_live_principal, audit
from ..db import write_txn
from ..ids import iso, new_id, utcnow
from ..offline_models import OfflineEpisode, OfflineEvaluation
from ..platform_models import MutationReceipt
from . import offline_recordings as recordings
from .identity import IdentityError, throttle_check
from .platform import previous_receipt, record_receipt, require_idempotency_key
from .replay import MAX_STEPS

EVALUATION_ID = r"^oev_[a-z0-9]{12}$"
EPISODE_ID = r"^oep_[a-z0-9]{12}$"
ROUTE_PREFIX = "/api/v1/offline-evaluations"
MAX_EVALUATIONS = 100
MAX_EPISODES = 200
MAX_IMAGES = 512
MAX_DIMENSION = recordings.MAX_DIMENSION
MAX_IMAGE_BYTES = recordings.MAX_IMAGE_BYTES
MAX_ACTION_DIM = 64
MAX_ACTION_VALUE = 1e6
MAX_METRICS = 32
MAX_METRICS_BYTES = 4096
# The episode request body: images as base64 plus every step. Checked as it arrives (ChatBodyLimit).
MAX_EPISODE_BODY = 16 * 1024 * 1024
WRITE_LIMIT = 120
WRITE_WINDOW_S = 600
RECEIPT_TTL = timedelta(hours=24)
MAX_RECEIPTS = 1024
SOURCE = "offline"
SCOPE = "Offline simulation import: recorded and uploaded by its owner; not run, verified or signed by Convoy"
REPLAY_SOURCE = "Offline simulation import · unsigned · frames and actions as uploaded"
OUTCOMES = ("success", "failure", "timeout", "safety-stop")
JSON_CONTENT = re.compile(r"application/(?:[a-z0-9.+-]*\+)?json\s*(?:;.*)?", re.IGNORECASE)
# Printable text without control characters.
TEXT = r"^[^\x00-\x1f\x7f]*$"
ENCODED_IMAGE = (MAX_IMAGE_BYTES + 2) // 3 * 4

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=TEXT)]
AxisLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=24, pattern=TEXT)]
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
NonNegative = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
# Reported local-clock measurements, bounded to one day in milliseconds. Imports
# carry observations from their owner; these values are not timing qualification.
ReportedMilliseconds = Annotated[float, Field(strict=True, ge=0, le=86_400_000, allow_inf_nan=False)]
ActionValue = Annotated[float, Field(strict=True, ge=-MAX_ACTION_VALUE, le=MAX_ACTION_VALUE, allow_inf_nan=False)]
MetricName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
MetricValue = StrictBool | Finite | Annotated[str, StringConstraints(max_length=200, pattern=TEXT)] | None


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationIn(Input):
    name: Label
    task: Label
    config_label: Label
    policy_label: Label


class HierarchyIn(Input):
    planner_state: Literal["idle", "pending", "accepted", "stale", "error"]
    task_revision: int = Field(strict=True, ge=0, le=2**31 - 1)
    active_skill: Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=TEXT)]
    target: AxisLabel | None = None
    planner_latency_ms: ReportedMilliseconds | None = None
    observation_age_ms: ReportedMilliseconds | None = None
    physics_lag_ms: ReportedMilliseconds | None = None


class FrameIn(Input):
    index: int = Field(strict=True, ge=0, le=MAX_STEPS)
    image_png_base64: Annotated[str, StringConstraints(max_length=ENCODED_IMAGE)] | None = None
    action: Annotated[list[ActionValue], Field(min_length=1, max_length=MAX_ACTION_DIM)] | None = None
    reward: Finite | None = None
    success: StrictBool | None = None
    policy_ms: NonNegative | None = None
    hierarchy: HierarchyIn | None = None


class EpisodeIn(Input):
    seed: int = Field(strict=True, ge=0, le=2**53 - 1)
    outcome: Literal["success", "failure", "timeout", "safety-stop"]
    metrics: Annotated[dict[MetricName, MetricValue], Field(max_length=MAX_METRICS)] = Field(default_factory=dict)
    action_labels: Annotated[list[AxisLabel], Field(min_length=1, max_length=MAX_ACTION_DIM)] | None = None
    skill: Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=TEXT)] | None = None
    planner_ms: NonNegative | None = None
    wall_seconds: NonNegative | None = None
    sim_seconds: NonNegative | None = None
    frames: Annotated[list[FrameIn], Field(min_length=2, max_length=MAX_STEPS + 1)]


@dataclass(frozen=True)
class Episode:
    """A validated upload, ready to store."""

    data: EpisodeIn
    action_dim: int
    images: list[tuple[int, str, bytes]]
    digest: str
    estimate: int

    @property
    def steps(self) -> int:
        return len(self.data.frames) - 1


# ---- requests ------------------------------------------------------------------------------------


def check_write_headers(key: str | None, content_type: str | None) -> str:
    key = require_idempotency_key(key)
    if not JSON_CONTENT.fullmatch(content_type or ""):
        raise HTTPException(422, "the request body must be JSON (Content-Type: application/json)")
    return key


def admit_write(db: Session, principal: Principal) -> None:
    """Counts a write against its owner's budget, before the request body is read."""
    try:
        throttle_check(db, f"offline-evaluations:{principal.user.id}", WRITE_LIMIT, WRITE_WINDOW_S)
    except IdentityError as error:
        raise HTTPException(
            429,
            f"too many offline evaluation writes (at most {WRITE_LIMIT} per {WRITE_WINDOW_S // 60} minutes)",
            headers={"Retry-After": str(error.retry_after or WRITE_WINDOW_S)},
        ) from None


def _parse(model: type[BaseModel], raw: bytes) -> Any:
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in error.errors(include_url=False, include_input=False)]
        ) from None


def _invalid(loc: tuple, message: str) -> RequestValidationError:
    return RequestValidationError([{"type": "value_error", "loc": ("body", *loc), "msg": message}])


def decode_episode(raw: bytes) -> Episode:
    """The upload validated as a whole: frame order, action width, labels, metrics and every image."""
    data: EpisodeIn = _parse(EpisodeIn, raw)
    frames = data.frames
    for index, frame in enumerate(frames):
        if frame.index != index:
            raise _invalid(("frames", index, "index"), f"expected {index}: list frames in order from 0")
    first = frames[0]
    if first.image_png_base64 is None:
        raise _invalid(("frames", 0, "image_png_base64"), "frame 0 needs the camera image before the first action")
    if (first.action, first.reward, first.success, first.policy_ms) != (None, None, None, None):
        raise _invalid(("frames", 0), "frame 0 precedes the first action: action, reward, success and policy_ms are null")
    width = None
    for index, frame in enumerate(frames[1:], start=1):
        if frame.action is None:
            raise _invalid(("frames", index, "action"), "every step after frame 0 needs its action")
        width = width or len(frame.action)
        if len(frame.action) != width:
            raise _invalid(("frames", index, "action"), f"expected {width} values, the width of frame 1's action")
    if data.action_labels is not None and len(data.action_labels) != width:
        raise _invalid(("action_labels",), f"expected {width} labels, one per action value")
    metrics = json.dumps(data.metrics, ensure_ascii=False, separators=(",", ":"))
    if len(metrics.encode()) > MAX_METRICS_BYTES:
        raise _invalid(("metrics",), f"metrics exceed {MAX_METRICS_BYTES} bytes as compact JSON")
    shown = [(index, frame.image_png_base64) for index, frame in enumerate(frames) if frame.image_png_base64 is not None]
    if len(shown) > MAX_IMAGES:
        raise _invalid(("frames",), f"at most {MAX_IMAGES} frames may carry an image")
    images, hashes = [], {}
    for index, encoded in shown:
        try:
            content = base64.b64decode(encoded, validate=True)
            media_type, _, _ = recordings.image(content)
        except (ValueError, binascii.Error) as error:
            reason = "invalid base64" if isinstance(error, binascii.Error) else str(error)
            raise _invalid(("frames", index, "image_png_base64"), reason) from None
        images.append((index, media_type, content))
        hashes[index] = hashlib.sha256(content).hexdigest()
    canonical = {
        **data.model_dump(exclude={"frames"}),
        "frames": [[f.action, f.reward, f.success, f.policy_ms, hashes.get(i)] for i, f in enumerate(frames)],
    }
    # Preserve the digest of existing uploads. A present trace contributes to
    # content identity, so different skill decisions cannot deduplicate together.
    for index, frame in enumerate(frames):
        if frame.hierarchy is not None:
            canonical["frames"][index].append(frame.hierarchy.model_dump())
    digest = canonical_digest(canonical)
    estimate = sum(len(content) for _, _, content in images) + len(json.dumps(canonical["frames"]))
    return Episode(data=data, action_dim=width, images=images, digest=digest, estimate=estimate)


# ---- views ---------------------------------------------------------------------------------------


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def summarize(episodes: list[OfflineEpisode]) -> dict:
    successes = sum(1 for episode in episodes if episode.outcome == "success")
    return {
        "episodes": len(episodes),
        "successes": successes,
        "success_rate": successes / len(episodes) if episodes else None,
        "median_steps": _median([episode.steps for episode in episodes]),
        "median_wall_seconds": _median([e.wall_seconds for e in episodes if e.wall_seconds is not None]),
        "median_sim_seconds": _median([e.sim_seconds for e in episodes if e.sim_seconds is not None]),
        "stored_bytes": sum(episode.stored_bytes for episode in episodes),
    }


def evaluation_out(row: OfflineEvaluation) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "task": row.task,
        "config_label": row.config_label,
        "policy_label": row.policy_label,
        "summary": row.summary,
        "source": SOURCE,
        "signed": False,
        "scope": SCOPE,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }


def episode_out(row: OfflineEpisode) -> dict:
    return {
        "id": row.id,
        "evaluation_id": row.evaluation_id,
        "seed": row.seed,
        "outcome": row.outcome,
        "steps": row.steps,
        "images": row.images,
        "action_dim": row.action_dim,
        "metrics": row.metrics,
        "wall_seconds": row.wall_seconds,
        "sim_seconds": row.sim_seconds,
        "stored_bytes": row.stored_bytes,
        "created_at": iso(row.created_at),
    }


# ---- reads ---------------------------------------------------------------------------------------


def owned_evaluation(db: Session, principal: Principal, evaluation_id: str) -> OfflineEvaluation:
    row = db.scalar(
        select(OfflineEvaluation).where(
            OfflineEvaluation.id == evaluation_id, OfflineEvaluation.owner_user_id == principal.user.id
        )
    )
    if row is None:
        raise HTTPException(404, "offline evaluation not found")
    return row


def _episodes(db: Session, evaluation_id: str) -> list[OfflineEpisode]:
    return list(db.scalars(
        select(OfflineEpisode)
        .where(OfflineEpisode.evaluation_id == evaluation_id)
        .order_by(OfflineEpisode.created_at, OfflineEpisode.id)
    ))


def owned_episode(db: Session, principal: Principal, evaluation_id: str, episode_id: str) -> OfflineEpisode:
    row = db.scalar(
        select(OfflineEpisode).where(
            OfflineEpisode.id == episode_id,
            OfflineEpisode.evaluation_id == evaluation_id,
            OfflineEpisode.owner_user_id == principal.user.id,
        )
    )
    if row is None:
        raise HTTPException(404, "offline episode not found")
    return row


def list_evaluations(db: Session, principal: Principal) -> list[dict]:
    rows = db.scalars(
        select(OfflineEvaluation)
        .where(OfflineEvaluation.owner_user_id == principal.user.id)
        .order_by(OfflineEvaluation.created_at.desc(), OfflineEvaluation.id.desc())
        .limit(MAX_EVALUATIONS)
    )
    return [evaluation_out(row) for row in rows]


def get_evaluation(db: Session, principal: Principal, evaluation_id: str) -> dict:
    row = owned_evaluation(db, principal, evaluation_id)
    return {**evaluation_out(row), "episodes": [episode_out(episode) for episode in _episodes(db, row.id)]}


def replay_manifest(db: Session, principal: Principal, evaluation_id: str, episode_id: str) -> dict:
    episode = owned_episode(db, principal, evaluation_id, episode_id)
    with recordings.recording(episode) as (_, manifest):
        result = {
            "episode_id": episode.id,
            "mission_id": None,
            "release_digest": None,
            "steps": episode.steps,
            "skill": manifest.get("skill"),
            "planner_ms": manifest.get("planner_ms"),
            "wall_seconds": episode.wall_seconds,
            "sim_seconds": episode.sim_seconds,
            "source": REPLAY_SOURCE,
            "evaluation_id": episode.evaluation_id,
            "action_labels": manifest.get("action_labels"),
            "action_dim": episode.action_dim,
            "images": episode.images,
        }
        for key in ("has_hierarchy", "measurement_source"):
            if key in manifest:
                result[key] = manifest[key]
        return result


def replay_frame(db: Session, principal: Principal, evaluation_id: str, episode_id: str, index: int) -> dict:
    episode = owned_episode(db, principal, evaluation_id, episode_id)
    if not 0 <= index <= episode.steps:
        raise recordings.unavailable()
    with recordings.recording(episode) as (connection, _):
        try:
            return recordings.frame(connection, index)
        except (ValueError, TypeError):
            raise recordings.unavailable() from None


# ---- writes --------------------------------------------------------------------------------------


def create_evaluation(db: Session, principal: Principal, route: str, key: str, raw: bytes) -> dict:
    data: EvaluationIn = _parse(EvaluationIn, raw)
    payload = data.model_dump()
    with write_txn(db):
        assert_live_principal(db, principal, "operator")
        previous = previous_receipt(db, principal, route, key, canonical_digest(payload))
        if previous is not None:
            return previous.response
        count = db.scalar(
            select(func.count()).select_from(OfflineEvaluation)
            .where(OfflineEvaluation.owner_user_id == principal.user.id)
        )
        if count >= MAX_EVALUATIONS:
            raise HTTPException(409, f"offline evaluation limit reached ({MAX_EVALUATIONS} per account); delete one first")
        now = utcnow()
        row = OfflineEvaluation(
            id=new_id("oev"), owner_user_id=principal.user.id, summary=summarize([]), created_at=now, updated_at=now,
            **payload,
        )
        db.add(row)
        response = evaluation_out(row)
        record_receipt(db, principal, route, key, canonical_digest(payload), response)
        audit(db, principal, "offline_evaluation.create", row.id, **payload)
        _prune_receipts(db, principal, now)
    return response


def upload_episode(
    db: Session, principal: Principal, route: str, key: str, evaluation_id: str, raw: bytes
) -> tuple[dict, bool]:
    """Stores one episode with its receipt and audit row. Returns (episode, created)."""
    episode = decode_episode(raw)
    digest = canonical_digest({"evaluation_id": evaluation_id, "episode": episode.digest})
    episode_id = new_id("oep")
    stored = False
    try:
        with write_txn(db):
            assert_live_principal(db, principal, "operator")
            previous = previous_receipt(db, principal, route, key, digest)
            if previous is not None:
                return previous.response, True
            evaluation = owned_evaluation(db, principal, evaluation_id)
            existing = db.scalar(select(OfflineEpisode).where(
                OfflineEpisode.evaluation_id == evaluation.id, OfflineEpisode.digest == episode.digest
            ))
            if existing is not None:
                response = episode_out(existing)
                record_receipt(db, principal, route, key, digest, response)
                return response, False
            count = db.scalar(
                select(func.count()).select_from(OfflineEpisode).where(OfflineEpisode.evaluation_id == evaluation.id)
            )
            if count >= MAX_EPISODES:
                raise HTTPException(409, f"offline episode limit reached ({MAX_EPISODES} per evaluation)")
            used = db.scalar(
                select(func.coalesce(func.sum(OfflineEpisode.stored_bytes), 0))
                .where(OfflineEpisode.owner_user_id == principal.user.id)
            )
            data = episode.data
            manifest = {
                "skill": data.skill, "planner_ms": data.planner_ms, "action_labels": data.action_labels,
                "action_dim": episode.action_dim,
            }
            metadata = [(frame.index, {"hierarchy": frame.hierarchy.model_dump()})
                        for frame in data.frames if frame.hierarchy is not None]
            if metadata:
                manifest["has_hierarchy"] = True
            if isinstance(data.metrics.get("measurement_source"), str):
                manifest["measurement_source"] = data.metrics["measurement_source"]
            steps = [
                (index, frame.action, frame.reward, frame.success, frame.policy_ms)
                for index, frame in enumerate(data.frames) if index
            ]
            with recordings.store() as root:
                recordings.sweep(db, root)
                stored = True
                size = recordings.write(
                    root, episode_id, (evaluation.id, episode.digest, episode.steps, manifest), steps,
                    episode.images, int(used), episode.estimate,
                    metadata=metadata,
                )
            now = utcnow()
            row = OfflineEpisode(
                id=episode_id, evaluation_id=evaluation.id, owner_user_id=principal.user.id, seed=data.seed,
                outcome=data.outcome, steps=episode.steps, images=len(episode.images), action_dim=episode.action_dim,
                metrics=data.metrics, wall_seconds=data.wall_seconds, sim_seconds=data.sim_seconds,
                digest=episode.digest, stored_bytes=size, created_at=now,
            )
            db.add(row)
            db.flush()
            evaluation.summary = summarize(_episodes(db, evaluation.id))
            evaluation.updated_at = now
            response = episode_out(row)
            record_receipt(db, principal, route, key, digest, response)
            audit(
                db, principal, "offline_evaluation.episode", evaluation.id, episode_id=episode_id, seed=data.seed,
                outcome=data.outcome, steps=episode.steps, images=len(episode.images), stored_bytes=size,
            )
            _prune_receipts(db, principal, now)
    except BaseException:
        if stored:
            recordings.discard(episode_id)
        raise
    recordings.publish(episode_id)
    return response, True


def delete_evaluation(db: Session, principal: Principal, route: str, key: str | None, evaluation_id: str) -> None:
    key = require_idempotency_key(key)
    admit_write(db, principal)
    digest = canonical_digest({"delete": evaluation_id})
    with _removal() as removed:
        with write_txn(db):
            assert_live_principal(db, principal, "operator")
            if previous_receipt(db, principal, route, key, digest) is not None:
                return
            evaluation = owned_evaluation(db, principal, evaluation_id)
            episodes = _episodes(db, evaluation.id)
            _withdraw(db, removed, [episode.id for episode in episodes])
            for episode in episodes:
                db.delete(episode)
            db.flush()
            record_receipt(db, principal, route, key, digest, {"id": evaluation.id, "deleted": True})
            audit(
                db, principal, "offline_evaluation.delete", evaluation.id, episodes=len(episodes),
                stored_bytes=sum(episode.stored_bytes for episode in episodes),
            )
            db.delete(evaluation)
            _prune_receipts(db, principal, utcnow())


def delete_episode(
    db: Session, principal: Principal, route: str, key: str | None, evaluation_id: str, episode_id: str
) -> None:
    key = require_idempotency_key(key)
    admit_write(db, principal)
    digest = canonical_digest({"delete": episode_id})
    with _removal() as removed:
        with write_txn(db):
            assert_live_principal(db, principal, "operator")
            if previous_receipt(db, principal, route, key, digest) is not None:
                return
            episode = owned_episode(db, principal, evaluation_id, episode_id)
            evaluation = owned_evaluation(db, principal, evaluation_id)
            _withdraw(db, removed, [episode.id])
            db.delete(episode)
            db.flush()
            now = utcnow()
            evaluation.summary = summarize(_episodes(db, evaluation.id))
            evaluation.updated_at = now
            record_receipt(db, principal, route, key, digest, {"id": episode.id, "deleted": True})
            audit(
                db, principal, "offline_evaluation.episode_delete", evaluation.id, episode_id=episode.id,
                stored_bytes=episode.stored_bytes,
            )
            _prune_receipts(db, principal, now)


@contextmanager
def _removal() -> Iterator[list[str]]:
    """Recordings withdrawn inside the block are freed once it commits, and put back if it fails."""
    removed: list[str] = []
    try:
        yield removed
    except BaseException:
        if removed:
            recordings.restore(removed)
        raise
    if removed:
        recordings.remove(removed)


def _withdraw(db: Session, removed: list[str], episode_ids: list[str]) -> None:
    with recordings.store() as root:
        recordings.sweep(db, root)
        recordings.withdraw(root, episode_ids)
        removed.extend(episode_ids)


def _prune_receipts(db: Session, principal: Principal, now: datetime) -> None:
    """Keeps the account's offline evaluation receipts younger than RECEIPT_TTL, and at most MAX_RECEIPTS."""
    db.flush()  # the receipt this write recorded counts among the newest
    owned = (
        MutationReceipt.owner_user_id == principal.user.id,
        MutationReceipt.route.startswith(ROUTE_PREFIX, autoescape=True),
    )
    newest = (
        select(MutationReceipt.id)
        .where(*owned)
        .order_by(MutationReceipt.created_at.desc(), MutationReceipt.id.desc())
        .limit(MAX_RECEIPTS)
    )
    db.execute(
        delete(MutationReceipt)
        .where(*owned, or_(MutationReceipt.created_at < now - RECEIPT_TTL, MutationReceipt.id.not_in(newest)))
        .execution_options(synchronize_session=False)
    )
