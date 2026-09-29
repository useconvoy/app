"""Loopback inference gateway (§8.6, R24, R28, R29): bounded OpenAI-compatible chat completions.

* binds 127.0.0.1 only; modes closed | eval | production; every set_mode() starts a new admission
  epoch and requests queued under an older epoch are rejected when they reach the slot
* browser bypass hardening: non-empty Origin -> 403; inference writes must be application/json (415);
  bodies bounded (413); no CORS preflight approval; socket read timeout; connection cap
* admission: render once via /apply-template, tokenize with add_special=true & parse_special=true,
  require len(ids) < n_ctx and len(ids) + max_tokens <= n_ctx (max_tokens >= 1), then submit the exact
  ids to /completion; limits are captured from the runtime that owns the slot; the runtime generation
  must be unchanged across render -> tokenize -> generate
* concurrency 1, queue depth 4 (503 on overload), per-request deadline; ownership held until the
  runtime confirms idle (/slots) or the child is stopped/restarted before the next request
* TTFT is measured from the first streamed token (internal streaming, non-streaming external result);
  when the runtime does not stream, TTFT is reported unavailable, never approximated
* every request (served, rejected, timed out, failed) emits a correlated span with its reason"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .runtime import RuntimeSupervisor, http_json

log = logging.getLogger("convoy.gateway")

MAX_BODY = 256 * 1024
UNSUPPORTED = (
    "tools",
    "tool_choice",
    "functions",
    "function_call",
    "response_format",
    "chat_template_kwargs",
    "reasoning",
    "reasoning_format",
    "reasoning_effort",
    "logit_bias",
    "n",
    "modalities",
    "audio",
)
MODEL_NAME = "convoy-active"


class Gateway:
    def __init__(
        self,
        supervisor: RuntimeSupervisor,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        queue_depth: int = 4,
        deadline_s: float = 30.0,
        on_span: Callable[[dict[str, Any]], None] | None = None,
        eval_token: str | None = None,
        max_connections: int = 16,
        on_request_done: Callable[[], None] | None = None,
    ):
        self.sup = supervisor
        self.incarnation = secrets.token_hex(16)
        self.implementation_sha256 = {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("gateway.py", "runtime.py")
        }
        self.implementation_sha256.update(supervisor.ownership_fingerprints())
        self.host = host
        self.port = port
        self.queue_depth = queue_depth
        self.deadline_s = deadline_s
        self.on_span = on_span
        self.on_request_done = on_request_done  # fires AFTER the slot interval is accounted
        self.slot_busy_since_wall: float | None = None  # wall instant the single slot was taken
        self.mode = "closed"
        self.epoch = 0
        self.eval_token = eval_token or secrets.token_urlsafe(16)
        self._slot = threading.Lock()
        self._waiting = 0
        self._waiting_lock = threading.Lock()
        self._conn_sem = threading.BoundedSemaphore(max_connections)
        # requests currently inside a handler (admission, queue wait, generation, accounting callback):
        # shutdown waits for this to reach zero before the journal is closed, so every answered
        # request's per-request checkpoint lands while the journal is open
        self._inflight = 0
        self._inflight_cv = threading.Condition()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.stats = {
            "requests": 0,
            "served": 0,
            "rejected": 0,
            "overloaded": 0,
            "timeouts": 0,
            "failed": 0,
            "served_tokens_out": 0,
            "served_tokens_in": 0,
            "restarts": 0,
            "inference_s": 0.0,  # deduplicated wall time the single inference slot was busy (all outcomes)
        }
        self.needs_restart = False

    def set_mode(self, mode: str) -> None:
        assert mode in ("closed", "eval", "production")
        self.mode = mode
        self.epoch += 1

    # ---- server ----
    def start(self) -> int:
        gw = self

        class H(BaseHTTPRequestHandler):
            server_version = "convoy-gateway/0.1"
            timeout = 10  # socket read timeout for headers and body (R29)

            def log_message(self, *a):
                pass

            def handle(self):
                if not gw._conn_sem.acquire(blocking=False):
                    try:
                        self.send_response(503)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                    except Exception:
                        pass
                    return
                with gw._inflight_cv:
                    gw._inflight += 1
                try:
                    super().handle()
                finally:
                    gw._conn_sem.release()
                    with gw._inflight_cv:
                        gw._inflight -= 1
                        gw._inflight_cv.notify_all()

            def _json(self, code: int, obj: Any, extra: dict[str, str] | None = None):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self):  # no CORS preflight approval, ever
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                if self.path == "/v1/runtime-identity":
                    try:
                        self._json(200, gw.runtime_identity(check_health=True))
                    except Exception:
                        self._json(503, _err("runtime identity unavailable", "unavailable"))
                elif self.path == "/health":
                    self._json(
                        200,
                        {
                            "gateway": "ok",
                            "mode": gw.mode,
                            "epoch": gw.epoch,
                            "runtime_state": gw.sup.state(),
                            "release_id": gw.sup.release_id,
                            "n_ctx": gw.sup.config.get("ctx_size"),
                            "n_predict": gw.sup.config.get("n_predict"),
                            "queue_depth": gw.queue_depth,
                            "stats": gw.stats,
                        },
                    )
                elif self.path == "/v1/models":
                    self._json(
                        200,
                        {
                            "object": "list",
                            "data": [
                                {
                                    "id": MODEL_NAME,
                                    "object": "model",
                                    "owned_by": "convoy",
                                    "release_id": gw.sup.release_id,
                                }
                            ],
                        },
                    )
                else:
                    self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

            def do_POST(self):
                if self.path not in ("/v1/chat/completions", "/v1/bound-completions"):
                    self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})
                    return
                trace_id = "tr_" + secrets.token_hex(8)
                t_arrive = time.monotonic()
                if self.headers.get("Origin"):
                    gw._reject(trace_id, t_arrive, "cross_origin")
                    self._json(
                        403,
                        {
                            "error": {
                                "message": "cross-origin requests are not accepted by the gateway",
                                "type": "forbidden",
                            }
                        },
                        {"X-Convoy-Trace-Id": trace_id},
                    )
                    return
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    gw._reject(trace_id, t_arrive, "content_type")
                    self._json(
                        415,
                        {
                            "error": {
                                "message": "Content-Type must be application/json",
                                "type": "invalid_request_error",
                            }
                        },
                        {"X-Convoy-Trace-Id": trace_id},
                    )
                    return
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > MAX_BODY:
                    gw._reject(trace_id, t_arrive, "body_size")
                    self._json(
                        413 if n > MAX_BODY else 400,
                        {"error": {"message": "body missing or too large", "type": "invalid_request_error"}},
                        {"X-Convoy-Trace-Id": trace_id},
                    )
                    return
                try:
                    body = json.loads(self.rfile.read(n))
                except (json.JSONDecodeError, UnicodeDecodeError, TimeoutError, OSError):
                    gw._reject(trace_id, t_arrive, "invalid_json")
                    self._json(
                        400,
                        {"error": {"message": "invalid JSON", "type": "invalid_request_error"}},
                        {"X-Convoy-Trace-Id": trace_id},
                    )
                    return
                try:
                    if self.path == "/v1/bound-completions":
                        if not isinstance(body, dict) or set(body) != {"request", "runtime_identity", "deadline_s"}:
                            self._json(400, _err("invalid bound completion envelope", "invalid_request_error"))
                            return
                        if (type(body["deadline_s"]) not in (int, float) or
                                not 0 < body["deadline_s"] <= 30 or not isinstance(body["runtime_identity"], dict)):
                            self._json(400, _err("invalid bound completion deadline or identity", "invalid_request_error"))
                            return
                        code, obj = gw.handle(body["request"], None, trace_id, t_arrive,
                                              deadline_s=body["deadline_s"],
                                              expected_runtime_identity=body["runtime_identity"])
                    else:
                        code, obj = gw.handle(body, self.headers.get("X-Convoy-Eval-Token"), trace_id, t_arrive)
                except Exception as e:  # R32: structured error boundary; never drop the connection
                    gw.stats["failed"] += 1
                    gw._span(trace_id, "failed", t_arrive, {"reason": f"internal_{type(e).__name__}"})
                    code, obj = (
                        500,
                        {
                            "error": {
                                "message": f"gateway internal error ({type(e).__name__})",
                                "type": "server_error",
                            }
                        },
                    )
                self._json(code, obj, {"X-Convoy-Trace-Id": trace_id})

        self.server = ThreadingHTTPServer((self.host, self.port), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, name="convoy-gateway", daemon=True)
        self.thread.start()
        return self.port

    def stop(self) -> None:
        """No new connections. Handlers already running keep going (one request per connection,
        HTTP/1.0): use `drain()` to wait for them."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    @property
    def inflight(self) -> int:
        with self._inflight_cv:
            return self._inflight

    def drain(self, timeout_s: float) -> bool:
        """Wait (bounded) until no request handler is running; True when quiescent."""
        with self._inflight_cv:
            return self._inflight_cv.wait_for(lambda: self._inflight == 0, timeout=max(0.0, timeout_s))

    # ---- spans ----
    def _span(self, trace_id: str, status: str, t_arrive: float, attrs: dict[str, Any]) -> None:
        if not self.on_span:
            return
        t_end = time.monotonic()
        try:
            self.on_span({
                "trace_id": trace_id, "span_id": secrets.token_hex(6), "name": "gateway.chat_completion", "kind": "inference", "status": status,
                "start_ts": time.time() - (t_end - t_arrive), "duration_ms": round((t_end - t_arrive) * 1000, 2),
                "attrs": {"mode": self.mode, "release_id": self.sup.release_id, **attrs},
            })  # fmt: skip
        except Exception:
            pass

    def _reject(self, trace_id: str, t_arrive: float, reason: str) -> None:
        self.stats["requests"] += 1
        self.stats["rejected"] += 1
        self._span(trace_id, "rejected", t_arrive, {"reason": reason})
        self._request_done()

    def _request_done(self) -> None:
        """Every terminal answered request that `stats["requests"]` counted (served, rejected before or
        after the slot, refused by admission, overloaded, timed out) reaches the durable per-request
        accounting exactly once, after its counters and any slot interval are accounted."""
        if self.on_request_done:
            try:
                self.on_request_done()
            except Exception:
                log.exception("request completion callback failed")

    def _admit(self, eval_token: str | None) -> tuple[int, dict[str, Any]] | None:
        if self.mode == "closed":
            return 503, _err("gateway closed: runtime cutover or recovery in progress", "unavailable")
        if self.mode == "eval" and eval_token != self.eval_token:
            return 503, _err("gateway in eval mode: production admission closed", "unavailable")
        if self.mode == "production" and eval_token:
            return 403, _err("eval token not valid in production mode", "forbidden")
        return None

    def handle_relay(
        self, body: dict[str, Any], *, expected_release_id: str, deadline_s: float = 30.0
    ) -> tuple[int, dict[str, Any]]:
        """Use ordinary production admission and account this worker during shutdown drain.

        Release binding is checked while holding the inference slot, not just before enqueueing.
        The caller cannot choose a runtime URL, eval token, or exceed the gateway deadline.
        """
        with self._inflight_cv:
            self._inflight += 1
        try:
            return self.handle(
                body,
                None,
                "tr_" + secrets.token_hex(8),
                expected_release_id=expected_release_id,
                deadline_s=deadline_s,
            )
        finally:
            with self._inflight_cv:
                self._inflight -= 1
                self._inflight_cv.notify_all()

    # ---- request handling ----
    def runtime_identity(self, *, check_health: bool = False) -> dict[str, Any]:
        if self.mode != "production" or self.needs_restart:
            raise RuntimeError("runtime is not serving production requests")
        epoch = self.epoch
        value = self.sup.provenance()
        if check_health and not self.sup.health():
            raise RuntimeError("native runtime is not healthy")
        if (epoch != self.epoch or self.mode != "production" or
                value["runtime_generation"] != self.sup.generation):
            raise RuntimeError("runtime admission changed during identity read")
        return {**value, "gateway_incarnation": self.incarnation, "gateway_epoch": epoch,
                "implementation_sha256": self.implementation_sha256.copy()}

    def handle(
        self,
        body: dict[str, Any],
        eval_token: str | None,
        trace_id: str,
        t_arrive: float | None = None,
        *,
        expected_release_id: str | None = None,
        deadline_s: float | None = None,
        expected_runtime_identity: dict | None = None,
    ) -> tuple[int, dict[str, Any]]:
        t_arrive = t_arrive if t_arrive is not None else time.monotonic()
        self.stats["requests"] += 1
        try:
            return self._handle(body, eval_token, trace_id, t_arrive, expected_release_id, deadline_s,
                                expected_runtime_identity)
        finally:
            self._request_done()  # after the slot (if taken) was released and its interval accounted

    def _handle(
        self,
        body: dict[str, Any],
        eval_token: str | None,
        trace_id: str,
        t_arrive: float,
        expected_release_id: str | None = None,
        deadline_s: float | None = None,
        expected_runtime_identity: dict | None = None,
    ) -> tuple[int, dict[str, Any]]:
        def reject(
            code: int, msg: str, typ: str = "invalid_request_error", reason: str | None = None
        ) -> tuple[int, dict[str, Any]]:
            self.stats["rejected"] += 1
            self._span(trace_id, "rejected", t_arrive, {"reason": reason or typ})
            return code, _err(msg, typ)

        if not isinstance(body, dict):
            return reject(400, "body must be an object")
        pre = self._admit(eval_token)
        if pre:
            self._span(trace_id, "rejected", t_arrive, {"reason": pre[1]["error"]["type"]})
            self.stats["rejected"] += 1
            return pre
        epoch_at_arrival = self.epoch
        for k in UNSUPPORTED:
            if k in body:
                return reject(400, f"unsupported feature: {k}", "unsupported_feature")
        if body.get("stream"):
            return reject(400, "streaming is not supported by this gateway", "unsupported_feature")
        msgs = body.get("messages")
        if not isinstance(msgs, list) or not msgs or len(msgs) > 64:
            return reject(400, "messages must be a non-empty list (<=64)")
        for m in msgs:
            if (
                not isinstance(m, dict)
                or m.get("role") not in ("system", "user", "assistant")
                or not isinstance(m.get("content"), str)
            ):
                return reject(
                    400,
                    "only text messages with role system|user|assistant are supported",
                    "unsupported_feature",
                )
        max_tokens = body.get("max_tokens")
        if max_tokens is not None and (
            not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1
        ):
            return reject(400, "max_tokens must be a positive integer")
        temperature = body.get("temperature", self.sup.config.get("temperature", 0.0))
        seed = body.get("seed", self.sup.config.get("seed", 42))
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not (0 <= float(temperature) <= 2)
        ):
            return reject(400, "temperature must be 0..2")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            return reject(400, "seed must be a non-negative integer")
        # queue admission
        with self._waiting_lock:
            if self._waiting >= self.queue_depth:
                self.stats["overloaded"] += 1
                self._span(trace_id, "overloaded", t_arrive, {"reason": "queue_full"})
                return 503, _err("gateway overloaded (queue full)", "overloaded")
            self._waiting += 1
        try:
            deadline = t_arrive + min(
                self.deadline_s, deadline_s if deadline_s is not None else self.deadline_s
            )
            acquired = (self._slot.acquire(blocking=False) if expected_runtime_identity is not None else
                        self._slot.acquire(timeout=max(0.0, deadline - time.monotonic())))
            if not acquired:
                self.stats["timeouts"] += 1
                self._span(trace_id, "timeout", t_arrive, {"reason": "slot_wait_deadline"})
                return 503, _err("timed out waiting for the inference slot", "overloaded")
        finally:
            with self._waiting_lock:
                self._waiting -= 1
        t_start = time.monotonic()
        self.slot_busy_since_wall = time.time()
        queue_ms = round((t_start - t_arrive) * 1000, 2)
        try:
            # R24: re-check admission after acquiring the slot; a mode change while queued invalidates
            post = self._admit(eval_token)
            if post or self.epoch != epoch_at_arrival:
                self.stats["rejected"] += 1
                self._span(
                    trace_id,
                    "rejected",
                    t_arrive,
                    {"reason": "admission_changed_while_queued", "queue_ms": queue_ms},
                )
                return post or (503, _err("admission changed while the request was queued", "unavailable"))
            if self.needs_restart or self.sup.state() != "running":
                self.stats["failed"] += 1
                self._span(
                    trace_id, "failed", t_arrive, {"reason": "runtime_unavailable", "queue_ms": queue_ms}
                )
                return 503, _err("runtime unavailable", "unavailable")
            if expected_release_id is not None and self.sup.release_id != expected_release_id:
                return reject(409, "active release changed before inference", "unavailable")
            admitted_identity = None
            if expected_runtime_identity is not None:
                admitted_identity = self.runtime_identity()
                if admitted_identity != expected_runtime_identity:
                    return reject(409, "loaded runtime identity changed before inference", "unavailable")
            # limits and identity captured from the runtime that owns the slot NOW
            gen = self.sup.generation
            base, key = self.sup.base_url, self.sup.api_key
            n_ctx = int(self.sup.config.get("ctx_size", 2048))
            n_predict_cap = int(self.sup.config.get("n_predict", 128))
            if max_tokens is None:
                max_tokens = n_predict_cap
            if max_tokens > n_predict_cap:
                return reject(400, f"max_tokens must be an integer in 1..{n_predict_cap}")
            remaining = deadline - time.monotonic()
            code, rendered = http_json(
                f"{base}/apply-template", key, {"messages": msgs}, timeout=max(1.0, min(10.0, remaining))
            )
            if code != 200 or not isinstance(rendered, dict) or not isinstance(rendered.get("prompt"), str):
                self.stats["failed"] += 1
                self._span(
                    trace_id, "failed", t_arrive, {"reason": f"template_render_{code}", "queue_ms": queue_ms}
                )
                return 502, _err(f"template render failed ({code})", "runtime_error")
            prompt = rendered["prompt"]
            if getattr(self.sup, "sim", None) is not None:
                self.sup.sim.remember(prompt)
            code, tok = http_json(
                f"{base}/tokenize",
                key,
                {"content": prompt, "add_special": True, "parse_special": True},
                timeout=max(1.0, min(10.0, deadline - time.monotonic())),
            )
            if code != 200 or not isinstance(tok, dict) or not isinstance(tok.get("tokens"), list):
                self.stats["failed"] += 1
                self._span(trace_id, "failed", t_arrive, {"reason": f"tokenize_{code}", "queue_ms": queue_ms})
                return 502, _err(f"tokenize failed ({code})", "runtime_error")
            ids = tok["tokens"]
            if len(ids) >= n_ctx or len(ids) + max_tokens > n_ctx:
                self.stats["rejected"] += 1
                self._span(
                    trace_id,
                    "rejected",
                    t_arrive,
                    {"reason": "context_length_exceeded", "tokens_in": len(ids), "queue_ms": queue_ms},
                )
                return 400, _err(
                    f"prompt ({len(ids)} tokens) + max_tokens ({max_tokens}) exceeds the context window ({n_ctx})",
                    "context_length_exceeded",
                )
            if self.sup.generation != gen or self.epoch != epoch_at_arrival:
                self.stats["failed"] += 1
                self._span(
                    trace_id,
                    "failed",
                    t_arrive,
                    {"reason": "runtime_changed_during_admission", "queue_ms": queue_ms},
                )
                return 503, _err("runtime changed during admission", "unavailable")
            req = {
                "prompt": ids,
                "n_predict": max_tokens,
                "temperature": float(temperature),
                "seed": int(seed),
                "cache_prompt": False,
            }
            if (
                isinstance(body.get("stop"), list)
                and all(isinstance(s, str) for s in body["stop"])
                and len(body["stop"]) <= 4
            ):
                req["stop"] = body["stop"]
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                self.stats["timeouts"] += 1
                self._span(
                    trace_id,
                    "timeout",
                    t_arrive,
                    {"reason": "deadline_before_generation", "queue_ms": queue_ms, "tokens_in": len(ids)},
                )
                return 504, _err("deadline exceeded before generation", "timeout")
            try:
                code, out, ttft_ms = _completion_with_ttft(base, key, req, remaining)
            except (
                StreamIncomplete
            ) as e:  # R82: EOF without a terminal native event is a failure, never success
                self.stats["failed"] += 1
                self._confirm_idle_or_restart()  # ownership is retained until idle is proven
                self._span(
                    trace_id,
                    "failed",
                    t_arrive,
                    {
                        "reason": "RUNTIME_STREAM_INCOMPLETE",
                        "queue_ms": queue_ms,
                        "tokens_in": len(ids),
                        "tokens_streamed": e.tokens_streamed,
                        "needs_restart": self.needs_restart,
                    },
                )
                return 502, _err(
                    "runtime stream ended without a terminal event (RUNTIME_STREAM_INCOMPLETE)",
                    "runtime_error",
                )
            except Exception as e:  # timeout or connection loss: ownership stays until idle is confirmed
                self.stats["timeouts"] += 1
                self._confirm_idle_or_restart()
                self._span(
                    trace_id,
                    "timeout",
                    t_arrive,
                    {
                        "reason": getattr(e, "cause_name", type(e).__name__),
                        "queue_ms": queue_ms,
                        "tokens_in": len(ids),
                        # locally observed generated tokens before the failure (0 = none arrived; >=1
                        # proves native generation was active); None only when the count is unknown
                        "tokens_streamed": getattr(e, "tokens_streamed", None),
                        "needs_restart": self.needs_restart,
                    },
                )
                return 504, _err(
                    f"generation timed out or the runtime dropped the connection ({type(e).__name__})",
                    "timeout",
                )
            if self.sup.generation != gen:
                self.stats["failed"] += 1
                self._span(
                    trace_id,
                    "failed",
                    t_arrive,
                    {"reason": "runtime_changed_during_generation", "queue_ms": queue_ms},
                )
                return 503, _err("runtime changed during generation", "unavailable")
            if admitted_identity is not None and (self.runtime_identity() != admitted_identity or
                                                 time.monotonic() > deadline):
                return reject(503, "runtime identity or original deadline changed during generation", "unavailable")
            if code != 200 or not isinstance(out, dict):
                self.stats["failed"] += 1
                if (
                    isinstance(out, dict)
                    and (out.get("error") or {}).get("type") == "exceed_context_size_error"
                ):
                    self._span(
                        trace_id,
                        "rejected",
                        t_arrive,
                        {"reason": "context_length_exceeded", "queue_ms": queue_ms},
                    )
                    return 400, _err("context length exceeded", "context_length_exceeded")
                self._span(
                    trace_id, "failed", t_arrive, {"reason": f"runtime_error_{code}", "queue_ms": queue_ms}
                )
                return 502, _err(f"runtime error ({code})", "runtime_error")
            t_end = time.monotonic()
            timings = out.get("timings") or {}
            n_in = int(out.get("tokens_evaluated") or timings.get("prompt_n") or len(ids))
            n_out = int(out.get("tokens_predicted") or timings.get("predicted_n") or 0)
            self.stats["served"] += 1
            self.stats["served_tokens_in"] += n_in
            self.stats["served_tokens_out"] += n_out
            latency_ms = round((t_end - t_start) * 1000, 2)
            self._span(trace_id, "ok", t_arrive, {
                "queue_ms": queue_ms, "latency_ms": latency_ms, "ttft_ms": ttft_ms, "prompt_eval_ms": timings.get("prompt_ms"), "predicted_ms": timings.get("predicted_ms"),
                "tokens_in": n_in, "tokens_out": n_out, "tok_s": timings.get("predicted_per_second"), "truncated": bool(out.get("truncated")), "runtime_generation": gen,
            })  # fmt: skip
            content = out.get("content", "")
            return 200, {
                "id": "chatcmpl-" + trace_id, "object": "chat.completion", "created": int(time.time()), "model": MODEL_NAME,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "length" if out.get("truncated") or out.get("stop_type") == "limit" else "stop"}],
                "usage": {"prompt_tokens": n_in, "completion_tokens": n_out, "total_tokens": n_in + n_out},
                "convoy": {"trace_id": trace_id, "release_id": self.sup.release_id, "queue_ms": queue_ms, "latency_ms": latency_ms, "ttft_ms": ttft_ms, "timings": timings, "simulated": bool(out.get("simulated")), **({"runtime_identity": admitted_identity} if admitted_identity is not None else {})},
            }  # fmt: skip
        finally:
            # single-slot ownership interval: the only honest source of "active" (inference) time
            self.stats["inference_s"] = round(self.stats["inference_s"] + (time.monotonic() - t_start), 6)
            self.slot_busy_since_wall = None
            self._slot.release()

    def _confirm_idle_or_restart(self) -> None:
        """Closing our HTTP connection is not proof the runtime stopped working. Poll /slots briefly; if
        idle cannot be confirmed, flag the child for a controlled stop/restart by the agent before any
        further request is accepted."""
        for _ in range(10):
            idle = self.sup.slots_idle()
            if idle:
                return
            time.sleep(0.2)
        self.needs_restart = True
        self.stats["restarts"] += 1


class StreamIncomplete(Exception):
    """The native event stream ended (clean EOF) before a terminal event (`stop: true`, `[DONE]` or a
    finish reason). Partial content is never served as a completion (R82)."""

    def __init__(self, tokens_streamed: int):
        super().__init__("stream ended without a terminal event")
        self.tokens_streamed = tokens_streamed


class StreamAborted(Exception):
    """The native request failed at the transport level (socket timeout, connection reset, read error)
    after `tokens_streamed` generated tokens had already arrived (0 when the failure preceded any
    token). The count is the locally observed progress, never an estimate."""

    def __init__(self, tokens_streamed: int, cause: BaseException):
        super().__init__(f"{type(cause).__name__}: {str(cause)[:200]}")
        self.tokens_streamed = tokens_streamed
        self.cause_name = type(cause).__name__


class StreamDeadline(TimeoutError):
    """The per-request deadline elapsed while the runtime was streaming: `tokens_streamed` generated
    tokens had already arrived, which is positive evidence that native generation was active."""

    def __init__(self, tokens_streamed: int):
        super().__init__("stream deadline")
        self.tokens_streamed = tokens_streamed


def _completion_with_ttft(
    base: str, key: str | None, req: dict[str, Any], timeout: float
) -> tuple[int, Any, float | None]:
    """R28: TTFT measured at the first streamed generated token; the external result stays non-streaming.
    If the runtime answers without an event stream, TTFT is None (unavailable), never approximated."""
    data = json.dumps({**req, "stream": True}).encode()
    r = urllib.request.Request(
        f"{base}/completion",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    if key:
        r.add_header("Authorization", f"Bearer {key}")
    t0 = time.monotonic()
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), None
        except Exception:
            return e.code, raw[:200].decode("utf-8", "replace"), None
    except Exception as e:
        raise StreamAborted(0, e) from e  # nothing streamed: observed progress is exactly zero
    n_pred = 0
    try:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0]
        if ctype != "text/event-stream":
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {}), None
        ttft: float | None = None
        content: list[str] = []
        final: dict[str, Any] = {}
        terminal = False
        deadline = t0 + timeout
        sock = getattr(getattr(resp, "fp", None), "raw", None)
        sock = getattr(sock, "_sock", None)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StreamDeadline(n_pred)
            if sock is not None:
                try:
                    sock.settimeout(max(0.05, remaining))  # each blocking read is bounded by the budget
                except OSError:
                    pass
            try:
                line = resp.readline()
            except Exception as e:  # transport failure mid-stream: keep the observed progress
                raise StreamAborted(n_pred, e) from e
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            ev: dict[str, Any] | None = None
            done_marker = False
            if text.startswith("data:"):
                payload = text[5:].strip()
                if payload == "[DONE]":
                    done_marker = True
                else:
                    try:
                        parsed = json.loads(payload)
                    except json.JSONDecodeError:
                        parsed = None
                    ev = parsed if isinstance(parsed, dict) else None
            if ev is not None and ev.get("content"):
                if ttft is None:
                    ttft = round((time.monotonic() - t0) * 1000, 2)
                content.append(ev["content"])
                n_pred += 1  # observed progress, even when this line arrived late
            if time.monotonic() > deadline:
                # a terminal (or any) line that lands AFTER the absolute deadline never becomes success
                raise StreamDeadline(n_pred)
            if done_marker:
                terminal = True
                break
            if ev is not None and (ev.get("stop") is True or ev.get("finish_reason") or ev.get("stop_type")):
                final = ev
                terminal = True
                break
        if not terminal:
            raise StreamIncomplete(n_pred)
        final = {**final, "content": "".join(content) if content else final.get("content", "")}
        final.setdefault("tokens_predicted", n_pred)
        return 200, final, ttft
    finally:
        try:
            resp.close()  # every path releases the native connection
        except Exception:
            pass


def _err(msg: str, typ: str = "invalid_request_error") -> dict[str, Any]:
    return {"error": {"message": msg, "type": typ}}
