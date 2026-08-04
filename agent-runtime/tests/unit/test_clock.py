"""RunClock unit tests: passthrough delegation, virtual advancement rules,
and the ratio mapping."""

from datetime import UTC, datetime, timedelta

import pytest
from temporalio import workflow

from convoy_runtime.clock import PassthroughClock, RatioClock, RunClock, VirtualClock

pytestmark = pytest.mark.anyio

FIXED_NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)


class _RecordingAsyncio:
    """Stands in for the `asyncio` name inside convoy_runtime.clock only."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


async def test_now_delegates_to_workflow_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow, "now", lambda: FIXED_NOW)
    clock: RunClock = PassthroughClock()  # PassthroughClock satisfies the Protocol
    assert clock.now() == FIXED_NOW


async def test_timer_sleeps_until_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow, "now", lambda: FIXED_NOW)
    recorder = _RecordingAsyncio()
    monkeypatch.setattr("convoy_runtime.clock.asyncio", recorder)
    await PassthroughClock().timer(FIXED_NOW + timedelta(seconds=90))
    assert recorder.slept == [90.0]


async def test_timer_past_deadline_does_not_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workflow, "now", lambda: FIXED_NOW)
    recorder = _RecordingAsyncio()
    monkeypatch.setattr("convoy_runtime.clock.asyncio", recorder)
    await PassthroughClock().timer(FIXED_NOW - timedelta(seconds=5))
    assert recorder.slept == []


def test_virtual_clock_only_moves_forward() -> None:
    clock = VirtualClock(FIXED_NOW)
    assert clock.now() == FIXED_NOW
    assert clock.advance_to(FIXED_NOW - timedelta(seconds=1)) is False
    assert clock.advance_to(FIXED_NOW) is False
    assert clock.now() == FIXED_NOW
    target = FIXED_NOW + timedelta(days=2)
    assert clock.advance_to(target) is True
    assert clock.now() == target
    # Duplicate delivery of the same advance is a no-op.
    assert clock.advance_to(target) is False


def test_virtual_clock_reports_no_deadline_without_timers() -> None:
    assert VirtualClock(FIXED_NOW).next_deadline is None


class _FixedBase:
    """A RunClock standing in for the passthrough underneath a ratio clock."""

    def __init__(self, now: datetime) -> None:
        self._now = now
        self.timer_deadlines: list[datetime] = []

    def now(self) -> datetime:
        return self._now

    async def timer(self, deadline: datetime) -> None:
        self.timer_deadlines.append(deadline)


async def test_ratio_clock_maps_real_elapsed_onto_virtual_time() -> None:
    base = _FixedBase(FIXED_NOW + timedelta(seconds=30))
    clock = RatioClock(
        real_anchor=FIXED_NOW,
        virtual_anchor=FIXED_NOW,
        ratio=60.0,
        base=base,
    )
    # 30 real seconds at 60x is 30 virtual minutes.
    assert clock.now() == FIXED_NOW + timedelta(minutes=30)
    # A virtual deadline arms a real timer at the scaled-down instant.
    await clock.timer(FIXED_NOW + timedelta(hours=2))
    assert base.timer_deadlines == [FIXED_NOW + timedelta(minutes=2)]


def test_ratio_clock_rejects_non_positive_ratios() -> None:
    with pytest.raises(ValueError):
        RatioClock(real_anchor=FIXED_NOW, virtual_anchor=FIXED_NOW, ratio=0.0)
