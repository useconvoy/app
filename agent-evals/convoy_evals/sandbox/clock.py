"""SimClock — the harness-owned simulated clock. The sole authority on domain
time inside a sandbox instance; only the DES driver (run_until) advances it.
Monotonic by contract: advancing backwards is a harness bug, so it raises.

Port of src/sandbox/clock.ts. All datetimes are tz-aware UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .api import SimClock


def _as_utc(t: datetime, what: str) -> datetime:
    if not isinstance(t, datetime):
        raise ValueError("%s: invalid date" % what)
    if t.tzinfo is None:
        return t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


class _SimClock(SimClock):
    def __init__(self, start: datetime) -> None:
        self._current = _as_utc(start, "create_sim_clock")

    def now(self) -> datetime:
        # datetime is immutable, so returning it directly is already a "copy".
        return self._current

    def advance_to(self, t: datetime) -> None:
        t = _as_utc(t, "SimClock.advance_to")
        if t < self._current:
            raise ValueError(
                "SimClock is monotonic: cannot advance to %s (now is %s)"
                % (t.isoformat(), self._current.isoformat())
            )
        self._current = t


def create_sim_clock(start: datetime) -> SimClock:
    return _SimClock(start)
