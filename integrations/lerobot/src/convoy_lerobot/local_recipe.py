"""Preflight for an operator-pinned, fixed local Qwen/SmolVLA development recipe.

No subprocesses, model loading, downloads, URLs or plugin imports are selected by
the registry. Activation owns the eventual component lifecycle and must compare
the newly loaded native identity with the prepared qualification before readiness.
"""

from __future__ import annotations

import copy
import json
import os
import platform
import stat
from dataclasses import dataclass
from pathlib import Path

from convoy_agent.runtime_args import canonical_config
from convoy_contracts.execution import _digest, _integer, _keys, canonical_digest
from convoy_contracts.pairing import PAIRED_PROFILE, PLANNER_RUNTIME, validate_release_manifest
from convoy_planner.artifact import artifact_descriptor, validate_gateway_identity
from convoy_planner.local_assets import bounded_json, local_path, prepare_text_assets, validate_text_receipt

from .artifact import reference_manifest, verify_assets, verify_versions

MAX_REGISTRY_BYTES = 4 * 1024 * 1024
RECIPE_FIELDS = {"manifest", "gateway_identity", "text_assets", "action_assets", "native_config"}


@dataclass(frozen=True)
class LocalRecipe:
    manifest: dict
    gateway_identity: dict
    text_assets: dict
    action_assets: Path
    native_config: dict


@dataclass(frozen=True)
class PreparedRecipe:
    release_digest: str
    manifest: dict
    gateway_identity: dict
    native_config: dict
    action_assets: Path
    model_path: Path
    binary_path: Path
    lib_dir: Path
    runtime_root: Path


def _recipe(value: dict) -> LocalRecipe:
    bounded_json(value)
    _keys(value, RECIPE_FIELDS, "local recipe")
    manifest = validate_release_manifest(value["manifest"])
    if (manifest["profile"] != PAIRED_PROFILE or manifest["planner"]["runtime"] != PLANNER_RUNTIME
            or manifest["placement"] != {"policy": "development-local-cpu", "planner": "development-local"}
            or manifest["action_manifest"] != reference_manifest()):
        raise ValueError("local recipe requires the current fixed local learned-action/text-planner release")
    identity = validate_gateway_identity(value["gateway_identity"])
    config = value["native_config"]
    _keys(config, {"ctx_size", "n_predict", "gpu_layers"}, "native config")
    if (type(config["ctx_size"]) is not int or config["ctx_size"] not in (2048, 4096)
            or type(config["n_predict"]) is not int or config["n_predict"] != 128
            or type(config["gpu_layers"]) is not int or config["gpu_layers"] != 0):
        raise ValueError("only the fixed CPU native configuration is supported")
    receipt = validate_text_receipt(value["text_assets"])
    return LocalRecipe(copy.deepcopy(manifest), copy.deepcopy(identity), receipt,
                       local_path(value["action_assets"], "action_assets"), copy.deepcopy(config))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in local recipe registry")
        result[key] = value
    return result


def load_registry(path: Path) -> dict[str, LocalRecipe]:
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("recipe registry must be a private regular file owned by this user")
    with path.open("rb") as source:
        raw = source.read(MAX_REGISTRY_BYTES + 1)
    if len(raw) > MAX_REGISTRY_BYTES:
        raise ValueError("local recipe registry exceeds its byte bound")
    value = json.loads(raw, object_pairs_hook=_unique_object)
    bounded_json(value)
    _keys(value, {"schema_version", "recipes"}, "local recipe registry")
    _integer(value["schema_version"], "registry schema_version", 1, 1)
    if not isinstance(value["recipes"], list) or not 1 <= len(value["recipes"]) <= 32:
        raise ValueError("local registry requires between one and 32 recipes")
    recipes = {}
    for item in value["recipes"]:
        recipe = _recipe(item)
        digest = canonical_digest(recipe.manifest)
        if digest in recipes:
            raise ValueError("duplicate release digest in local registry")
        recipes[digest] = recipe
    return recipes


def recipe_for(registry: dict[str, LocalRecipe], release_digest: str) -> LocalRecipe:
    _digest(release_digest, "release digest")
    try:
        return copy.deepcopy(registry[release_digest])
    except KeyError:
        raise ValueError("release has no trusted local recipe") from None


def preflight(recipe: LocalRecipe, output: Path, *, current_gateway_implementation: dict[str, str]) -> PreparedRecipe:
    """Validate before the caller stops any old bundle; never launch a component.

    output must be new. Failed staging can be retained for diagnosis or discarded
    by its owner. The trusted action/model cache remains in place and is rechecked
    by the actual runtimes before they advertise readiness.
    """
    recipe = _recipe({"manifest": recipe.manifest, "gateway_identity": recipe.gateway_identity,
                      "text_assets": recipe.text_assets, "action_assets": str(recipe.action_assets),
                      "native_config": recipe.native_config})
    identity = recipe.gateway_identity
    if identity["implementation_sha256"] != current_gateway_implementation:
        raise ValueError("qualified gateway implementation differs from current code")
    if canonical_digest(artifact_descriptor(identity)) != recipe.manifest["planner"]["artifact_sha256"]:
        raise ValueError("planner artifact differs from the current code or packages")
    config = canonical_config(recipe.native_config)
    if canonical_digest(config) != identity["config_sha256"]:
        raise ValueError("native configuration differs from qualified gateway identity")
    for field, key in (("model_sha256", "model"), ("runtime_artifact_sha256", "archive"),
                       ("binary_sha256", "binary")):
        if recipe.text_assets[key]["sha256"] != identity[field]:
            raise ValueError(f"native {key} pin differs from qualified gateway identity")
    host = recipe.text_assets["host"]
    if host["system"] != platform.system() or host["machine"] != platform.machine():
        raise ValueError("native runtime receipt was built for a different host platform")
    verify_versions()
    if not recipe.action_assets.is_dir():
        raise ValueError("local action asset root is not a directory")
    verify_assets(recipe.action_assets)
    output = Path(output).absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    model, binary = prepare_text_assets(recipe.text_assets, output)
    return PreparedRecipe(canonical_digest(recipe.manifest), copy.deepcopy(recipe.manifest),
                          copy.deepcopy(identity), copy.deepcopy(config), recipe.action_assets,
                          model, binary, output / "runtime/lib", output / "runtime")
