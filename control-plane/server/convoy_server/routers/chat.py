"""Text-only operator chat; production inference through the credentialled outbound agent."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import (
    Principal,
    StaleBinding,
    assert_admitted_binding,
    assert_live_principal,
    current_device,
    require_role,
)
from ..config import get_settings
from ..db import get_db, write_txn
from ..ids import aware, utcnow
from ..models import Device, Installation, Release
from ..services import chat
from ..services.operations import dispatch_paused

router = APIRouter(tags=["chat"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8192)


class Create(StrictModel):
    request_id: UUID
    expected_release_id: str = Field(min_length=1, max_length=64)
    messages: list[Message] = Field(min_length=1, max_length=16)
    max_tokens: int = Field(default=128, ge=1, le=128, strict=True)

    @model_validator(mode="after")
    def bounded_text(self):
        if sum(len(m.content.encode("utf-8")) for m in self.messages) > 8192:
            raise ValueError("conversation must not exceed 8192 UTF-8 bytes")
        if self.messages[-1].role != "user":
            raise ValueError("last message must be from the user")
        return self


class Claim(StrictModel):
    active_release_id: str = Field(min_length=1, max_length=64)


class Usage(StrictModel):
    prompt_tokens: int = Field(ge=0, le=1000000, strict=True)
    completion_tokens: int = Field(ge=0, le=128, strict=True)
    total_tokens: int = Field(ge=0, le=1000128, strict=True)

    @model_validator(mode="after")
    def consistent_total(self):
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("token usage total is inconsistent")
        return self


class Metrics(StrictModel):
    latency_ms: float | None = Field(default=None, ge=0, le=300000, allow_inf_nan=False, strict=True)
    ttft_ms: float | None = Field(default=None, ge=0, le=300000, allow_inf_nan=False, strict=True)
    queue_ms: float | None = Field(default=None, ge=0, le=300000, allow_inf_nan=False, strict=True)


class Result(StrictModel):
    claim_token: str = Field(min_length=16, max_length=128)
    release_id: str = Field(min_length=1, max_length=64)
    status: Literal["succeeded", "failed"]
    content: str | None = Field(default=None, max_length=16384)
    finish_reason: Literal["stop", "length"] | None = None
    usage: Usage | None = None
    metrics: Metrics | None = None
    trace_id: str | None = Field(default=None, max_length=64, pattern=r"^tr_[a-f0-9]+$")
    error_code: (
        Literal[
            "device_changed",
            "device_unavailable",
            "context_length_exceeded",
            "timeout",
            "runtime_error",
            "unavailable",
            "invalid_request_error",
            "internal_error",
            "output_too_large",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def valid_result(self):
        if self.content is not None and len(self.content.encode("utf-8")) > 32768:
            raise ValueError("response exceeds 32768 UTF-8 bytes")
        if self.status == "succeeded" and (self.content is None or self.error_code is not None):
            raise ValueError("success requires content and no error")
        if self.status == "failed" and (self.error_code is None or self.content is not None):
            raise ValueError("failure requires an error and no content")
        return self


def _admitted(db, dev):
    try:
        return assert_admitted_binding(db, dev)
    except StaleBinding as exc:
        raise HTTPException(409, "device credentials or binding changed") from exc


def _scope(db):
    installation = db.get(Installation, 1)
    return hashlib.sha256(
        json.dumps(
            {
                "created": str(installation.created_at) if installation else None,
                "restored": installation.restored_from if installation else {},
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()


def _device(db, device_id):
    dev = db.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "device not found")
    return dev


def _details(db, dev):
    online = bool(
        dev.live_at and (utcnow() - aware(dev.live_at)).total_seconds() <= get_settings().offline_after_s
    )
    observed = dev.observed or {}
    supported = (observed.get("chat") or {}).get("protocol_version") == 1 and (
        observed.get("chat") or {}
    ).get("supported") is True
    release = db.get(Release, dev.observed_active_release_id) if dev.observed_active_release_id else None
    config = (release.spec or {}).get("config", {}) if release else {}
    reason = None
    if dev.simulated:
        reason = "Chat requires a physical Jetson device."
    elif dev.retired_at or dev.credential_revoked_at:
        reason = "Device credentials are inactive."
    elif not supported:
        reason = "Device agent needs the chat relay update."
    elif not online:
        reason = "Device is offline or has no recent live report."
    elif dispatch_paused(db):
        reason = "Control plane dispatch is paused or restoring."
    elif dev.active_operation_id:
        reason = "Device has a deployment or evaluation in progress."
    elif (
        not release
        or release.simulated
        or dev.observed_health != "ok"
        or (observed.get("gateway") or {}).get("mode") != "production"
    ):
        reason = "Device needs a healthy active model in production mode."
    cap = config.get("n_predict")
    context = config.get("ctx_size")
    return {
        "id": dev.id,
        "name": dev.name,
        "simulated": dev.simulated,
        "online": online,
        "eligible": reason is None,
        "reason": reason,
        "release_id": dev.observed_active_release_id,
        "release_name": release.name if release else None,
        "max_tokens": min(128, cap) if isinstance(cap, int) and cap > 0 else 128,
        "context_window": context if isinstance(context, int) and context > 0 else None,
        "chat_supported": supported,
    }


def _ready(db, dev, release_id):
    details = _details(db, dev)
    if not details["eligible"]:
        raise HTTPException(409, details["reason"])
    if dev.observed_active_release_id != release_id:
        raise HTTPException(409, "active release changed; refresh device before sending")


@router.get("/api/v1/chat/devices")
def devices(
    response: Response, p: Principal = Depends(require_role("viewer")), db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store"
    return {"devices": [_details(db, d) for d in db.scalars(select(Device).order_by(Device.name))]}


@router.post("/api/v1/devices/{device_id}/chat", status_code=202)
def create(
    device_id: str,
    body: Create,
    response: Response,
    p: Principal = Depends(require_role("operator")),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    with write_txn(db):
        assert_live_principal(db, p, "operator")
        dev = _device(db, device_id)
        db.refresh(dev)
        # Gate even retries: a repeated submit is never an authorization bypass.
        _ready(db, dev, body.expected_release_id)
        with chat.transaction() as c:
            return chat.create(
                c,
                request_id=str(body.request_id),
                device=dev,
                user_id=p.user.id,
                scope=_scope(db),
                body=body.model_dump(mode="json"),
            )


@router.get("/api/v1/devices/{device_id}/chat/{request_id}")
def get(
    device_id: str,
    request_id: UUID,
    response: Response,
    p: Principal = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    # Serialize the scope check with restore/rebind just as for writes. This does not change
    # main database state; it prevents a stale snapshot exposing a pre-restore reply.
    with write_txn(db):
        dev = _device(db, device_id)
        db.refresh(dev)
        with chat.transaction() as c:
            return chat.fetch(c, str(request_id), dev, _scope(db), p.user.id, p.user.role == "admin")


@router.post("/api/agent/v1/chat/claim")
def claim(
    body: Claim, response: Response, dev: Device = Depends(current_device), db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store"
    with write_txn(db):
        _admitted(db, dev)
        _ready(db, dev, body.active_release_id)
        with chat.transaction() as c:
            return {"request": chat.claim(c, dev, _scope(db), body.active_release_id)}


@router.post("/api/agent/v1/chat/{request_id}/result")
def result(
    request_id: UUID,
    body: Result,
    response: Response,
    dev: Device = Depends(current_device),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    with write_txn(db):
        _admitted(db, dev)
        if dispatch_paused(db):
            raise HTTPException(409, "control plane dispatch is paused or restoring")
        with chat.transaction() as c:
            return chat.finish(c, dev, _scope(db), str(request_id), body.model_dump())
