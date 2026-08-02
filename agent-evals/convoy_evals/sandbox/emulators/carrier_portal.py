"""Carrier-portal emulator — request/poll/download over `portal_request`
records. The counterparty engine plays the carrier: it watches for pending
requests and fulfills them (status → 'fulfilled' + fields.documentFileId).

Port of src/sandbox/emulators/carrierPortal.ts. portal.download additionally
returns the world fileId so executors can re-attach the document to email.
"""

from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, ConfigDict

from ..api import ToolCallCtx, ToolEmulator, WorldRecord, WorldStore


class _RequestLossRunsArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    carrier: str
    policyId: str


class _CheckStatusArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    requestId: str


class _DownloadArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    requestId: str


def _require_request(world: WorldStore, request_id: str) -> WorldRecord:
    record = world.get_record("portal_request", request_id)
    if not record:
        raise ValueError("portal: no such request: %s" % request_id)
    return record


def _request_loss_runs(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    a = _RequestLossRunsArgs.model_validate(args)
    # Deterministic id: next ordinal over existing portal_request records.
    request_id = "req_%d" % (len(world.list_records("portal_request")) + 1)
    record = world.upsert_record(
        "portal_request",
        request_id,
        {"status": "pending", "carrier": a.carrier, "policyId": a.policyId},
    )
    return {"requestId": request_id, "status": record.fields["status"]}


def _check_status(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    a = _CheckStatusArgs.model_validate(args)
    record = _require_request(world, a.requestId)
    return {
        "requestId": a.requestId,
        "status": record.fields.get("status"),
        "carrier": record.fields.get("carrier"),
        "policyId": record.fields.get("policyId"),
    }


def _download(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    a = _DownloadArgs.model_validate(args)
    record = _require_request(world, a.requestId)
    file_id = record.fields.get("documentFileId")
    if record.fields.get("status") != "fulfilled" or not isinstance(file_id, str):
        raise ValueError(
            "portal.download: request %s not fulfilled (status: %s)"
            % (a.requestId, record.fields.get("status"))
        )
    file = world.get_file(file_id)
    if not file:
        raise ValueError("portal.download: dangling document file id %s" % file_id)
    return {
        "name": file.name,
        "mime": file.mime,
        "hash": file.hash,
        "content": file.content,
        "fileId": file.id,
    }


carrier_portal_emulator: List[ToolEmulator] = [
    ToolEmulator(tool="portal.request_loss_runs", effectful=True, handler=_request_loss_runs),
    ToolEmulator(tool="portal.check_status", effectful=False, handler=_check_status),
    ToolEmulator(tool="portal.download", effectful=False, handler=_download),
]
