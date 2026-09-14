"""Single source of truth for the llama-server argv (§8.6, R21). Both server (display/manifest) and agent
(execution) call `argv_for`; the agent re-derives and compares before launch.

Source-verified against llama.cpp v0.4.0 (5266f24d): --fit [on|off], --gpu-layers N|all|auto,
--cache-ram N, --context-shift/--no-context-shift, --flash-attn [on|off|auto], --offline,
--api-key-file, --cors-origins, --no-cors-credentials, --metrics, --props, --slots, --no-webui.

Configuration is strict: known keys only, exact types (no string/bool coercion), documented bounds, and
fixed-family invariants that cannot be overridden (fit off, context shift off, prompt cache cap 0,
parallel 1)."""

from __future__ import annotations

from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "ctx_size": 2048,
    "n_predict": 128,
    "batch_size": 256,
    "ubatch_size": 128,
    "parallel": 1,
    "gpu_layers": "all",
    "cache_ram_mib": 0,
    "context_shift": False,
    "fit": "off",
    "flash_attn": "auto",
    "temperature": 0.0,
    "seed": 42,
    "health_timeout_s": 120,
    "request_deadline_s": 30,
    "queue_depth": 4,
}
# key -> (type, min, max) ; None means unbounded on that side
BOUNDS: dict[str, tuple[type, Any, Any]] = {
    "ctx_size": (int, 512, 32768),
    "n_predict": (int, 1, 4096),
    "batch_size": (int, 1, 4096),
    "ubatch_size": (int, 1, 4096),
    "parallel": (int, 1, 1),
    "cache_ram_mib": (int, 0, 0),
    "context_shift": (bool, False, False),
    "temperature": (float, 0.0, 2.0),
    "seed": (int, 0, 2**31 - 1),
    "health_timeout_s": (int, 10, 900),
    "request_deadline_s": (int, 1, 300),
    "queue_depth": (int, 1, 16),
}
FIXED_FAMILY = {"fit": "off", "context_shift": False, "cache_ram_mib": 0, "parallel": 1}
SCRUB_ENV_PREFIXES = ("LLAMA_ARG_", "LLAMA_", "HF_", "GGML_", "CUDA_LAUNCH_BLOCKING")
KEEP_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES", "TMPDIR")


class ConfigError(ValueError):
    pass


def canonical_config(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(config or {})
    unknown = sorted(k for k in cfg if k not in DEFAULT_CONFIG and k != "sim")
    if unknown:
        raise ConfigError(f"unknown runtime config keys: {unknown}")
    c = dict(DEFAULT_CONFIG)
    for k, v in cfg.items():
        if k == "sim":
            continue
        c[k] = v
    for k, (typ, lo, hi) in BOUNDS.items():
        v = c[k]
        if typ is float:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ConfigError(f"{k} must be a number")
            v = float(v)
            if v != v or v in (float("inf"), float("-inf")):
                raise ConfigError(f"{k} must be finite")
        elif typ is int:
            if isinstance(v, bool) or not isinstance(v, int):
                raise ConfigError(f"{k} must be an integer")
        elif typ is bool:
            if not isinstance(v, bool):
                raise ConfigError(f"{k} must be a boolean")
        if lo is not None and v < lo or hi is not None and v > hi:
            raise ConfigError(f"{k} must be within [{lo}, {hi}]")
        c[k] = v
    if c["fit"] != "off":
        raise ConfigError("fit is fixed to 'off' in this release family (explicit fixed budget)")
    for k, v in FIXED_FAMILY.items():
        if c[k] != v:
            raise ConfigError(f"{k} is fixed to {v!r} in this release family")
    if c["n_predict"] >= c["ctx_size"]:
        raise ConfigError("n_predict must be < ctx_size")
    if c["ubatch_size"] > c["batch_size"]:
        raise ConfigError("ubatch_size must be <= batch_size")
    gl = c["gpu_layers"]
    if not (gl == "all" or (isinstance(gl, int) and not isinstance(gl, bool) and 0 <= gl <= 999)):
        raise ConfigError("gpu_layers must be 'all' or an integer 0..999")
    if c["flash_attn"] not in ("auto", "on", "off"):
        raise ConfigError("flash_attn must be auto|on|off")
    if "sim" in cfg:
        if not isinstance(cfg["sim"], dict):
            raise ConfigError("sim must be an object")
        c["sim"] = cfg["sim"]
    return c


def argv_for(
    config: dict[str, Any],
    *,
    model_path: str,
    template_path: str | None,
    host: str,
    port: int,
    api_key_file: str | None,
    binary: str = "llama-server",
) -> list[str]:
    c = canonical_config(config)
    argv = [
        binary,
        "--model", model_path,
        "--host", host,
        "--port", str(port),
        "--fit", "off",
        "--parallel", "1",
        "--ctx-size", str(c["ctx_size"]),
        "--n-predict", str(c["n_predict"]),
        "--batch-size", str(c["batch_size"]),
        "--ubatch-size", str(c["ubatch_size"]),
        "--gpu-layers", str(c["gpu_layers"]),
        "--cache-ram", "0",
        "--no-context-shift",
        "--flash-attn", str(c["flash_attn"]),
        "--temp", repr(float(c["temperature"])),
        "--seed", str(c["seed"]),
        "--offline",
        "--verbosity", "4",  # R29: INFO-level ggml lines (load_tensors offload evidence) need verbosity 4 at this commit; prompts are NOT logged (no --verbose-prompt)
        "--jinja",
        "--metrics",
        "--props",
        "--slots",
        "--no-webui",
        "--cors-origins", "",
        "--no-cors-credentials",
    ]  # fmt: skip
    if template_path:
        argv += ["--chat-template-file", template_path]
    if api_key_file:
        argv += ["--api-key-file", api_key_file]
    return argv


def scrub_env(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k in KEEP_ENV}
