from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    disabled: bool
    created_at: str | None


class UserCreate(BaseModel):
    email: str
    name: str = ""
    role: str = "viewer"
    password: str = Field(min_length=8)


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class ApiTokenCreate(BaseModel):
    name: str
    expires_in_days: int | None = None


class EnrollmentCreate(BaseModel):
    label: str = ""
    group_name: str = "default"
    simulated: bool = False
    ttl_s: int | None = Field(default=None, ge=60, le=7 * 86400)
    rebind_device_id: str | None = None


class DeviceUpdate(BaseModel):
    name: str | None = None
    group_name: str | None = None
    notes: str | None = None
    settings: dict[str, Any] | None = None


class DeviceDesiredIn(BaseModel):
    release_id: str | None
    reason: str = "operator"


class EvalSetCreate(BaseModel):
    name: str
    version: str
    cases_jsonl: str | None = None
    cases: list[dict[str, Any]] | None = None
    thresholds: dict[str, Any] = Field(default_factory=dict)
    scorer: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class ModelSpecIn(BaseModel):
    source: str = "hf"  # hf | fixture | supplied
    repo: str
    revision: str
    files: list[str] = Field(default_factory=list)
    supplied_files: list[dict[str, Any]] = Field(default_factory=list)  # for source=supplied
    metadata: dict[str, Any] = Field(
        default_factory=dict
    )  # n_layers, n_kv_heads, head_dim, n_vocab, quant, format


class RuntimeSpecIn(BaseModel):
    name: str = "llama.cpp"
    tag: str = "v0.4.0"
    commit: str = "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
    build: dict[str, Any] = Field(default_factory=dict)  # cuda arch etc.
    args: list[str] = Field(default_factory=list)
    image: str | None = None
    image_digest: str | None = None
    overhead_mb: int | None = None


class ReleaseCreate(BaseModel):
    name: str
    version: str
    model: ModelSpecIn
    runtime: RuntimeSpecIn = Field(default_factory=RuntimeSpecIn)
    dependencies: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    eval_set_id: str | None = None
    hardware_profile: str = "jetson-orin-nano-8gb"
    notes: str = ""


class ReleaseResolveIn(BaseModel):
    model: ModelSpecIn


class RolloutCreate(BaseModel):
    name: str
    release_id: str
    strategy: str = "fixed"
    target: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class ScheduleCreate(BaseModel):
    name: str
    kind: str
    cron: str
    timezone: str = "UTC"
    target: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    missed_policy: str = "skip"
    grace_s: int = 600
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    cron: str | None = None
    timezone: str | None = None
    target: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    missed_policy: str | None = None
    grace_s: int | None = None
    enabled: bool | None = None


class CommandIn(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ---- agent protocol ----
class EnrollIn(BaseModel):
    enrollment_token: str
    request_id: str = Field(min_length=8, max_length=64)
    secret_hash: str = Field(min_length=64, max_length=64)
    name: str = ""
    agent_version: str = ""
    hardware: dict[str, Any] = Field(default_factory=dict)
    simulated: bool = False
    profile_id: str | None = None


class HeartbeatIn(BaseModel):
    agent_version: str = ""
    observed: dict[str, Any] = Field(default_factory=dict)
    telemetry: dict[str, Any] | None = None
    hardware: dict[str, Any] | None = None
    lease_ids: list[str] = Field(default_factory=list)


class AckIn(BaseModel):
    lease_id: str
    desired_version: int
    status: str  # running | done | failed
    result: dict[str, Any] = Field(default_factory=dict)
    observed: dict[str, Any] | None = None


class IngestIn(BaseModel):
    logs: list[dict[str, Any]] = Field(default_factory=list)
    spans: list[dict[str, Any]] = Field(default_factory=list)
    telemetry: list[dict[str, Any]] = Field(default_factory=list)
    eval_results: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    usage: list[dict[str, Any]] = Field(default_factory=list)
