"""Bounded skill proposals for the System 1/System 2 timing experiment.

Only the executive may admit a proposal. This module validates its syntax and
request identity; task age, cancellation and physical preconditions stay local.
An unavailable or malformed model never becomes a reference-controller answer.
"""

from __future__ import annotations

import json
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Protocol

SKILLS = frozenset({"pick_place", "hold"})
TARGETS = frozenset({"A", "B"})
RESPONSE_LIMIT = 16384
DECISION_FIELDS = frozenset({"request_id", "observation_seq", "task_revision", "skill", "target"})


@dataclass(frozen=True)
class Decision:
    request_id: str
    observation_seq: int
    task_revision: int
    skill: str
    target: str | None


@dataclass(frozen=True)
class PlannerResult:
    decision: Decision
    model_rtt_ms: float
    injected_delay_ms: float = 0.0
    fault_mode: str | None = None
    backend: str = "deterministic-reference"


class PlannerError(Exception):
    """Safe error text and timing: no URL credentials or server response bodies."""

    def __init__(self, code: str, message: str, *, model_rtt_ms: float = 0.0,
                 injected_delay_ms: float = 0.0, fault_mode: str | None = None,
                 backend: str = "text-model"):
        super().__init__(message)
        self.code = code
        self.model_rtt_ms = model_rtt_ms
        self.injected_delay_ms = injected_delay_ms
        self.fault_mode = fault_mode
        self.backend = backend


class Planner(Protocol):
    def plan(self, context: dict[str, Any]) -> PlannerResult: ...


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON key")
    return dict(pairs)


def _constant(_: str):
    raise ValueError("nonfinite JSON value")


def _json(text: str | bytes) -> Any:
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)


def validate_context(context: dict[str, Any]) -> None:
    if not isinstance(context, dict):
        raise PlannerError("invalid_context", "Planner context must be an object")
    request_id = context.get("request_id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise PlannerError("invalid_context", "A bounded request id is required")
    for name in ("observation_seq", "task_revision"):
        if type(context.get(name)) is not int or not 0 <= context[name] <= 2**53 - 1:
            raise PlannerError("invalid_context", f"{name} must be a nonnegative integer")
    if not isinstance(context.get("instruction"), str) or not 1 <= len(context["instruction"]) <= 2048:
        raise PlannerError("invalid_context", "A bounded task instruction is required")
    for name, allowed in (("available_skills", SKILLS), ("available_targets", TARGETS)):
        values = context.get(name)
        if (not isinstance(values, list) or not values or
                any(not isinstance(item, str) or item not in allowed for item in values) or
                len(set(values)) != len(values)):
            raise PlannerError("invalid_context", f"{name} must list supported, unique choices")
    try:
        encoded = json.dumps(context, allow_nan=False).encode()
    except (TypeError, ValueError):
        raise PlannerError("invalid_context", "Planner context must be finite JSON") from None
    if len(encoded) > 8192:
        raise PlannerError("invalid_context", "Planner context exceeds its size bound")


def parse_decision(text: str, context: dict[str, Any]) -> Decision:
    """Accept exactly one complete JSON decision; do not repair or strip fences."""
    validate_context(context)
    if not isinstance(text, str) or len(text.encode()) > RESPONSE_LIMIT:
        raise PlannerError("invalid_reply", "Model reply must be bounded text")
    try:
        value = _json(text)
    except (ValueError, UnicodeError):
        raise PlannerError("invalid_reply", "Model reply must be one strict JSON object") from None
    if not isinstance(value, dict) or set(value) != DECISION_FIELDS:
        raise PlannerError("invalid_reply", "Model reply has an invalid decision schema")
    for name in ("request_id", "observation_seq", "task_revision"):
        if type(value[name]) is not type(context[name]) or value[name] != context[name]:
            raise PlannerError("identity_mismatch", "Model reply does not match the requested observation and task")
    if not isinstance(value["skill"], str) or value["skill"] not in context["available_skills"]:
        raise PlannerError("invalid_reply", "Model selected an unavailable skill")
    if value["skill"] == "hold":
        if value["target"] is not None:
            raise PlannerError("invalid_reply", "Hold must have a null target")
    elif not isinstance(value["target"], str) or value["target"] not in context["available_targets"]:
        raise PlannerError("invalid_reply", "Pick and place needs an available target")
    return Decision(**value)


def build_prompt(context: dict[str, Any]) -> tuple[str, str]:
    validate_context(context)
    system = (
        "You choose one bounded robot skill. Reply with exactly one JSON object and no other text. "
        "Use exactly these keys: request_id, observation_seq, task_revision, skill, target. "
        "Copy all three ids exactly from the request. Choose a listed available skill and target. "
        "pick_place moves the puck to the requested task_target. hold has target:null. "
        "If the instruction says stop or wait, choose hold. "
        "Choose pick_place for the task_target when movement is requested. "
        "Do not invent movements or change the ids."
    )
    return system, json.dumps(context, separators=(",", ":"), allow_nan=False)


def _finite_bound(value: float, label: str, lower: float, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if not lower <= value <= upper:
        raise ValueError(f"{label} must be between {lower} and {upper}")
    return float(value)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TextPlanner:
    """Actual text inference through a configured OpenAI-compatible endpoint.

    model_rtt_ms includes HTTP transport, queueing and inference. It is not a
    measurement of neural execution alone. Credentials are sent only to this
    origin and are never included in the returned decision or error.
    """

    backend = "text-model"

    def __init__(self, base_url: str, model: str = "convoy-active", *, timeout_s: float = 5.0,
                 max_tokens: int = 80, bearer_token: str | None = None):
        parts = urllib.parse.urlsplit(base_url)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or
                parts.path not in {"", "/", "/v1", "/v1/"} or parts.query or parts.fragment):
            raise ValueError("Planner endpoint must be an explicit HTTP(S) origin or /v1 base URL")
        if not isinstance(model, str) or not model or len(model) > 256:
            raise ValueError("A bounded model name is required")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 80:
            raise ValueError("Planner output is limited to 1..80 tokens")
        if bearer_token is not None and (not isinstance(bearer_token, str) or not bearer_token or
                                         any(char in bearer_token for char in "\r\n")):
            raise ValueError("Invalid bearer token")
        self.url = base_url.rstrip("/") + ("/chat/completions" if parts.path.rstrip("/") == "/v1"
                                             else "/v1/chat/completions")
        self.model = model
        self.timeout_s = _finite_bound(timeout_s, "timeout_s", 0.001, 120.0)
        self.max_tokens = max_tokens
        self._token = bearer_token
        self._opener = urllib.request.build_opener(_NoRedirect())

    def plan(self, context: dict[str, Any]) -> PlannerResult:
        system, prompt = build_prompt(context)
        body = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                 {"role": "user", "content": prompt}],
                "temperature": 0.0, "max_tokens": self.max_tokens, "stream": False}
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers, method="POST")
        started = time.monotonic()
        try:
            with self._opener.open(request, timeout=self.timeout_s) as response:
                raw = response.read(RESPONSE_LIMIT + 1)
            if len(raw) > RESPONSE_LIMIT:
                raise PlannerError("invalid_reply", "Model response exceeded its size bound")
            try:
                envelope = _json(raw)
            except (ValueError, UnicodeError):
                raise PlannerError("invalid_reply", "Model response was not strict JSON") from None
            metadata = envelope.get("convoy") if isinstance(envelope, dict) else None
            if isinstance(metadata, dict) and metadata.get("simulated") is True:
                raise PlannerError("simulated_backend", "A simulated gateway is not actual text-model inference")
            choices = envelope.get("choices") if isinstance(envelope, dict) else None
            if (not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict) or
                    choices[0].get("finish_reason") != "stop"):
                raise PlannerError("incomplete_reply", "Model response was incomplete or ambiguous")
            message = choices[0].get("message")
            content = message.get("content") if isinstance(message, dict) else None
            decision = parse_decision(content, context)
        except PlannerError as exc:
            exc.model_rtt_ms = (time.monotonic() - started) * 1000
            raise
        except urllib.error.HTTPError as exc:
            raise PlannerError("http_error", f"Planner HTTP request failed ({exc.code})",
                               model_rtt_ms=(time.monotonic() - started) * 1000) from None
        except TimeoutError:
            raise PlannerError("timeout", "Planner request exceeded its timeout",
                               model_rtt_ms=(time.monotonic() - started) * 1000) from None
        except urllib.error.URLError as exc:
            code = "timeout" if isinstance(exc.reason, TimeoutError) else "transport_error"
            raise PlannerError(code, "Planner transport failed",
                               model_rtt_ms=(time.monotonic() - started) * 1000) from None
        return PlannerResult(decision, (time.monotonic() - started) * 1000, backend=self.backend)


class DeterministicPlanner:
    """A declared reference baseline, not a model or fallback for one."""

    backend = "deterministic-reference"

    def plan(self, context: dict[str, Any]) -> PlannerResult:
        try:
            validate_context(context)
        except PlannerError as exc:
            exc.backend = self.backend
            raise
        instruction = context["instruction"].lower().split()
        hold = any(word.strip(".,!?") in {"stop", "wait", "hold"} for word in instruction)
        target = None if hold else context.get("task_target")
        if not hold and target not in context["available_targets"]:
            raise PlannerError("invalid_context", "Reference planner requires an available task_target",
                               backend=self.backend)
        value = {name: context[name] for name in ("request_id", "observation_seq", "task_revision")}
        value.update(skill="hold" if hold else "pick_place", target=target)
        return PlannerResult(parse_decision(json.dumps(value), context), 0.0, backend=self.backend)


class FaultInjectedPlanner:
    """Explicit, reproducible *modeled* transport faults around either backend.

    Outage requests are 1-based call indices. Delays happen before requesting the
    backend; they remain separate from its measured HTTP round trip. The caller
    runs this wrapper off the physics thread and owns admission/cancellation.
    """

    def __init__(self, planner: Planner, *, delay_ms: float = 0.0, jitter_ms: float = 0.0,
                 outage_requests: tuple[int, ...] | list[int] = (), seed: int = 0):
        self.planner = planner
        self.delay_ms = _finite_bound(delay_ms, "delay_ms", 0.0, 60000.0)
        self.jitter_ms = _finite_bound(jitter_ms, "jitter_ms", 0.0, 60000.0)
        if self.delay_ms + self.jitter_ms > 60000:
            raise ValueError("Combined modeled delay is limited to 60 seconds")
        if any(type(index) is not int or index < 1 for index in outage_requests):
            raise ValueError("Outage requests must be positive call indices")
        if type(seed) is not int:
            raise ValueError("Fault seed must be an integer")
        self.outage_requests = frozenset(outage_requests)
        self.random = random.Random(seed)
        self.calls = 0

    @property
    def backend(self) -> str:
        return getattr(self.planner, "backend", "planner")

    def plan(self, context: dict[str, Any]) -> PlannerResult:
        try:
            validate_context(context)
        except PlannerError as exc:
            exc.backend = self.backend
            raise
        self.calls += 1
        injected = max(0.0, self.delay_ms + self.random.uniform(-self.jitter_ms, self.jitter_ms))
        fault = "modeled-outage" if self.calls in self.outage_requests else (
            "modeled-delay-jitter" if injected else None)
        if injected:
            time.sleep(injected / 1000)
        if self.calls in self.outage_requests:
            raise PlannerError("modeled_outage", "Injected planner outage; no model request was sent",
                               injected_delay_ms=injected, fault_mode=fault, backend=self.backend)
        try:
            result = self.planner.plan(context)
        except PlannerError as exc:
            exc.injected_delay_ms += injected
            exc.fault_mode = fault or exc.fault_mode
            raise
        return replace(result, injected_delay_ms=result.injected_delay_ms + injected,
                       fault_mode=fault or result.fault_mode)
