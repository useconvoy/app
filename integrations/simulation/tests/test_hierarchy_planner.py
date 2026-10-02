"""Strict proposals and observable model failures, without replacing inference."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from convoy_sim.hierarchy.planner import (
    DeterministicPlanner,
    FaultInjectedPlanner,
    PlannerError,
    TextPlanner,
    parse_decision,
)


def context():
    return {"request_id": "test-1", "observation_seq": 3, "task_revision": 2,
            "instruction": "Move the puck to target B.", "task_target": "B",
            "available_skills": ["pick_place", "hold"], "available_targets": ["A", "B"],
            "robot": {"gripper": "open"}, "puck": {"position": [0.1, 0.0, 0.03]}}


def decision(**changes):
    value = {key: context()[key] for key in ("request_id", "observation_seq", "task_revision")}
    value.update(skill="pick_place", target="B")
    value.update(changes)
    return value


def test_proposal_is_bound_to_exact_observation_and_available_capabilities():
    proposal = parse_decision(json.dumps(decision()), context())
    assert proposal.target == "B" and proposal.observation_seq == 3
    assert parse_decision(json.dumps(decision(skill="hold", target=None)), context()).target is None
    for changed in ({"request_id": "other"}, {"observation_seq": True}, {"task_revision": 1},
                    {"skill": "fly"}, {"target": "C"}, {"skill": "hold"}, {"target": None},
                    {"explanation": "added key"}):
        with pytest.raises(PlannerError):
            parse_decision(json.dumps(decision(**changed)), context())
    limited = context() | {"available_targets": ["A"]}
    with pytest.raises(PlannerError, match="available target"):
        parse_decision(json.dumps(decision()), limited)


@pytest.mark.parametrize("reply", [
    "```json\n{}\n```", '{"request_id":"test-1","request_id":"test-1"}',
    "[]", "null", "{} {}", '{"target":NaN}', "",
])
def test_invalid_model_json_is_never_repaired(reply):
    with pytest.raises(PlannerError):
        parse_decision(reply, context())


@pytest.fixture
def model_server():
    state = {"requests": [], "delay": 0.0, "status": 200, "finish": "stop", "reply": decision(),
             "simulated": False}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            time.sleep(state["delay"])
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {"choices": [{"finish_reason": state["finish"],
                                     "message": {"content": json.dumps(state["reply"])}}],
                       "convoy": {"simulated": state["simulated"]}}
            try:
                self.wfile.write(json.dumps(payload).encode())
            except BrokenPipeError:
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_http_model_keeps_round_trip_separate_from_modeled_delay(model_server):
    url, state = model_server
    result = FaultInjectedPlanner(TextPlanner(url), delay_ms=10, seed=4).plan(context())
    assert result.backend == "text-model" and result.decision.target == "B"
    assert result.model_rtt_ms > 0 and result.injected_delay_ms == 10
    assert result.fault_mode == "modeled-delay-jitter"
    sent = state["requests"][0]
    assert sent["max_tokens"] == 80 and sent["temperature"] == 0 and sent["stream"] is False
    assert json.loads(sent["messages"][1]["content"]) == context()
    state["finish"] = "length"
    with pytest.raises(PlannerError) as failed:
        TextPlanner(url).plan(context())
    assert failed.value.code == "incomplete_reply" and failed.value.model_rtt_ms > 0


def test_simulated_gateway_cannot_be_reported_as_actual_model_inference(model_server):
    url, state = model_server
    state["simulated"] = True
    with pytest.raises(PlannerError) as failed:
        TextPlanner(url).plan(context())
    assert failed.value.code == "simulated_backend"


def test_model_timeout_and_http_error_remain_failures(model_server):
    url, state = model_server
    state["delay"] = 0.15
    with pytest.raises(PlannerError) as failed:
        TextPlanner(url, timeout_s=0.03).plan(context())
    assert failed.value.code == "timeout" and 20 <= failed.value.model_rtt_ms < 150
    state["delay"], state["status"] = 0.0, 503
    with pytest.raises(PlannerError) as failed:
        TextPlanner(url).plan(context())
    assert failed.value.code == "http_error" and "503" in str(failed.value)


def test_reference_and_outage_are_explicit_and_do_not_call_model(model_server):
    url, state = model_server
    wrapper = FaultInjectedPlanner(TextPlanner(url), outage_requests=[1])
    with pytest.raises(PlannerError) as failed:
        wrapper.plan(context())
    assert failed.value.code == "modeled_outage" and failed.value.fault_mode == "modeled-outage"
    assert failed.value.model_rtt_ms == 0 and state["requests"] == []
    reference = DeterministicPlanner().plan(context())
    assert reference.backend == "deterministic-reference" and reference.model_rtt_ms == 0
    held = DeterministicPlanner().plan(context() | {"instruction": "Stop."})
    assert held.decision.skill == "hold" and held.decision.target is None


@pytest.mark.parametrize("kwargs", [
    {"delay_ms": float("nan")}, {"jitter_ms": float("inf")}, {"delay_ms": -1},
    {"delay_ms": 60000, "jitter_ms": 1}, {"outage_requests": [0]}, {"outage_requests": [True]},
])
def test_fault_configuration_is_bounded(kwargs):
    with pytest.raises(ValueError):
        FaultInjectedPlanner(DeterministicPlanner(), **kwargs)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, 121])
def test_model_timeout_configuration_is_bounded(timeout):
    with pytest.raises(ValueError):
        TextPlanner("http://127.0.0.1:1", timeout_s=timeout)
