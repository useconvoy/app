"""Simulated llama-server: a real loopback HTTP server exposing the subset of endpoints the gateway and
harness use (/health, /props, /slots, /metrics, /apply-template, /tokenize, /completion), with the same
auth (Bearer api key) and deterministic answers plus fault injection. Orchestration evidence only."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

ANSWERS = [
    (("capital", "france"), "Paris"),
    (("capital", "japan"), "Tokyo"),
    (("7", "times", "8"), "56"),
    (("12", "plus", "30"), "42"),
    (("stop",), "STOP"),
    (("json", "dock"), '{"action": "dock"}'),
    (("sky",), "blue"),
    (("thank", "spanish"), "gracias"),
    (("ok",), "OK"),
]
BOS = 151644


def tokenize(text: str, add_special: bool) -> list[int]:
    ids = [BOS] if add_special else []
    for tok in text.replace("\n", " \n ").split(" "):
        if tok == "":
            continue
        ids.append(1000 + int(hashlib.sha256(tok.encode()).hexdigest()[:6], 16) % 150000)
    return ids


def answer_for(prompt: str, faults: dict[str, Any], counter: int) -> str:
    p = prompt.lower()
    wrong_every = int(faults.get("wrong_every", 0) or 0)
    if wrong_every and counter % wrong_every == 0:
        return "I am not sure."
    for keys, ans in ANSWERS:
        if all(k in p for k in keys):
            return ans
    return "The answer is unknown."


class SimRuntime:
    def __init__(
        self,
        *,
        api_key: str,
        n_ctx: int = 2048,
        n_predict: int = 128,
        model_path: str = "sim.gguf",
        template: str | None = None,
        faults: dict[str, Any] | None = None,
        sensors=None,
    ):
        self.api_key = api_key
        self.n_ctx = n_ctx
        self.n_predict = n_predict
        self.model_path = model_path
        self.template = (
            template
            or "{% for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content }}<|im_end|>\n{% endfor %}<|im_start|>assistant\n"
        )
        self.faults = dict(faults or {})
        self.sensors = sensors
        self.requests = 0
        self.busy = threading.Lock()
        self.processing = False
        self.crashed = False
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port = 0
        self.started_monotonic = 0.0

    def start(self) -> int:
        rt = self
        health_delay = float(self.faults.get("health_delay_s", 0.05))

        class H(BaseHTTPRequestHandler):
            server_version = "convoy-sim-llama-server/0.4.0-sim"

            def log_message(self, *a):  # silent
                pass

            def _json(self, code: int, obj: Any):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _auth(self) -> bool:
                if self.headers.get("Authorization") == f"Bearer {rt.api_key}":
                    return True
                self._json(
                    401,
                    {"error": {"message": "Invalid API Key", "type": "authentication_error", "code": 401}},
                )
                return False

            def do_GET(self):
                if rt.crashed:
                    self.close_connection = True
                    return
                if self.path == "/health":
                    if time.monotonic() - rt.started_monotonic < health_delay or rt.faults.get("health_fail"):
                        self._json(503, {"error": {"message": "Loading model", "code": 503}})
                    else:
                        self._json(200, {"status": "ok"})
                    return
                if not self._auth():
                    return
                if self.path == "/props":
                    self._json(
                        200,
                        {
                            "build_info": "b0-sim (5266f24d simulated)",
                            "model_path": rt.model_path,
                            "n_ctx": rt.n_ctx,
                            "total_slots": 1,
                            "chat_template": rt.template,
                            "default_generation_settings": {
                                "n_predict": rt.n_predict,
                                "params": {"seed": 42},
                            },
                            "simulated": True,
                        },
                    )
                elif self.path == "/slots":
                    self._json(200, [{"id": 0, "is_processing": rt.processing, "n_ctx": rt.n_ctx}])
                elif self.path == "/metrics":
                    body = f"# simulated\nllamacpp:requests_processing {1 if rt.processing else 0}\nllamacpp:n_requests {rt.requests}\n".encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                if rt.crashed:
                    self.close_connection = True
                    return
                if not self._auth():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                if n > 1 << 20:
                    self._json(413, {"error": "too large"})
                    return
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    self._json(400, {"error": "bad json"})
                    return
                if self.path == "/apply-template":
                    msgs = body.get("messages") or []
                    if any(not isinstance(m.get("content"), str) for m in msgs):
                        self._json(400, {"error": {"message": "only text content supported by simulator"}})
                        return
                    rendered = (
                        "".join(f"<|im_start|>{m.get('role')}\n{m.get('content')}<|im_end|>\n" for m in msgs)
                        + "<|im_start|>assistant\n"
                    )
                    self._json(200, {"prompt": rendered})
                elif self.path == "/tokenize":
                    self._json(
                        200,
                        {
                            "tokens": tokenize(
                                str(body.get("content", "")), bool(body.get("add_special", False))
                            )
                        },
                    )
                elif self.path == "/completion":
                    self._completion(body)
                else:
                    self._json(404, {"error": "not found"})

            def _stream(self, text, out_ids, ids, lat, t0, truncated):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                words = text.split(" ")
                for i, w in enumerate(words):
                    time.sleep(lat * 0.2 / max(1, len(words)))
                    chunk = {"content": (w if i == 0 else " " + w), "stop": False}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                final = {
                    "content": "",
                    "stop": True,
                    "tokens_evaluated": len(ids),
                    "tokens_predicted": len(out_ids),
                    "truncated": truncated,
                    "stop_type": "limit" if truncated else "eos",
                    "timings": {
                        "prompt_n": len(ids),
                        "prompt_ms": round(lat * 1000 * 0.3, 2),
                        "predicted_n": len(out_ids),
                        "predicted_ms": round((time.monotonic() - t0) * 1000 * 0.7, 2),
                        "predicted_per_second": round(len(out_ids) / max(0.001, lat * 0.7), 2),
                    },
                    "simulated": True,
                }
                self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
                self.wfile.flush()

            def _completion(self, body):
                prompt = body.get("prompt")
                if isinstance(prompt, list):
                    if not all(isinstance(x, int) and 0 <= x < 200000 for x in prompt):
                        self._json(400, {"error": {"message": "Prompt contains invalid tokens", "code": 400}})
                        return
                    ids = prompt
                    text = "<ids>"
                elif isinstance(prompt, str):
                    ids = tokenize(prompt, True)
                    text = prompt
                else:
                    self._json(400, {"error": "prompt required"})
                    return
                n_pred = int(body.get("n_predict", rt.n_predict))
                if len(ids) >= rt.n_ctx or len(ids) + n_pred > rt.n_ctx:
                    self._json(
                        400,
                        {
                            "error": {
                                "message": f"input ({len(ids)} tokens) exceeds the available context size ({rt.n_ctx})",
                                "type": "exceed_context_size_error",
                                "code": 400,
                                "n_prompt_tokens": len(ids),
                                "n_ctx": rt.n_ctx,
                            }
                        },
                    )
                    return
                with rt.busy:
                    rt.processing = True
                    try:
                        rt.requests += 1
                        crash_after = int(rt.faults.get("crash_after_requests", 0) or 0)
                        if crash_after and rt.requests > crash_after:
                            rt.crashed = True
                            self.close_connection = True
                            return
                        if rt.faults.get("hang"):
                            time.sleep(float(rt.faults.get("hang_s", 3600)))
                        lat = float(rt.faults.get("latency_ms", 40)) / 1000.0
                        t0 = time.monotonic()
                        time.sleep(lat)
                        answer = answer_for(rt.last_rendered(ids, text), rt.faults, rt.requests)
                        out_ids = tokenize(answer, False)[:n_pred]
                        truncated = len(tokenize(answer, False)) > n_pred
                        if rt.sensors is not None:
                            rt.sensors.load = 0.9

                        if body.get("stream"):
                            self._stream(
                                answer if not truncated else " ".join(answer.split(" ")[:n_pred]),
                                out_ids,
                                ids,
                                lat,
                                t0,
                                truncated,
                            )

                            return
                        self._json(200, {
                            "content": answer if not truncated else " ".join(answer.split(" ")[:n_pred]),
                            "tokens_evaluated": len(ids), "tokens_predicted": len(out_ids), "truncated": truncated, "stop_type": "limit" if truncated else "eos",
                            "timings": {"prompt_n": len(ids), "prompt_ms": round(lat * 1000 * 0.3, 2), "predicted_n": len(out_ids), "predicted_ms": round((time.monotonic() - t0) * 1000 * 0.7, 2), "predicted_per_second": round(len(out_ids) / max(0.001, lat * 0.7), 2)},
                            "simulated": True,
                        })  # fmt: skip
                    finally:
                        rt.processing = False
                        if rt.sensors is not None:
                            rt.sensors.load = 0.2

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, name="sim-runtime", daemon=True)
        self.thread.start()
        self.started_monotonic = time.monotonic()
        self._prompts: dict[int, str] = {}
        return self.port

    def last_rendered(self, ids: list[int], text: str) -> str:
        # the simulator keeps a map from token-id digests to rendered prompts so id-array prompts can be answered
        key = hash(tuple(ids))
        if text != "<ids>":
            self._prompts[key] = text
            return text
        return self._prompts.get(key, "")

    def remember(self, rendered: str) -> None:
        self._prompts[hash(tuple(tokenize(rendered, True)))] = rendered

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=5)
            self.thread = None
