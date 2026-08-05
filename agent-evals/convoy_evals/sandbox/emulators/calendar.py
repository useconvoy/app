"""Calendar emulator — how the agent perceives "today". The date comes from
the sim clock (via the world's clock_ref), never the wall clock, which is the
property that lets a 45-day mission run in seconds.

Port of src/sandbox/emulators/calendar.ts.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, List

from ..api import ToolCallCtx, ToolEmulator, WorldStore
from ..world import iso_ts
from .util import world_clock


def _today(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    now = world_clock(world).now().astimezone(timezone.utc)
    return {"today": now.strftime("%Y-%m-%d"), "ts": iso_ts(now)}


calendar_emulator: List[ToolEmulator] = [
    ToolEmulator(tool="calendar.today", effectful=False, handler=_today),
]
