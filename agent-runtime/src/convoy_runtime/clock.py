"""RunClock — the single seam all workflow time flows through.

Workflow code never calls `workflow.now()`, `datetime.now()`, or raw timers
directly; it calls `RunClock.now()` / `RunClock.timer()`. Production is a
passthrough to Temporal's deterministic primitives. TODO: virtual mode —
time as workflow state advanced by signals/policy, for sandbox rehearsals.

This module deliberately lives outside `workflows/` so the AST lint test can
ban the raw primitives inside `workflows/` while this implementation remains
the one sanctioned caller.
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


# TODO: VirtualClock — `now` derived from RunState.virtual_now; timers register
# deadlines in workflow state and wait on conditions; advancement via the
# `advance_time` signal, on-idle auto-advance, or a wall-clock ratio.
