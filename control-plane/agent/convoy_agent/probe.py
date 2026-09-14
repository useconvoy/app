"""Optional host harness (no Convoy server): drive a REAL llama-server binary through the same supervisor
and gateway the device uses, and report exactly what was observed. Evidence label: whatever host runs
it (e.g. "host CPU build, tiny fixture"); never Qwen quality, never Jetson CUDA."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .gateway import Gateway
from .hardware import Sensors
from .runtime import RuntimeSupervisor, http_json


def _chat(
    port: int, body: dict[str, Any], timeout: float = 60.0, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None


def runtime_probe(
    binary: Path,
    gguf_path: Path,
    *,
    template_file: Path | None = None,
    gpu_layers: str | int = "all",
    ctx: int = 2048,
    n_predict: int = 128,
) -> dict[str, Any]:
    scrubbed = sorted(k for k in os.environ if k.startswith(("LLAMA_ARG_", "LLAMA_SERVER_DEBUG")))
    out: dict[str, Any] = {
        "ok": False,
        "label": f"host probe: {platform.system()} {platform.machine()} — CPU/host evidence only; NOT Convoy qualification, NOT Jetson CUDA",
        "binary": str(binary), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest() if binary.exists() else None,
        "gguf": str(gguf_path), "gguf_sha256": None, "gguf_size": gguf_path.stat().st_size if gguf_path.exists() else None,
        "env_scrubbed_from_parent": scrubbed, "checks": [],
    }  # fmt: skip
    h = hashlib.sha256()
    with open(gguf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    out["gguf_sha256"] = h.hexdigest()
    work = Path(tempfile.mkdtemp(prefix="convoy-probe-"))
    gl = int(gpu_layers) if str(gpu_layers).isdigit() else "all"
    sup = RuntimeSupervisor(work / "rt", simulate=False, sensors=Sensors(str(work)))
    spec = {
        "config": {"ctx_size": ctx, "n_predict": n_predict, "gpu_layers": gl},
        "model": {"total_bytes": out["gguf_size"]},
    }
    gw = None

    def check(name: str, ok: bool, **detail: Any) -> bool:
        out["checks"].append({"name": name, "ok": bool(ok), **detail})
        return ok

    try:
        t0 = time.monotonic()
        ev = sup.start(
            release_id="probe",
            spec=spec,
            model_path=gguf_path,
            template_path=template_file,
            binary=binary,
            lib_dir=binary.parent,
            health_timeout_s=180,
        )
        out["startup_s"] = round(time.monotonic() - t0, 2)
        out["runtime_evidence"] = ev
        out["argv"] = ev.get("argv")
        check("health_ok", True, seconds=out["startup_s"])
        code, _ = http_json(f"{sup.base_url}/props", None)
        check("raw_runtime_requires_api_key", code == 401, status=code)
        code, props = http_json(f"{sup.base_url}/props", sup.api_key)
        check(
            "props_with_key",
            code == 200 and isinstance(props, dict),
            n_ctx=(props or {}).get("n_ctx"),
            build_info=(props or {}).get("build_info"),
        )
        msgs = [
            {"role": "user", "content": "Répondez en un mot: quelle est la capitale de la France? 東京 ✓"}
        ]
        code, rendered = http_json(f"{sup.base_url}/apply-template", sup.api_key, {"messages": msgs})
        check(
            "apply_template_render",
            code == 200 and isinstance(rendered.get("prompt"), str) and "東京" in rendered.get("prompt", ""),
            status=code,
        )
        prompt = rendered.get("prompt", "")
        code, t_default = http_json(f"{sup.base_url}/tokenize", sup.api_key, {"content": prompt})
        code2, t_special = http_json(
            f"{sup.base_url}/tokenize",
            sup.api_key,
            {"content": prompt, "add_special": True, "parse_special": True},
        )
        ids = t_special.get("tokens", [])
        check(
            "tokenize_explicit_specials",
            code == 200 and code2 == 200 and len(ids) >= len(t_default.get("tokens", [])),
            default_count=len(t_default.get("tokens", [])),
            explicit_count=len(ids),
        )
        code, comp = http_json(
            f"{sup.base_url}/completion",
            sup.api_key,
            {"prompt": ids, "n_predict": 8, "temperature": 0.0, "seed": 42, "cache_prompt": False},
            timeout=120,
        )
        n_eval = (comp or {}).get("tokens_evaluated") or ((comp or {}).get("timings") or {}).get("prompt_n")
        check(
            "completion_consumes_exact_ids",
            code == 200 and n_eval == len(ids),
            tokens_evaluated=n_eval,
            ids=len(ids),
            truncated=(comp or {}).get("truncated"),
            predicted=(comp or {}).get("tokens_predicted"),
        )
        big = [ids[0]] + [ids[-1]] * (ctx + 5)
        code, err = http_json(
            f"{sup.base_url}/completion", sup.api_key, {"prompt": big, "n_predict": 8}, timeout=30
        )
        check(
            "oversize_prompt_rejected_by_runtime",
            code == 400,
            status=code,
            error=((err or {}).get("error") or {}).get("type") if isinstance(err, dict) else None,
        )
        gw = Gateway(sup, deadline_s=60)
        port = gw.start()
        gw.set_mode("production")
        code, resp = _chat(port, {"messages": msgs, "max_tokens": 8})
        check(
            "gateway_chat_completion",
            code == 200 and bool((resp or {}).get("choices")),
            status=code,
            usage=(resp or {}).get("usage"),
            convoy=(resp or {}).get("convoy"),
        )
        code, _ = _chat(
            port, {"messages": msgs, "max_tokens": 8}, headers={"Origin": "http://localhost:5173"}
        )
        check("gateway_rejects_cross_origin", code == 403, status=code)
        code, _ = _chat(port, {"messages": msgs, "max_tokens": 8, "tools": []})
        check("gateway_rejects_unsupported_features", code == 400, status=code)
        code, _ = _chat(port, {"messages": [{"role": "user", "content": "x " * (ctx * 2)}], "max_tokens": 8})
        check("gateway_enforces_context_bound", code == 400, status=code)
        code, _ = _chat(port, {"messages": msgs, "max_tokens": n_predict + 1})
        check("gateway_enforces_output_bound", code == 400, status=code)
        gw.deadline_s = 0.05
        code, _ = _chat(
            port,
            {"messages": [{"role": "user", "content": "write a long story " * 20}], "max_tokens": n_predict},
        )
        gw.deadline_s = 60
        check(
            "gateway_deadline_then_ownership_check",
            code in (504, 503),
            status=code,
            needs_restart=gw.needs_restart,
            restarts=gw.stats["restarts"],
        )
        idle = sup.slots_idle()
        check("runtime_idle_after_cancel", idle is True or gw.needs_restart, idle=idle)
        gw.set_mode("closed")
        code, _ = _chat(port, {"messages": msgs, "max_tokens": 4})
        check("gateway_closed_returns_503", code == 503, status=code)
        out["ok"] = all(c["ok"] for c in out["checks"])
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["log_tail"] = sup.log_tail(60)
    finally:
        if gw:
            gw.stop()
        out["stop"] = sup.stop()
        out["log_tail"] = out.get("log_tail") or sup.log_tail(30)
    return out
