"""RunClock — the single seam all workflow time flows through.

Workflow code never calls `workflow.now()`, `datetime.now()`, or raw timers
directly; it calls `RunClock.now()` / `RunClock.timer()`. Three
implementations:

- `PassthroughClock`: production — Temporal's deterministic primitives.
- `VirtualClock`: sandbox rehearsals — time is workflow state. `now()` reads
  the virtual instant; a timer registers its deadline and waits on a
  condition; time moves only when the workflow applies an advance (manual
  signal, idle auto-advance). Replay-deterministic and fully audited.
- `RatioClock`: scaled wall clock — virtual = anchor + real elapsed x ratio.
  Timers are real durable timers at the scaled-down real deadline.

This module deliberately lives outside `workflows/` so the AST lint test can
ban the raw primitives inside `workflows/` while these implementations remain
the sanctioned callers.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Protocol

from temporalio import workflow


class RunClock(Protocol):
    """Deterministic time source for workflow code."""

    def now(self) -> datetime:
        """Current time — real under passthrough, virtual under virtual/ratio."""
        ...

    async def timer(self, deadline: datetime) -> None:
        """Durable timer that resolves at `deadline` (in this clock's time)."""
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


class VirtualClock:
    """Virtual time as workflow state.

    `now()` is whatever the workflow last advanced to; `timer()` registers
    its deadline and waits for the clock to reach it. Advancement is the
    workflow's decision (drained from signals or idle policy), so replay sees
    the identical sequence of instants. The workflow mirrors `now()` into
    `RunState.virtual_now` so the instant survives continue_as_new.
    """

    def __init__(self, initial: datetime) -> None:
        self._now = initial
        # Armed, unresolved deadlines — what idle auto-advance targets.
        self._deadlines: list[datetime] = []

    def now(self) -> datetime:
        return self._now

    async def timer(self, deadline: datetime) -> None:
        if deadline <= self._now:
            return
        self._deadlines.append(deadline)
        try:
            await workflow.wait_condition(lambda: self._now >= deadline)
        finally:
            self._deadlines.remove(deadline)

    def advance_to(self, target: datetime) -> bool:
        """Move time forward to `target`; backward moves are no-ops. Returns
        whether time actually moved. Every armed timer with a deadline at or
        before the new instant resolves on the next scheduler pass."""
        if target <= self._now:
            return False
        self._now = target
        return True

    @property
    def next_deadline(self) -> datetime | None:
        """The earliest armed timer deadline still in the future, if any."""
        pending = [d for d in self._deadlines if d > self._now]
        return min(pending) if pending else None


class RatioClock:
    """Scaled wall-clock mapping: virtual = anchor + real elapsed x ratio.

    Anchors are fixed at run start and carried across hops, so the mapping is
    one straight line for the run's whole life. Timers convert their virtual
    deadline to the real instant the mapping reaches it and arm a durable
    real timer there.
    """

    def __init__(
        self,
        *,
        real_anchor: datetime,
        virtual_anchor: datetime,
        ratio: float,
        base: RunClock | None = None,
    ) -> None:
        if ratio <= 0:
            raise ValueError(f"ratio must be positive, got {ratio}")
        self._real_anchor = real_anchor
        self._virtual_anchor = virtual_anchor
        self._ratio = ratio
        self._base: RunClock = base if base is not None else PassthroughClock()

    def now(self) -> datetime:
        elapsed = self._base.now() - self._real_anchor
        return self._virtual_anchor + elapsed * self._ratio

    def real_deadline(self, virtual_deadline: datetime) -> datetime:
        remaining = virtual_deadline - self._virtual_anchor
        return self._real_anchor + remaining / self._ratio

    async def timer(self, deadline: datetime) -> None:
        await self._base.timer(self.real_deadline(deadline))
