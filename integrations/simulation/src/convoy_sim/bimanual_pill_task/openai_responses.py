"""The cloud vision planner's transport: OpenAI's Responses API, called from this machine, with a spend cap.

``OpenAIResponsesClient`` sends one request at a time (``POST https://api.openai.com/v1/responses``,
the image as an ``input_image`` data URL) and measures each call end to end: request sent to the
response received, as seen by the client. Every call is priced and appended to a ``SpendLedger``
(JSON lines): a response by its ``usage`` (``cost_usd``), an HTTP error by nothing (the API bills no
failed request), a call that got no response (a timeout or a broken connection) by its worst case,
since the API may have run it. Before each call the ledger books the call's worst case
(``worst_case_usd``: its estimated input, all uncached, and the whole output cap, reasoning included)
and refuses when the spend so far plus open bookings plus this worst case would pass the cap: the
client then sends nothing (``SpendCapReached``).

The API key is read from ``OPEN_AI_API_KEY`` when the client is made and kept only in the request
header. It is never printed, logged or written to a record, and ``repr`` does not show it.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

KEY_ENV = "OPEN_AI_API_KEY"
ENDPOINT = "https://api.openai.com/v1/responses"
MODEL = "gpt-6-luna"
PROVIDER = "openai"
EFFORT = "low"
TRANSPORT = "openai-responses from cloud container"
# USD per 1M tokens for gpt-6-luna. Cached input is part of the input count; reasoning tokens bill as output.
PRICE_INPUT_PER_M, PRICE_CACHED_INPUT_PER_M, PRICE_OUTPUT_PER_M = 0.10, 0.01, 0.50
DEFAULT_CAP_USD = 3.00


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def usage_counts(usage: dict | None) -> dict:
    """Input tokens (and their cached part) and output tokens (and their reasoning part) of a response's usage."""
    usage = usage if isinstance(usage, dict) else {}
    details_in = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    details_out = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    return {"input_tokens": _int(usage.get("input_tokens")), "cached_tokens": _int(details_in.get("cached_tokens")),
            "output_tokens": _int(usage.get("output_tokens")), "reasoning_tokens": _int(details_out.get("reasoning_tokens"))}


def cost_usd(usage: dict | None) -> float:
    """The price of one response: uncached input, cached input and output (reasoning included)."""
    counts = usage_counts(usage)
    cached = min(counts["cached_tokens"], counts["input_tokens"])
    return round(((counts["input_tokens"] - cached) * PRICE_INPUT_PER_M + cached * PRICE_CACHED_INPUT_PER_M
                  + counts["output_tokens"] * PRICE_OUTPUT_PER_M) / 1e6, 9)


def worst_case_usd(input_tokens: int, max_output_tokens: int) -> float:
    """The most one call can cost: every input token uncached and the whole output cap used."""
    return (input_tokens * PRICE_INPUT_PER_M + max_output_tokens * PRICE_OUTPUT_PER_M) / 1e6


class SpendCapReached(RuntimeError):
    """The next call could take the spend past the cap: it is not sent."""


class SpendLedger:
    """The spend of every call, one JSON line each, appended to `path`.

    The total is always read from the file, so the cap holds across runs (and processes) that share it.
    Before a call, ``reserve`` books the call's worst case (in ``<path>.pending.json``) and refuses when
    recorded spend + open bookings + this worst case would pass the cap; ``record`` appends the call's
    cost and releases its booking. A booking left by a process that died stays booked (the cap errs on
    the safe side). One lock file serialises both.
    """

    def __init__(self, path, cap_usd: float = DEFAULT_CAP_USD):
        self.path, self.cap_usd = Path(path), float(cap_usd)
        self.pending_path = self.path.with_name(self.path.name + ".pending.json")
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _locked(self):
        import fcntl

        with self.lock_path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def _pending(self) -> dict[str, float]:
        if not self.pending_path.exists():
            return {}
        value = json.loads(self.pending_path.read_text() or "{}")
        return {str(k): float(v) for k, v in value.items()} if isinstance(value, dict) else {}

    @property
    def spent_usd(self) -> float:
        return round(sum(float(entry.get("cost_usd") or 0.0) for entry in self.entries()), 9)

    @property
    def booked_usd(self) -> float:
        return round(sum(self._pending().values()), 9)

    def reserve(self, worst_usd: float) -> str:
        """Book a call's worst case; SpendCapReached (nothing booked) when it could pass the cap."""
        with self._locked():
            spent, pending = self.spent_usd, self._pending()
            booked = sum(pending.values())
            if spent + booked + worst_usd > self.cap_usd + 1e-12:
                raise SpendCapReached(f"spent ${spent:.4f} (+${booked:.4f} booked); the next call could cost up to "
                                      f"${worst_usd:.4f}, over the ${self.cap_usd:.2f} cap")
            token = uuid.uuid4().hex
            pending[token] = worst_usd
            self.pending_path.write_text(json.dumps(pending))
            return token

    def record(self, entry: dict, reservation: str | None = None) -> dict:
        """Append one call's cost (and release its booking)."""
        cost = float(entry.get("cost_usd") or 0.0)
        with self._locked():
            line = {**entry, "cost_usd": cost, "cumulative_usd": round(self.spent_usd + cost, 9),
                    "cap_usd": self.cap_usd}
            with self.path.open("a") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            if reservation is not None:
                pending = self._pending()
                pending.pop(reservation, None)
                self.pending_path.write_text(json.dumps(pending))
        return line


@dataclass
class ResponseOutcome:
    """One Responses API call as the client measured it. No credentials."""

    status: str  # completed | incomplete | failed | http_error | transport_error | timeout
    e2e_ms: float  # request sent -> response received (or the failure)
    sent_at: str
    finished_at: str
    http_status: int | None = None
    response_id: str | None = None
    request_id: str | None = None  # the x-request-id response header
    model: str | None = None  # the model the API reports having used
    text: str | None = None  # every output_text part, joined
    refusal: str | None = None  # a refusal part, when the model refused
    incomplete_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    input_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float = 0.0
    cost_basis: str = "usage"  # usage | not_billed (HTTP error) | worst_case (no response)
    processing_ms: float | None = None  # the openai-processing-ms response header (time on the API side)
    request_bytes: int = 0
    retry_after_s: float | None = None


def response_text(body: dict) -> tuple[str | None, str | None]:
    """(output text, refusal) of a Responses API result: the output_text and refusal parts of its message items."""
    texts, refusals = [], []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif isinstance(part, dict) and part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                refusals.append(part["refusal"])
    return ("".join(texts) if texts else None), ("".join(refusals) if refusals else None)


def _short(value, limit: int = 300) -> str | None:
    return None if value is None else str(value)[:limit]


class OpenAIResponsesClient:
    """One request at a time to the Responses API, priced and capped through `ledger`."""

    transport = TRANSPORT
    provider = PROVIDER

    def __init__(self, ledger: SpendLedger, *, model: str = MODEL, effort: str = EFFORT, max_output_tokens: int = 2048,
                 timeout_s: float = 60.0, input_estimate: int = 6000, key: str | None = None, endpoint: str = ENDPOINT,
                 opener=None, clock=time.monotonic, wall=time.time, sleep=time.sleep):
        key = os.environ.get(KEY_ENV) if key is None else key
        if not key:
            raise ValueError(f"set {KEY_ENV} to call the cloud planner")
        self._authorization = f"Bearer {key}"
        self.ledger, self.model, self.effort = ledger, model, effort
        self.max_output_tokens, self.timeout_s, self.endpoint = max_output_tokens, timeout_s, endpoint
        self.input_estimate = input_estimate  # until a response reports its real input count
        self.largest_input = 0
        self.opener = opener or urllib.request.build_opener()
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self._not_before = 0.0
        self.sends = 0

    def __repr__(self) -> str:  # never shows the key
        return f"OpenAIResponsesClient(model={self.model!r}, effort={self.effort!r})"

    def settings(self) -> dict:
        return {"provider": self.provider, "model": self.model, "reasoning_effort": self.effort,
                "max_output_tokens": self.max_output_tokens, "client_timeout_s": self.timeout_s,
                "transport": self.transport, "endpoint": self.endpoint, "store": False,
                "price_usd_per_1m_tokens": {"input": PRICE_INPUT_PER_M, "cached_input": PRICE_CACHED_INPUT_PER_M,
                                            "output": PRICE_OUTPUT_PER_M},
                "spend_cap_usd": self.ledger.cap_usd}

    def worst_case_usd(self) -> float:
        estimate = max(self.input_estimate, int(self.largest_input * 1.5))
        return worst_case_usd(estimate, self.max_output_tokens)

    def body(self, content: list[dict], text_format: dict | None = None) -> dict:
        body = {"model": self.model, "input": [{"role": "user", "content": content}],
                "reasoning": {"effort": self.effort}, "max_output_tokens": self.max_output_tokens, "store": False}
        if text_format is not None:
            body["text"] = {"format": text_format}
        return body

    def send(self, content: list[dict], *, text_format: dict | None = None, label: str = "") -> ResponseOutcome:
        """One call. Raises SpendCapReached (nothing sent) when the call could pass the cap."""
        worst = round(self.worst_case_usd(), 9)
        reservation = self.ledger.reserve(worst)
        try:
            result = self._send(content, text_format)
        except BaseException:
            self.ledger.record({"at": _utc(self.wall()), "label": label, "model": self.model, "effort": self.effort,
                                "status": "client_error", "cost_usd": worst, "cost_basis": "worst_case"}, reservation)
            raise
        if result.status in ("timeout", "transport_error"):
            result.cost_usd, result.cost_basis = worst, "worst_case"
        elif result.http_status != 200:
            result.cost_basis = "not_billed"
        entry = {"at": result.finished_at, "label": label, "model": self.model, "effort": self.effort,
                 **{k: v for k, v in asdict(result).items() if k not in ("text", "refusal", "error_message")}}
        self.ledger.record(entry, reservation)
        return result

    def _send(self, content: list[dict], text_format: dict | None) -> ResponseOutcome:
        wait = max(0.0, self._not_before - self.clock())
        if wait:
            self.sleep(wait)  # a 429's Retry-After: wall clock only
        data = json.dumps(self.body(content, text_format), separators=(",", ":")).encode()
        request = urllib.request.Request(self.endpoint, data=data, method="POST", headers={
            "Authorization": self._authorization, "Content-Type": "application/json", "Accept": "application/json"})
        sent_wall, t0 = self.wall(), self.clock()
        self.sends += 1

        def outcome(status: str, **values) -> ResponseOutcome:
            return ResponseOutcome(status=status, e2e_ms=round((self.clock() - t0) * 1000, 1), sent_at=_utc(sent_wall),
                                   finished_at=_utc(self.wall()), request_bytes=len(data), **values)

        try:
            with self.opener.open(request, timeout=self.timeout_s) as response:
                raw, status, headers = response.read(), response.status, dict(response.headers)
        except urllib.error.HTTPError as error:
            raw, status, headers = error.read() or b"", error.code, dict(error.headers or {})
        except TimeoutError:
            return outcome("timeout", error_code="client_timeout")
        except (urllib.error.URLError, ConnectionError, OSError) as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, TimeoutError):
                return outcome("timeout", error_code="client_timeout")
            return outcome("transport_error", error_code=type(error).__name__, error_message=_short(type(reason).__name__))
        headers = {str(k).lower(): v for k, v in headers.items()}
        processing = str(headers.get("openai-processing-ms", "")).strip()
        meta = {"http_status": status, "request_id": headers.get("x-request-id"),
                "processing_ms": float(processing) if processing.isdigit() else None}
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        if status != 200:
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            retry = str(headers.get("retry-after", "")).strip()
            retry_s = float(retry) if retry.replace(".", "", 1).isdigit() and 0 < float(retry) <= 120 else None
            if status == 429:
                self._not_before = self.clock() + (retry_s or 20.0)
            return outcome("http_error", error_code=_short(error.get("code") or error.get("type"), 80),
                           error_message=_short(error.get("message")), retry_after_s=retry_s, **meta)
        counts = usage_counts(body.get("usage"))
        self.largest_input = max(self.largest_input, counts["input_tokens"])
        text, refusal = response_text(body)
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        incomplete = body.get("incomplete_details") if isinstance(body.get("incomplete_details"), dict) else {}
        return outcome(
            str(body.get("status") or "unknown"), response_id=body.get("id"), model=body.get("model"), text=text,
            refusal=refusal, incomplete_reason=incomplete.get("reason"),
            error_code=_short(error.get("code"), 80), error_message=_short(error.get("message")),
            cost_usd=cost_usd(body.get("usage")), **counts, **meta)
