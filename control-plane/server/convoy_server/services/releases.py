from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session as DbSession

from ..config import Settings
from ..hardware import plan_budget, profile
from ..ids import iso, new_id, utcnow
from ..models import Device, EvalSet, Release
from ..modelsource import FixtureSource, HFSource, ModelSourceError, Resolved, SuppliedSource

# Pinned runtime (verified: git tag v0.4.0 peels to this commit on github.com/ggml-org/llama.cpp)
LLAMA_CPP_TAG = "v0.4.0"
LLAMA_CPP_COMMIT = "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
DEFAULT_RUNTIME_ARGS = [
    "--fit", "off", "--ctx-size", "2048", "--parallel", "1", "--n-predict", "128",
    "--batch-size", "256", "--ubatch-size", "128", "--n-gpu-layers", "99", "--jinja", "--metrics",
]  # fmt: skip
DEFAULT_CONFIG = {
    "ctx_size": 2048,
    "parallel": 1,
    "n_predict": 128,
    "batch_size": 256,
    "ubatch_size": 128,
    "n_gpu_layers": 99,
    "kv_cache_type": "f16",
    "temperature": 0.0,
    "seed": 42,
    "health_timeout_s": 120,
    "probe_prompt": "Reply with the single word OK.",
}


class ReleaseError(ValueError):
    pass


def resolve_model(db: DbSession, settings: Settings, spec: dict[str, Any]) -> Resolved:
    source = spec.get("source", "hf")
    if source == "fixture":
        if not settings.simulator:
            raise ReleaseError("fixture source is only available in simulator mode")
        return FixtureSource(settings, db).resolve(spec["repo"], spec["revision"], spec.get("files", []))
    if source == "supplied":
        return SuppliedSource(settings).resolve(
            spec["repo"], spec["revision"], spec.get("supplied_files", [])
        )
    if source == "hf":
        try:
            return HFSource(settings).resolve(spec["repo"], spec["revision"], spec.get("files", []))
        except ModelSourceError:
            raise
        except Exception as e:  # network blocked etc.
            raise ModelSourceError(f"hf unreachable: {type(e).__name__}: {e}") from e
    raise ReleaseError(f"unknown model source {source}")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def release_content_hash(
    model: dict, runtime: dict, deps: dict, config: dict, budget: dict, eval_set_hash: str | None, hw: str
) -> str:
    payload = _canonical(
        {
            "model": model,
            "runtime": runtime,
            "dependencies": deps,
            "config": config,
            "budget": budget,
            "eval_set": eval_set_hash,
            "hardware_profile": hw,
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def create_release(
    db: DbSession, settings: Settings, data: dict[str, Any], created_by: str | None
) -> Release:
    spec = data["model"]
    resolved = resolve_model(db, settings, spec)
    if not resolved.files:
        raise ReleaseError("release must include at least one model file")
    model = {
        "source": resolved.source,
        "repo": resolved.repo,
        "revision": resolved.revision_requested,
        "commit": resolved.commit,
        "files": [
            {"path": f.path, "size": f.size, "sha256": f.sha256, "url": f.url, "auth": f.auth}
            for f in resolved.files
        ],
        "total_bytes": sum(f.size for f in resolved.files),
        "metadata": dict(spec.get("metadata") or {}),
        "verified": resolved.verified,
    }
    rt_in = dict(data.get("runtime") or {})
    runtime = {
        "name": rt_in.get("name", "llama.cpp"),
        "tag": rt_in.get("tag", LLAMA_CPP_TAG),
        "commit": rt_in.get("commit", LLAMA_CPP_COMMIT),
        "build": {
            "cuda_arch": "87",
            "cmake": ["-DGGML_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=87"],
            **(rt_in.get("build") or {}),
        },
        "args": rt_in.get("args") or list(DEFAULT_RUNTIME_ARGS),
        "image": rt_in.get("image"),
        "image_digest": rt_in.get("image_digest"),
        "overhead_mb": rt_in.get("overhead_mb"),
        "validation": "pending_device_verification",
    }
    config = dict(DEFAULT_CONFIG)
    config.update(data.get("config") or {})
    deps = dict(data.get("dependencies") or {})
    budget = dict(data.get("budget") or {})
    hw = data.get("hardware_profile") or "jetson-orin-nano-8gb"
    if runtime.get("overhead_mb"):
        budget.setdefault("runtime_overhead_mb", runtime["overhead_mb"])
    es_hash = None
    if data.get("eval_set_id"):
        es = db.get(EvalSet, data["eval_set_id"])
        if not es:
            raise ReleaseError("eval set not found")
        es_hash = es.content_hash
    chash = release_content_hash(model, runtime, deps, config, budget, es_hash, hw)
    existing = db.query(Release).filter(Release.content_hash == chash).first()
    if existing:
        raise ReleaseError(
            f"identical release already exists: {existing.name} {existing.version} ({existing.id})"
        )
    if db.query(Release).filter(Release.name == data["name"], Release.version == data["version"]).first():
        raise ReleaseError("a release with this name and version already exists; releases are immutable")
    plan = plan_budget(model, config, budget, profile(hw))
    row = Release(
        id=new_id("rel"),
        name=data["name"],
        version=data["version"],
        content_hash=chash,
        model=model,
        runtime=runtime,
        dependencies=deps,
        config=config,
        budget=budget,
        eval_set_id=data.get("eval_set_id"),
        hardware_profile=hw,
        simulated=(resolved.source == "fixture"),
        notes=data.get("notes", ""),
        provenance={
            "resolved_at": iso(utcnow()),
            "resolver": resolved.verified,
            "plan": plan,
            "eval_set_hash": es_hash,
        },
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def release_manifest(rel: Release, settings: Settings, device: Device | None = None) -> dict[str, Any]:
    """The immutable document the agent consumes. Never includes HF tokens; the agent gets
    `auth` hints and uses its own configured HF token when auth == 'hf'."""
    es = None
    if rel.eval_set_id:
        es = {"id": rel.eval_set_id}
    return {
        "release_id": rel.id,
        "name": rel.name,
        "version": rel.version,
        "content_hash": rel.content_hash,
        "hardware_profile": rel.hardware_profile,
        "simulated": rel.simulated,
        "model": rel.model,
        "runtime": rel.runtime,
        "dependencies": rel.dependencies,
        "config": rel.config,
        "budget": rel.budget,
        "eval_set": es,
        "created_at": iso(rel.created_at),
    }


def plan_for_device(rel: Release, dev: Device) -> dict[str, Any]:
    measured = {}
    t = dev.last_telemetry or {}
    for k in ("mem_total_mb", "os_baseline_mb", "disk_free_mb"):
        if t.get(k) is not None:
            measured[k] = t[k]
    return plan_budget(
        rel.model, rel.config, rel.budget, profile(dev.hardware_profile), dev.settings or {}, measured
    )
