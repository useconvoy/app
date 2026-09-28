"""One plan per mission; cancellation can fence an in-flight text completion."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException


@dataclass
class Session:
    grant: dict
    next_sequence: int = 0
    poisoned: bool = False
    gateway_identity: dict | None = None
    planner_identity: dict | None = None


class Sessions:
    def __init__(self):
        self.lock = threading.Lock()
        self.owner: Session | None = None
        self.closed: dict[str, dict] = {}

    def _prune(self):
        now = time.time()
        self.closed = {key: value for key, value in self.closed.items() if value["expires_at"] > now}
        if self.owner and self.owner.grant["expires_at"] <= now:
            self.owner = None

    def start(self, grant: dict) -> tuple[Session, bool]:
        with self.lock:
            self._prune()
            if grant["mission_id"] in self.closed:
                raise HTTPException(409, "planner mission is closed")
            if self.owner:
                if self.owner.grant != grant or self.owner.poisoned:
                    raise HTTPException(409, "planner owns another or failed mission")
                return self.owner, False
            if len(self.closed) >= 1024:
                raise HTTPException(429, "planner retention full until original grants expire")
            self.owner = Session(grant.copy())
            return self.owner, True

    def check(self, session: Session):
        with self.lock:
            if self.owner is not session or session.poisoned or time.time() >= session.grant["expires_at"]:
                raise HTTPException(409, "planner mission ended, expired or failed")

    def reserve(self, grant: dict) -> Session:
        with self.lock:
            session = self.owner
            if (session is None or session.grant != grant or session.poisoned or session.next_sequence != 0 or
                    session.gateway_identity is None or session.planner_identity is None):
                raise HTTPException(409, "start exact planner mission once; plans cannot be regenerated")
            session.next_sequence = 1
            return session

    def poison(self, session: Session):
        with self.lock:
            session.poisoned = True

    def close(self, grant: dict):
        with self.lock:
            self._prune()
            is_owner = self.owner is not None and self.owner.grant == grant
            occupied = len(self.closed) + int(self.owner is not None and not is_owner)
            if grant["mission_id"] not in self.closed and occupied >= 1024:
                raise HTTPException(429, "planner retention full")
            previous = self.closed.get(grant["mission_id"])
            if previous is not None and previous != grant:
                raise HTTPException(409, "original planner authorization changed")
            self.closed[grant["mission_id"]] = grant.copy()
            if is_owner:
                self.owner = None
