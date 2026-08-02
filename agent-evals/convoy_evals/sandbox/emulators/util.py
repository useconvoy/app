"""Shared emulator helpers. Emulator handlers only receive the WorldStore, but
time-perceiving tools (calendar.today, ams.list_expiring) need the sim clock —
the concrete world store carries it as `clock_ref` (see world.py).

Port of src/sandbox/emulators/util.ts.
"""

from __future__ import annotations

import re

from ...runtime.ports import ClockPort
from ..api import WorldStore


def world_clock(world: WorldStore) -> ClockPort:
    clock = getattr(world, "clock_ref", None)
    if clock is None:
        raise ValueError(
            "emulator requires a sim world store (create_world_store) — clock_ref missing"
        )
    return clock


# The agent's own address in the sim — the from: on outbound email.
AGENT_ADDRESS = "agent@convoy.sim"

_REPLY_PREFIX = re.compile(r"^\s*((re|fwd?):\s*)+", re.IGNORECASE)
_WS = re.compile(r"\s+")


def thread_id_for_subject(subject: str) -> str:
    """Thread key from a subject line: strip reply prefixes, normalize."""
    return "thread_" + _WS.sub("-", _REPLY_PREFIX.sub("", subject).strip().lower())
