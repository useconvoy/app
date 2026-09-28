"""One stateful policy owner. Closed grants cannot re-enter before their expiry."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from fastapi import HTTPException


@dataclass
class Session:
    grant: dict
    next_sequence: int = 0
    poisoned: bool = False
    requests: set[str] = field(default_factory=set)
    observations: set[str] = field(default_factory=set)


class Sessions:
    """Called under inference admission, except close which must fence in-flight work.

    The model runs outside this lock. The worker's admission semaphore excludes
    reset/inference overlap, while close can revoke their eventual response.
    State is process-local: after worker restart a decision without start fails.
    """

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
                raise HTTPException(409, "mission session is closed")
            if self.owner:
                if self.owner.grant != grant or self.owner.poisoned:
                    raise HTTPException(409, "worker already owns another or failed session")
                return self.owner, False  # a lost start response must not reset the policy
            if len(self.closed) >= 1024:
                raise HTTPException(429, "session retention is full; wait for original grant expiry")
            self.owner = Session(grant.copy())
            return self.owner, True

    def close(self, grant: dict):
        with self.lock:
            self._prune()
            is_owner = self.owner is not None and self.owner.grant == grant
            occupied = len(self.closed) + int(self.owner is not None and not is_owner)
            if grant["mission_id"] not in self.closed and occupied >= 1024:
                raise HTTPException(429, "session retention is full")
            existing = self.closed.get(grant["mission_id"])
            if existing is not None and existing != grant:
                raise HTTPException(409, "mission identity or original authorization changed")
            # Even an unseen start is fenced if end arrived first.
            self.closed[grant["mission_id"]] = grant.copy()
            if self.owner and self.owner.grant == grant:
                self.owner = None

    def check(self, session: Session):
        with self.lock:
            if self.owner is not session or session.poisoned or time.time() >= session.grant["expires_at"]:
                raise HTTPException(409, "session was ended, expired, or failed")

    def reserve(self, grant: dict, body: dict, maximum: int) -> Session:
        with self.lock:
            session = self.owner
            if session is None or session.grant != grant or session.poisoned:
                raise HTTPException(409, "start this exact mission session before inference")
            if (body["sequence"] != session.next_sequence or session.next_sequence >= maximum or
                    body["request_id"] in session.requests or body["observation_id"] in session.observations):
                raise HTTPException(409, "duplicate or nonconsecutive observation; no cached actions")
            # A failed/ambiguous inference is never replayed under a new request ID.
            session.next_sequence += 1
            session.requests.add(body["request_id"])
            session.observations.add(body["observation_id"])
            return session

    def poison(self, session: Session):
        with self.lock:
            session.poisoned = True
