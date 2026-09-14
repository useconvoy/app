"""One-shot text relay through the agent's existing owned production gateway.

No URLs, tools, deployment commands, or prompt journalling. Claims are never retried. A result
upload may be retried unchanged, but generation is never repeated after an uncertain outcome.
"""

from __future__ import annotations

import math
import time
from uuid import UUID

from .client import ApiError, Transient

ERROR_CODES = {
    "context_length_exceeded",
    "timeout",
    "runtime_error",
    "unavailable",
    "invalid_request_error",
}


def run(agent) -> None:
    if agent.stop.is_set() or agent._stop_requested or agent.simulate or agent.gw.mode != "production":
        return
    release = agent.journal.get("active_release_id")
    if not release or agent.sup.release_id != release:
        return
    started = time.monotonic()
    try:
        response = agent.client.post("/api/agent/v1/chat/claim", {"active_release_id": release}, retries=0)
    except (ApiError, Transient):
        return  # unsupported server, unavailable, or lost claim reply: no claim retry
    request = response.get("request") if isinstance(response, dict) else None
    if not request or agent.stop.is_set() or agent._stop_requested:
        return
    # Validate the complete server envelope before letting remote text reach the local gateway.
    try:
        request_id = str(UUID(request["id"]))
        token = request["claim_token"]
        remaining = request["remaining_s"]
        messages = request["messages"]
        cap = request["max_tokens"]
        if not isinstance(token, str) or not 16 <= len(token) <= 128:
            return
        if (
            not isinstance(remaining, (float, int))
            or isinstance(remaining, bool)
            or not math.isfinite(remaining)
            or not 0 < remaining <= 120
        ):
            return
        if request["expected_release_id"] != release:
            return
        if not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= 128:
            return
        if not isinstance(messages, list) or not 1 <= len(messages) <= 16:
            return
        if any(
            not isinstance(m, dict)
            or set(m) != {"role", "content"}
            or m["role"] not in ("user", "assistant")
            or not isinstance(m["content"], str)
            or not m["content"]
            for m in messages
        ):
            return
        if messages[-1]["role"] != "user" or sum(len(m["content"].encode("utf-8")) for m in messages) > 8192:
            return
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return
    # Subtract the whole claim roundtrip, conservatively: execution never gains time from transit.
    remaining -= time.monotonic() - started
    if remaining <= 1:
        return
    result = {"claim_token": token, "release_id": release, "status": "failed", "error_code": "internal_error"}
    try:
        code, output = agent.gw.handle_relay(
            {
                "model": "convoy-active",
                "messages": messages,
                "max_tokens": min(cap, int(agent.sup.config.get("n_predict", 128))),
                "stream": False,
            },
            expected_release_id=release,
            deadline_s=min(30.0, remaining),
        )
        if code == 200:
            content = output["choices"][0]["message"]["content"]
            meta = output.get("convoy") or {}
            if meta.get("release_id") != release or meta.get("simulated") is not False:
                result["error_code"] = "device_changed"
            elif not isinstance(content, str) or len(content) > 16384 or len(content.encode("utf-8")) > 32768:
                result["error_code"] = "output_too_large"
            else:
                result.update(
                    status="succeeded",
                    error_code=None,
                    content=content,
                    usage=output.get("usage"),
                    trace_id=meta.get("trace_id"),
                    finish_reason=output["choices"][0].get("finish_reason"),
                    metrics={k: meta.get(k) for k in ("latency_ms", "ttft_ms", "queue_ms")},
                )
        else:
            error = (output.get("error") or {}).get("type")
            result["error_code"] = error if error in ERROR_CODES else "runtime_error"
    except Exception:
        # Never include exception text: a local model exception may contain prompt bytes.
        result["error_code"] = "internal_error"
    if agent.stop.is_set() or agent._stop_requested:
        return  # shutdown owns final accounting; no new network I/O or durable prompt queue
    try:
        agent.client.post(f"/api/agent/v1/chat/{request_id}/result", result, retries=1)
    except (ApiError, Transient):
        pass  # uncertain delivery expires; never regenerate
