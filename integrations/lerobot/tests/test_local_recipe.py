"""Real tiny local archives exercise preflight without loading model weights."""

from __future__ import annotations

import copy
import io
import json
import platform
import tarfile
from pathlib import Path

import pytest
from convoy_agent.runtime_args import canonical_config
from convoy_contracts.execution import canonical_digest
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
)
from convoy_planner.artifact import artifact_descriptor
from convoy_planner.local_assets import sha256

from convoy_lerobot import local_recipe
from convoy_lerobot.artifact import reference_manifest


def archive_at(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o755 if name == "bin/llama-server" else 0o644
            archive.addfile(member, io.BytesIO(data))


def bind_planner(entry: dict) -> None:
    entry["manifest"]["planner"]["artifact_sha256"] = canonical_digest(artifact_descriptor(entry["gateway_identity"]))


def registry_at(path: Path, entries: list[dict]) -> dict:
    path.write_text(json.dumps({"schema_version": 1, "recipes": entries}))
    path.chmod(0o600)
    return local_recipe.load_registry(path)


@pytest.fixture
def sample(tmp_path, monkeypatch):
    root = tmp_path / "original-runtime"
    (root / "bin").mkdir(parents=True)
    (root / "lib").mkdir()
    binary = root / "bin/llama-server"
    binary.write_bytes(b"tiny native executable fixture")
    library = root / "lib/libtest.so"
    library.write_bytes(b"tiny native library fixture")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"tiny model fixture")
    archive = tmp_path / "runtime.tar.gz"
    archive_at(archive, {"bin/llama-server": binary.read_bytes(), "lib/libtest.so": library.read_bytes()})
    action_root = tmp_path / "action-assets"
    action_root.mkdir()
    receipt = {
        "schema_version": 1,
        "source": {"repo": "https://github.com/ggml-org/llama.cpp", "commit": "a" * 40, "dirty": False},
        "host": {"system": platform.system(), "machine": platform.machine()},
        "runtime_root": str(root),
        "model": {"path": str(model), "sha256": sha256(model), "bytes": model.stat().st_size},
        "archive": {"path": str(archive), "sha256": sha256(archive)},
        "binary": {"path": str(binary), "sha256": sha256(binary)},
        "libraries": [{"path": str(library), "sha256": sha256(library)}],
    }
    config = {"ctx_size": 2048, "n_predict": 128, "gpu_layers": 0}
    implementation = {name: "b" * 64 for name in (
        "gateway.py", "runtime.py", "owned_process.py", "runtime_args.py", "psutil-7.2.2",
    )}
    identity = {
        "model_sha256": receipt["model"]["sha256"], "runtime_artifact_sha256": receipt["archive"]["sha256"],
        "binary_sha256": receipt["binary"]["sha256"], "template_sha256": "c" * 64,
        "config_sha256": canonical_digest(canonical_config(config)), "implementation_sha256": implementation,
        "simulated": False, "release_id": "qualified-native-release", "runtime_generation": 1,
        "gateway_incarnation": "qualified-gateway", "gateway_epoch": 1,
    }
    entry = {
        "manifest": {
            "schema_version": 2, "profile": PAIRED_PROFILE, "action_manifest": reference_manifest(),
            "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": "d" * 64,
                        "protocol_sha256": PLANNER_PROTOCOL_SHA256},
            "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
            "planning": {"timeout_ms": 30000},
            "placement": {"policy": "development-local-cpu", "planner": "development-local"},
        },
        "gateway_identity": identity, "native_config": config,
        "text_assets": receipt, "action_assets": str(action_root),
    }
    bind_planner(entry)
    checks = []
    monkeypatch.setattr(local_recipe, "verify_versions", lambda: checks.append("versions"))
    monkeypatch.setattr(local_recipe, "verify_assets", lambda path: checks.append(("assets", path)))
    return entry, copy.deepcopy(implementation), checks


def test_fixed_recipes_index_and_stage_pinned_members_without_copying_model(tmp_path, sample):
    entry, implementation, checks = sample
    second = copy.deepcopy(entry)
    second["native_config"]["ctx_size"] = 4096
    second["gateway_identity"]["config_sha256"] = canonical_digest(canonical_config(second["native_config"]))
    bind_planner(second)
    registry = registry_at(tmp_path / "registry.json", [entry, second])
    assert set(registry) == {canonical_digest(item["manifest"]) for item in (entry, second)}
    digest = canonical_digest(entry["manifest"])
    recipe = local_recipe.recipe_for(registry, digest)
    prepared = local_recipe.preflight(recipe, tmp_path / "staging", current_gateway_implementation=implementation)
    assert checks == ["versions", ("assets", Path(entry["action_assets"]))]
    assert prepared.release_digest == digest
    assert prepared.manifest == entry["manifest"]
    assert prepared.model_path == Path(entry["text_assets"]["model"]["path"])
    assert prepared.binary_path == tmp_path / "staging/runtime/bin/llama-server"
    assert sha256(prepared.binary_path) == entry["text_assets"]["binary"]["sha256"]
    assert sha256(prepared.lib_dir / "libtest.so") == entry["text_assets"]["libraries"][0]["sha256"]
    assert prepared.native_config == canonical_config(entry["native_config"])
    recipe.manifest["planning"]["timeout_ms"] = 1
    assert prepared.manifest["planning"]["timeout_ms"] == 30000
    assert registry[digest].manifest["planning"]["timeout_ms"] == 30000
    with pytest.raises(ValueError, match="no trusted local recipe"):
        local_recipe.recipe_for(registry, "0" * 64)
    with pytest.raises(FileExistsError):
        local_recipe.preflight(registry[digest], tmp_path / "staging", current_gateway_implementation=implementation)


@pytest.mark.parametrize("fault", ["duplicate", "command", "remote", "changed_action", "extra_config", "coerced_config"])
def test_registry_rejects_unsupported_execution_choices(tmp_path, sample, fault):
    entry, _, _ = sample
    entries = [entry]
    if fault == "duplicate":
        entries.append(copy.deepcopy(entry))
    elif fault == "command":
        entry["command"] = ["/bin/sh"]
    elif fault == "remote":
        entry["manifest"]["placement"]["planner"] = "development-jetson-lan"
    elif fault == "changed_action":
        entry["manifest"]["action_manifest"]["policy"]["artifact_sha256"] = "e" * 64
    elif fault == "extra_config":
        entry["native_config"]["parallel"] = 2
    else:
        entry["native_config"]["gpu_layers"] = False
    with pytest.raises(ValueError):
        registry_at(tmp_path / "registry.json", entries)


@pytest.mark.parametrize("fault", ["implementation", "planner_artifact", "config", "model", "archive", "binary", "host"])
def test_preflight_rejects_stale_qualification_before_staging(tmp_path, sample, fault):
    entry, implementation, checks = sample
    if fault == "implementation":
        implementation["gateway.py"] = "f" * 64
    elif fault == "planner_artifact":
        entry["manifest"]["planner"]["artifact_sha256"] = "f" * 64
    elif fault == "host":
        entry["text_assets"]["host"]["machine"] = "unsupported-architecture"
    else:
        key = {"config": "config_sha256", "model": "model_sha256",
               "archive": "runtime_artifact_sha256", "binary": "binary_sha256"}[fault]
        entry["gateway_identity"][key] = "f" * 64
        bind_planner(entry)
    registry = registry_at(tmp_path / "registry.json", [entry])
    with pytest.raises(ValueError):
        local_recipe.preflight(next(iter(registry.values())), tmp_path / "staging",
                               current_gateway_implementation=implementation)
    assert not (tmp_path / "staging").exists()
    assert checks == []


@pytest.mark.parametrize("verifier", ["verify_versions", "verify_assets"])
def test_heavy_asset_and_dependency_verification_precedes_staging(tmp_path, sample, monkeypatch, verifier):
    entry, implementation, _ = sample
    registry = registry_at(tmp_path / "registry.json", [entry])

    def reject(*_):
        raise ValueError("changed action dependency or asset")

    monkeypatch.setattr(local_recipe, verifier, reject)
    with pytest.raises(ValueError, match="changed action"):
        local_recipe.preflight(next(iter(registry.values())), tmp_path / "staging",
                               current_gateway_implementation=implementation)
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize("fault", ["model", "archive", "library", "traversal"])
def test_preflight_checks_actual_text_bytes_and_extracted_members(tmp_path, sample, fault):
    entry, implementation, _ = sample
    receipt = entry["text_assets"]
    if fault in {"model", "archive"}:
        Path(receipt[fault]["path"]).write_bytes(b"changed bytes")
    elif fault == "library":
        receipt["libraries"][0]["sha256"] = "e" * 64
    else:
        archive = Path(receipt["archive"]["path"])
        archive_at(archive, {"../escaped": b"must not escape"})
        receipt["archive"]["sha256"] = sha256(archive)
        entry["gateway_identity"]["runtime_artifact_sha256"] = sha256(archive)
        bind_planner(entry)
    registry = registry_at(tmp_path / "registry.json", [entry])
    with pytest.raises((ValueError, tarfile.FilterError)):
        local_recipe.preflight(next(iter(registry.values())), tmp_path / "staging",
                               current_gateway_implementation=implementation)
    assert not (tmp_path / "staging/escaped").exists()
    assert not (tmp_path / "escaped").exists()


def test_registry_private_bounded_and_rejects_duplicate_json_keys(tmp_path, sample, monkeypatch):
    entry, _, _ = sample
    path = tmp_path / "registry.json"
    registry_at(path, [entry])
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private regular"):
        local_recipe.load_registry(path)
    path.chmod(0o600)
    monkeypatch.setattr(local_recipe, "MAX_REGISTRY_BYTES", 8)
    with pytest.raises(ValueError, match="byte bound"):
        local_recipe.load_registry(path)
    monkeypatch.setattr(local_recipe, "MAX_REGISTRY_BYTES", 1024)
    path.write_text('{"schema_version":1,"schema_version":1,"recipes":[]}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        local_recipe.load_registry(path)
