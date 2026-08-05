"""SqlEventLog — the Postgres-backed EventLog.

Honors the same contract as agent-evals' in-memory EventLog (append stamps
eventId/seq/ts/wallTs and validates through convoy_core; for_mission returns
(ts, seq)-ordered models), so graders and projections consume either backend
unchanged. Concurrency: seq is assigned as max+1 inside the transaction and
the (mission_id, seq) unique constraint turns races into retries.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from convoy_core.mission_events import parse_event
from convoy_core.ports import ClockPort, system_clock
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .tables import Event

_MAX_SEQ_RETRIES = 5


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class SqlEventLog:
    def __init__(self, session_factory, clock: ClockPort = system_clock, workspace_id: Optional[str] = None) -> None:
        self._sf = session_factory
        self._clock = clock
        self._workspace_id = workspace_id
        self._listeners: List[Callable[[Any], None]] = []

    def append(self, input: Dict[str, Any], workspace_id: Optional[str] = None) -> Any:
        """Validate + stamp + insert one event. `input` carries the payload
        without eventId/seq/wallTs (ts optional override)."""
        mission_id = input["missionId"]
        ws = workspace_id or self._workspace_id
        last_err: Optional[Exception] = None
        for _ in range(_MAX_SEQ_RETRIES):
            with self._sf() as session:
                seq = session.execute(
                    select(func.coalesce(func.max(Event.seq) + 1, 0)).where(Event.mission_id == mission_id)
                ).scalar_one()
                raw = dict(input)
                raw["eventId"] = str(uuid.uuid4())
                raw["seq"] = seq
                raw.setdefault("ts", _iso(self._clock.now()))
                raw["wallTs"] = _iso(datetime.now(timezone.utc))
                event = parse_event(raw)
                payload = json.loads(event.model_dump_json(exclude_none=True))
                session.add(
                    Event(
                        event_id=event.eventId,
                        workspace_id=ws,
                        mission_id=event.missionId,
                        seq=event.seq,
                        type=payload["type"],
                        ts=event.ts,
                        wall_ts=event.wallTs,
                        item_ref=event.itemRef,
                        step_id=event.stepId,
                        attempt_id=event.attemptId,
                        payload=payload,
                    )
                )
                try:
                    session.commit()
                except IntegrityError as err:  # (mission_id, seq) race — retry
                    session.rollback()
                    last_err = err
                    continue
                for fn in self._listeners:
                    fn(event)
                return event
        raise RuntimeError("could not assign event seq for mission %s" % mission_id) from last_err

    def for_mission(self, mission_id: str) -> List[Any]:
        with self._sf() as session:
            rows = session.execute(
                select(Event.payload).where(Event.mission_id == mission_id).order_by(Event.ts, Event.seq)
            ).scalars().all()
        return [parse_event(p) for p in rows]

    def on_append(self, fn: Callable[[Any], None]) -> None:
        self._listeners.append(fn)

    def open_gates(self, workspace_id: Optional[str] = None) -> List[Any]:
        """Projection: gate_raised events with no matching gate_resolved.
        Returns the raised events (payloads), console-ready."""
        with self._sf() as session:
            q = select(Event.payload).where(Event.type.in_(["gate_raised", "gate_resolved"]))
            if workspace_id:
                q = q.where(Event.workspace_id == workspace_id)
            rows = session.execute(q.order_by(Event.ts, Event.seq)).scalars().all()
        open_by_id: Dict[str, Any] = {}
        for p in rows:
            if p["type"] == "gate_raised":
                open_by_id[p["gateId"]] = p
            else:
                open_by_id.pop(p["gateId"], None)
        return list(open_by_id.values())
