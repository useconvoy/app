"""RunClock passthrough unit tests (TESTING §5-M0)."""

from datetime import UTC, datetime, timedelta

import pytest
from temporalio import workflow

from convoy_runtime.clock import PassthroughClock, RunClock

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
