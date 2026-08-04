"""RunClock — the single seam all workflow time flows through (CLAUDE.md rule 13).

Workflow code never calls `workflow.now()`, `datetime.now()`, or raw timers
directly; it calls `RunClock.now()` / `RunClock.timer()`. Production is a
passthrough to Temporal's deterministic primitives. Virtual mode is workflow
state + signals per DESIGN.md section 8.4 — TODO(milestone-4).

This module deliberately lives outside `workflows/` so the AST lint test
(TESTING.md section 4.4) can ban the raw primitives inside `workflows/` while
this implementation remains the one sanctioned caller.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Protocol

from temporalio import workflow


class RunClock(Protocol):
    """Deterministic time source for workflow code."""

    def now(self) -> datetime:
        """Current time — real under passthrough, `RunState.virtual_now` under virtual."""
        ...

    async def timer(self, deadline: datetime) -> None:
        """Durable timer that resolves at `deadline`."""
        ...


class PassthroughClock:
    """Production clock: delegates to Temporal's replay-safe primitives."""

    def now(self) -> datetime:
        return workflow.now()

    async def timer(self, deadline: datetime) -> None:
        remaining = deadline - self.now()
        if remaining > timedelta(0):
            # asyncio.sleep inside a workflow is a durable Temporal timer.
            await asyncio.sleep(remaining.total_seconds())


# TODO(milestone-4): VirtualClock — `now` derived from RunState.virtual_now; timers
# register deadlines in workflow state and wait on conditions; advancement via the
# `advance_time` signal / on_idle / ratio policies (DESIGN.md section 8.4).
