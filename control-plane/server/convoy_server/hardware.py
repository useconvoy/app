"""Hardware profiles and the shared-memory budget plan (§8.9, §1 rows 21-23).

A *plan* shows field-level provenance for every number. Unknown inputs stay unknown: a missing live
MemAvailable or an unknown KV estimate never yields `fit`. The old two-model boolean is gone."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

ORIN_NANO_8GB = "jetson-orin-nano-8gb"
SIM_HOST = "simulated-host"


@dataclass
class HardwareProfile:
    id: str
    display: str
    arch: str
    compute_capability: str | None
    robot_reserve_mb: int
    runtime_overhead_mb: int
    margin_mb: int
    disk_margin_mb: int
    notes: list[str] = field(default_factory=list)


PROFILES: dict[str, HardwareProfile] = {
    ORIN_NANO_8GB: HardwareProfile(
        id=ORIN_NANO_8GB,
        display="NVIDIA Jetson Orin Nano 8GB",
        arch="aarch64",
        compute_capability="8.7",
        robot_reserve_mb=1536,
        runtime_overhead_mb=700,
        margin_mb=512,
        disk_margin_mb=1024,
        notes=[
            "Unified memory: CPU, GPU and the robot stack share one pool; total is read from the device, never assumed.",
            "robot_reserve_mb, runtime_overhead_mb and margin_mb are hypotheses pending physical validation; per-device settings override them.",
        ],
    ),  # fmt: skip
    SIM_HOST: HardwareProfile(
        id=SIM_HOST,
        display="Simulated host",
        arch="simulated",
        compute_capability=None,
        robot_reserve_mb=1536,
        runtime_overhead_mb=700,
        margin_mb=512,
        disk_margin_mb=1024,
        notes=["Simulator: orchestration only. Never evidence for Orin Nano CUDA performance."],
    ),  # fmt: skip
}


def profile(profile_id: str) -> HardwareProfile | None:
    return PROFILES.get(profile_id)


def profile_dict(profile_id: str) -> dict[str, Any] | None:
    p = profile(profile_id)
    return asdict(p) if p else None


def _dim(inputs: dict[str, Any], name: str) -> int | None:
    """A dimension only when it is a strict, positive, bounded integer under the SAME rules the device
    applies (convoy_agent.gguf: bool/float/str/negative/zero/out-of-bounds are not dimensions)."""
    from convoy_agent.gguf import DIMENSION_BOUNDS, strict_int

    v = strict_int(inputs.get(name))
    lo, hi = DIMENSION_BOUNDS.get(name, (1, 1 << 31))
    return v if (v is not None and lo <= v <= hi) else None


def kv_cache_mb(inputs: dict[str, Any], ctx: int, parallel: int = 1, kv_bytes: int = 2) -> float | None:
    """2 (K,V) * n_layers * n_kv_heads * head_dim * ctx * bytes per slot; None when any input is missing
    or is not a strict positive bounded integer (an explicit head_dim such as 128 is used as given)."""
    n_layers, n_kv, head_dim = _dim(inputs, "n_layers"), _dim(inputs, "n_kv_heads"), _dim(inputs, "head_dim")
    if n_layers is None or n_kv is None or head_dim is None:
        return None
    return 2 * n_layers * n_kv * head_dim * kv_bytes * ctx * max(1, parallel) / (1024 * 1024)


def compute_buffer_mb(inputs: dict[str, Any], ubatch: int) -> float | None:
    """Logits + activation estimate; None when n_embd is missing or invalid. n_vocab is optional: None
    falls back to the documented 152064 default, but a PRESENT invalid n_vocab is refused."""
    n_embd = _dim(inputs, "n_embd")
    if n_embd is None:
        return None
    raw_vocab = inputs.get("n_vocab")
    vocab = 152064 if raw_vocab is None else _dim(inputs, "n_vocab")
    if vocab is None:
        return None
    logits = vocab * ubatch * 4
    act = n_embd * ubatch * 4 * 16
    return (logits + act) / (1024 * 1024)


LIVE_BUDGET_MAX_AGE_S = 180
KV_BYTES_PER_ELEMENT = 2  # f16 K and V
DEFAULT_UBATCH = 128
DEVICE_OVERRIDABLE = ("robot_reserve_mb", "margin_mb")  # runtime_overhead_mb is the release's/profile's


def effective_budget(
    release_spec: dict[str, Any], hw: HardwareProfile, device_settings: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The single budget policy contract shared by the planner (`plan_budget`) and operation dispatch
    (the agent receives it in the deploy/recover payload as `effective_budget`):

        required = model weights
                 + KV cache   (2 * n_layers * n_kv_heads * head_dim * kv_bytes_per_element * ctx_size * parallel)
                 + compute    (((n_vocab or 152064) * ubatch + n_embd * ubatch * 16) * 4 bytes)
                 + runtime_overhead_mb + robot_reserve_mb + margin_mb

    Provenance order: robot_reserve_mb and margin_mb come from device settings, else the release budget,
    else the hardware profile; runtime_overhead_mb from the release budget, else the profile. Every
    number is an int and its origin is named in `source`."""
    budget = release_spec.get("budget") or {}
    cfg = release_spec.get("config") or {}
    settings = device_settings or {}
    out: dict[str, Any] = {}
    source: dict[str, str] = {}
    for key in ("runtime_overhead_mb", "robot_reserve_mb", "margin_mb"):
        if key in DEVICE_OVERRIDABLE and settings.get(key) is not None:
            out[key], source[key] = int(settings[key]), "device.settings"
        elif budget.get(key) is not None:
            out[key], source[key] = int(budget[key]), "release.budget"
        else:
            out[key], source[key] = int(getattr(hw, key)), "profile"
    out["ubatch_size"] = int(cfg.get("ubatch_size") or DEFAULT_UBATCH)
    out["kv_bytes_per_element"] = KV_BYTES_PER_ELEMENT
    out["source"] = source
    return out


def plan_budget(
    release_spec: dict[str, Any],
    hw: HardwareProfile,
    device_settings: dict[str, Any] | None = None,
    live: dict[str, Any] | None = None,
    *,
    observed_age_s: float | None = None,
) -> dict[str, Any]:
    """Budget breakdown with provenance. `live` carries device-reported mem_total_mb, mem_available_mb,
    disk_free_mb (None when not reported) and `observed_age_s` says how old that observation is.
    The admission verdict is computed only from FRESH measurements (R31); stale measurements are shown
    as a historical estimate with their age and the verdict is `unknown`."""
    device_settings = device_settings or {}
    live = live or {}
    stale = observed_age_s is None or observed_age_s > LIVE_BUDGET_MAX_AGE_S
    model = release_spec.get("model", {})
    cfg = release_spec.get("config", {})
    ctx = int(cfg.get("ctx_size", 2048))
    parallel = int(cfg.get("parallel", 1))
    eff = effective_budget(release_spec, hw, device_settings)
    ubatch = int(eff["ubatch_size"])
    weights_mb = float(model.get("total_bytes", 0)) / (1024 * 1024)
    kv_inputs = (model.get("gguf") or {}).get("kv_estimate_inputs") or model.get("kv_estimate_inputs") or {}
    prov = model.get("gguf_provenance") or {}
    kv_src = "GGUF counts"
    if prov.get("byte_verified") is False and prov.get("method") in (
        "header_range_fetch",
        "operator_supplied",
    ):
        kv_src = (
            f"GGUF counts ({prov['method']}, advisory: not byte-verified; the device verifies at staging)"
        )
    kv = kv_cache_mb(kv_inputs, ctx, parallel, eff["kv_bytes_per_element"])
    comp = compute_buffer_mb(kv_inputs, ubatch)
    overhead = float(eff["runtime_overhead_mb"])
    reserve = float(eff["robot_reserve_mb"])
    margin = float(eff["margin_mb"])
    src = eff["source"]
    items = [
        {
            "item": "model_weights_mb",
            "mb": round(weights_mb, 1),
            "source": "release.model.total_bytes (manifest)",
        },
        {
            "item": "kv_cache_mb",
            "mb": None if kv is None else round(kv, 1),
            "source": f"{kv_src}, ctx={ctx}, parallel={parallel}"
            if kv is not None
            else "unknown: GGUF layer/head counts not recorded on the release; admission is pending byte inspection on the device",
        },
        {
            "item": "compute_buffer_mb",
            "mb": None if comp is None else round(comp, 1),
            "source": f"estimate, ubatch={ubatch}" if comp is not None else "unknown: embedding size missing",
        },
        {
            "item": "runtime_overhead_mb",
            "mb": overhead,
            "source": f"{src['runtime_overhead_mb']} (release.budget or profile default; hypothesis)",
        },
        {
            "item": "robot_reserve_mb",
            "mb": reserve,
            "source": f"{src['robot_reserve_mb']} (device.settings, release.budget or profile default)",
        },
        {
            "item": "margin_mb",
            "mb": margin,
            "source": f"{src['margin_mb']} (device.settings, release.budget or profile default)",
        },
    ]
    unknown = [i["item"] for i in items if i["mb"] is None]
    required = sum(float(i["mb"]) for i in items if i["mb"] is not None)
    mem_total = live.get("mem_total_mb")
    mem_avail = live.get("mem_available_mb")
    disk_free = live.get("disk_free_mb")
    out: dict[str, Any] = {
        "hardware_profile": hw.id,
        "items": items,
        "effective_budget": eff,
        "required_mb_known": round(required, 1),
        "unknown_items": unknown,
        "mem_total_mb": mem_total,
        "mem_available_mb": mem_avail,
        "mem_source": ("device live report" if not stale else "historical device report (stale)")
        if mem_avail is not None
        else "not reported",
        "observed_age_s": None if observed_age_s is None else round(observed_age_s, 1),
        "fresh": (mem_avail is not None and not stale),
        "disk_needed_mb": round(weights_mb + hw.disk_margin_mb, 1),
        "disk_free_mb": disk_free,
        "cutover": "stop_start",
    }
    if mem_avail is None:
        out["verdict"] = "unknown"
        out["reason"] = "live MemAvailable not reported by the device; admission requires a live measurement"
    elif stale:
        out["headroom_mb_historical"] = round(float(mem_avail) - required, 1) if not unknown else None
        out["verdict"] = "unknown"
        out["reason"] = (
            f"last measurement is {round(observed_age_s or 0)}s old (> {LIVE_BUDGET_MAX_AGE_S}s); admission requires a fresh live report and the device re-measures at cutover"
        )
    elif unknown:
        out["headroom_mb_known"] = round(float(mem_avail) - required, 1)
        out["verdict"] = "does_not_fit" if out["headroom_mb_known"] < 0 else "unknown"
        out["reason"] = f"unknown items: {', '.join(unknown)}"
    else:
        out["headroom_mb"] = round(float(mem_avail) - required, 1)
        out["verdict"] = "fit" if out["headroom_mb"] >= 0 else "does_not_fit"
        out["reason"] = None
    out["disk_ok"] = None if disk_free is None else float(disk_free) >= out["disk_needed_mb"]
    if out["verdict"] == "fit" and out["disk_ok"] is False:
        out["verdict"] = "does_not_fit"
        out["reason"] = "insufficient free disk"
    if out["verdict"] == "fit" and out["disk_ok"] is None:
        out["verdict"] = "unknown"
        out["reason"] = "free disk not reported"
    return out
