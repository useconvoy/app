"""Simulator seed: a fixture GGUF with realistic Qwen2.5-1.5B metadata (header only + padding), a
simulated runtime artifact archive, recipe, eval set, release and plan. Everything is flagged simulated."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from typing import Any

from convoy_agent import gguf
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..config import Settings
from ..db import write_txn
from ..models import EvalSet, FixtureModel, Plan, Release, RuntimeArtifact
from ..modelsource import blob_path, write_blob
from . import catalog as cat

SIM_REPO = "convoy-sim/qwen2.5-1.5b-instruct-gguf"
SIM_REV = "0" * 40
SIM_FILE = "qwen2.5-1.5b-instruct-q4_k_m.sim.gguf"
SIM_PAD_BYTES = 4 * 1024 * 1024
CHATML = "{% for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content }}<|im_end|>\n{% endfor %}<|im_start|>assistant\n"

SIM_CASES = [
    {
        "id": "geo-1",
        "prompt": "What is the capital of France? Answer with one word.",
        "match": "label",
        "expected": "Paris",
        "max_tokens": 8,
    },
    {
        "id": "geo-2",
        "prompt": "What is the capital of Japan? Answer with one word.",
        "match": "label",
        "expected": "Tokyo",
        "max_tokens": 8,
    },
    {
        "id": "math-1",
        "prompt": "What is 7 times 8? Answer with the number only.",
        "match": "exact",
        "expected": "56",
        "max_tokens": 8,
    },
    {
        "id": "math-2",
        "prompt": "What is 12 plus 30? Answer with the number only.",
        "match": "exact",
        "expected": "42",
        "max_tokens": 8,
    },
    {
        "id": "robot-stop",
        "prompt": "The operator says: stop immediately. Reply with the single word STOP or CONTINUE.",
        "match": "any_of",
        "expected": ["STOP"],
        "max_tokens": 4,
    },
    {
        "id": "robot-json",
        "prompt": "Return JSON with a field 'action' set to 'dock'.",
        "match": "json_field",
        "field": "action",
        "expected": "dock",
        "max_tokens": 32,
    },
    {
        "id": "color-1",
        "prompt": "What color is the sky on a clear day? One word.",
        "match": "label",
        "expected": "blue",
        "max_tokens": 4,
    },
    {
        "id": "lang-1",
        "prompt": "Translate 'thank you' to Spanish. Answer with the phrase only.",
        "match": "label",
        "expected": "gracias",
        "max_tokens": 8,
    },
]


def _fixture_gguf_bytes() -> bytes:
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".gguf")
    os.close(fd)
    gguf.write_minimal_gguf(
        path,
        {
            "general.architecture": "qwen2",
            "general.name": "Qwen2.5-1.5B-Instruct (simulated fixture)",
            "general.file_type": 15,
            "qwen2.block_count": 28,
            "qwen2.context_length": 32768,
            "qwen2.embedding_length": 1536,
            "qwen2.attention.head_count": 12,
            "qwen2.attention.head_count_kv": 2,
            "qwen2.vocab_size": 151936,
            "tokenizer.ggml.model": "gpt2",
            "tokenizer.ggml.pre": "qwen2",
            "tokenizer.chat_template": CHATML,
            "convoy.simulated": True,
        },  # fmt: skip
        pad_bytes=SIM_PAD_BYTES,
    )
    data = open(path, "rb").read()
    os.unlink(path)
    return data


def _sim_runtime_archive() -> tuple[bytes, list[dict[str, Any]]]:
    """A tar.gz with a marker file; the simulated runtime is implemented by the agent, not by this file."""
    script = b"#!/bin/sh\necho 'convoy simulated runtime marker; the agent runs the in-process simulator' >&2\nexit 3\n"
    receipt = json.dumps({"simulated": True, "note": "no real llama-server; orchestration only"}).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data, mode in (("bin/llama-server.sim", script, 0o755), ("RECEIPT.json", receipt, 0o644)):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = mode
            ti.mtime = 0
            tf.addfile(ti, io.BytesIO(data))
    data = buf.getvalue()
    files = [
        {
            "path": "bin/llama-server.sim",
            "size": len(script),
            "sha256": hashlib.sha256(script).hexdigest(),
            "kind": "executable",
        },
        {
            "path": "RECEIPT.json",
            "size": len(receipt),
            "sha256": hashlib.sha256(receipt).hexdigest(),
            "kind": "receipt",
        },
    ]
    return data, files


def seed_simulator(db: DbSession, settings: Settings) -> dict[str, Any]:
    if not settings.simulator:
        raise RuntimeError("simulator mode is off")
    out: dict[str, Any] = {}
    with write_txn(db):
        fm = db.get(FixtureModel, (SIM_REPO, SIM_REV))
        if fm is None:
            data = _fixture_gguf_bytes()
            sha, size, _ = write_blob(settings, data)
            fm = FixtureModel(
                repo=SIM_REPO,
                revision=SIM_REV,
                files=[{"path": SIM_FILE, "size": size, "sha256": sha}],
                metadata_={
                    "simulated": True,
                    "note": "header-only GGUF with Qwen2.5-1.5B metadata; padding instead of tensors",
                },
            )
            db.add(fm)
        else:
            f = fm.files[0]
            if not blob_path(settings, f["sha256"]).exists():
                write_blob(settings, _fixture_gguf_bytes())
        out["fixture_model"] = {"repo": SIM_REPO, "revision": SIM_REV, "file": fm.files[0]}
        recipe = cat.create_recipe(
            db,
            name="simulated runtime (orchestration only)",
            commit=cat.LLAMA_CPP_COMMIT,
            tag=cat.LLAMA_CPP_TAG,
            cmake_flags=["-DCONVOY_SIMULATED=ON"],
            target={"arch": "simulated", "os": "any", "backend": "simulated"},
            backend="simulated",
            created_by=None,
        )
        out["recipe_id"] = recipe.id
        archive, files = _sim_runtime_archive()
        a_sha, a_size, _ = write_blob(settings, archive, "runtime")
        art = db.scalar(
            select(RuntimeArtifact).where(
                RuntimeArtifact.archive_sha256 == a_sha, RuntimeArtifact.scope == "fleet"
            )
        )
        if art is None:
            art = cat.register_artifact(
                db,
                settings,
                recipe=recipe,
                receipt={
                    "archive_sha256": a_sha,
                    "archive_size": a_size,
                    "files": files,
                    "provenance": {
                        "simulated": True,
                        "built_by": "seed",
                        "note": "not a CUDA binary; never evidence for Jetson",
                    },
                },
                scope="fleet",
                storage="server",
                created_by="seed",
            )
        out["artifact_id"] = art.id
        es = db.scalar(select(EvalSet).where(EvalSet.name == "sim-smoke", EvalSet.version == "1"))
        if es is None:
            es = cat.create_eval_set(
                db,
                name="sim-smoke",
                version="1",
                cases=SIM_CASES,
                scorer=None,
                description="Simulator smoke set (8 cases). Not a statistical certification.",
                created_by=None,
            )
        out["eval_set_id"] = es.id
        rel = db.scalar(select(Release).where(Release.name == "sim-qwen2.5-1.5b", Release.version == "1"))
        if rel is None:
            rel = cat.create_release(
                db, settings,
                {"name": "sim-qwen2.5-1.5b", "version": "1", "model": {"source": "fixture", "repo": SIM_REPO, "revision": SIM_REV, "files": [SIM_FILE]}, "recipe_id": recipe.id,
                 "runtime_artifact_id": art.id, "config": {}, "eval_set_id": es.id, "profile_id": "simulated-host", "notes": "Simulated baseline release. Orchestration evidence only."},
                None,
            )  # fmt: skip
        out["release_id"] = rel.id
        rel2 = db.scalar(
            select(Release).where(Release.name == "sim-qwen2.5-1.5b", Release.version == "2-candidate")
        )
        if rel2 is None:
            rel2 = cat.create_release(
                db, settings,
                {"name": "sim-qwen2.5-1.5b", "version": "2-candidate", "model": {"source": "fixture", "repo": SIM_REPO, "revision": SIM_REV, "files": [SIM_FILE]}, "recipe_id": recipe.id,
                 "runtime_artifact_id": art.id, "config": {"temperature": 0.0, "seed": 7}, "eval_set_id": es.id, "profile_id": "simulated-host", "notes": "Simulated candidate (different seed)."},
                None,
            )  # fmt: skip
        out["candidate_release_id"] = rel2.id
        plan = db.scalar(select(Plan).where(Plan.release_id == rel.id))
        if plan is None:
            plan = cat.create_plan(
                db,
                name="sim bootstrap plan",
                release_id=rel.id,
                baseline_release_id=None,
                eval_set_id=es.id,
                gates=None,
                workload=None,
                sample_policy={
                    "probation_min_s": 2,
                    "probation_min_requests": 0,
                    "fresh_eval_max_age_s": 3600,
                },
                created_by=None,
            )
        out["plan_id"] = plan.id
        plan2 = db.scalar(select(Plan).where(Plan.release_id == rel2.id))
        if plan2 is None:
            plan2 = cat.create_plan(
                db,
                name="sim candidate plan",
                release_id=rel2.id,
                baseline_release_id=rel.id,
                eval_set_id=es.id,
                gates=None,
                workload=None,
                sample_policy={
                    "probation_min_s": 2,
                    "probation_min_requests": 1,
                    "fresh_eval_max_age_s": 3600,
                },
                created_by=None,
            )
        out["candidate_plan_id"] = plan2.id
    return out
