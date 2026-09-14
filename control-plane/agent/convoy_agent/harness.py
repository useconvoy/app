"""On-device eval harness: runs a frozen plan through the gateway in eval mode, records per-case
structured evidence, request timings (monotonic), sensor samples, and the runtime evidence; scores with
the shared evaluator. Never trusts itself: the server recomputes from the same structured records."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from . import __version__
from .evaluator import EVALUATOR_VERSION, evaluate, summarize
from .gateway import Gateway
from .runtime import http_json
from .scoring import EVIDENCE_METHOD, SCORER_VERSION, score_case

SENSOR_SAMPLE_CAP = 720  # bound at source: the summary is computed from EXACTLY the transmitted list
TIMING_KEYS = ("case_id", "status", "latency_ms", "ttft_ms", "queue_ms", "tok_s")
SENSOR_KEYS = (
    "ts",
    "mem_used_mb",
    "mem_available_mb",
    "temp_max_c",
    "power_w",
    "gpu_pct",
    "clock_confidence",
)


class Harness:
    def __init__(self, gateway: Gateway, sensors, *, device_id: str, simulated: bool):
        self.gw = gateway
        self.sensors = sensors
        self.device_id = device_id
        self.simulated = simulated

    def _call(
        self, prompt: str, max_tokens: int, temperature: float, seed: int, timeout_s: float
    ) -> tuple[str, dict[str, Any], str | None, dict[str, Any] | None]:
        t0 = time.monotonic()
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "seed": seed,
        }
        try:
            code, out = _post_gateway(self.gw, body, timeout_s)
        except TimeoutError:
            return (
                "timeout",
                {"status": "timeout", "latency_ms": round((time.monotonic() - t0) * 1000, 2)},
                None,
                None,
            )
        except Exception as e:
            return (
                "error",
                {
                    "status": "error",
                    "latency_ms": round((time.monotonic() - t0) * 1000, 2),
                    "error": type(e).__name__,
                },
                None,
                None,
            )
        lat = round((time.monotonic() - t0) * 1000, 2)
        if code != 200 or not isinstance(out, dict) or not out.get("choices"):
            err = (out.get("error") or {}).get("type") if isinstance(out, dict) else None
            return "error", {"status": "error", "latency_ms": lat, "error": err or f"http_{code}"}, None, None
        cv = out.get("convoy") or {}
        timings = cv.get("timings") or {}
        usage = out.get("usage") or {}
        sample = {
            "status": "ok", "latency_ms": lat, "queue_ms": cv.get("queue_ms"), "ttft_ms": cv.get("ttft_ms"),  # R28-fu: measured first-token time only (None when the runtime did not stream), never prompt_ms
            "tokens_in": usage.get("prompt_tokens"), "tokens_out": usage.get("completion_tokens"), "tok_s": timings.get("predicted_per_second"),
            "trace_id": cv.get("trace_id"), "finish_reason": out["choices"][0].get("finish_reason"),
        }  # fmt: skip
        return "ok", sample, out["choices"][0]["message"]["content"], cv

    def run(
        self,
        plan: dict[str, Any],
        *,
        release_id: str,
        release_digest: str,
        runtime_evidence: dict[str, Any],
        operation_id: str | None,
        stage: str = "eval",
        cancel: threading.Event | None = None,
        raw_outputs: bool = False,
        generation: int | None = None,
    ) -> dict[str, Any]:
        es = plan["eval_set"]
        cases = list(es["cases"])
        wl = plan.get("workload") or {}
        max_tokens = int(wl.get("max_tokens", 128))
        temperature = float(wl.get("temperature", 0.0))
        seed = int(wl.get("seed", 42))
        timeout_s = float(wl.get("request_timeout_s", 30))
        warmup = int(wl.get("warmup", 1))
        sensor_samples: list[dict[str, Any]] = []
        stop = threading.Event()
        cadence = {"s": 0.5}

        def sampler():
            while not stop.is_set():
                try:
                    s = self.sensors.sample("running")
                    sensor_samples.append(
                        {
                            "ts": time.time(),
                            "mem_used_mb": (s["mem_total_mb"] - s["mem_available_mb"])
                            if (s.get("mem_total_mb") is not None and s.get("mem_available_mb") is not None)
                            else None,
                            "mem_available_mb": s.get("mem_available_mb"),
                            "temp_max_c": s.get("temp_max_c"),
                            "power_w": s.get("power_w"),
                            "gpu_pct": s.get("gpu_pct"),
                            "clock_confidence": s.get("clock_confidence"),
                        }
                    )
                    if len(sensor_samples) >= SENSOR_SAMPLE_CAP:
                        # consistent decimation: keep every other sample and halve the cadence
                        del sensor_samples[1::2]
                        cadence["s"] *= 2
                except Exception:
                    pass
                stop.wait(cadence["s"])

        th = threading.Thread(target=sampler, daemon=True)
        th.start()
        t_start = time.monotonic()
        started_at = time.time()
        for _ in range(warmup):
            if cases:
                self._call(cases[0]["prompt"], min(8, max_tokens), temperature, seed, timeout_s)
        records: list[dict[str, Any]] = []
        timings: list[dict[str, Any]] = []
        raw: list[dict[str, Any]] = []
        cancelled = False
        for case in cases:  # fixed ordering
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            status, sample, output, cv = self._call(
                case["prompt"],
                min(int(case.get("max_tokens", max_tokens)), max_tokens),
                temperature,
                seed,
                timeout_s,
            )
            sample["case_id"] = case["id"]
            timings.append(sample)
            rec = score_case(case, output, status)
            rec["trace_id"] = sample.get("trace_id")
            rec["latency_ms"] = sample.get("latency_ms")
            records.append(rec)
            if raw_outputs and output is not None:
                raw.append({"id": case["id"], "output": output[:4096]})
        stop.set()
        th.join(timeout=2)
        complete = (not cancelled) and len(records) == len(cases)
        # the transmitted lists ARE the summary inputs (the server recomputes from them and must agree)
        wire_timings = [{k: t.get(k) for k in TIMING_KEYS} for t in timings]
        wire_sensors = [{k: x.get(k) for k in SENSOR_KEYS} for x in sensor_samples[:SENSOR_SAMPLE_CAP]]
        runtime_props = {
            "gpu_offloaded_layers": runtime_evidence.get("gpu_offloaded_layers"),
            "backend": runtime_evidence.get("backend"),
        }
        summary = summarize(
            records, wire_timings, wire_sensors, runtime_props=runtime_props, expected_cases=len(cases)
        )
        verdict, report = evaluate(summary, plan.get("gates") or [], complete=complete, cancelled=cancelled)
        evidence_methods = {g["metric"]: g.get("evidence") for g in (plan.get("gates") or [])}
        result_id = (
            "evr_"
            + hashlib.sha256(
                f"{self.device_id}{operation_id}{started_at}{plan['digest']}".encode()
            ).hexdigest()[:12]
        )
        return {
            "id": result_id,
            "device_id": self.device_id,
            "operation_id": operation_id,
            "generation": generation,
            "release_id": release_id,
            "release_digest": release_digest,
            "plan_id": plan["id"],
            "plan_digest": plan["digest"],
            "eval_set_id": es["id"],
            "eval_set_digest": es["digest"],
            "stage": stage,
            "device_verdict": verdict,
            "gates": report,
            "summary": summary,
            "cases": records,
            "timings": wire_timings,
            "sensor_samples": wire_sensors,
            "runtime_props": runtime_props,
            "coverage": {
                "generation": generation,
                "expected": len(cases),
                "scored": len(records),
                "completed": summary["coverage"]["completed"],
                "cancelled": cancelled,
                "complete": complete,
            },
            "evidence_methods": evidence_methods,
            "scorer_methods": {c["id"]: EVIDENCE_METHOD.get(c.get("match", "exact")) for c in cases},
            "raw_outputs": raw if raw_outputs else None,
            "provenance": {
                "agent_version": __version__,
                "evaluator_version": EVALUATOR_VERSION,
                "scorer_version": SCORER_VERSION,
                "runtime": runtime_evidence,
                "started_at": started_at,
                "duration_s": round(time.monotonic() - t_start, 3),
                "sensor_samples": len(wire_sensors),
                "sensor_samples_collected": len(sensor_samples),
                "sensor_cadence_s": cadence["s"],
                "simulated": self.simulated,
                "clock_confidence": wire_sensors[0]["clock_confidence"] if wire_sensors else "unknown",
                "workload": wl,
            },  # fmt: skip
            "simulated": self.simulated,
        }


def _post_gateway(gw: Gateway, body: dict[str, Any], timeout_s: float) -> tuple[int, Any]:
    """Call the gateway over loopback HTTP with the eval token (production admission stays closed)."""
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{gw.port}/v1/chat/completions",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-Convoy-Eval-Token": gw.eval_token},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except (TimeoutError, OSError) as e:
        if "timed out" in str(e).lower() or isinstance(e, TimeoutError):
            raise TimeoutError() from e
        raise


_ = http_json
