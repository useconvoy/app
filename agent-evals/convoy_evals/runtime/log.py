"""EventLog — in-memory append-only event log with JSONL persistence.

Port of src/runtime/log.ts. Interface shaped like the future Postgres events
table (append + ordered reads); graders and scoring consume ONLY this plus
exported world/artifact bundles. JSONL written by the TS harness loads here
unchanged (same camelCase field names).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .events import ConvoyEvent, MissionId, parse_event
from .ports import ClockPort


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class EventLog:
    def __init__(self, clock: ClockPort) -> None:
        self._clock = clock
        self._events: List[Any] = []
        self._seq_by_mission: Dict[MissionId, int] = {}
        self._listeners: List[Callable[[Any], None]] = []

    def append(self, input: Dict[str, Any]) -> Any:
        """Validate + stamp ids/seq/timestamps and append. `input` carries the
        event payload without eventId/seq/wallTs (ts optional override)."""
        mission_id = input["missionId"]
        seq = self._seq_by_mission.get(mission_id, 0)
        raw = dict(input)
        raw["eventId"] = str(uuid.uuid4())
        raw["seq"] = seq
        raw.setdefault("ts", _iso(self._clock.now()))
        raw["wallTs"] = _iso(datetime.now(timezone.utc))
        event = parse_event(raw)
        self._seq_by_mission[mission_id] = seq + 1
        self._events.append(event)
        for fn in self._listeners:
            fn(event)
        return event

    def for_mission(self, mission_id: MissionId) -> List[Any]:
        """Ordered by (ts, seq) — zero-duration sim steps tie on ts, resolve by append order."""
        rows = [e for e in self._events if e.missionId == mission_id]
        return sorted(rows, key=lambda e: (e.ts, e.seq))

    def all(self) -> List[Any]:
        return list(self._events)

    def on_append(self, fn: Callable[[Any], None]) -> None:
        self._listeners.append(fn)

    def to_jsonl(self) -> str:
        lines = [json.dumps(e.model_dump(exclude_none=True), separators=(",", ":")) for e in self._events]
        return "\n".join(lines) + ("\n" if lines else "")

    def write_jsonl(self, path: Path) -> None:
        Path(path).write_text(self.to_jsonl())

    @classmethod
    def from_jsonl(cls, path: Path, clock: ClockPort) -> "EventLog":
        log = cls(clock)
        for line in Path(path).read_text().splitlines():
            if not line.strip():
                continue
            event = parse_event(json.loads(line))
            log._events.append(event)
            cur = log._seq_by_mission.get(event.missionId, 0)
            log._seq_by_mission[event.missionId] = max(cur, event.seq + 1)
        return log


def load_events_jsonl(path: Path) -> List[Any]:
    """Parse a recorded events.jsonl (TS- or Python-written) into event models."""
    out: List[Any] = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            out.append(parse_event(json.loads(line)))
    return out
