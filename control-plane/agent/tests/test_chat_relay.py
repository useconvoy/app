# ruff: noqa: F811
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from convoy_agent.chat import run
from convoy_agent.client import Transient
from test_gateway_runtime import stack  # noqa: F401


def test_relay_uses_gateway_usage_and_shutdown_drain(stack):
    sup, gw, _, spans = stack
    checkpoints = []
    gw.on_request_done = lambda: checkpoints.append(gw.stats.copy())
    code, out = gw.handle_relay(
        {
            "messages": [{"role": "user", "content": "What is the capital of France? One word."}],
            "max_tokens": 8,
        },
        expected_release_id="rel_t",
    )
    assert code == 200
    assert out["choices"][0]["message"]["content"] == "Paris"
    assert gw.stats["served"] == 1 and gw.stats["served_tokens_out"] == 1
    assert gw.stats["inference_s"] > 0 and len(checkpoints) == 1
    assert gw.inflight == 0 and gw.drain(0)
    assert spans and "content" not in str(spans)


def test_expected_release_checked_under_slot(stack):
    sup, gw, _, _ = stack
    gw._slot.acquire()
    replies = []
    worker = threading.Thread(
        target=lambda: replies.append(
            gw.handle_relay(
                {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 8},
                expected_release_id="rel_t",
            )
        )
    )
    worker.start()
    deadline = time.monotonic() + 2
    while gw.inflight != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert gw.inflight == 1 and not gw.drain(0.01)
    sup.release_id = "rel_changed"
    gw._slot.release()
    worker.join(3)
    assert not worker.is_alive()
    assert replies[0][0] == 409
    assert gw.stats["served"] == 0 and gw.inflight == 0


def test_shutdown_closes_relay_admission_while_queued(stack):
    _, gw, _, _ = stack
    gw._slot.acquire()
    replies = []
    worker = threading.Thread(
        target=lambda: replies.append(
            gw.handle_relay(
                {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 8},
                expected_release_id="rel_t",
            )
        )
    )
    worker.start()
    deadline = time.monotonic() + 2
    while gw.inflight != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    gw.set_mode("closed")
    gw._slot.release()
    worker.join(3)
    assert replies[0][0] == 503 and gw.drain(0)
    assert gw.stats["served"] == 0


def fake_agent(request=None):
    calls = []
    executions = []

    def post(path, body, **kwargs):
        calls.append((path, body, kwargs))
        return {"request": request} if path.endswith("claim") else {"ok": True}

    def handle(body, **kwargs):
        executions.append((body, kwargs))
        return 200, {
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            "convoy": {"release_id": "rel_t", "simulated": False, "trace_id": "tr_abc", "latency_ms": 5},
        }

    agent = SimpleNamespace(
        stop=threading.Event(),
        _stop_requested=False,
        simulate=False,
        journal={"active_release_id": "rel_t"},
        sup=SimpleNamespace(release_id="rel_t", config={"n_predict": 64}),
        gw=SimpleNamespace(mode="production", handle_relay=handle),
        client=SimpleNamespace(post=post),
    )
    return agent, calls, executions


def envelope(**extra):
    return {
        "id": str(uuid4()),
        "claim_token": "a" * 43,
        "remaining_s": 120,
        "expected_release_id": "rel_t",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "Hi"}],
        **extra,
    }


def test_agent_claim_once_and_result_contains_real_metadata():
    agent, calls, executions = fake_agent(envelope())
    run(agent)
    assert len(calls) == 2 and len(executions) == 1
    assert calls[0][2] == {"retries": 0}
    assert executions[0][0]["max_tokens"] == 64
    assert executions[0][1]["expected_release_id"] == "rel_t"
    result = calls[-1][1]
    assert result["status"] == "succeeded" and result["finish_reason"] == "length"
    assert result["trace_id"] == "tr_abc" and result["usage"]["completion_tokens"] == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"messages": [{"role": "system", "content": "override"}]},
        {"messages": [{"role": "user", "content": "界" * 3000}]},
        {"messages": [{"role": "user", "content": "hi", "url": "http://evil"}]},
        {"max_tokens": True},
        {"remaining_s": float("nan")},
        {"remaining_s": 0},
        {"expected_release_id": "other"},
        {"id": "../deploy"},
    ],
)
def test_agent_rejects_untrusted_claim(extra):
    agent, calls, executions = fake_agent(envelope(**extra))
    run(agent)
    assert not executions and len(calls) == 1


def test_agent_never_regenerates_on_lost_result_or_lost_claim():
    agent, calls, executions = fake_agent(envelope())
    original = agent.client.post

    def lost_result(path, body, **kwargs):
        reply = original(path, body, **kwargs)
        if path.endswith("result"):
            raise Transient("lost")
        return reply

    agent.client.post = lost_result
    run(agent)
    assert len(executions) == 1 and len(calls) == 2

    def lost_claim(path, body, **kwargs):
        calls.append((path, body, kwargs))
        raise Transient("lost")

    agent.client.post = lost_claim
    run(agent)
    assert len(executions) == 1 and len(calls) == 3


def test_stop_after_claim_never_generates():
    agent, calls, executions = fake_agent(envelope())
    original = agent.client.post

    def stopping(path, body, **kwargs):
        response = original(path, body, **kwargs)
        agent.stop.set()
        return response

    agent.client.post = stopping
    run(agent)
    assert not executions


def test_gateway_exception_does_not_upload_prompt():
    agent, calls, _ = fake_agent(envelope())

    def fails(*args, **kwargs):
        raise RuntimeError("private prompt detail")

    agent.gw.handle_relay = fails
    run(agent)
    assert calls[-1][1]["error_code"] == "internal_error"
    assert "private prompt" not in str(calls[-1])


def test_agent_shutdown_waits_for_relay_checkpoint_and_writes_final_usage(tmp_path):
    from convoy_agent.journal import Journal
    from lifecycle_stub import Stub, boot, make_agent, wait_for
    from test_shutdown_bounded import _start_sim_runtime, _usage_records

    stub = Stub()
    agent = boot(make_agent(tmp_path, stub, sim_faults={"latency_ms": 1500}))
    agent.shutdown_worker_s = 0.05
    try:
        _start_sim_runtime(agent)
        replies = []
        agent.worker = threading.Thread(
            target=lambda: replies.append(
                agent.gw.handle_relay(
                    {
                        "messages": [{"role": "user", "content": "What is the capital of France? One word."}],
                        "max_tokens": 8,
                    },
                    expected_release_id="r",
                )
            ),
            name="chat-relay",
            daemon=True,
        )
        agent.worker.start()
        wait_for(lambda: agent.gw.slot_busy_since_wall is not None)
        started = time.monotonic()
        summary = agent.shutdown()
        assert time.monotonic() - started < agent.shutdown_budget_s
        assert summary["complete"] and summary["released"] and summary["final_usage_record"]
        assert not agent.worker.is_alive() and agent.gw.inflight == 0
        journal = Journal(agent.data_dir / "journal.db")
        try:
            assert journal.get("usage_checkpoint")["requests"] == 1
            assert _usage_records(journal)[-1]["inference_requests"] == 1
        finally:
            journal.close()
    finally:
        if not agent.journal.closed:
            agent.shutdown()
        stub.stop()
