"""Email emulator — the agent's mailbox over the WorldStore message log.
Outbound send is effectful (world.send_message → message_sent, which is what
wakes the counterparty engine); reads are pure.

Port of src/sandbox/emulators/email.ts — tool names and arg/result shapes are
load-bearing (the golden executor reads them).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict

from ..api import ToolCallCtx, ToolEmulator, WorldMessage, WorldStore
from .util import AGENT_ADDRESS, thread_id_for_subject


class _AttachmentRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    fileId: str


class _SendArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    to: Union[str, List[str]]
    subject: str
    body: str
    # World file ids, or {name?, fileId} objects (both executor shapes seen in the wild).
    attachments: Optional[List[Union[str, _AttachmentRef]]] = None


class _ListInboxArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    toContains: Optional[str] = None
    fromContains: Optional[str] = None
    subjectRegex: Optional[str] = None
    sinceTs: Optional[str] = None


class _ReadArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    messageId: str


class _DownloadAttachmentArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    messageId: str
    name: str


def _require_message(world: WorldStore, message_id: str) -> WorldMessage:
    for m in world.list_messages():
        if m.id == message_id:
            return m
    raise ValueError("email: no such message: %s" % message_id)


def _message_view(m: WorldMessage) -> Dict[str, Any]:
    return {
        "messageId": m.id,
        "threadId": m.threadId,
        "from": m.from_,
        "to": list(m.to),
        "subject": m.subject,
        "body": m.body,
        "ts": m.ts,
        "attachmentNames": [x.name for x in m.attachments],
    }


def _send(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    a = _SendArgs.model_validate(args)
    to = a.to if isinstance(a.to, list) else [a.to]
    if not to:
        raise ValueError("email.send: 'to' must not be empty")
    attachments = []
    for att in a.attachments or []:
        file_id = att if isinstance(att, str) else att.fileId
        file = world.get_file(file_id)
        if not file:
            raise ValueError("email.send: unknown attachment file id: %s" % file_id)
        attachments.append({"name": file.name, "fileId": file.id})
    message = world.send_message(
        threadId=thread_id_for_subject(a.subject),
        from_=AGENT_ADDRESS,
        to=to,
        subject=a.subject,
        body=a.body,
        attachments=attachments,
        direction="outbound",
    )
    return {"messageId": message.id, "threadId": message.threadId, "ts": message.ts}


def _list_inbox(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    a = _ListInboxArgs.model_validate(args or {})
    inbound = world.list_messages(direction="inbound", to_contains=a.toContains)
    if a.sinceTs is not None:
        inbound = [m for m in inbound if m.ts >= a.sinceTs]
    if a.fromContains is not None:
        inbound = [m for m in inbound if a.fromContains in m.from_]
    if a.subjectRegex is not None:
        pattern = re.compile(a.subjectRegex, re.IGNORECASE)
        inbound = [m for m in inbound if pattern.search(m.subject)]
    return {"messages": [_message_view(m) for m in inbound]}


def _read(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    a = _ReadArgs.model_validate(args)
    return _message_view(_require_message(world, a.messageId))


def _download_attachment(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    a = _DownloadAttachmentArgs.model_validate(args)
    m = _require_message(world, a.messageId)
    att = next((x for x in m.attachments if x.name == a.name), None)
    if att is None:
        raise ValueError(
            'email.download_attachment: message %s has no attachment named "%s"'
            % (a.messageId, a.name)
        )
    file = world.get_file(att.fileId)
    if not file:
        raise ValueError("email.download_attachment: dangling file id %s" % att.fileId)
    return {"name": file.name, "mime": file.mime, "hash": file.hash, "content": file.content}


email_emulator: List[ToolEmulator] = [
    ToolEmulator(tool="email.send", effectful=True, handler=_send),
    ToolEmulator(tool="email.list_inbox", effectful=False, handler=_list_inbox),
    ToolEmulator(tool="email.read", effectful=False, handler=_read),
    ToolEmulator(tool="email.download_attachment", effectful=False, handler=_download_attachment),
]
