"""Deterministic Chat scheduling checks: no network, inference or wall-clock sleeps."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from convoy_agent import agent as module
from convoy_agent.agent import Agent
from convoy_agent.client import Transient


class Clock:
    def __init__(self):
        self.now = 0.0
        self.after_wait = None


class Event:
    def __init__(self, clock):
        self.clock = clock
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.clock.now += seconds
        if self.clock.after_wait:
            self.clock.after_wait()
        return self.stopped


@pytest.fixture
def harness(monkeypatch):
    clock = Clock()
    starts = []

    class Worker:
        def __init__(self, *, target, args=(), name=None, daemon=None):
            self.target, self.args, self.name = target, args, name
            self.alive = False

        def start(self):
            starts.append((clock.now, self.name, self))
            # Empty claims return immediately. Tests explicitly hold a worker alive to
            # represent generation or an operation; no target executes in this harness.
            self.alive = self.name != "chat-relay"

        def is_alive(self):
            return self.alive

    monkeypatch.setattr(module.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(module.threading, "Thread", Worker)
    a = Agent.__new__(Agent)
    a.stop = Event(clock)
    a._stop_requested = False
    a._chat_poll_allowed = False
    a._next_chat_poll = 0.0
    a.simulate = False
    a.gw = SimpleNamespace(mode="production")
    a.worker = None
    a.exec = SimpleNamespace(current=None)
    a.journal = SimpleNamespace(unacked_terminal=lambda: [], operation=lambda op_id: None)
    a.pending_challenge = None
    a._server_context = {}
    a._lanes_probed = True
    a._supervise_runtime = lambda: None
    a._record_usage = lambda: None
    a._run_operation = lambda op: None
    a._drop_unwanted_pre_grant_row = lambda *args: None
    a.flush_spool = lambda **kwargs: {}
    response = {}
    reports = []

    def report(kind, **kwargs):
        reports.append((clock.now, kind))
        if isinstance(response.get("error"), Exception):
            raise response["error"]
        return response.copy()

    a.report = report
    return SimpleNamespace(agent=a, clock=clock, starts=starts, response=response, reports=reports)


def test_claims_run_between_heartbeats_without_moving_the_heartbeat_deadline(harness):
    h = harness
    h.agent.tick()
    h.agent._wait_or_stop(15)
    assert h.clock.now == 15
    assert h.reports == [(0, "heartbeat")]
    assert [t for t, name, _ in h.starts if name == "chat-relay"] == list(range(15))
    # The end of the wait deliberately yields to tick first, rather than admitting
    # another Chat request at the same instant a delivered operation can arrive.
    h.response["operations"] = [{"id": "op_new", "status": "pending"}]
    h.agent.tick()
    assert h.reports == [(0, "heartbeat"), (15, "heartbeat")]
    assert h.starts[-1][:2] == (15, "op-op_new")
    assert len([s for s in h.starts if s[1] == "chat-relay"]) == 15


def test_busy_worker_is_never_replaced_and_empty_claims_do_not_overlap(harness):
    h = harness
    h.agent.tick()
    first = h.agent.worker
    first.alive = True
    h.agent._wait_or_stop(5)
    assert h.agent.worker is first
    assert len(h.starts) == 1
    first.alive = False
    h.agent._maybe_start_chat()
    assert len(h.starts) == 2
    second = h.agent.worker
    for _ in range(5):
        h.agent._maybe_start_chat()
    assert h.agent.worker is second
    assert len(h.starts) == 2


def test_operation_arriving_during_generation_waits_for_same_worker_without_new_chat(harness):
    h = harness
    h.agent.tick()
    chat_worker = h.agent.worker
    chat_worker.alive = True
    h.clock.now = 15
    h.response["operations"] = [{"id": "op_waiting", "status": "delivered"}]
    h.agent.tick()
    assert h.agent.worker is chat_worker
    assert len(h.starts) == 1
    chat_worker.alive = False
    h.agent._wait_or_stop(15)
    assert len(h.starts) == 1
    h.agent.tick()
    assert h.starts[-1][:2] == (30, "op-op_waiting")
    assert h.agent.worker is not chat_worker


@pytest.mark.parametrize("blocked", [{"dispatch_paused": True}, {"quarantine": True}, {"operations": [{"id": "op_1", "status": "pending"}]}])
def test_pause_restore_and_delivered_operation_disable_claims_for_entire_wait(harness, blocked):
    h = harness
    h.agent.tick()
    h.clock.now = 15
    h.response.update(blocked)
    h.agent.tick()
    if h.agent.worker:
        h.agent.worker.alive = False  # even an operation finishing cannot bypass report permission
    h.agent._wait_or_stop(15)
    assert len([s for s in h.starts if s[1] == "chat-relay"]) == 1
    assert h.agent._chat_poll_allowed is False


def test_report_failure_removes_previous_permission_until_a_successful_report(harness):
    h = harness
    h.agent.tick()
    h.clock.now = 15
    h.response["error"] = Transient("offline")
    h.agent.tick()
    h.agent._wait_or_stop(15)
    assert len(h.starts) == 1
    assert h.agent._chat_poll_allowed is False
    h.response.clear()
    h.agent.tick()
    h.agent._wait_or_stop(1)
    assert 30 <= h.starts[-1][0] <= 31
    assert h.starts[-1][1] == "chat-relay"


def test_tick_exception_before_report_cannot_reuse_old_chat_permission(harness):
    h = harness
    h.agent.tick()
    h.clock.now = 15

    def failed_supervision():
        raise RuntimeError("supervision failure")

    h.agent._supervise_runtime = failed_supervision
    with pytest.raises(RuntimeError):
        h.agent.tick()
    h.agent._wait_or_stop(15)
    assert len(h.starts) == 1
    assert h.agent._chat_poll_allowed is False


@pytest.mark.parametrize("mode", ["closed", "eval", "draining"])
def test_runtime_modes_gate_fast_claims(harness, mode):
    h = harness
    h.agent.gw.mode = mode
    h.agent.tick()
    h.agent._wait_or_stop(15)
    assert h.starts == []


def test_simulator_never_uses_the_fast_chat_lane(harness):
    h = harness
    h.agent.simulate = True
    h.agent.tick()
    h.agent._wait_or_stop(15)
    assert h.starts == []


@pytest.mark.parametrize("stop_kind", ["flag", "event"])
def test_stop_during_wait_starts_no_claim_and_preserves_shutdown_granularity(harness, stop_kind):
    h = harness
    h.agent.tick()
    stops = []

    def request_stop():
        stops.append(h.clock.now)
        h.agent._stop_requested = True
        h.agent.stop.set()

    h.agent.request_stop = request_stop

    def interrupt():
        if stop_kind == "flag":
            h.agent._stop_requested = True
        else:
            h.agent.stop.set()

    h.clock.after_wait = interrupt
    h.agent._wait_or_stop(15)
    assert h.clock.now <= 0.5
    assert len(h.starts) == 1
    assert h.agent.stop.is_set()
    assert stops == ([0.5] if stop_kind == "flag" else [])


def test_stop_arriving_with_report_response_never_enables_chat(harness):
    h = harness

    def report(*args, **kwargs):
        h.agent._stop_requested = True
        return {}

    h.agent.report = report
    h.agent.tick()
    assert not h.agent._chat_poll_allowed
    assert h.starts == []
