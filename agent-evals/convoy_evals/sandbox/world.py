"""WorldStore — the in-memory simulated world one sandbox instance owns.
Seeded from a fixture pack, mutated only through the tool gateway and the
counterparty engine, exported as a content-addressed bundle at teardown.

Port of src/sandbox/world.ts. Beyond the api.py interface the concrete store
carries `clock_ref` (emulators that perceive time — calendar, ams — need a
clock, but the ToolEmulator handler signature only passes the world); the
counterparty engine's `portal_transition` goes through `emit_event`, which the
Python WorldStore ABC already declares.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Literal

from ..runtime.ports import ClockPort
from ..schema.match import resolve_path
from .api import (
    WorldAttachment,
    WorldBundle,
    WorldEvent,
    WorldFile,
    WorldMessage,
    WorldRecord,
    WorldStore,
)

# ---------------------------------------------------------------------------
# Fixture-pack manifest (pack.json)
# ---------------------------------------------------------------------------


class _PackRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    collection: str
    id: str
    fields: Dict[str, Any]


class _PackFile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # Pack-relative logical path, e.g. "attachments/loss-runs.txt".
    path: str
    name: str
    mime: str
    # Where the content lives inside the pack dir; defaults to `path`.
    contentFile: Optional[str] = None


class _PackThreadMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    from_: str = Field(alias="from")
    to: List[str]
    subject: str
    body: str
    direction: Literal["outbound", "inbound"] = "inbound"
    # ISO or a {{t0±Nd}} template; defaults to t0.
    ts: Optional[str] = None
    # Pack file paths.
    attachments: List[str] = Field(default_factory=list)


class _PackThread(BaseModel):
    model_config = ConfigDict(extra="ignore")
    threadId: str
    messages: List[_PackThreadMessage]


class _PackManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    records: List[_PackRecord] = Field(default_factory=list)
    files: List[_PackFile] = Field(default_factory=list)
    threads: Optional[List[_PackThread]] = None


# ---------------------------------------------------------------------------
# Date templating: {{t0}}, {{t0+3d}}, {{t0-14d}} → date-only ISO (YYYY-MM-DD)
# ---------------------------------------------------------------------------

_T0_TEMPLATE = re.compile(r"\{\{t0(?:([+-])(\d+)d)?\}\}")


def _iso_date(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d")


def iso_ts(d: datetime) -> str:
    """Full ISO timestamp, UTC, Z-suffixed (matches runtime/log.py)."""
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def materialize_string(s: str, t0: datetime) -> str:
    def sub(m: "re.Match[str]") -> str:
        sign, n = m.group(1), m.group(2)
        if not sign or not n:
            return _iso_date(t0)
        days = int(n) * (-1 if sign == "-" else 1)
        return _iso_date(t0 + timedelta(days=days))

    return _T0_TEMPLATE.sub(sub, s)


def materialize_deep(v: Any, t0: datetime) -> Any:
    """Deep-walk any JSON value, materializing date templates in every string."""
    if isinstance(v, str):
        return materialize_string(v, t0)
    if isinstance(v, list):
        return [materialize_deep(x, t0) for x in v]
    if isinstance(v, dict):
        return {k: materialize_deep(val, t0) for k, val in v.items()}
    return v


# ---------------------------------------------------------------------------
# Canonical serialization + hashing
# ---------------------------------------------------------------------------


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def stable_stringify(v: Any) -> str:
    """Deterministic JSON: object keys sorted at every level (TS stableStringify)."""
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def message_to_json(m: WorldMessage) -> Dict[str, Any]:
    """Serialize with the wire key 'from' (not from_) so query paths match TS."""
    d = asdict(m)
    d["from"] = d.pop("from_")
    return d


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class _SimWorldStore(WorldStore):
    def __init__(self, clock: ClockPort, seed: int) -> None:
        self.clock_ref: ClockPort = clock
        self._seed = seed  # reserved for future stochastic seeding choices
        self._messages: List[WorldMessage] = []
        self._records: Dict[str, WorldRecord] = {}  # key: f"{collection} {id}"
        self._files: Dict[str, WorldFile] = {}
        self._pack_path_to_file_id: Dict[str, str] = {}
        self._handlers: List[Callable[[WorldEvent], None]] = []
        self._msg_counter = 0
        self._file_counter = 0

    # -- events ------------------------------------------------------------

    def on_event(self, handler: Callable[[WorldEvent], None]) -> None:
        self._handlers.append(handler)

    def emit_event(self, event: WorldEvent) -> None:
        for h in list(self._handlers):
            h(event)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _rec_key(collection: str, id: str) -> str:
        return "%s %s" % (collection, id)

    @staticmethod
    def _normalize_attachments(attachments: Any) -> List[WorldAttachment]:
        out: List[WorldAttachment] = []
        for a in attachments or []:
            if isinstance(a, WorldAttachment):
                out.append(a)
            elif isinstance(a, dict):
                out.append(WorldAttachment(name=a["name"], fileId=a["fileId"]))
            else:
                raise ValueError("bad attachment: %r" % (a,))
        return out

    def _add_message(
        self,
        kw: Dict[str, Any],
        direction: str,
        ts: Optional[str] = None,
        silent: bool = False,
    ) -> WorldMessage:
        kw = dict(kw)
        from_ = kw.pop("from_", kw.pop("from", None))
        if from_ is None:
            raise ValueError("message requires from_")
        kw.pop("direction", None)
        self._msg_counter += 1
        message = WorldMessage(
            id="msg_%d" % self._msg_counter,
            threadId=kw["threadId"],
            from_=from_,
            to=list(kw["to"]),
            subject=kw["subject"],
            body=kw["body"],
            attachments=self._normalize_attachments(kw.get("attachments")),
            ts=ts if ts is not None else iso_ts(self.clock_ref.now()),
            direction=direction,  # type: ignore[arg-type]
        )
        self._messages.append(message)
        if not silent:
            kind = "message_sent" if direction == "outbound" else "message_delivered"
            self.emit_event({"kind": kind, "message": message})
        return message

    def _put_file_internal(self, name: str, mime: str, content: str) -> WorldFile:
        self._file_counter += 1
        file = WorldFile(
            id="file_%d" % self._file_counter,
            name=name,
            mime=mime,
            hash=sha256_hex(content),
            content=content,
        )
        self._files[file.id] = file
        return file

    # -- seeding -----------------------------------------------------------

    def seed_from_pack(self, pack_dir: str, t0: datetime, seed: int) -> None:
        del seed
        raw = (Path(pack_dir) / "pack.json").read_text(encoding="utf-8")
        manifest = _PackManifest.model_validate(json.loads(raw))

        # Files first so thread attachments can resolve by pack path.
        for pf in manifest.files:
            content_path = Path(pack_dir) / (pf.contentFile or pf.path)
            content = materialize_string(content_path.read_text(encoding="utf-8"), t0)
            file = self._put_file_internal(
                name=materialize_string(pf.name, t0), mime=pf.mime, content=content
            )
            self._pack_path_to_file_id[pf.path] = file.id

        for pr in manifest.records:
            fields = materialize_deep(pr.fields, t0)
            record = WorldRecord(
                collection=pr.collection, id=pr.id, fields=fields, updatedAt=iso_ts(t0)
            )
            # Silent: seeding is not a mutation.
            self._records[self._rec_key(pr.collection, pr.id)] = record

        for thread in manifest.threads or []:
            for tm in thread.messages:
                attachments: List[WorldAttachment] = []
                for p in tm.attachments:
                    file_id = self._pack_path_to_file_id.get(p)
                    if not file_id:
                        raise ValueError("pack thread attachment not in pack files: %s" % p)
                    file = self._files.get(file_id)
                    attachments.append(
                        WorldAttachment(name=file.name if file else p, fileId=file_id)
                    )
                self._add_message(
                    {
                        "threadId": thread.threadId,
                        "from_": materialize_string(tm.from_, t0),
                        "to": [materialize_string(a, t0) for a in tm.to],
                        "subject": materialize_string(tm.subject, t0),
                        "body": materialize_string(tm.body, t0),
                        "attachments": attachments,
                    },
                    tm.direction,
                    ts=materialize_string(tm.ts, t0) if tm.ts else iso_ts(t0),
                    silent=True,
                )

    # -- messages ----------------------------------------------------------

    def send_message(self, **kw: Any) -> WorldMessage:
        return self._add_message(kw, "outbound")

    def deliver_message(self, **kw: Any) -> WorldMessage:
        return self._add_message(kw, "inbound")

    def list_messages(
        self,
        direction: Optional[str] = None,
        to_contains: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> List[WorldMessage]:
        out: List[WorldMessage] = []
        for m in self._messages:
            if direction and m.direction != direction:
                continue
            if thread_id and m.threadId != thread_id:
                continue
            if to_contains and not any(to_contains in a for a in m.to):
                continue
            out.append(m)
        return out

    # -- records -----------------------------------------------------------

    def upsert_record(self, collection: str, id: str, fields: Dict[str, Any]) -> WorldRecord:
        existing = self._records.get(self._rec_key(collection, id))
        merged: Dict[str, Any] = dict(existing.fields) if existing else {}
        merged.update(fields)
        record = WorldRecord(
            collection=collection,
            id=id,
            fields=merged,
            updatedAt=iso_ts(self.clock_ref.now()),
        )
        self._records[self._rec_key(collection, id)] = record
        self.emit_event({"kind": "record_changed", "record": record})
        return record

    def get_record(self, collection: str, id: str) -> Optional[WorldRecord]:
        return self._records.get(self._rec_key(collection, id))

    def list_records(self, collection: str) -> List[WorldRecord]:
        return [r for r in self._records.values() if r.collection == collection]

    # -- files -------------------------------------------------------------

    def put_file(self, name: str, mime: str, content: str) -> WorldFile:
        return self._put_file_internal(name, mime, content)

    def get_file(self, id: str) -> Optional[WorldFile]:
        return self._files.get(id)

    def file_by_pack_path(self, path: str) -> Optional[WorldFile]:
        file_id = self._pack_path_to_file_id.get(path)
        return self._files.get(file_id) if file_id else None

    # -- query + export ----------------------------------------------------

    def query(self, q: str) -> List[Any]:
        records_root: Dict[str, Dict[str, Any]] = {}
        for r in self._records.values():
            records_root.setdefault(r.collection, {})[r.id] = r.fields
        files_root = {f.id: asdict(f) for f in self._files.values()}
        all_msgs = [message_to_json(m) for m in self._messages]
        root = {
            "records": records_root,
            "messages": {
                "sent": [d for d in all_msgs if d["direction"] == "outbound"],
                "inbound": [d for d in all_msgs if d["direction"] == "inbound"],
                "all": all_msgs,
            },
            "files": files_root,
        }
        return resolve_path(root, q)

    def export_bundle(self) -> WorldBundle:
        sorted_records = sorted(self._records.values(), key=lambda r: (r.collection, r.id))
        sorted_files = sorted(self._files.values(), key=lambda f: f.id)
        body = {
            "messages": [message_to_json(m) for m in self._messages],
            "records": [asdict(r) for r in sorted_records],
            "files": [asdict(f) for f in sorted_files],
        }
        return WorldBundle(
            hash=sha256_hex(stable_stringify(body)),
            messages=list(self._messages),
            records=sorted_records,
            files=sorted_files,
        )


def create_world_store(clock: ClockPort, seed: int) -> _SimWorldStore:
    return _SimWorldStore(clock, seed)


# Alias mirroring the TS `SimWorldStore` export.
SimWorldStore = _SimWorldStore
