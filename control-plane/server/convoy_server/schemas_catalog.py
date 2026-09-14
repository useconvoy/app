"""Strict, bounded input schemas for immutable catalog objects (R18). extra='forbid', finite numbers
only, validated before any transaction so invalid state never reaches an immutable row."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Strict = ConfigDict(extra="forbid", allow_inf_nan=False, str_max_length=65536, strict=True)


class GateIn(BaseModel):
    model_config = Strict
    metric: str = Field(min_length=1, max_length=64)
    op: Literal["min", "max"]
    limit: float
    required: bool = True
    evidence: str | None = Field(default=None, max_length=32)


class WorkloadIn(BaseModel):
    model_config = Strict
    warmup: int = Field(default=1, ge=0, le=16)
    ordering: Literal["fixed"] = "fixed"
    max_tokens: int = Field(default=128, ge=1, le=4096)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    co_workload: Literal["none", "robot_sim"] = "none"
    request_timeout_s: int = Field(default=30, ge=1, le=300)


class SamplePolicyIn(BaseModel):
    model_config = Strict
    probation_min_s: int = Field(default=60, ge=0, le=7 * 86400)
    probation_min_requests: int = Field(default=0, ge=0, le=100000)
    fresh_eval_max_age_s: int = Field(default=3600, ge=60, le=30 * 86400)


class PlanIn(BaseModel):
    model_config = Strict
    name: str = Field(min_length=1, max_length=255)
    release_id: str = Field(min_length=1, max_length=32)
    baseline_release_id: str | None = Field(default=None, max_length=32)
    eval_set_id: str | None = Field(default=None, max_length=32)
    gates: list[GateIn] | None = Field(default=None, max_length=32)
    workload: WorkloadIn | None = None
    sample_policy: SamplePolicyIn | None = None


class RuntimeConfigIn(BaseModel):
    """Mirrors convoy_agent.runtime_args bounds; the agent module is the authority and is re-run."""

    model_config = Strict
    ctx_size: int = Field(default=2048, ge=512, le=32768)
    n_predict: int = Field(default=128, ge=1, le=4096)
    batch_size: int = Field(default=256, ge=1, le=4096)
    ubatch_size: int = Field(default=128, ge=1, le=4096)
    parallel: Literal[1] = 1
    gpu_layers: int | Literal["all"] = "all"
    cache_ram_mib: Literal[0] = 0
    context_shift: Literal[False] = False
    fit: Literal["off"] = "off"
    flash_attn: Literal["auto", "on", "off"] = "auto"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    health_timeout_s: int = Field(default=120, ge=10, le=900)
    request_deadline_s: int = Field(default=30, ge=1, le=300)
    queue_depth: int = Field(default=4, ge=1, le=16)
    sim: dict[str, Any] | None = None

    @field_validator("gpu_layers")
    @classmethod
    def _gl(cls, v):
        if isinstance(v, bool) or (isinstance(v, int) and not 0 <= v <= 999):
            raise ValueError("gpu_layers must be 'all' or 0..999")
        return v

    @field_validator("sim")
    @classmethod
    def _sim(cls, v):
        if v is None:
            return v
        allowed = {
            "wrong_every",
            "latency_ms",
            "hang",
            "hang_s",
            "crash_after_requests",
            "health_fail",
            "health_delay_s",
            "runtime_mb",
            "weights_resident_fraction",
            "mem_total_mb",
            "temp_c",
            "disk_free_mb",
            "fail_stage",
        }
        bad = sorted(set(v) - allowed)
        if bad:
            raise ValueError(f"unknown sim keys: {bad}")
        for k, x in v.items():
            if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
                raise ValueError(f"sim.{k} must be finite")
            if not isinstance(x, (int, float, str, bool)):
                raise ValueError(f"sim.{k} must be scalar")
        return v


class BudgetIn(BaseModel):
    model_config = Strict
    runtime_overhead_mb: int | None = Field(default=None, ge=0, le=16384)
    robot_reserve_mb: int | None = Field(default=None, ge=0, le=16384)
    margin_mb: int | None = Field(default=None, ge=0, le=16384)


class ScorerIn(BaseModel):
    model_config = Strict
    version: str = "1"
    warmup: int = Field(default=1, ge=0, le=16)
    ordering: Literal["fixed"] = "fixed"


class ModelIn(BaseModel):
    model_config = Strict
    source: Literal["hf", "supplied", "fixture"] = "hf"
    repo: str = Field(min_length=3, max_length=255)
    revision: str = Field(min_length=1, max_length=64)
    files: list[str] = Field(default_factory=list, max_length=4)
    supplied_files: list[dict[str, Any]] = Field(default_factory=list, max_length=4)
    gguf: dict[str, Any] | None = None


class TemplateIn(BaseModel):
    model_config = Strict
    name: str = Field(default="reviewed", max_length=64)
    text: str = Field(min_length=1, max_length=65536)
    reviewed_by: str | None = Field(default=None, max_length=255)


class ReleaseIn(BaseModel):
    model_config = Strict
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    model: ModelIn
    recipe_id: str = Field(min_length=1, max_length=32)
    runtime_artifact_id: str | None = Field(default=None, max_length=32)
    template: TemplateIn | None = None
    config: RuntimeConfigIn | None = None
    budget: BudgetIn | None = None
    eval_set_id: str | None = Field(default=None, max_length=32)
    profile_id: str | None = Field(default=None, max_length=64)
    notes: str = Field(default="", max_length=4000)


class ReceiptFileIn(BaseModel):
    model_config = Strict
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    size: int | None = Field(default=None, ge=0)
    kind: str | None = Field(default=None, max_length=32)
    soname: str | None = Field(default=None, max_length=128)


class ReceiptIn(BaseModel):
    model_config = Strict
    archive_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    archive_size: int = Field(ge=1)
    files: list[ReceiptFileIn] = Field(min_length=1, max_length=512)
    provenance: dict[str, Any] = Field(default_factory=dict)
    system_dependencies: list[str] = Field(default_factory=list, max_length=256)
    schema_version: int = 1


class ArtifactIn(BaseModel):
    model_config = Strict
    recipe_id: str = Field(min_length=1, max_length=32)
    receipt: ReceiptIn
    scope: str = Field(default="fleet", max_length=64)
    storage: Literal["device", "server"] = "device"


class RecipeIn(BaseModel):
    model_config = Strict
    name: str = Field(min_length=1, max_length=255)
    commit: str = Field(min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$")
    tag: str | None = Field(default=None, max_length=64)
    cmake_flags: list[str] = Field(default_factory=list, max_length=64)
    target: dict[str, str] = Field(default_factory=dict)
    # explicit CUDA track (jp623 = L4T 36.5.2 pin, l4t3647 = board-observed L4T 36.4.7); mutually
    # exclusive with a full `target`, and only meaningful for the cuda backend
    track: str | None = Field(default=None, max_length=16, pattern=r"^[a-z0-9_-]+$")
    backend: Literal["cuda", "cpu", "simulated"] = "cuda"


class EvalSetIn(BaseModel):
    model_config = Strict
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    cases_jsonl: str | None = Field(default=None, max_length=8_000_000)
    cases: list[dict[str, Any]] | None = Field(default=None, max_length=5000)
    scorer: ScorerIn | None = None
    description: str = Field(default="", max_length=4000)
