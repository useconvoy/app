"""Release identity binds loaded bytes and every qualified inference transform."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path

from convoy_contracts.execution import VISUAL_PROFILE, VISUAL_SPEC, canonical_digest

ASSETS = json.loads(Path(__file__).with_name("assets.json").read_text())
VERSIONS = {
    "lerobot": "0.6.1", "metaworld": "3.0.0", "mujoco": "3.3.0", "gymnasium": "1.2.1",
    "numpy": "2.2.6", "torch": "2.11.0", "torchvision": "0.26.0", "transformers": "5.5.4",
    "safetensors": "0.8.0", "huggingface-hub": "1.33.0", "pillow": "12.3.0",
}
RUNTIME = "lerobot-smolvla-metaworld-cpu-v1"
ARTIFACT = {
    "schema_version": 1, "runtime": RUNTIME, "profile": VISUAL_PROFILE,
    "assets": ASSETS, "packages": VERSIONS, "observation_action_contract": VISUAL_SPEC,
    "execution": {
        "device": "cpu", "dtype": "float32", "torch_threads": 4,
        "strict_weight_load": True, "load_base_vlm_weights": False,
        "model_seed_per_mission": 0, "actions_per_replan": 1, "compile_model": False,
        "preprocessing": "saved-processors-rgb-chw-float32-divide255-state4-task-v1",
        "postprocessing": "saved-unnormalizer-then-clip-minus1-plus1-float32-v1",
        "reset": "policy.reset-and-torch.manual_seed-before-every-mission-v1",
    },
}
ARTIFACT_SHA256 = canonical_digest(ARTIFACT)


def verify_versions(names=None):
    actual = {name: version(name) for name in (names or VERSIONS)}
    if any(actual[name] != VERSIONS[name] for name in actual):
        raise ValueError(f"installed packages do not match the qualified visual release: {actual}")


def verify_assets(root: Path):
    for relative, expected in ASSETS["assets"].items():
        digest = hashlib.sha256()
        with (root / relative).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise ValueError(f"artifact checksum mismatch: {relative}")


def reference_manifest():
    return {
        "schema_version": 1, "profile": VISUAL_PROFILE,
        "policy": {"runtime": RUNTIME, "artifact_sha256": ARTIFACT_SHA256},
        "environment": {"name": "pick-place-v3", "metaworld": "3.0.0", "mujoco": "3.3.0"},
        "execution": {"max_steps": 500, "decision_timeout_ms": 5000, "mission_timeout_s": 300},
    }
